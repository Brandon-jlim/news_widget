"""Cache public Benzinga Korea RSS metadata for article navigation only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from xml.etree import ElementTree as ET

try:
    from .storage import read_json, atomic_write_json
except ImportError:
    from storage import read_json, atomic_write_json

FEED_URL = 'https://kr.benzinga.com/feed/'
MAX_BYTES = 2_000_000


def korean_article_url(value):
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.hostname != 'kr.benzinga.com' or url.username or url.password or url.port not in {None, 443}:
            return ''
        if not re.match(r'^/(?:news|crypto|education)/.+/.+', url.path):
            return ''
        return quote(value, safe=':/%?=&-._~')
    except (TypeError, ValueError):
        return ''


def parse_feed(body):
    root = ET.fromstring(body)
    channel = root.find('channel')
    if channel is None:
        raise ValueError('Expected RSS channel')
    items = []
    for item in channel.findall('item')[:100]:
        link = korean_article_url(item.findtext('link', ''))
        title = html.unescape(item.findtext('title', '')).strip()
        try:
            date = parsedate_to_datetime(item.findtext('pubDate', '')).astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        if not link or not title:
            continue
        # Related English links in the body are NOT the source article's identity.
        content = item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded', '')
        text = html.unescape(re.sub(r'<[^>]+>', ' ', content))
        symbols = sorted(set(re.findall(r'(?:NASDAQ|NYSE|AMEX|나스닥)\s*:\s*([A-Z][A-Z0-9.-]{0,9})\b', text)))
        items.append({'title':title[:500], 'url':link, 'publishedAt':date.isoformat(), 'symbols':symbols[:30]})
    interval = 300
    try:
        interval = max(interval, int(channel.findtext('ttl', '0')) * 60)
    except ValueError:
        pass
    period = channel.findtext('{http://purl.org/rss/1.0/modules/syndication/}updatePeriod', '').strip()
    try:
        frequency = max(1, int(channel.findtext('{http://purl.org/rss/1.0/modules/syndication/}updateFrequency', '1')))
        interval = max(interval, {'hourly':3600, 'daily':86400}.get(period, 0) // frequency)
    except ValueError:
        pass
    return items, interval


class FeedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.hostname != 'kr.benzinga.com' or target.username or target.password or target.port not in {None, 443}:
            raise ValueError('Unexpected feed redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_index():
    request = Request(FEED_URL, headers={'User-Agent':'NewsOverlayWidget/1.4', 'Accept':'application/rss+xml'})
    with build_opener(FeedRedirects()).open(request, timeout=6) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('RSS too large')
    return parse_feed(body)


class KoreanArticleIndex:
    def __init__(self, path: Path):
        self.path = path
        cached = read_json(path, {})
        cached = cached if isinstance(cached, dict) else {}
        previous = cached.get('items') if isinstance(cached.get('items'), list) else []
        self.items = [item for item in previous if isinstance(item, dict) and korean_article_url(item.get('url'))][:500]
        self.next_check = 0
        self.updated_at = cached.get('updatedAt')
        self.status = 'cached' if self.items else 'waiting'
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='korean-links')
        self.future = None

    def snapshot(self):
        with self.lock:
            if self.future is not None and self.future.done():
                try:
                    incoming, interval = self.future.result()
                    cutoff = time.time() - 7 * 86400
                    merged = {item['url']:item for item in [*self.items, *incoming]}
                    valid = []
                    for item in merged.values():
                        try:
                            if datetime.fromisoformat(item['publishedAt']).timestamp() >= cutoff:
                                valid.append(item)
                        except (ValueError, KeyError, TypeError):
                            continue
                    self.items = sorted(valid, key=lambda x:x['publishedAt'], reverse=True)[:500]
                    self.updated_at = datetime.now(timezone.utc).isoformat()
                    self.next_check = time.time() + interval
                    self.status = 'ok'
                    try:
                        atomic_write_json(self.path, {'items':self.items, 'updatedAt':self.updated_at})
                    except OSError:
                        pass  # Navigation remains usable without a writable cache.
                except Exception as exc:
                    self.status = 'unavailable'
                    delay = 3600 if isinstance(exc, HTTPError) and exc.code in {401, 403} else 300
                    if isinstance(exc, HTTPError) and exc.headers:
                        retry = exc.headers.get('Retry-After', '')
                        try:
                            delay = max(delay, int(retry))
                        except ValueError:
                            try:
                                delay = max(delay, parsedate_to_datetime(retry).timestamp() - time.time())
                            except (ValueError, TypeError, OverflowError):
                                pass
                    self.next_check = time.time() + delay
                self.future = None
            if self.future is None and time.time() >= self.next_check:
                self.future = self.executor.submit(fetch_index)
                self.status = 'loading'
            return {'items':self.items, 'updatedAt':self.updated_at, 'status':self.status}

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
