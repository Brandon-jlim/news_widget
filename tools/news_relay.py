#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # noqa: BLE001
    boto3 = None
    BotoCoreError = Exception
    ClientError = Exception

from rss_to_json import clean_text, parse_feed, read_source

USER_AGENT = "NewsOverlayWidget/0.8"
DEFAULT_BROAD_MARKET_QUERY = (
    '"federal reserve" | fed | fomc | powell | inflation | cpi | pce | '
    '"nonfarm payrolls" | payrolls | unemployment | jobs | gdp | recession | '
    'treasury | yields | "bond yields" | "10-year" | "2-year" | '
    'oil | crude | opec | tariffs | sanctions | geopolitics | '
    '"china stimulus" | "consumer confidence" | pmi | "retail sales" | '
    '"u.s. stocks" | "wall street" | nasdaq | "s&p 500" | dow'
)
PREFERRED_BROAD_DOMAINS = [
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "cnbc.com",
    "ft.com",
    "marketwatch.com",
    "investing.com",
    "finance.yahoo.com",
]
INDEX_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM", "VIX", "ES", "NQ", "YM", "SPX", "NDX", "DJI", "RUT"}
BROAD_ENTITY_TYPES = {"index", "etf", "commodity", "currency", "bond", "macro"}

MACRO_KEYWORDS: dict[str, tuple[str, ...]] = {
    "연준": ("federal reserve", "fed", "fomc", "powell", "rate cut", "rate hike", "interest rate"),
    "물가": ("inflation", "cpi", "pce", "ppi", "prices"),
    "고용": ("payrolls", "nonfarm payrolls", "jobs", "jobless", "unemployment"),
    "국채금리": ("treasury", "bond yields", "10-year", "2-year", "yield"),
    "원유": ("oil", "crude", "opec", "brent", "wti"),
    "지정학": ("geopolitics", "sanctions", "tariffs", "war", "middle east", "china stimulus"),
    "지수": ("wall street", "u.s. stocks", "stock market", "s&p 500", "nasdaq", "dow"),
    "경기": ("gdp", "recession", "retail sales", "consumer confidence", "pmi"),
}
SOURCE_BOOSTS = {
    "reuters": 2.5,
    "bloomberg": 2.3,
    "wall street journal": 1.8,
    "wsj": 1.8,
    "cnbc": 1.4,
    "financial times": 1.6,
    "marketwatch": 1.1,
}
STOCK_NOISE_HINTS = (
    "price target",
    "analyst",
    "upgrades",
    "downgrades",
    "insider",
    "options",
    "dividend",
    "buyback",
)


@dataclass
class JsonResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


@dataclass
class ProviderResult:
    items: list[dict[str, Any]]
    meta: dict[str, Any]


@dataclass
class UsageHeaders:
    daily_limit: str = ""
    daily_remaining: str = ""
    rate_limit: str = ""
    rate_remaining: str = ""


class Provider:
    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        raise NotImplementedError


class HeadlineTranslator:
    def translate_item(self, item: dict[str, Any], args: argparse.Namespace) -> str:
        raise NotImplementedError


class AmazonHeadlineTranslator(HeadlineTranslator):
    def __init__(self, args: argparse.Namespace):
        if boto3 is None:
            raise RuntimeError("Amazon Translate를 사용하려면 boto3가 필요합니다. pip install boto3")
        session_kwargs: dict[str, Any] = {}
        if args.aws_profile:
            session_kwargs["profile_name"] = args.aws_profile
        session = boto3.Session(**session_kwargs)
        self.client = session.client("translate", region_name=args.aws_region)
        self.args = args

    def translate_item(self, item: dict[str, Any], args: argparse.Namespace) -> str:
        title = clean_text(item.get("title"))
        if not title:
            return ""
        request: dict[str, Any] = {
            "Text": title,
            "SourceLanguageCode": args.translate_source,
            "TargetLanguageCode": args.translate_target,
        }
        settings: dict[str, str] = {}
        if args.translate_formality:
            settings["Formality"] = args.translate_formality
        if args.translate_brevity and brevity_supported(args.translate_source, args.translate_target):
            settings["Brevity"] = "ON"
        if settings:
            request["Settings"] = settings
        if args.aws_terminology_name:
            request["TerminologyNames"] = [args.aws_terminology_name]

        try:
            response = self.client.translate_text(**request)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Amazon Translate 요청 실패: {exc}") from exc
        return clean_text(response.get("TranslatedText"))


class RSSProvider(Provider):
    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        items: list[dict[str, Any]] = []
        if not args.rss:
            raise ValueError("RSS provider를 사용할 때는 --rss 를 한 번 이상 지정해야 합니다.")

        for source in args.rss:
            xml_bytes = read_source(source, timeout=args.timeout)
            feed_items = parse_feed(xml_bytes, source)
            for item in feed_items:
                title = clean_text(item.get("title"))
                if not title:
                    continue
                items.append(
                    {
                        "id": clean_text(item.get("id") or item.get("url") or title),
                        "title": title,
                        "titleKo": title if args.assume_ko else "",
                        "publishedAt": clean_text(item.get("publishedAt")),
                        "source": clean_text(item.get("source") or args.provider_label or "RSS"),
                        "category": clean_text(item.get("category") or args.category_label),
                        "url": clean_text(item.get("url")),
                        "snippet": "",
                        "entities": [],
                    }
                )
        return ProviderResult(items=items, meta={})


class GNewsProvider(Provider):
    BASE_SEARCH = "https://gnews.io/api/v4/search"
    BASE_TOP = "https://gnews.io/api/v4/top-headlines"

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        api_key = args.api_key or args.env_gnews_api_key
        if not api_key:
            raise ValueError("GNews provider를 사용할 때는 --api-key 또는 GNEWS_API_KEY 환경변수가 필요합니다.")

        params: dict[str, Any] = {
            "apikey": api_key,
            "max": args.fetch_limit or args.limit,
            "lang": args.lang,
        }
        if args.country:
            params["country"] = args.country
        if args.from_iso:
            params["from"] = args.from_iso
        if args.to_iso:
            params["to"] = args.to_iso

        if args.query:
            params["q"] = args.query
            params["sortby"] = "publishedAt"
            url = f"{self.BASE_SEARCH}?{urlencode(params)}"
        else:
            if args.category:
                params["category"] = args.category
            url = f"{self.BASE_TOP}?{urlencode(params)}"

        response = request_json(url, timeout=args.timeout)
        articles = response.payload.get("articles") or []
        items: list[dict[str, Any]] = []
        for article in articles:
            title = clean_text(article.get("title"))
            if not title:
                continue
            lang = clean_text(article.get("lang") or args.lang)
            source = article.get("source") or {}
            items.append(
                {
                    "id": clean_text(article.get("id") or article.get("url") or title),
                    "title": title,
                    "titleKo": title if lang == "ko" else "",
                    "publishedAt": clean_text(article.get("publishedAt")),
                    "source": clean_text(source.get("name") or args.provider_label or "GNews"),
                    "category": clean_text(args.category_label or args.category or args.query or ""),
                    "url": clean_text(article.get("url")),
                    "snippet": clean_text(article.get("description") or article.get("content") or ""),
                    "entities": [],
                }
            )
        return ProviderResult(items=items, meta={})


class MarketauxProvider(Provider):
    BASE = "https://api.marketaux.com/v1/news/all"

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        api_token = args.api_key or args.env_marketaux_api_token
        if not api_token:
            raise ValueError("marketaux provider를 사용할 때는 --api-key 또는 MARKETAUX_API_TOKEN 환경변수가 필요합니다.")

        params: dict[str, Any] = {
            "api_token": api_token,
            "limit": min(max(args.fetch_limit or args.limit, 1), 50),
            "language": args.lang or "en",
            "countries": args.countries or "us",
            "must_have_entities": as_bool_param(args.must_have_entities),
            "group_similar": as_bool_param(args.group_similar),
            "filter_entities": as_bool_param(args.filter_entities),
        }
        search_query = build_marketaux_query(args)
        if search_query:
            params["search"] = search_query
        if args.symbols:
            params["symbols"] = args.symbols
        if args.industries:
            params["industries"] = args.industries
        if args.domains:
            params["domains"] = args.domains
        elif args.use_preferred_domains and args.marketaux_mode == "broad":
            params["domains"] = ",".join(PREFERRED_BROAD_DOMAINS)
        if args.source_ids:
            params["source_ids"] = args.source_ids
        if args.published_after:
            params["published_after"] = args.published_after
        if args.sort:
            params["sort"] = args.sort
        elif search_query:
            params["sort"] = "relevance_score"

        url = f"{self.BASE}?{urlencode(params)}"
        response = request_json(url, timeout=args.timeout)
        articles = response.payload.get("data") or []
        meta = response.payload.get("meta") or {}
        usage = parse_marketaux_usage_headers(response.headers)
        if args.debug:
            print("[news_relay][debug] marketaux request_url=", url)
            print("[news_relay][debug] marketaux meta=", json.dumps(meta, ensure_ascii=False))
            print("[news_relay][debug] marketaux raw_count=", len(articles))
            for idx, article in enumerate(articles[:5], start=1):
                print(f"[news_relay][debug] raw {idx}. {clean_text(article.get('title'))} | {clean_text(article.get('source'))}")
        items: list[dict[str, Any]] = []
        for article in articles:
            title = clean_text(article.get("title"))
            if not title:
                continue
            lang = clean_text(article.get("language") or args.lang)
            entities = article.get("entities") or []
            category = derive_category(article, args)
            item = {
                "id": clean_text(article.get("uuid") or article.get("url") or title),
                "title": title,
                "titleKo": title if lang == "ko" else "",
                "publishedAt": clean_text(article.get("published_at")),
                "source": clean_text(article.get("source") or args.provider_label or "marketaux"),
                "category": category,
                "url": clean_text(article.get("url")),
                "snippet": clean_text(article.get("description") or article.get("snippet") or ""),
                "entities": entities,
            }
            item["_score"] = score_marketaux_item(item)
            items.append(item)
        return ProviderResult(items=items, meta={"usage": usage.__dict__, "request_url": url})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().astimezone().isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def request_json(url: str, timeout: float) -> JsonResponse:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = json.loads(response.read().decode(charset))
        headers = {key: value for key, value in response.headers.items()}
        return JsonResponse(payload=payload, headers=headers)



def brevity_supported(source_lang: str, target_lang: str) -> bool:
    source = clean_text(source_lang).lower()
    target = clean_text(target_lang).lower()
    brevity_targets = {"fr", "de", "it", "pt", "es"}
    return target in brevity_targets or (source in brevity_targets and target == "en")

def build_provider(name: str) -> Provider:
    if name == "rss":
        return RSSProvider()
    if name == "gnews":
        return GNewsProvider()
    if name == "marketaux":
        return MarketauxProvider()
    raise ValueError(f"지원하지 않는 provider 입니다: {name}")


def build_marketaux_query(args: argparse.Namespace) -> str:
    mode = args.marketaux_mode
    query = clean_text(args.query)
    if mode == "query":
        return query
    if mode == "watchlist":
        return query
    if mode == "indices":
        return query or '"u.s. stocks" | "wall street" | nasdaq | "s&p 500" | dow | treasury | yields | fed'
    if mode == "broad":
        return f"({DEFAULT_BROAD_MARKET_QUERY}) + ({query})" if query else DEFAULT_BROAD_MARKET_QUERY
    return query


def parse_marketaux_usage_headers(headers: dict[str, str]) -> UsageHeaders:
    return UsageHeaders(
        daily_limit=headers.get("X-UsageLimit-Limit", ""),
        daily_remaining=headers.get("X-UsageLimit-Remaining", ""),
        rate_limit=headers.get("X-RateLimit-Limit", ""),
        rate_remaining=headers.get("X-RateLimit-Remaining", ""),
    )


def derive_category(article: dict[str, Any], args: argparse.Namespace) -> str:
    forced = clean_text(args.category_label)
    if forced:
        return forced
    entities = article.get("entities") or []
    title = clean_text(article.get("title")).lower()
    for label, keywords in MACRO_KEYWORDS.items():
        if any(keyword in title for keyword in keywords):
            return label
    for entity in entities:
        industry = clean_text((entity or {}).get("industry"))
        if industry:
            return industry
    return "시장"


def score_marketaux_item(item: dict[str, Any]) -> float:
    score = 0.0
    title = clean_text(item.get("title")).lower()
    snippet = clean_text(item.get("snippet")).lower()
    source = clean_text(item.get("source")).lower()
    text = f"{title} {snippet}".strip()

    for label, keywords in MACRO_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in text)
        if hits:
            score += 1.5 + 0.6 * hits
            if label in {"연준", "물가", "고용", "국채금리", "지정학"}:
                score += 0.5

    for source_hint, boost in SOURCE_BOOSTS.items():
        if source_hint in source:
            score += boost
            break

    entities = item.get("entities") or []
    entity_types = {clean_text((entity or {}).get("type")).lower() for entity in entities}
    symbols = {clean_text((entity or {}).get("symbol")).upper() for entity in entities}
    if entity_types & BROAD_ENTITY_TYPES:
        score += 1.8
    if symbols & INDEX_SYMBOLS:
        score += 1.6
    if len(symbols) == 1 and not (symbols & INDEX_SYMBOLS):
        score -= 1.0

    if any(noise in text for noise in STOCK_NOISE_HINTS):
        score -= 1.4

    published = parse_iso_datetime(item.get("publishedAt"))
    if published:
        age_hours = max((now_utc() - published).total_seconds() / 3600, 0)
        freshness_bonus = max(3.0 - min(age_hours, 12) * 0.2, 0)
        score += freshness_bonus

    return round(score, 4)


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = clean_text(item.get("url") or item.get("id") or item.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[float, str]:
        score = float(item.get("_score") or 0)
        published_at = clean_text(item.get("publishedAt") or "")
        return (score, published_at)

    return sorted(items, key=key, reverse=True)


def load_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


def save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_terms_mapping(path_str: str) -> dict[str, str]:
    if not path_str:
        return {}
    path = Path(path_str)
    data = load_json_file(path, {})
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for source, target in data.items():
        source_text = clean_text(source)
        target_text = clean_text(target)
        if source_text and target_text:
            result[source_text] = target_text
    return result


def apply_korean_terms(text: str, mapping: dict[str, str]) -> str:
    output = clean_text(text)
    for source, target in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        output = output.replace(source, target)
    return clean_text(output)


def headline_cache_key(provider: str, src: str, tgt: str, title: str, formality: str, brevity: bool) -> str:
    raw = "|".join([provider, src, tgt, title, formality or "", "brevity-on" if brevity else "brevity-off"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_translator(args: argparse.Namespace) -> HeadlineTranslator | None:
    if args.translate_provider == "none":
        return None
    if args.translate_provider == "amazon":
        return AmazonHeadlineTranslator(args)
    raise ValueError(f"지원하지 않는 translate-provider 입니다: {args.translate_provider}")


def translate_items(items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    translator = build_translator(args)
    if translator is None:
        return items

    cache_path = Path(args.translate_cache)
    cache = load_json_file(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}
    terms_mapping = load_terms_mapping(args.translate_terms_file)
    updated = False

    for item in items:
        title = clean_text(item.get("title"))
        existing_ko = clean_text(item.get("titleKo"))
        if not title:
            continue
        if existing_ko and args.translate_skip_existing:
            item["titleKo"] = apply_korean_terms(existing_ko, terms_mapping)
            continue
        cache_key = headline_cache_key(
            args.translate_provider,
            args.translate_source,
            args.translate_target,
            title,
            args.translate_formality,
            args.translate_brevity,
        )
        translated = clean_text(cache.get(cache_key))
        if not translated:
            translated = translator.translate_item(item, args)
            translated = apply_korean_terms(translated, terms_mapping)
            if translated:
                cache[cache_key] = translated
                updated = True
        if translated:
            item["titleKo"] = translated
        elif not item.get("titleKo"):
            item["titleKo"] = ""

    if updated:
        save_json_file(cache_path, cache)
    return items


def trim_runtime_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": clean_text(item.get("id")),
        "title": clean_text(item.get("title")),
        "titleKo": clean_text(item.get("titleKo")),
        "publishedAt": clean_text(item.get("publishedAt")),
        "source": clean_text(item.get("source")),
        "category": clean_text(item.get("category")),
        "url": clean_text(item.get("url")),
    }


def write_payload(out_path: Path, items: list[dict[str, Any]]) -> None:
    payload = {
        "updatedAt": now_iso(),
        "items": [trim_runtime_fields(item) for item in items],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def debug_log(enabled: bool, *parts: Any) -> None:
    if enabled:
        print("[news_relay][debug]", *parts)


def refresh_once(provider: Provider, args: argparse.Namespace) -> None:
    result = provider.fetch(args)
    items = dedupe_items(result.items)
    items = sort_items(items)
    items = items[: args.limit]
    items = translate_items(items, args)
    write_payload(Path(args.out), items)

    print(f"[news_relay] wrote {len(items)} items -> {args.out}")
    usage = (result.meta or {}).get("usage") or {}
    if usage.get("daily_remaining") or usage.get("rate_remaining"):
        print(
            "[news_relay] marketaux usage"
            f" daily {usage.get('daily_remaining', '?')}/{usage.get('daily_limit', '?')}"
            f" | rate {usage.get('rate_remaining', '?')}/{usage.get('rate_limit', '?')}"
        )
    debug_log(args.debug, "translate_provider=", args.translate_provider)
    for idx, item in enumerate(items, start=1):
        debug_log(
            args.debug,
            f"{idx}. score={item.get('_score', 0)}",
            clean_text(item.get("titleKo") or item.get("title")),
            "|",
            clean_text(item.get("source")),
        )


def as_bool_param(value: bool) -> str:
    return "true" if value else "false"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="허용된 뉴스 피드/API를 오버레이용 JSON(data/news.live.json)으로 정규화합니다."
    )
    parser.add_argument("--provider", choices=["rss", "gnews", "marketaux"], required=True)
    parser.add_argument("--out", default="data/news.live.json", help="출력 JSON 경로")
    parser.add_argument("--limit", type=int, default=5, help="최종 표시 뉴스 개수")
    parser.add_argument("--fetch-limit", type=int, default=20, help="원본 API/RSS에서 먼저 가져올 개수")
    parser.add_argument("--timeout", type=float, default=8.0, help="피드 요청 타임아웃(초)")
    parser.add_argument("--watch", type=int, default=0, help="0이면 1회 실행, 양수면 N초마다 반복")
    parser.add_argument("--api-key", default="", help="뉴스 API 키. 비워 두면 환경변수 사용")
    parser.add_argument("--provider-label", default="", help="강제 source 라벨")
    parser.add_argument("--category-label", default="", help="강제 category 라벨")
    parser.add_argument("--lang", default="en", help="뉴스 언어 코드. 예: en, ko")
    parser.add_argument("--country", default="", help="GNews 국가 코드. 예: us, kr")
    parser.add_argument("--query", default="", help="검색 질의어")
    parser.add_argument("--category", default="business", help="GNews top-headlines 카테고리")
    parser.add_argument("--from-iso", default="", help="GNews 시작 시각 ISO8601")
    parser.add_argument("--to-iso", default="", help="GNews 종료 시각 ISO8601")
    parser.add_argument("--rss", action="append", help="RSS/Atom URL 또는 로컬 XML 파일 경로")

    parser.add_argument("--marketaux-mode", choices=["broad", "indices", "watchlist", "query"], default="broad")
    parser.add_argument("--symbols", default="", help="marketaux 심볼 필터. 예: SPY,QQQ,NVDA")
    parser.add_argument("--countries", default="", help="marketaux 국가 필터. 예: us")
    parser.add_argument("--industries", default="", help="marketaux 업종 필터")
    parser.add_argument("--domains", default="", help="marketaux 도메인 필터")
    parser.add_argument("--source-ids", default="", help="marketaux source_ids 필터")
    parser.add_argument("--published-after", default="", help="marketaux published_after 값. 예: 2026-05-01")
    parser.add_argument("--sort", default="", help="marketaux sort 파라미터. 기본은 marketaux 기본값 사용")
    parser.add_argument("--must-have-entities", action="store_true", help="엔티티가 있는 기사만 사용")
    parser.add_argument("--group-similar", action="store_true", default=True, help="유사 기사 묶기")
    parser.add_argument("--filter-entities", action="store_true", default=True, help="엔티티 필터 사용")
    parser.add_argument("--use-preferred-domains", action="store_true", help="broad 모드에서 선호 도메인 우선")

    parser.add_argument("--translate-provider", choices=["none", "amazon"], default="amazon")
    parser.add_argument("--translate-source", default="en", help="번역 원문 언어")
    parser.add_argument("--translate-target", default="ko", help="번역 대상 언어")
    parser.add_argument("--translate-cache", default="data/translate_cache.ko.json", help="번역 캐시 JSON 경로")
    parser.add_argument("--translate-terms-file", default="tools/ko_terms.sample.json", help="번역 후처리 용어집 JSON 경로")
    parser.add_argument("--translate-formality", choices=["FORMAL", "INFORMAL", ""], default="FORMAL")
    parser.add_argument("--no-translate-brevity", dest="translate_brevity", action="store_false", help="Amazon brevity 끄기")
    parser.set_defaults(translate_brevity=True)
    parser.add_argument("--translate-skip-existing", action="store_true", default=True, help="이미 titleKo가 있으면 재번역 생략")
    parser.add_argument("--aws-region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"), help="Amazon Translate 리전")
    parser.add_argument("--aws-profile", default="", help="AWS profile 이름")
    parser.add_argument("--aws-terminology-name", default="", help="Amazon Translate custom terminology 이름")

    parser.add_argument("--assume-ko", action="store_true", help="RSS 제목을 그대로 titleKo 로도 저장")
    parser.add_argument("--debug", action="store_true", help="디버그 로그 출력")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    load_env_file(project_root / ".env.local")
    load_env_file(project_root / ".env")

    args.env_gnews_api_key = os.getenv("GNEWS_API_KEY", "")
    args.env_marketaux_api_token = os.getenv("MARKETAUX_API_TOKEN", "")
    if not args.published_after and args.provider == "marketaux":
        args.published_after = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    return args


def main() -> int:
    args = parse_args()
    provider = build_provider(args.provider)

    try:
        if args.watch > 0:
            while True:
                try:
                    refresh_once(provider, args)
                except Exception as exc:  # noqa: BLE001
                    print(f"[news_relay] refresh failed: {exc}", file=sys.stderr)
                time.sleep(args.watch)
        else:
            refresh_once(provider, args)
    except KeyboardInterrupt:
        print("\n[news_relay] stopped")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
