"""On-demand, bounded article excerpts. API keys and source bodies stay on the server."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode

try:
    from . import news_relay as relay
    from .storage import atomic_write_json, read_json
    from .translation_quality import inspect_translation
except ImportError:
    import news_relay as relay
    from storage import atomic_write_json, read_json
    from translation_quality import inspect_translation

VERSION = 1
MAX_CHARS = 1800
INSTRUCTIONS = (
    "Translate this news excerpt faithfully into natural Korean. Preserve the speaker of each "
    "statement, attribution, quotes, uncertainty, negation, names, dates and numbers. "
    "Do not add facts, opinions, predictions or a headline. Do not shorten the excerpt."
)


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.ignored += 1
        if not self.ignored and tag in {'p', 'div', 'br', 'li', 'h2', 'h3', 'blockquote'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in {'p', 'div', 'li', 'h2', 'h3', 'blockquote'}:
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.ignored:
            self.parts.append(text)


def excerpt(value: str) -> str:
    """Take up to three leading paragraphs; never synthesize claims from the title."""
    parser = ArticleText()
    parser.feed(str(value or '')[:200_000])
    paragraphs = []
    remaining = MAX_CHARS
    for line in ''.join(parser.parts).splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        if (not line or re.fullmatch(r'https?://\S+', line) or
                re.match(r'^(?:read (?:also|next|more)|also read|see also|photo(?:graph)? (?:by|via|credit)|image (?:via|credit)|disclaimer)\b', line, re.I)):
            continue
        if len(line) > remaining:
            # Keep complete sentences only. Oversized first sentences use the teaser instead.
            ends = list(re.finditer(r'[.!?。](?:[”\'"’])?(?=\s|$)', line[:remaining + 1]))
            if not ends:
                break
            line = line[:ends[-1].end()]
        paragraphs.append(line)
        remaining -= len(line) + 2
        if len(paragraphs) == 3 or remaining < 100:
            break
    return '\n\n'.join(paragraphs)


def fetch_massive_article(item: dict, args) -> dict:
    match = re.fullmatch(r'benzinga:(\d+)', str(item.get('id', '')))
    if not match or not args.massive_token:
        return {}
    published = relay.parse_iso_datetime(item.get('publishedAt', ''))
    if published is None:
        return {}
    query = urlencode({
        'published.gte': published.isoformat(),
        'published.lte': (published + timedelta(seconds=1)).isoformat(),
        'limit': 100,
        'fields': 'benzinga_id,body,teaser,title,url,published,last_updated',
    })
    response = relay.request_json(
        'https://api.massive.com/benzinga/v2/news?' + query,
        headers={'Authorization': 'Bearer ' + args.massive_token},
        timeout=8, retries=0, retry_backoff=1, debug=False,
    )
    # The time filter may include several stories. Never use a nearby story's body.
    return next((row for row in response.payload.get('results', [])
                 if str(row.get('benzinga_id')) == match[1]), {})


def identity(item: dict) -> str:
    fields = [VERSION, item.get('id'), item.get('url'), item.get('title'),
              item.get('updatedAt') or item.get('publishedAt'), item.get('description') or item.get('snippet')]
    return sha256(str(fields).encode()).hexdigest()


class ArticleSummaryService:
    def __init__(self, path: Path, feeds: dict[str, Path], options: list[str] | None = None):
        self.path, self.feeds = path, feeds
        self.options = options or ['--translate-provider', 'none']
        raw = read_json(path, {})
        self.cache = raw.get('entries', {}) if isinstance(raw, dict) and raw.get('version') == VERSION else {}
        if not isinstance(self.cache, dict):
            self.cache = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='article-summary')
        self.jobs = {}
        self.next_translate = 0
        self.next_massive = 0

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _public(self, key: str, entry: dict) -> dict:
        return {'job': key, **{k: entry[k] for k in (
            'status', 'text', 'kind', 'message', 'retryAt', 'createdAt', 'notice'
        ) if k in entry}}

    def snapshot(self, key: str) -> dict:
        with self.lock:
            entry = self.cache.get(key)
            return self._public(key, entry) if entry else {'status': 'missing', 'message': '요청한 기사를 다시 열어 주세요.'}

    def start(self, article_id: str, url: str) -> dict:
        if not isinstance(article_id, str) or not isinstance(url, str) or len(article_id) > 300 or len(url) > 3000:
            return {'status': 'missing', 'message': '기사 정보를 확인할 수 없습니다.'}
        item = None
        for path in dict.fromkeys(self.feeds.values()):
            feed = read_json(path, {})
            for candidate in feed.get('items', []) if isinstance(feed, dict) else []:
                if isinstance(candidate, dict) and candidate.get('id') == article_id and candidate.get('url', '') == url:
                    item = candidate
                    break
            if item:
                break
        with self.lock:
            # A pinned story may have left today's feed. Reuse only a previously verified record.
            if item is None:
                matches = [v for v in self.cache.values() if v.get('article', {}).get('id') == article_id
                           and v['article'].get('url', '') == url]
                if matches:
                    item = max(matches, key=lambda v: v.get('createdAt', ''))['article']
            if item is None:
                return {'status': 'missing', 'message': '현재 피드에서 기사 내용을 확인할 수 없습니다. 저장된 내용이 없는 오래된 고정 기사일 수 있습니다.'}
            key = identity(item)
            entry = self.cache.get(key, {})
            if entry.get('status') in {'ready', 'review'} or key in self.jobs or time.time() < entry.get('retryAt', 0):
                return self._public(key, entry)
            if len(self.jobs) >= 3:
                return {'status': 'busy', 'message': '앞서 연 기사의 번역을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.', 'retryAt': time.time() + 5}
            entry = {**entry, 'article': item, 'status': 'pending', 'createdAt': datetime.now(timezone.utc).isoformat()}
            self.cache[key] = entry
            future = self.pool.submit(self._produce, key, dict(entry))
            self.jobs[key] = future
            future.add_done_callback(lambda task: self._done(key, task))
            return self._public(key, self.cache[key])

    def _done(self, key, future):
        with self.lock:
            self.jobs.pop(key, None)

    def _save(self, key, entry):
        with self.lock:
            self.cache[key] = entry
            # Keep pending records and the latest 300 completed articles.
            completed = sorted((k for k, v in self.cache.items() if v.get('status') != 'pending'),
                               key=lambda k: self.cache[k].get('createdAt', ''), reverse=True)
            for stale in completed[300:]:
                self.cache.pop(stale, None)
            try:
                atomic_write_json(self.path, {'version': VERSION, 'entries': self.cache})
            except OSError:
                entry['notice'] = '이번 번역은 메모리에만 저장됩니다. 서버 재시작 후에는 다시 번역할 수 있습니다.'

    def _produce(self, key: str, entry: dict):
        item = entry['article']
        try:
            args = relay.parse_args(self.options)
            args.timeout, args.request_retries = 12, 0
            args.translate_target = 'ko'
            args.translate_instructions = INSTRUCTIONS
            if time.time() < self.next_translate:
                entry.update(status='error', message='번역 서비스가 잠시 대기 중입니다. 잠시 후 다시 시도해 주세요.', retryAt=self.next_translate)
                return self._save(key, entry)
            source = entry.get('excerpt', '')
            kind = entry.get('kind', '')
            notice = ''
            if not source:
                row = {}
                failed_fetch = False
                if str(item.get('id', '')).startswith('benzinga:') and args.massive_token:
                    try:
                        if time.time() < self.next_massive:
                            failed_fetch = True
                        else:
                            row = fetch_massive_article(item, args)
                    except (relay.RelayError, OSError, ValueError) as exc:
                        failed_fetch = True
                        self.next_massive = time.time() + max(60, getattr(exc, 'retry_after', 0))
                source = excerpt(row.get('body', ''))
                kind = 'body' if source else 'teaser'
                if not source:
                    source = excerpt(row.get('teaser') or item.get('description') or item.get('snippet', ''))
                if failed_fetch:
                    notice = '본문 조회가 일시적으로 실패해 피드에 제공된 소개문을 사용했습니다.'
                    if not source:
                        entry.update(status='error', message='기사 본문을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.', retryAt=self.next_massive)
                        return self._save(key, entry)
                if not source or source.strip() == str(item.get('title', '')).strip():
                    kind = 'headline'
                    source = str(item.get('title') or item.get('originalTitle') or '')
                entry.update(excerpt=source, kind=kind, notice=notice)
            if not source:
                entry.update(status='error', message='이 기사에는 번역할 내용이 제공되지 않았습니다.')
                return self._save(key, entry)
            if kind == 'headline' and re.search('[가-힣]', item.get('titleKo', '')):
                translated = item['titleKo']
            elif len(re.findall('[가-힣]', source)) > len(re.findall('[A-Za-z]', source)):
                translated = source
            else:
                if args.translate_provider != 'deepl':
                    entry.update(status='error', message='한국어 내용 번역이 꺼져 있습니다. DeepL 번역을 켠 뒤 서버를 다시 실행해 주세요.')
                    return self._save(key, entry)
                result = relay.DeepLTranslator(args).translate_item({'title': source, 'snippet': item.get('title', '')}, args, preserve_paragraphs=True)
                translated = result.text
                entry['billedCharacters'] = result.billed_characters
            translated, issue = inspect_translation(source, translated, 'KO')
            if issue or not re.search('[가-힣]', translated):
                entry.update(status='review', message='번역 결과에 확인이 필요한 표기가 있어 표시를 보류했습니다.')
            else:
                entry.update(status='ready', text=translated, retryAt=0)
        except relay.RequestFailure as exc:
            self.next_translate = time.time() + max(300 if exc.status in {401, 403, 429, 456} else 60, exc.retry_after)
            message = '번역 서비스의 인증 또는 이용 한도를 확인해 주세요.' if exc.status in {401, 403, 429, 456} else '번역 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.'
            entry.update(status='error', message=message, retryAt=self.next_translate)
        except Exception:
            # Never expose request headers, keys, or provider response bodies to the browser.
            entry.update(status='error', message='한국어 내용을 준비하지 못했습니다. 번역 키와 연결 상태를 확인해 주세요.', retryAt=time.time() + 60)
        self._save(key, entry)
