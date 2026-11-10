#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import socket
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

try:
    from .storage import atomic_write_json, read_json
    from .feed_sources import OFFICIAL_FEEDS, PUBLIC_RSS_FEEDS
    from .translation_quality import inspect_translation, review_record
    from .market_impact import attach_impact, assess_impact
except ImportError:
    from storage import atomic_write_json, read_json
    from feed_sources import OFFICIAL_FEEDS, PUBLIC_RSS_FEEDS
    from translation_quality import inspect_translation, review_record
    from market_impact import attach_impact, assess_impact


# Keep an application identifier, but use a browser-compatible User-Agent shape.
# Some public XML/sitemap endpoints return HTML or reject very short bot-like
# agents even when the endpoint is listed in robots.txt.
USER_AGENT = "Mozilla/5.0 (compatible; NewsOverlayWidget/1.0; metadata-only)"
HTTP_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
SECRET_QUERY_PARAM_PATTERN = re.compile(r"(?i)([?&](?:api_token|api_key|apikey|auth_key|token|key)=)([^&]+)")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYS_DIR = PROJECT_ROOT / "keys"
DEFAULT_MARKETAUX_KEY_FILES = (
    DEFAULT_KEYS_DIR / "marketaux_token.txt",
    DEFAULT_KEYS_DIR / "marketaux.txt",
)
DEFAULT_DEEPL_KEY_FILES = (
    DEFAULT_KEYS_DIR / "deepl_api_key.txt",
    DEFAULT_KEYS_DIR / "deepl_key.txt",
    DEFAULT_KEYS_DIR / "deepl.txt",
)

DEFAULT_BROAD_SEARCH = (
    '"federal reserve" | fed | fomc | powell | inflation | cpi | pce | '
    '"nonfarm payrolls" | payrolls | unemployment | jobs | gdp | recession | '
    'treasury | yields | "bond yields" | "10-year" | "2-year" | oil | crude | opec | '
    'tariffs | sanctions | geopolitics | "china stimulus" | "consumer confidence" | '
    'pmi | "retail sales" | "u.s. stocks" | "wall street" | nasdaq | "s&p 500" | dow | '
    'earnings | guidance | nvidia | microsoft | apple | amazon | alphabet | google | '
    'meta | tesla | broadcom | semiconductor | "artificial intelligence" | '
    'war | missile | ceasefire | iran | taiwan | "export controls" | shutdown | default'
)

DEFAULT_PREFERRED_DOMAINS = ",".join(
    [
        "reuters.com",
        "bloomberg.com",
        "wsj.com",
        "cnbc.com",
        "ft.com",
        "marketwatch.com",
        "investing.com",
        "finance.yahoo.com",
    ]
)

# Reuters advertises this news sitemap in robots.txt. The relay only reads
# metadata/headlines from sitemap/RSS/Atom sources; it does not fetch article bodies.
DEFAULT_REUTERS_SOURCES = (
    "https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
    # Direct sitemap fallback. Keeping this as a second default source makes the
    # relay more resilient if the index endpoint is temporarily unavailable.
    "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml",
)

TAB_FEED_KEYS = ("combined", "marketaux", "official", "rss", "benzinga", "reuters")
DEFAULT_REUTERS_OUT = "data/news.reuters.json"
DEFAULT_MARKETAUX_OUT = "data/news.marketaux.json"

# Reuters sitemap entries normally expose news:publication_date. Some Arc
# sitemap rows, however, can omit it while still carrying a top-level lastmod,
# and many article URLs contain a YYYY-MM-DD slug. Use these as conservative
# fallbacks so the widget does not show "시간 미상" for Reuters cards.
URL_DATE_PATTERNS = (
    re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)"),
    re.compile(r"-(20\d{2})-(\d{1,2})-(\d{1,2})(?:/|$)"),
    re.compile(r"[?&]date=(20\d{2})-(\d{1,2})-(\d{1,2})(?:&|$)"),
)

INDEX_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM", "VIX", "TLT", "USO", "GLD"}
MEGACAP_SYMBOLS = {"NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "AVGO", "TSM"}
MARKET_EVENTS = ("earnings", "guidance", "revenue", "profit", "merger", "acquisition", "bankruptcy",
                 "default", "export control", "antitrust", "semiconductor", "artificial intelligence",
                 "nvidia", "microsoft", "apple", "amazon", "alphabet", "tesla", "broadcom",
                 "war", "missile", "ceasefire", "iran", "taiwan", "shutdown", "hormuz")
BROAD_ENTITY_TYPES = {"country", "currency", "commodity", "etf", "index", "macro"}

DEFAULT_DEEPL_INSTRUCTIONS = [
    "Translate into concise, natural Korean financial-news headline style.",
    "Prefer familiar Korean market terms such as 연준, 미 국채금리, 월가, S&P 500, 나스닥.",
    "Avoid honorifics, keep the meaning faithful, and make the headline readable in a small broadcast overlay.",
    "Write company names consistently in Hangul or retain their original Latin spelling. Never mix Chinese characters into a transliterated Korean company name.",
]

DEFAULT_KO_REPLACEMENTS = {
    "연방준비제도이사회": "연준",
    "연방준비제도": "연준",
    "미국 연방준비제도": "연준",
    "미국 국채 수익률": "미 국채금리",
    "국채 수익률": "국채금리",
    "10년 만기 국채 수익률": "10년물 국채금리",
    "2년 만기 국채 수익률": "2년물 국채금리",
    "미국 주식시장": "미국 증시",
    "미국 주식": "미국 증시",
    "월스트리트": "월가",
    "석유수출국기구": "OPEC",
    "서부텍사스산원유": "WTI",
    "연방공개시장위원회": "FOMC",
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "연준": ("federal reserve", "fed", "fomc", "powell"),
    "물가": ("inflation", "cpi", "pce", "ppi"),
    "고용": ("payroll", "jobs", "unemployment", "labor market", "employment"),
    "채권": ("treasury", "yield", "bond", "10-year", "2-year"),
    "원유": ("oil", "crude", "wti", "brent", "opec"),
    "지정학": ("tariff", "sanction", "war", "ceasefire", "geopolit", "iran", "china"),
    "지수": ("s&p 500", "nasdaq", "dow", "u.s. stocks", "wall street", "futures"),
}

MARKET_SIGNAL_TERMS = tuple(
    sorted(
        {
            "federal reserve", "fed", "fomc", "powell", "rate cut", "rate cuts", "interest rate",
            "inflation", "cpi", "pce", "ppi", "payroll", "nonfarm", "unemployment", "jobs",
            "labor market", "gdp", "recession", "treasury", "treasuries", "yield", "yields",
            "bond", "bonds", "10-year", "2-year", "oil", "crude", "wti", "brent", "opec",
            "tariff", "tariffs", "sanction", "sanctions", "geopolit", "china stimulus",
            "consumer confidence", "retail sales", "pmi", "wall street", "u.s. stocks",
            "us stocks", "stock market", "stocks", "equities", "equity", "s&p 500", "nasdaq",
            "dow", "futures", "markets", "market", "dollar", "currency", "currencies",
            "commodities", "commodity", "earnings", "guidance", "shares", "index", "indexes", "indices",
            # Korean signals are useful for local test feeds and for any
            # licensed/translated Reuters feed a user may provide.
            "연준", "fomc", "물가", "인플레이션", "cpi", "pce", "고용", "실업", "gdp", "침체",
            "국채", "국채금리", "금리", "채권", "원유", "유가", "달러", "환율", "증시", "미국 증시",
            "주식", "선물", "나스닥", "다우", "s&p", "실적", "성장주", "시장",
        }
    )
)

class RelayError(RuntimeError):
    pass


class RequestFailure(RelayError):
    def __init__(self, status: int, *, retry_after: float = 0, usage: dict | None = None):
        super().__init__(f"HTTP {status} 요청 실패")
        self.status = status
        self.retry_after = retry_after
        self.usage = usage or {}


@dataclass
class ProviderResult:
    items: list[dict[str, Any]]
    meta: dict[str, Any]


@dataclass
class HttpResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


@dataclass
class TextResponse:
    text: str
    headers: dict[str, str]


@dataclass
class TranslationResult:
    text: str
    provider: str
    from_cache: bool
    billed_characters: int = 0


class Translator:
    provider_name = "none"

    def translate_item(self, item: dict[str, Any], args: argparse.Namespace) -> TranslationResult:
        raise NotImplementedError


class NullTranslator(Translator):
    provider_name = "none"

    def translate_item(self, item: dict[str, Any], args: argparse.Namespace) -> TranslationResult:
        return TranslationResult(
            text=clean_text(item.get("titleKo") or item.get("title") or ""),
            provider=self.provider_name,
            from_cache=True,
        )


class DeepLTranslator(Translator):
    provider_name = "deepl"

    def __init__(self, args: argparse.Namespace) -> None:
        api_key = clean_text(
            args.deepl_api_key
            or args.translate_api_key
            or args.file_deepl_api_key
            or args.env_deepl_api_key
        )
        if not api_key:
            raise RelayError("DeepL 번역을 사용하려면 keys/deepl_api_key.txt 또는 --deepl-api-key 가 필요합니다.")

        self.api_key = api_key
        self.timeout = args.timeout
        self.source_lang = clean_text(args.translate_source).upper() or None
        self.target_lang = clean_text(args.translate_target).upper() or "KO"
        self.glossary_id = clean_text(args.deepl_glossary_id)
        self.custom_instructions = build_custom_instructions(args)
        self.base_url = clean_text(args.deepl_api_url) or (
            "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"
        )

    def translate_item(self, item: dict[str, Any], args: argparse.Namespace) -> TranslationResult:
        title = clean_text(item.get("title"))
        if not title:
            return TranslationResult(text="", provider=self.provider_name, from_cache=True)

        payload: dict[str, Any] = {
            "text": [title],
            "target_lang": self.target_lang,
            "model_type": "quality_optimized",
            "show_billed_characters": True,
        }
        if self.source_lang:
            payload["source_lang"] = self.source_lang
        if self.glossary_id and self.source_lang:
            payload["glossary_id"] = self.glossary_id

        context = build_translation_context(item, max_chars=args.translate_context_chars)
        if context:
            payload["context"] = context
        if self.custom_instructions:
            payload["custom_instructions"] = self.custom_instructions

        response = request_json(
            f"{self.base_url}/v2/translate",
            timeout=self.timeout,
            method="POST",
            headers={
                "Authorization": f"DeepL-Auth-Key {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            retries=args.request_retries,
            retry_backoff=args.retry_backoff,
            debug=args.debug,
        )

        translations = response.payload.get("translations") or []
        if not translations:
            raise RelayError("DeepL 응답에 translations 가 없습니다.")

        translated = clean_text((translations[0] or {}).get("text"))
        if not translated:
            raise RelayError("DeepL 응답의 번역문이 비어 있습니다.")

        billed = int((translations[0] or {}).get("billed_characters") or response.payload.get("billed_characters") or 0)
        return TranslationResult(text=translated, provider=self.provider_name, from_cache=False, billed_characters=billed)


class Provider:
    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        raise NotImplementedError


class MarketauxProvider(Provider):
    BASE = "https://api.marketaux.com/v1/news/all"

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        api_token = clean_text(args.api_key or args.file_marketaux_api_token or args.env_marketaux_api_token)
        if not api_token:
            raise RelayError("marketaux 를 사용하려면 keys/marketaux_token.txt 또는 --api-key 가 필요합니다.")

        search_query = build_marketaux_query(args)
        params: dict[str, Any] = {
            "api_token": api_token,
            "limit": min(max(args.fetch_limit, 1), 50),
            "language": clean_text(args.lang or "en"),
            "countries": clean_text(args.countries),
            "must_have_entities": as_bool_param(args.must_have_entities),
            "group_similar": as_bool_param(args.group_similar),
            "filter_entities": as_bool_param(args.filter_entities),
            "published_after": clean_text(args.published_after),
        }
        if search_query:
            params["search"] = search_query
        if args.domains:
            params["domains"] = clean_text(args.domains)
        if args.sort:
            params["sort"] = clean_text(args.sort)

        response, effective_sort, request_url = self.request_marketaux(params, args)
        if effective_sort and effective_sort != clean_text(params.get("sort")):
            params["sort"] = effective_sort
        meta = response.payload.get("meta") or {}
        raw_items = response.payload.get("data") or []

        if args.debug:
            print(f"[news_relay][debug] marketaux meta= {json.dumps(meta, ensure_ascii=False)}")
            print(f"[news_relay][debug] marketaux raw_count= {len(raw_items)}")
            for index, article in enumerate(raw_items[:5], start=1):
                print(
                    f"[news_relay][debug] raw {index}. {clean_text(article.get('title'))} | {clean_text(article.get('source'))}"
                )

        items: list[dict[str, Any]] = []
        for article in raw_items:
            normalized = self.normalize_article(article, args)
            if normalized:
                items.append(normalized)

        usage = extract_usage_headers(response.headers)
        return ProviderResult(
            items=items,
            meta={
                "usage": usage,
                "found": meta.get("found"),
                "returned": meta.get("returned"),
                "limit": meta.get("limit"),
                "page": meta.get("page"),
                "search": search_query,
                "sort": clean_text(params.get("sort")),
            },
        )

    def request_marketaux(self, params: dict[str, Any], args: argparse.Namespace) -> tuple[HttpResponse, str, str]:
        """Request Marketaux, with a conservative sort fallback.

        The overlay should feel live. In broad/search mode the relay asks for
        newest-first Marketaux results; if an API plan/version rejects that sort
        value, it falls back instead of stopping the relay.
        """
        initial_sort = clean_text(params.get("sort"))
        sort_candidates = [initial_sort]
        if initial_sort == "published_at":
            sort_candidates.extend(["published_on", "relevance_score"])
        elif initial_sort == "published_on":
            sort_candidates.extend(["published_at", "relevance_score"])
        elif not initial_sort:
            sort_candidates.append("")

        seen_sorts: set[str] = set()
        last_error: Exception | None = None
        last_url = ""
        for sort_value in sort_candidates:
            if sort_value in seen_sorts:
                continue
            seen_sorts.add(sort_value)
            request_params = dict(params)
            if sort_value:
                request_params["sort"] = sort_value
            else:
                request_params.pop("sort", None)
            request_url = f"{self.BASE}?{urlencode({k: v for k, v in request_params.items() if v != ''})}"
            last_url = request_url
            if args.debug:
                print(f"[news_relay][debug] marketaux request_url= {redact_url_secrets(request_url)}")
            try:
                response = request_json(
                    request_url,
                    timeout=args.timeout,
                    retries=0,  # Each HTTP attempt consumes quota; retry on the next scheduled cycle.
                    retry_backoff=args.retry_backoff,
                    debug=args.debug,
                )
                if sort_value != initial_sort and args.debug:
                    print(
                        f"[news_relay][warn] marketaux sort fallback: {initial_sort or '(none)'} -> {sort_value or '(none)'}",
                        file=sys.stderr,
                    )
                return response, sort_value, request_url
            except RelayError as exc:
                last_error = exc
                if not is_probable_marketaux_sort_error(exc):
                    raise
                if args.debug:
                    print(
                        f"[news_relay][warn] marketaux sort rejected: {sort_value or '(none)'}; trying fallback",
                        file=sys.stderr,
                    )
                continue

        if last_error is not None:
            raise last_error
        raise RelayError(f"Marketaux 요청 실패: {last_url}")

    def normalize_article(self, article: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
        title = clean_text(article.get("title"))
        if not title:
            return None
        snippet = clean_text(article.get("snippet") or article.get("description") or "")
        entities = article.get("entities") or []
        score = score_article(article)
        return {
            "id": clean_text(article.get("uuid") or article.get("url") or title),
            "title": title,
            "titleKo": "",
            "publishedAt": clean_text(article.get("published_at")),
            "source": clean_text(article.get("source") or args.provider_label or "marketaux"),
            "category": clean_text(args.category_label) or categorize_article(title, snippet, entities),
            "sentiment": classify_market_sentiment(title, snippet, entities),
            "url": clean_text(article.get("url")),
            "description": clean_text(article.get("description")),
            "snippet": snippet,
            "entities": entities,
            "_score": score,
            "_source_provider": "marketaux",
        }


class MassiveNewsProvider(Provider):
    """Poll the newest Benzinga headlines through Massive's News v2 REST API."""

    BASE = "https://api.massive.com/benzinga/v2/news"
    group = "benzinga"
    label = "Benzinga · Massive"

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        if not args.massive_token:
            raise RelayError("Massive 키가 필요합니다: keys/massive_api_key.txt 또는 MASSIVE_API_KEY")
        published_after = parse_iso_datetime(args.published_after)
        if published_after is None:
            raise RelayError("Massive 시작 시각이 올바르지 않습니다 (--published-after)")
        # A bounded latest-news snapshot also restores recent headlines on startup
        # and after disconnection. Re-reading it captures edits without advancing
        # the original publication timestamp. This is not a full historical export.
        params = {
            "limit": args.massive_fetch_limit,
            "sort": "published.desc",
            "published.gte": published_after.isoformat(),
            "fields": "benzinga_id,title,published,last_updated,url,teaser,tickers,channels,tags",
        }
        response = request_json(
            self.BASE + "?" + urlencode(params), timeout=args.timeout,
            headers={"Authorization": "Bearer " + args.massive_token}, retries=0,
        )
        payload = response.payload
        if not isinstance(payload, dict) or payload.get("status") != "OK" or not isinstance(payload.get("results"), list):
            raise RelayError("Massive News 응답 형식 또는 구독 권한을 확인하세요 (정상 뉴스 응답이 아님)")
        items = []
        invalid = 0
        for article in payload["results"]:
            item = self.normalize_article(article, args)
            if item is None:
                invalid += 1
            else:
                items.append(item)
        if payload["results"] and not items:
            raise RelayError("Massive 뉴스 응답에 유효한 ID·제목·발행 시각이 없습니다")
        # Do not save the raw response or next_url: neither is needed by the UI,
        # and server-returned pagination links may contain credentials.
        meta = {"provider": "massive", "group": "benzinga", "label": self.label,
                "transport": "rest", "returned": len(items), "snapshotLimit": args.massive_fetch_limit,
                "moreAvailable": bool(payload.get("next_url")), "invalidItems": invalid}
        if invalid:
            meta["errors"] = [f"Massive 뉴스 {invalid}건의 필수 필드가 없어 제외했습니다"]
        return ProviderResult(dedupe_items(items), meta)

    @staticmethod
    def normalize_article(article: Any, args: argparse.Namespace) -> dict[str, Any] | None:
        if not isinstance(article, dict):
            return None
        identity = clean_text(article.get("benzinga_id"))
        title = clean_markup_text(article.get("title"))
        published = parse_datetime_text(article.get("published"))
        if not identity or not title or not published:
            return None
        symbols = article.get("tickers") if isinstance(article.get("tickers"), list) else []
        snippet = clean_markup_text(article.get("teaser", ""))
        item = MarketauxProvider().normalize_article({
            "uuid": "benzinga:" + identity, "title": title, "published_at": published,
            "source": "Benzinga", "url": clean_text(article.get("url")), "description": snippet,
            "entities": [{"symbol": symbol, "type": "equity"} for symbol in symbols if isinstance(symbol, str)],
        }, args)
        item.update(_source_provider="benzinga", via="Massive",
                    updatedAt=parse_datetime_text(article.get("last_updated")) or published,
                    channels=normalize_news_labels(article.get("channels")),
                    tags=normalize_news_labels(article.get("tags")))
        return item


class ReutersProvider(Provider):
    XML_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        sources = [source for source in (clean_text(value) for value in (args.reuters_feed or [])) if source]
        if not sources and not args.no_reuters_default_feed:
            sources = list(DEFAULT_REUTERS_SOURCES)
        if not sources:
            raise RelayError("Reuters 수집을 사용하려면 --reuters-feed 를 지정하거나 기본 Reuters sitemap 사용을 허용해야 합니다.")

        combined: list[dict[str, Any]] = []
        errors: list[str] = []
        fetched_source_count = 0
        request_failure = None
        self.date_stats: dict[str, int] = {
            "news_publication_date": 0,
            "lastmod_fallback": 0,
            "url_date_fallback": 0,
            "missing_date_allowed": 0,
            "missing_date_skipped": 0,
        }

        for source in sources:
            try:
                feed_items, source_count = self.fetch_source(source, args, depth=0)
                fetched_source_count += source_count
                combined.extend(feed_items)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, RequestFailure):
                    request_failure = exc
                errors.append(f"{redact_url_secrets(source)}: {safe_error(exc, args)}")
                if args.debug:
                    print(f"[news_relay][warn] Reuters source failed: {redact_url_secrets(source)}: {safe_error(exc, args)}", file=sys.stderr)

        combined = [item for item in combined if clean_text(item.get("title"))]
        published_after = parse_iso_datetime(args.published_after)
        if published_after is not None:
            combined = [item for item in combined if self.is_recent_enough(item, published_after)]

        combined = dedupe_items(combined)
        combined = sort_items(combined, rank_mode="fresh")

        raw_limit = max(int(args.reuters_fetch_limit or 0), int(args.fetch_limit or 0), int(args.limit or 0))
        if raw_limit > 0:
            combined = combined[:raw_limit]

        if errors and not combined:
            if request_failure is not None:
                raise request_failure
            raise RelayError("Reuters 갱신 실패: " + " | ".join(errors[:3]))

        if args.debug:
            print(f"[news_relay][debug] reuters sources= {len(sources)} fetched_xml= {fetched_source_count} raw_count= {len(combined)}")
            print(f"[news_relay][debug] reuters date_sources= {json.dumps(self.date_stats, ensure_ascii=False)}")
            for index, item in enumerate(combined[:5], start=1):
                date_note = item.get("publishedAt") or item.get("publishedDate") or ""
                source_note = item.get("_date_source") or ""
                if source_note:
                    date_note = f"{date_note} ({source_note})"
                print(f"[news_relay][debug] reuters raw {index}. {item.get('title')} | {date_note}")

        meta: dict[str, Any] = {
            "provider": "reuters",
            "sources": [redact_url_secrets(source) for source in sources],
            "fetched_sources": fetched_source_count,
            "returned": len(combined),
            "source_kind": "sitemap/rss/atom",
            "date_sources": dict(self.date_stats),
        }
        if errors:
            meta["errors"] = errors[:10]
        return ProviderResult(items=combined, meta=meta)

    def fetch_source(self, source: str, args: argparse.Namespace, *, depth: int) -> tuple[list[dict[str, Any]], int]:
        if depth > 2:
            return [], 0

        response = read_text_source(
            source,
            timeout=args.timeout,
            headers={"Accept": self.XML_ACCEPT},
            retries=args.request_retries,
            retry_backoff=args.retry_backoff,
            debug=args.debug,
        )
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            preview = clean_text(response.text[:500])
            raise RelayError(f"Reuters XML 파싱 실패: {exc}; response_preview={preview}") from exc
        root_tag = strip_ns(root.tag).lower()

        if root_tag == "sitemapindex":
            sitemap_entries: list[tuple[str, str]] = []
            for sitemap in children(root, "sitemap"):
                loc = child_text(sitemap, "loc")
                if not loc:
                    continue
                lastmod = parse_datetime_text(child_text(sitemap, "lastmod")) or ""
                sitemap_entries.append((loc, lastmod))

            sitemap_entries.sort(key=lambda entry: entry[1], reverse=True)
            sitemap_limit = max(int(args.reuters_sitemap_limit or 0), 0)
            if sitemap_limit > 0:
                sitemap_entries = sitemap_entries[:sitemap_limit]

            items: list[dict[str, Any]] = []
            fetched = 1
            for index, (loc, _) in enumerate(sitemap_entries):
                if index > 0 and args.reuters_request_delay > 0:
                    time.sleep(args.reuters_request_delay)
                child_items, child_count = self.fetch_source(loc, args, depth=depth + 1)
                fetched += child_count
                items.extend(child_items)
            return items, fetched

        if root_tag == "urlset":
            return self.parse_news_sitemap(root, args), 1

        if root_tag == "rss":
            return self.parse_rss(root, source, args), 1

        if root_tag == "feed":
            return self.parse_atom(root, source, args), 1

        raise RelayError(f"지원하지 않는 Reuters XML 형식입니다: {root_tag}")

    def parse_news_sitemap(self, root: ET.Element, args: argparse.Namespace) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for url_el in children(root, "url"):
            loc = child_text(url_el, "loc")
            url_lastmod = parse_datetime_text(child_text(url_el, "lastmod")) or ""
            url_date = extract_date_from_url(loc)
            news_el = first_child(url_el, "news")
            title = ""
            publication_date = ""
            source_name = clean_text(args.reuters_source_label) or "Reuters"
            keywords = ""
            language = ""

            if news_el is not None:
                title = child_text(news_el, "title")
                publication_date = child_text(news_el, "publication_date")
                keywords = child_text(news_el, "keywords")
                publication = first_child(news_el, "publication")
                source_name = child_text(publication, "name") or source_name
                language = child_text(publication, "language")

            if not self.language_allowed(language, args):
                continue

            # Regular sitemap entries do not carry a reliable headline. Keep the
            # relay conservative and skip entries whose title is unavailable.
            if not title:
                continue

            published_at, published_date, date_source = self.resolve_sitemap_date(
                publication_date=publication_date,
                lastmod=url_lastmod,
                url_date=url_date,
            )

            item = self.normalize_item(
                title=title,
                url=loc,
                published_at=published_at,
                published_date=published_date,
                date_source=date_source,
                source=source_name,
                summary=keywords,
                language=language,
                args=args,
            )
            if item:
                items.append(item)
        return items

    def parse_rss(self, root: ET.Element, source: str, args: argparse.Namespace) -> list[dict[str, Any]]:
        channel = first_child(root, "channel")
        feed_title = child_text(channel, "title") or clean_text(args.reuters_source_label) or "Reuters"
        feed_language = child_text(channel, "language")
        items: list[dict[str, Any]] = []
        for item_el in children(channel, "item"):
            if not self.language_allowed(feed_language, args):
                continue
            title = child_text(item_el, "title")
            link = child_text(item_el, "link")
            guid = child_text(item_el, "guid")
            published_at = parse_datetime_text(
                child_text(item_el, "pubDate") or child_text(item_el, "published") or child_text(item_el, "date")
            ) or ""
            summary = child_text(item_el, "description") or child_text(item_el, "summary")
            source_name = child_text(item_el, "source") or feed_title
            normalized = self.normalize_item(
                title=title,
                url=link or guid,
                published_at=published_at,
                published_date=iso_date_from_datetime(published_at),
                date_source="rss_pubDate" if published_at else "",
                source=source_name,
                summary=summary,
                language=feed_language,
                args=args,
            )
            if normalized:
                items.append(normalized)
        return items

    def parse_atom(self, root: ET.Element, source: str, args: argparse.Namespace) -> list[dict[str, Any]]:
        feed_title = child_text(root, "title") or clean_text(args.reuters_source_label) or "Reuters"
        feed_language = clean_text(root.attrib.get("{http://www.w3.org/XML/1998/namespace}lang") or root.attrib.get("lang"))
        items: list[dict[str, Any]] = []
        for entry in children(root, "entry"):
            entry_language = clean_text(entry.attrib.get("{http://www.w3.org/XML/1998/namespace}lang") or entry.attrib.get("lang")) or feed_language
            if not self.language_allowed(entry_language, args):
                continue
            title = child_text(entry, "title")
            link = ""
            for link_el in children(entry, "link"):
                rel = clean_text(link_el.attrib.get("rel"))
                href = clean_text(link_el.attrib.get("href"))
                if href and (not rel or rel == "alternate"):
                    link = href
                    break
            if not link:
                link = child_text(entry, "id")
            published_at = parse_datetime_text(child_text(entry, "updated") or child_text(entry, "published")) or ""
            summary = child_text(entry, "summary") or child_text(entry, "content")
            normalized = self.normalize_item(
                title=title,
                url=link,
                published_at=published_at,
                published_date=iso_date_from_datetime(published_at),
                date_source="atom_updated" if published_at else "",
                source=feed_title,
                summary=summary,
                language=entry_language,
                args=args,
            )
            if normalized:
                items.append(normalized)
        return items

    def normalize_item(
        self,
        *,
        title: str,
        url: str,
        published_at: str,
        published_date: str,
        date_source: str,
        source: str,
        summary: str,
        args: argparse.Namespace,
        language: str = "",
    ) -> dict[str, Any] | None:
        title = clean_markup_text(title)
        if not title:
            return None
        summary = clean_markup_text(summary)
        source = clean_text(source or args.reuters_source_label or "Reuters")
        language = clean_text(language)
        published_at = clean_text(published_at)
        published_date = clean_text(published_date) or iso_date_from_datetime(published_at)
        date_source = clean_text(date_source)

        if not published_at and not published_date:
            if getattr(args, "allow_undated_reuters", False):
                self.bump_date_stat("missing_date_allowed")
            else:
                self.bump_date_stat("missing_date_skipped")
                if args.debug:
                    print(f"[news_relay][debug] skip undated Reuters item: {title}", file=sys.stderr)
                return None

        article_like = {
            "title": title,
            "description": summary,
            "snippet": summary,
            "source": source,
            "published_at": published_at,
            "entities": [],
        }
        score = score_article(article_like)
        stable_id = clean_text(url) or f"reuters:{hashlib.sha256((title + published_at + published_date).encode('utf-8')).hexdigest()}"
        return {
            "id": stable_id,
            "title": title,
            "titleKo": "",
            "publishedAt": published_at,
            "publishedDate": published_date,
            "source": source,
            "category": clean_text(args.category_label) or categorize_article(title, summary, []),
            "sentiment": classify_market_sentiment(title, summary, []),
            "url": clean_text(url),
            "description": summary,
            "snippet": summary,
            "entities": [],
            "_score": score,
            "_source_provider": "reuters",
            "_market_signal": has_market_signal(title, summary, []),
            "_date_source": date_source,
            "_language": language,
        }

    @staticmethod
    def language_allowed(language: str, args: argparse.Namespace) -> bool:
        wanted = clean_text(getattr(args, "reuters_lang", "") or getattr(args, "lang", "")).lower()
        language = clean_text(language).lower()
        if not wanted or not language:
            # Keep entries with missing language metadata rather than accidentally
            # dropping valid Reuters/RSS rows that do not expose news:language.
            return True
        wanted_codes = {part.strip().split("-", 1)[0] for part in wanted.split(",") if part.strip()}
        language_code = language.split("-", 1)[0]
        return language_code in wanted_codes

    def resolve_sitemap_date(self, *, publication_date: str, lastmod: str, url_date: str) -> tuple[str, str, str]:
        published_at = parse_datetime_text(publication_date) or ""
        if published_at:
            self.bump_date_stat("news_publication_date")
            return published_at, iso_date_from_datetime(published_at), "news:publication_date"

        published_at = parse_datetime_text(lastmod) or ""
        if published_at:
            self.bump_date_stat("lastmod_fallback")
            return published_at, iso_date_from_datetime(published_at), "sitemap:lastmod"

        if url_date:
            self.bump_date_stat("url_date_fallback")
            return "", url_date, "url_date"

        return "", "", ""

    def bump_date_stat(self, name: str) -> None:
        stats = getattr(self, "date_stats", None)
        if isinstance(stats, dict):
            stats[name] = int(stats.get(name, 0)) + 1

    @staticmethod
    def is_recent_enough(item: dict[str, Any], published_after: datetime) -> bool:
        published_at = parse_iso_datetime(item.get("publishedAt") or item.get("published_at"))
        if published_at is not None:
            return published_at >= published_after
        published_date = parse_date_text(item.get("publishedDate") or item.get("published_date"))
        if published_date is not None:
            return published_date >= published_after.date()
        return True


class PublicRSSFeedProvider(ReutersProvider):
    """One independently scheduled RSS/Atom source with conditional requests."""

    group = "rss"

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.headers: dict[str, str] = {}
        self.items: list[dict] | None = None
        self.recommended_interval = 0

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        headers = {"User-Agent": USER_AGENT, "Accept": self.XML_ACCEPT, **self.headers}
        try:
            with urlopen(Request(self.spec["url"], headers=headers), timeout=args.timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise RelayError("RSS 피드 응답이 너무 큽니다")
                root = ET.fromstring(body)
                options = argparse.Namespace(**vars(args))
                options.reuters_source_label = self.spec["label"]
                options.category_label = self.spec["category"]
                kind = strip_ns(root.tag).lower()
                if kind not in {"rss", "feed"}:
                    raise RelayError("피드가 RSS/Atom 형식이 아닙니다")
                ttl = child_text(first_child(root, "channel"), "ttl")
                self.recommended_interval = int(ttl) * 60 if ttl.isdigit() else 0
                parse = self.parse_rss if kind == "rss" else self.parse_atom
                parsed = parse(root, self.spec["url"], options)
                for item in parsed:
                    item.update(source=self.spec["label"], _source_provider=self.group,
                                feedId=self.spec["id"], official=self.group == "official",
                                contentKind=self.spec.get("content_kind", "official" if self.group == "official" else "reporting"))
                self.items = parsed
                self.headers = {}
                if response.headers.get("ETag"):
                    self.headers["If-None-Match"] = response.headers["ETag"]
                if response.headers.get("Last-Modified"):
                    self.headers["If-Modified-Since"] = response.headers["Last-Modified"]
        except HTTPError as exc:
            if exc.code != 304 or self.items is None:
                raise RequestFailure(exc.code, retry_after=retry_after_seconds(exc.headers)) from exc
        cutoff = (now_utc() - timedelta(hours=args.official_lookback_hours)) if self.group == "official" else parse_iso_datetime(args.published_after)
        items = [item for item in (self.items or []) if cutoff is None or self.is_recent_enough(item, cutoff)]
        return ProviderResult(items, {"label": self.spec["label"], "group": self.group, "returned": len(items),
                                      "recommendedIntervalSeconds": self.recommended_interval})


class OfficialFeedProvider(PublicRSSFeedProvider):
    group = "official"


class BenzingaStreamProvider(Provider):
    """Optional single news socket. Keeps editorial updates/deletions by article ID."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.socket = None
        self.status = {"status": "waiting", "label": "Benzinga", "effectiveIntervalSeconds": 60}

    def handle_message(self, message: str, args: argparse.Namespace) -> None:
        if message in {"ping", "pong"}:
            return
        envelope = json.loads(message)
        if not isinstance(envelope, dict) or envelope.get("kind") != "news":
            return
        data = envelope.get("data") or {}
        article = data.get("content") or {}
        identity = str(data.get("id") or article.get("id") or "")
        if not identity:
            return
        with self.lock:
            if data.get("action") == "deleted":
                self.items.pop(identity, None)
                return
            if data.get("action") not in {"created", "updated"}:
                return
            created = parse_datetime_text(article.get("created"))
            if not created or not clean_text(article.get("title")):
                return
            normalized = MarketauxProvider().normalize_article({
                "uuid": "benzinga:" + identity, "title": clean_markup_text(article["title"]),
                "published_at": created, "source": "Benzinga", "url": article.get("url", ""),
                "description": clean_markup_text(article.get("teaser", "")),
                "entities": [{"symbol": stock.get("name", ""), "type": "equity"} for stock in article.get("stocks", [])],
            }, args)
            previous = self.items.get(identity, {})
            updated = parse_datetime_text(article.get("updated")) or created
            if previous.get("updatedAt") and parse_iso_datetime(updated) < parse_iso_datetime(previous["updatedAt"]):
                return
            normalized.update(_source_provider="benzinga", updatedAt=updated,
                              channels=normalize_news_labels(article.get("channels")),
                              tags=normalize_news_labels(article.get("tags")),
                              firstSeenAt=previous.get("firstSeenAt") or now_iso())
            self.items[identity] = normalized
            if len(self.items) > 500:
                oldest = min(self.items, key=lambda key: item_timestamp(self.items[key]))
                del self.items[oldest]

    def run(self, args: argparse.Namespace) -> None:
        try:
            import websocket
        except ImportError:
            with self.lock:
                self.status.update(status="error", error="Benzinga: pip install -r requirements-stream.txt 필요")
            return
        delay = 2
        while not self.stop.is_set():
            try:
                self.socket = websocket.create_connection(
                    "wss://api.benzinga.com/api/v1/news/stream?" + urlencode({"token": self.token}), timeout=5)
                last_ping = time.monotonic()
                last_received = last_ping
                with self.lock:
                    self.status.update(status="ok", error="", lastSuccessAt=now_iso())
                delay = 2
                while not self.stop.is_set():
                    try:
                        opcode, frame = self.socket.recv_data(control_frame=True)
                        last_received = time.monotonic()
                        if opcode == websocket.ABNF.OPCODE_CLOSE:
                            raise RelayError("Benzinga 연결 종료")
                        if opcode == websocket.ABNF.OPCODE_TEXT:
                            message = frame.decode("utf-8") if isinstance(frame, bytes) else frame
                            if message == "ping":
                                self.socket.send("pong")
                            self.handle_message(message, args)
                        with self.lock:
                            self.status.update(status="ok", error="", lastSuccessAt=now_iso())
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_received > 90:
                            raise RelayError("Benzinga heartbeat 시간 초과")
                    if time.monotonic() - last_ping >= 30:
                        self.socket.send("ping")
                        last_ping = time.monotonic()
            except Exception as exc:
                code = getattr(exc, "status_code", None)
                # Never include the token-bearing websocket URL or third-party exception body.
                with self.lock:
                    self.status.update(status="error", error=f"Benzinga 스트림 연결 오류 ({code or type(exc).__name__})")
                delay = 3600 if code in {401, 403} else min(delay * 2, 60)
            finally:
                if self.socket is not None:
                    self.socket.close()
                    self.socket = None
            self.stop.wait(delay)

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, args=(argparse.Namespace(**vars(args)),), daemon=True)
            self.thread.start()
        with self.lock:
            cutoff = parse_iso_datetime(args.published_after)
            items = [dict(item) for item in self.items.values()
                     if cutoff is None or ReutersProvider.is_recent_enough(item, cutoff)]
            return ProviderResult(items, {"provider": "benzinga", "source_statuses": {"benzinga": dict(self.status)}})

    def close(self) -> None:
        self.stop.set()
        connection = self.socket
        if connection is not None:
            connection.abort()
        if self.thread is not None:
            self.thread.join(timeout=6)


class CombinedProvider(Provider):
    def __init__(self, args: argparse.Namespace | None = None) -> None:
        self.providers: list[tuple[str, Provider]] = [
            ("marketaux", ManagedProvider("marketaux", MarketauxProvider())),
            ("reuters", ManagedProvider("reuters", ReutersProvider())),
        ]
        if args is not None:
            if args.no_reuters_default_feed and not args.reuters_feed:
                self.providers = [(name, provider) for name, provider in self.providers if name != "reuters"]
            if args.no_marketaux:
                self.providers = [(name, provider) for name, provider in self.providers if name != "marketaux"]
            if not args.no_official_feeds:
                self.providers.extend((spec["id"], ManagedProvider(spec["id"], OfficialFeedProvider(spec))) for spec in OFFICIAL_FEEDS)
            if not args.no_public_rss:
                self.providers.extend((spec["id"], ManagedProvider(spec["id"], PublicRSSFeedProvider(spec))) for spec in PUBLIC_RSS_FEEDS)
            if args.massive_token and not args.no_massive and not args.no_benzinga:
                self.providers.append(("massive", ManagedProvider("massive", MassiveNewsProvider())))
            elif args.benzinga_token and not args.no_benzinga:
                self.providers.append(("benzinga", BenzingaStreamProvider(args.benzinga_token)))
        if not self.providers:
            raise RelayError("활성화된 뉴스 소스가 없습니다. Massive 키 또는 공개 피드 설정을 확인하세요.")
        self.executor = ThreadPoolExecutor(max_workers=len(self.providers), thread_name_prefix="news-source")
        self.pending = {}
        self.latest = {}

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        combined_items: list[dict[str, Any]] = []
        source_meta: list[dict[str, Any]] = []
        errors: list[str] = []
        statuses: dict[str, Any] = {}

        for name, provider in self.providers:
            try:
                future = self.pending.get(name)
                if future is not None and future.done():
                    del self.pending[name]
                    self.latest[name] = future.result()
                if name not in self.pending:
                    options = argparse.Namespace(**{key: value for key, value in vars(args).items() if key != "live_translator"})
                    self.pending[name] = self.executor.submit(provider.fetch, options)
                if args.watch <= 0:
                    self.latest[name] = self.pending.pop(name).result()
                result = self.latest.get(name, ProviderResult([], {"source_statuses": {
                    name: {"status": "waiting", "label": getattr(provider, "label", name), "group": getattr(provider, "group", name)}
                }}))
                combined_items.extend(result.items)
                source_meta.append({"provider": name, "returned": len(result.items), "meta": result.meta})
                statuses.update(result.meta.get("source_statuses", {}))
                errors.extend(result.meta.get("errors", []))
            except Exception as exc:  # noqa: BLE001
                message = f"{name}: {safe_error(exc, args)}"
                errors.append(message)
                statuses[name] = {"status": "error", "error": safe_error(exc, args)}
                if args.debug:
                    print(f"[news_relay][warn] combined source failed: {message}", file=sys.stderr)

        meta: dict[str, Any] = {
            "provider": "combined",
            "sources": source_meta,
            "source_statuses": statuses,
        }
        if errors:
            meta["errors"] = errors[:10]
        return ProviderResult(items=combined_items, meta=meta)

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)
        for _, provider in self.providers:
            if hasattr(provider, "close"):
                provider.close()


class ManagedProvider(Provider):
    """Persist successful data and request deadlines independently for each source."""

    def __init__(self, name: str, provider: Provider):
        self.name = name
        self.provider = provider
        self.group = getattr(provider, "group", name) if isinstance(provider, (PublicRSSFeedProvider, MassiveNewsProvider)) else name
        self.label = provider.spec["label"] if isinstance(provider, PublicRSSFeedProvider) else (provider.label if isinstance(provider, MassiveNewsProvider) else name.title())

    def interval(self, args: argparse.Namespace, usage: dict, source_meta: dict | None = None) -> int:
        if self.name == "massive":
            return args.massive_interval
        if self.name != "marketaux":
            if isinstance(self.provider, PublicRSSFeedProvider):
                requested = args.official_interval if self.group == "official" else args.rss_interval
                return max(requested, self.provider.spec.get("interval", 60),
                           (source_meta or {}).get("recommendedIntervalSeconds", 0))
            return args.reuters_interval
        try:
            daily_limit = max(1, int(usage.get("daily_limit") or 100))
            per_minute = max(1, int(usage.get("rate_limit") or 30))
        except (TypeError, ValueError):
            daily_limit, per_minute = 100, 30
        return max(args.marketaux_interval, math.ceil(86400 * 1.04 / daily_limit), math.ceil(60 * 1.04 / per_minute))

    def fetch(self, args: argparse.Namespace) -> ProviderResult:
        config_keys = ("query", "countries", "lang", "domains", "sort", "marketaux_mode", "fetch_limit",
                       "must_have_entities", "group_similar", "filter_entities", "lookback_hours",
                       "explicit_published_after", "reuters_feed", "no_reuters_default_feed", "reuters_lang",
                       "reuters_fetch_limit", "reuters_sitemap_limit", "allow_undated_reuters", "reuters_source_label",
                       "provider_label", "category_label", "official_lookback_hours")
        config = {key: getattr(args, key, None) for key in config_keys}
        if isinstance(self.provider, PublicRSSFeedProvider):
            config["rss_source"] = self.provider.spec
        if self.name == "massive":
            config["massive_fetch_limit"] = args.massive_fetch_limit
        # Store only a hash, never credentials or a credential-bearing request URL.
        config["credential"] = (args.api_key or args.file_marketaux_api_token or args.env_marketaux_api_token) if self.name == "marketaux" else ""
        if self.name == "massive":
            config["credential"] = args.massive_token
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        path = resolve_path(args.state_dir) / f"{self.name}.json"
        state = read_json(path, {})
        if not isinstance(state, dict):
            state = {}
        # Preserve the rate-limit deadline even when filters change, but never reuse
        # results from another query. This prevents config edits from bypassing quota.
        if state.get("fingerprint") != fingerprint:
            state = {"nextFetchAt": state.get("nextFetchAt"), "effectiveIntervalSeconds": state.get("effectiveIntervalSeconds"),
                     "meta": {"usage": (state.get("meta") or {}).get("usage", {})}}
        interval = self.interval(args, (state.get("meta") or {}).get("usage", {}), state.get("meta"))
        current = now_utc()
        deadline = parse_iso_datetime(state.get("nextFetchAt"))
        if deadline is None or current >= deadline:
            state["lastAttemptAt"] = current.isoformat()
            # Reserve the slot before network I/O, so a process restart cannot retry for free.
            state.update(nextFetchAt=(current + timedelta(seconds=interval)).isoformat(), fingerprint=fingerprint)
            atomic_write_json(path, state)
            try:
                result = self.provider.fetch(args)
                usage = result.meta.get("usage") or {}
                interval = self.interval(args, usage, result.meta)
                old_items = {item_identity(item): item for item in state.get("items", [])}
                for item in result.items:
                    item["firstSeenAt"] = old_items.get(item_identity(item), {}).get("firstSeenAt") or now_iso()
                state.update(items=result.items, meta=result.meta, lastSuccessAt=now_utc().isoformat(),
                             status="ok", error="", effectiveIntervalSeconds=interval)
                if result.meta.get("errors"):
                    state.update(status="degraded", error="; ".join(result.meta["errors"])[:300])
                delay = interval
                if self.name == "marketaux" and str(usage.get("daily_remaining")) == "0":
                    # No reset instant is guaranteed by this response: wait a full day.
                    delay = max(delay, 86400)
                    state.update(status="limited", error="Marketaux 일일 요청 한도 소진")
            except Exception as exc:
                delay = interval
                status_code = getattr(exc, "status", None)
                # Reuters wraps per-URL failures, so inspect its safe HTTP summary too.
                if status_code in {401, 403} or re.search(r"HTTP (401|403)\b", str(exc)):
                    delay = max(delay, 3600)
                elif status_code == 429:
                    delay = max(delay, 60)
                elif not isinstance(exc, RequestFailure):
                    delay = max(delay, 300)
                delay = max(delay, getattr(exc, "retry_after", 0))
                if str(getattr(exc, "usage", {}).get("daily_remaining")) == "0":
                    delay = max(delay, 86400)
                state.update(status="error", error=safe_error(exc, args), effectiveIntervalSeconds=interval)
                print(f"[news_relay][warn] {self.name}: {state['error']} · {delay:.0f}s 후 재시도", file=sys.stderr)
            state["nextFetchAt"] = (now_utc() + timedelta(seconds=delay)).isoformat()
            state["fingerprint"] = fingerprint
            atomic_write_json(path, state)
        elif not state.get("lastSuccessAt") and not state.get("error"):
            state.update(status="waiting", error="요청 간격 대기 중")
        status = {key: state.get(key) for key in ("status", "error", "lastAttemptAt", "lastSuccessAt", "nextFetchAt", "effectiveIntervalSeconds")}
        status.update(label=self.label, group=self.group)
        if self.name == "marketaux":
            status["usage"] = (state.get("meta") or {}).get("usage", {})
        meta = dict(state.get("meta") or {})
        meta.update(provider=self.name, source_statuses={self.name: status})
        if status.get("error"):
            meta["errors"] = [f"{self.name}: {status['error']}"]
        cutoff = (now_utc() - timedelta(hours=args.official_lookback_hours)) if self.group == "official" else parse_iso_datetime(args.published_after)
        items = [item for item in (state.get("items") or []) if cutoff is None or ReutersProvider.is_recent_enough(item, cutoff)]
        return ProviderResult(items=items, meta=meta)


def safe_error(exc: Exception, args: argparse.Namespace) -> str:
    message = redact_url_secrets(str(exc))
    for key in ("api_key", "file_marketaux_api_token", "env_marketaux_api_token", "deepl_api_key",
                "translate_api_key", "file_deepl_api_key", "env_deepl_api_key", "benzinga_token", "massive_token"):
        secret = getattr(args, key, "")
        if secret:
            message = message.replace(secret, "***")
    return clean_markup_text(message)[:300]


def retry_after_seconds(headers: Any) -> float:
    value = headers.get("Retry-After", "")
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        when = parse_iso_datetime(value)
        return max(0.0, (when - now_utc()).total_seconds()) if when else 0.0



def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def redact_url_secrets(value: Any) -> str:
    return SECRET_QUERY_PARAM_PATTERN.sub(lambda match: f"{match.group(1)}***", str(value or ""))


def clean_markup_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = TAG_PATTERN.sub(" ", text)
    return clean_text(text)


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def children(element: ET.Element | None, local_name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [child for child in list(element) if strip_ns(child.tag) == local_name]


def first_child(element: ET.Element | None, local_name: str) -> ET.Element | None:
    matches = children(element, local_name)
    return matches[0] if matches else None


def child_text(element: ET.Element | None, local_name: str) -> str:
    child = first_child(element, local_name)
    if child is None:
        return ""
    return clean_markup_text("".join(child.itertext()))


def parse_datetime_text(value: Any) -> str | None:
    raw = clean_text(value)
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def normalize_news_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels = [clean_text(item.get("name", "") if isinstance(item, dict) else item)
              for item in value if isinstance(item, (str, dict))]
    return list(dict.fromkeys(label for label in labels if label))


def extract_secret_from_text(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _, value = line.split("=", 1)
            candidate = value.strip().strip('"').strip("'")
            if candidate:
                return candidate
        else:
            return line.strip().strip('"').strip("'")
    return clean_text(text)


def load_secret_from_file(path_text: str | Path | None) -> tuple[str, str]:
    if not path_text:
        return "", ""
    path = resolve_path(path_text)
    if not path.exists() or not path.is_file():
        return "", ""
    try:
        value = extract_secret_from_text(path.read_text(encoding="utf-8"))
    except OSError:
        return "", ""
    return value, str(path)


def first_existing_secret(candidates: list[Path]) -> tuple[str, str]:
    for candidate in candidates:
        value, source_path = load_secret_from_file(candidate)
        if value:
            return value, source_path
    return "", ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_iso_datetime(value: Any) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


def iso_date_from_datetime(value: Any) -> str:
    parsed = parse_iso_datetime(value)
    return parsed.date().isoformat() if parsed else ""


def parse_date_text(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    # Accept a pure YYYY-MM-DD first, then fall back to full datetime parsing.
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        parsed = parse_iso_datetime(text)
        return parsed.date() if parsed else None


def extract_date_from_url(url: Any) -> str:
    text = clean_text(url)
    if not text:
        return ""
    for pattern in URL_DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year, month, day = (int(value) for value in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return ""


def as_bool_param(value: bool) -> str:
    return "true" if value else "false"


def is_probable_marketaux_sort_error(error: Exception) -> bool:
    text = str(error).lower()
    if "sort" not in text:
        return False
    return any(token in text for token in ["400", "422", "invalid", "unsupported", "not allowed", "published_at", "published_on"])


def request_json(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    retries: int = 0,
    retry_backoff: float = 1.5,
    debug: bool = False,
) -> HttpResponse:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = Request(url, headers=request_headers, method=method, data=data)
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read().decode(charset, errors="replace")
                payload = json.loads(raw)
                return HttpResponse(payload=payload, headers=dict(response.headers.items()))
        except HTTPError as exc:
            if exc.code in {401, 403, 429, 456} or retry_after_seconds(exc.headers) > 0:
                raise RequestFailure(exc.code, retry_after=retry_after_seconds(exc.headers),
                                     usage=extract_usage_headers(dict(exc.headers.items()))) from exc
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                detail_payload = json.loads(body)
                detail = json.dumps(detail_payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

            retryable = exc.code in {408, 425, 500, 502, 503, 504}
            last_error = RelayError(f"HTTP {exc.code} 요청 실패: {detail}")
            if retryable and attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] HTTP {exc.code} 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc
        except (TimeoutError, socket.timeout) as exc:
            last_error = RelayError(f"네트워크 요청 시간 초과: {exc}")
            if attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] 요청 시간 초과 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            last_error = RelayError(f"네트워크 요청 실패: {reason}")
            if attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] 네트워크 오류 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc
        except json.JSONDecodeError as exc:
            raise RelayError(f"JSON 파싱 실패: {exc}") from exc

    assert last_error is not None
    raise last_error


def request_text(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    retries: int = 0,
    retry_backoff: float = 1.5,
    debug: bool = False,
) -> TextResponse:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "text/plain, application/xml, text/xml, */*"}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if debug:
            print(f"[news_relay][debug] text request attempt={attempt + 1}/{retries + 1} url={redact_url_secrets(url)}")
        request = Request(url, headers=request_headers, method=method, data=data)
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read().decode(charset, errors="replace")
                return TextResponse(text=raw, headers=dict(response.headers.items()))
        except HTTPError as exc:
            if exc.code in {401, 403, 429} or retry_after_seconds(exc.headers) > 0:
                raise RequestFailure(exc.code, retry_after=retry_after_seconds(exc.headers)) from exc
            body = exc.read().decode("utf-8", errors="replace")
            detail = clean_text(body)[:800]
            retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
            last_error = RelayError(f"HTTP {exc.code} 요청 실패: {detail}")
            if retryable and attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] HTTP {exc.code} 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc
        except (TimeoutError, socket.timeout) as exc:
            last_error = RelayError(f"네트워크 요청 시간 초과: {exc}")
            if attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] 요청 시간 초과 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            last_error = RelayError(f"네트워크 요청 실패: {reason}")
            if attempt < retries:
                sleep_seconds = retry_backoff * (attempt + 1)
                if debug:
                    print(
                        f"[news_relay][warn] 네트워크 오류 재시도 {attempt + 1}/{retries} · {sleep_seconds:.1f}s 후 재요청",
                        file=sys.stderr,
                    )
                time.sleep(sleep_seconds)
                continue
            raise last_error from exc

    assert last_error is not None
    raise last_error


def read_text_source(
    source: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    retries: int = 0,
    retry_backoff: float = 1.5,
    debug: bool = False,
) -> TextResponse:
    if HTTP_PATTERN.match(source):
        return request_text(
            source,
            timeout=timeout,
            headers=headers,
            retries=retries,
            retry_backoff=retry_backoff,
            debug=debug,
        )

    path = Path(source)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RelayError(f"로컬 피드 파일을 읽을 수 없습니다: {path}: {exc}") from exc
    if debug:
        print(f"[news_relay][debug] local xml source={path}")
    return TextResponse(text=raw.decode("utf-8-sig", errors="replace"), headers={})


def extract_usage_headers(headers: dict[str, str]) -> dict[str, str]:
    lowered = {key.lower(): value for key, value in headers.items()}

    def get(name: str) -> str:
        return lowered.get(name.lower(), "")

    return {
        "daily_limit": get("x-usagelimit-limit"),
        "daily_remaining": get("x-usagelimit-remaining"),
        "rate_limit": get("x-ratelimit-limit"),
        "rate_remaining": get("x-ratelimit-remaining"),
    }


def build_marketaux_query(args: argparse.Namespace) -> str:
    if clean_text(args.query):
        return clean_text(args.query)
    if args.marketaux_mode == "broad":
        return DEFAULT_BROAD_SEARCH
    if args.marketaux_mode == "indices":
        return '"u.s. stocks" | "wall street" | nasdaq | "s&p 500" | dow | futures | "stock market"'
    return ""


def has_market_signal(title: str, snippet: str = "", entities: list[dict[str, Any]] | None = None) -> bool:
    text = f"{clean_text(title)} {clean_text(snippet)}".lower()
    if any(term_matches(term, text) for term in (*MARKET_SIGNAL_TERMS, *MARKET_EVENTS)):
        return True

    entity_types = {clean_text((entity or {}).get("type")).lower() for entity in (entities or [])}
    symbols = {clean_text((entity or {}).get("symbol")).upper() for entity in (entities or [])}
    return bool(entity_types & BROAD_ENTITY_TYPES or symbols & (INDEX_SYMBOLS | MEGACAP_SYMBOLS))


def term_matches(term: str, text: str) -> bool:
    # Avoid "fed" in "federation", "war" in "award", "oil" in "soil", etc.
    return bool(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text)) if term.isascii() else term in text


def score_article(article: dict[str, Any]) -> float:
    title = clean_text(article.get("title")).lower()
    description = clean_text(article.get("description")).lower()
    snippet = clean_text(article.get("snippet")).lower()
    text = " ".join([part for part in [title, description, snippet] if part])
    source = clean_text(article.get("source")).lower()
    entities = article.get("entities") or []
    symbols = {clean_text((entity or {}).get("symbol")).upper() for entity in entities}
    entity_types = {clean_text((entity or {}).get("type")).lower() for entity in entities}

    score = 0.0

    weights = {
        "federal reserve": 10,
        "fed": 8,
        "fomc": 10,
        "powell": 8,
        "inflation": 8,
        "cpi": 9,
        "pce": 8,
        "payrolls": 9,
        "nonfarm payrolls": 10,
        "unemployment": 8,
        "jobs": 5,
        "gdp": 7,
        "recession": 7,
        "treasury": 7,
        "yield": 6,
        "bond": 4,
        "10-year": 7,
        "2-year": 6,
        "oil": 6,
        "crude": 6,
        "opec": 7,
        "tariff": 6,
        "sanction": 6,
        "geopolit": 6,
        "china stimulus": 6,
        "consumer confidence": 5,
        "retail sales": 5,
        "pmi": 5,
        "wall street": 8,
        "u.s. stocks": 9,
        "stock market": 7,
        "s&p 500": 9,
        "nasdaq": 8,
        "dow": 7,
        "futures": 5,
        # Korean headline support, mainly for local fixtures or translated
        # Reuters-compatible feeds supplied by the user.
        "연준": 8,
        "fomc": 10,
        "물가": 7,
        "인플레이션": 8,
        "cpi": 9,
        "pce": 8,
        "고용": 6,
        "실업": 6,
        "국채": 7,
        "국채금리": 8,
        "금리": 5,
        "채권": 4,
        "원유": 6,
        "유가": 6,
        "opec": 7,
        "달러": 4,
        "환율": 4,
        "증시": 7,
        "미국 증시": 9,
        "주식": 5,
        "선물": 5,
        "나스닥": 8,
        "다우": 7,
        "s&p": 9,
        "실적": 5,
        "성장주": 5,
        "시장": 3,
    }
    for keyword, weight in weights.items():
        if term_matches(keyword, text):
            score += weight

    source_bonus = {
        "reuters": 5,
        "bloomberg": 4,
        "wsj": 3,
        "cnbc": 3,
        "marketwatch": 2,
        "ft": 3,
        "investing.com": 1,
        "finance.yahoo.com": 1,
    }
    for keyword, weight in source_bonus.items():
        if keyword in source:
            score += weight

    if entity_types & BROAD_ENTITY_TYPES:
        score += 3
    if symbols & INDEX_SYMBOLS:
        score += 4
    if symbols & MEGACAP_SYMBOLS or any(term_matches(term, text) for term in MARKET_EVENTS):
        score += 10

    equity_symbols = [symbol for symbol in symbols if symbol and symbol not in INDEX_SYMBOLS]
    if len(equity_symbols) == 1 and score < 10 and not symbols & MEGACAP_SYMBOLS:
        score -= 4

    published_at = parse_iso_datetime(article.get("published_at") or article.get("publishedAt"))
    if published_at is not None:
        age_hours = max((now_utc() - published_at).total_seconds() / 3600, 0)
        score += max(0.0, 6 - age_hours * 0.2)

    return round(score, 3)



def classify_market_sentiment(title: str, snippet: str, entities: list[dict[str, Any]]) -> str:
    impact = assess_impact({"title": title, "snippet": snippet, "entities": entities})
    return impact["direction"] if impact["direction"] in {"positive", "negative"} else "neutral"


def categorize_article(title: str, snippet: str, entities: list[dict[str, Any]]) -> str:
    text = f"{clean_text(title)} {clean_text(snippet)}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category

    entity_types = {clean_text((entity or {}).get("type")).lower() for entity in entities}
    if "commodity" in entity_types:
        return "원자재"
    if "currency" in entity_types:
        return "외환"
    if "index" in entity_types:
        return "지수"
    if entities:
        return "마켓"
    return "뉴스"


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = clean_text(item.get("url") or item.get("id") or item.get("title")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def apply_broad_market_filter(items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    # In combined/reuters mode the Reuters sitemap may contain general world news.
    # Keep the overlay market-focused by requiring a finance/market signal for
    # Reuters-only metadata entries unless the user deliberately lowers the score
    # threshold to 0.
    if args.provider == "official" or args.marketaux_mode == "watchlist" or args.min_overlay_score <= 0:
        return items

    filtered: list[dict[str, Any]] = []
    for item in items:
        # Corporate boilerplate often lists markets and risks unrelated to the
        # announcement. Require relevance in the headline for press releases.
        if item.get("contentKind") == "press_release" and not has_market_signal(item.get("title", "")):
            continue
        score_ok = float(item.get("_score") or 0) >= args.min_overlay_score
        signal_ok = bool(item.get("official")) or has_market_signal(item.get("title", ""), item.get("snippet", ""), item.get("entities", []))
        if score_ok and signal_ok:
            filtered.append(item)
    return filtered


def sort_items(items: list[dict[str, Any]], *, rank_mode: str = "score") -> list[dict[str, Any]]:
    mode = clean_text(rank_mode).lower() or "score"

    def score_value(item: dict[str, Any]) -> float:
        try:
            return float(item.get("_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    def key_score(item: dict[str, Any]) -> tuple[float, float, int]:
        return (score_value(item), item_timestamp(item), item_time_precision_rank(item))

    def key_fresh(item: dict[str, Any]) -> tuple[float, int, float]:
        return (item_timestamp(item), item_time_precision_rank(item), score_value(item))

    def key_balanced(item: dict[str, Any]) -> tuple[float, float, int, float]:
        timestamp = item_timestamp(item)
        precision_rank = item_time_precision_rank(item)
        score = score_value(item)
        if timestamp <= 0:
            freshness_points = 0.0
        else:
            age_hours = max((now_utc().timestamp() - timestamp) / 3600.0, 0.0)
            # Up to 30 freshness points for very recent items, fading over a day.
            freshness_points = max(0.0, 30.0 * (1.0 - min(age_hours, 24.0) / 24.0))
        return (score + freshness_points, timestamp, precision_rank, score)

    if mode in {"fresh", "latest", "recent"}:
        return sorted(items, key=key_fresh, reverse=True)
    if mode in {"balanced", "hybrid"}:
        return sorted(items, key=key_balanced, reverse=True)
    return sorted(items, key=key_score, reverse=True)


def item_timestamp(item: dict[str, Any]) -> float:
    published = parse_iso_datetime(item.get("publishedAt") or item.get("published_at"))
    if published:
        return published.timestamp()
    published_date = parse_date_text(item.get("publishedDate") or item.get("published_date"))
    if published_date:
        # A date-only Reuters fallback means we know the calendar day, but not
        # the clock time.  Earlier versions ranked this at 12:00 UTC, which can
        # make unknown-time Reuters rows look newer than exact Marketaux rows
        # from 30-60 minutes ago.  Rank date-only rows at the start of the day so
        # precise timestamps always win during the live-news portion of the day.
        return datetime(
            published_date.year,
            published_date.month,
            published_date.day,
            0,
            0,
            tzinfo=timezone.utc,
        ).timestamp()
    return 0.0


def item_time_precision_rank(item: dict[str, Any]) -> int:
    if parse_iso_datetime(item.get("publishedAt") or item.get("published_at")) is not None:
        return 2
    if parse_date_text(item.get("publishedDate") or item.get("published_date")) is not None:
        return 1
    return 0


def item_time_precision_label(item: dict[str, Any]) -> str:
    rank = item_time_precision_rank(item)
    if rank == 2:
        return "exact"
    if rank == 1:
        return "date-only"
    return "missing"


def source_provider_name(item: dict[str, Any]) -> str:
    """Return the internal provider, not merely the visible publisher label."""
    provider = clean_text(item.get("_source_provider")).lower()
    return provider or "marketaux"


def item_identity(item: dict[str, Any]) -> str:
    return clean_text(item.get("url") or item.get("id") or item.get("title")).lower()


def count_source_providers(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        name = source_provider_name(item)
        counts[name] = counts.get(name, 0) + 1
    return counts


def provider_names_in_items(items: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = source_provider_name(item)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    preferred = [name for name in ("marketaux", "reuters") if name in seen]
    extras = [name for name in names if name not in {"marketaux", "reuters"}]
    return preferred + extras


def apply_provider_aware_market_filter(items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Apply the broad market score filter independently per source provider.

    A single merged score filter can make the combined tab look source-biased:
    for example, many high-scoring Reuters rows can leave fresh Marketaux rows
    out, even though those Marketaux rows are visible in the Marketaux tab.
    Provider-aware filtering evaluates each source independently; the
    combined tab then simply ranks the eligible union by freshness.
    """
    if clean_text(getattr(args, "provider", "")).lower() != "combined":
        return apply_broad_market_filter(items, args)

    filtered_parts: list[dict[str, Any]] = []
    for provider_name in provider_names_in_items(items):
        provider_items = provider_filtered_items(items, provider_name)
        if not provider_items:
            continue
        filtered_parts.extend(apply_broad_market_filter(provider_items, clone_args_for_feed(args, provider_name)))
    return dedupe_items(filtered_parts)


def count_time_precision(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = item_time_precision_label(item)
        counts[label] = counts.get(label, 0) + 1
    return counts


def select_final_items(items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Sort and limit items, while optionally reserving slots for Reuters.

    v3 selected by score first and the browser later re-sorted only those five
    cards by time. That could produce a 20-minute Reuters card followed by a
    13-hour-old Marketaux card even when newer candidates existed. The default
    rank mode is now fresh, so the limiting step itself is newest-first.
    """
    limit = max(int(args.limit or 0), 0)
    if limit <= 0:
        return []

    rank_mode = clean_text(getattr(args, "rank_mode", "fresh") or "fresh")
    sorted_items = sort_items(items, rank_mode=rank_mode)
    selected = sorted_items[:limit]

    min_reuters = max(int(getattr(args, "min_reuters_items", 0) or 0), 0)
    if args.provider != "combined" or min_reuters <= 0:
        return selected

    reuters_available = [item for item in sorted_items if source_provider_name(item) == "reuters"]
    if not reuters_available:
        return selected

    target_reuters = min(min_reuters, limit, len(reuters_available))
    selected_reuters_count = sum(1 for item in selected if source_provider_name(item) == "reuters")
    if selected_reuters_count >= target_reuters:
        return selected

    selected_keys = {item_identity(item) for item in selected}
    needed = target_reuters - selected_reuters_count
    for candidate in reuters_available:
        if needed <= 0:
            break
        key = item_identity(candidate)
        if key in selected_keys:
            continue

        replace_index = None
        for index in range(len(selected) - 1, -1, -1):
            if source_provider_name(selected[index]) != "reuters":
                replace_index = index
                break
        if replace_index is None:
            break

        removed = selected.pop(replace_index)
        selected_keys.discard(item_identity(removed))
        selected.append(candidate)
        selected_keys.add(key)
        needed -= 1

    return sort_items(selected, rank_mode=rank_mode)[:limit]


def item_age_label(item: dict[str, Any]) -> str:
    # Only exact timestamps should produce a relative age. Date-only Reuters
    # fallbacks are shown as a calendar date in the widget, so a derived "8h"
    # debug age would imply clock precision we do not actually have.
    if item_time_precision_rank(item) != 2:
        return ""
    timestamp = item_timestamp(item)
    if timestamp <= 0:
        return ""
    age_seconds = max(now_utc().timestamp() - timestamp, 0)
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)}m"
    if age_seconds < 86400:
        return f"{age_seconds / 3600:.1f}h"
    return f"{age_seconds / 86400:.1f}d"


def build_translation_context(item: dict[str, Any], *, max_chars: int) -> str:
    parts: list[str] = []
    for key in ["snippet", "description", "summary"]:
        text = clean_text(item.get(key))
        if text:
            parts.append(text)
    entities = item.get("entities") or []
    entity_names = [clean_text((entity or {}).get("name")) for entity in entities]
    entity_names = [name for name in entity_names if name]
    if entity_names:
        parts.append("Entities: " + ", ".join(entity_names[:10]))
    context = " ".join(parts)
    if max_chars > 0 and len(context) > max_chars:
        return context[:max_chars].rsplit(" ", 1)[0]
    return context


def build_custom_instructions(args: argparse.Namespace) -> list[str]:
    instructions: list[str] = []
    if args.translate_instructions:
        for piece in args.translate_instructions.split("||"):
            text = clean_text(piece)
            if text:
                instructions.append(text)
    else:
        instructions.extend(DEFAULT_DEEPL_INSTRUCTIONS)
    return instructions[:10]


def load_replacements(path_text: str) -> dict[str, str]:
    replacements = dict(DEFAULT_KO_REPLACEMENTS)
    path = Path(path_text) if path_text else None
    if path and not path.is_absolute():
        path = PROJECT_ROOT / path
    if path and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key, value in payload.items():
                    key_text = clean_text(key)
                    value_text = clean_text(value)
                    if key_text and value_text:
                        replacements[key_text] = value_text
        except json.JSONDecodeError:
            pass
    return replacements


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    out = text
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def load_translation_cache(path_text: str) -> tuple[Path | None, dict[str, Any]]:
    if not path_text:
        return None, {}
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return path, payload
        except json.JSONDecodeError:
            pass
    return path, {}


TRANSLATION_CACHE_LOCK = threading.Lock()


def save_translation_cache(path: Path | None, updates: dict[str, Any]) -> None:
    if path is None or not updates:
        return
    with TRANSLATION_CACHE_LOCK:
        current = read_json(path, {})
        if not isinstance(current, dict):
            current = {}
        current.update(updates)
        atomic_write_json(path, current)


def checked_translation(item: dict, cached: Any, args: argparse.Namespace,
                        replacements: dict, status: str = "cached") -> tuple[dict, Any] | None:
    output = dict(item)
    output.pop("translationError", None)
    output.pop("translationIssue", None)
    if isinstance(cached, dict) and cached.get("status") == "review":
        output.update(titleKo="", translationStatus="review", translationIssue="mixed_script")
        return output, review_record()
    if not isinstance(cached, str) or not cached.strip():
        return None
    source = clean_text(item.get("title"))
    repaired, raw_issue = inspect_translation(source, cached, args.translate_target)
    text, issue = inspect_translation(source, apply_replacements(cached if raw_issue else repaired, replacements),
                                     args.translate_target)
    if issue:
        output.update(titleKo="", translationStatus="review", translationIssue=issue)
        return output, review_record()
    output.update(titleKo=text, translationStatus=status)
    # Keep normal term substitutions reversible, but never persist a known typo.
    return output, text if raw_issue else repaired


def build_translator(args: argparse.Namespace) -> Translator:
    provider = clean_text(args.translate_provider).lower()
    if provider in {"", "none"}:
        return NullTranslator()
    if provider == "deepl":
        return DeepLTranslator(args)
    raise RelayError(f"지원하지 않는 translate_provider 입니다: {args.translate_provider}")


def translation_cache_key(item: dict[str, Any], args: argparse.Namespace) -> str:
    base = "|".join(
        [
            clean_text(item.get("title")),
            clean_text(args.translate_source).upper(),
            clean_text(args.translate_target).upper(),
            clean_text(args.deepl_glossary_id),
            clean_text(args.translate_instructions),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class BackgroundTranslator:
    """Publish originals immediately; translate one headline at a time without blocking collection."""

    def __init__(self, args: argparse.Namespace):
        self.args = argparse.Namespace(**{key: value for key, value in vars(args).items() if key != "live_translator"})
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="headline-translation")
        self.future = None
        self.active_key = None
        self.cache_path, self.cache = load_translation_cache(args.translate_cache)
        self.replacements = load_replacements(args.translate_terms_file)
        self.failed = {}
        self.retry_at = 0.0

    def translate(self, items: list[dict], args: argparse.Namespace) -> tuple[list[dict], int]:
        billed = 0
        if self.future is not None and self.future.done():
            try:
                completed, billed = self.future.result()
                item = completed[0]
                if item.get("titleKo"):
                    self.cache[self.active_key] = item["titleKo"]
                    self.failed.pop(self.active_key, None)
                elif item.get("translationStatus") == "review":
                    self.cache[self.active_key] = review_record()
                    self.failed.pop(self.active_key, None)
                else:
                    self.failed[self.active_key] = time.monotonic() + 300
                    error = item.get("translationError", "")
                    if re.search(r"HTTP (401|403|429|456)\b", error) or "키" in error:
                        self.retry_at = time.monotonic() + 3600
            except Exception:
                self.failed[self.active_key] = time.monotonic() + 300
            self.future = None
        output = []
        cache_updates = {}
        for item in items:
            key = translation_cache_key(item, args)
            cached = self.cache.get(key)
            checked = checked_translation(item, cached, args, self.replacements)
            if checked:
                translated_item, entry = checked
                output.append(translated_item)
                if entry != cached:
                    self.cache[key] = entry
                    cache_updates[key] = entry
                continue
            failed = self.failed.get(key, 0) > time.monotonic() or self.retry_at > time.monotonic()
            output.append(dict(item, titleKo="", translationStatus="failed" if failed else "pending"))
            if not failed and self.future is None:
                self.active_key = key
                self.future = self.executor.submit(translate_items, [dict(item)], self.args)
        save_translation_cache(self.cache_path, cache_updates)
        return output, billed

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


def translate_items(items: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    if args.translate_provider == "none":
        return [dict(item, translationStatus="disabled") for item in items], 0
    if getattr(args, "live_translator", None) is not None:
        return args.live_translator.translate(items, args)
    replacements = load_replacements(args.translate_terms_file)
    cache_path, cache = load_translation_cache(args.translate_cache)
    total_billed = 0
    translated_items: list[dict[str, Any]] = []
    cache_updates: dict[str, Any] = {}
    translator = None
    translator_error = ""
    try:
        translator = build_translator(args)
    except RelayError as exc:
        translator_error = safe_error(exc, args)
        print(f"[news_relay][warn] translation: {translator_error}", file=sys.stderr)

    for item in items:
        cache_key = translation_cache_key(item, args)
        item_copy = dict(item)
        # An original headline must never be labeled as a Korean translation.
        item_copy.update(titleKo="", translationStatus="failed")
        item_copy.pop("translationError", None)
        cached = cache.get(cache_key)
        checked = checked_translation(item, cached, args, replacements)
        if checked:
            item_copy, entry = checked
            if entry != cached:
                cache_updates[cache_key] = entry
        elif translator_error:
            item_copy["translationError"] = translator_error
        else:
            try:
                result = translator.translate_item(item, args)
                translated_text = clean_text(result.text)
                if not translated_text:
                    raise RelayError("번역문이 비어 있습니다")
                total_billed += result.billed_characters
                item_copy, entry = checked_translation(item, translated_text, args, replacements, "translated")
                cache[cache_key] = entry
                cache_updates[cache_key] = entry
            except Exception as exc:
                message = safe_error(exc, args)
                item_copy["translationError"] = message
                print(f"[news_relay][warn] translation: {message}", file=sys.stderr)
                if isinstance(exc, RequestFailure) and exc.status in {401, 403, 429, 456}:
                    translator_error = message
        translated_items.append(item_copy)
    save_translation_cache(cache_path, cache_updates)
    return translated_items, total_billed


def strip_internal_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in items]


def write_payload(out_path: Path, items: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    meta = dict(meta or {})
    statuses = meta.get("source_statuses") or {}
    previous = read_json(out_path, {})
    if not isinstance(previous, dict):
        previous = {}
    failed = statuses and all(value.get("status") in {"error", "waiting"} for value in statuses.values())
    if failed and isinstance(previous.get("items"), list):
        items = previous["items"]
        updated_at = previous.get("updatedAt")
        meta["retained"] = True
        for key in ("final_source_counts", "final_time_precision_counts"):
            if key in (previous.get("meta") or {}):
                meta[key] = previous["meta"][key]
    else:
        successes = [value.get("lastSuccessAt") for value in statuses.values() if value.get("lastSuccessAt")]
        updated_at = max(successes) if successes else (None if failed else now_iso())
    # Also check retained public snapshots from older versions, even during outages.
    checked_items = []
    for item in items:
        if item.get("titleKo"):
            text, issue = inspect_translation(clean_text(item.get("title")), clean_text(item["titleKo"]))
            item = dict(item, titleKo=text)
            if issue:
                item.update(translationStatus="review", translationIssue=issue)
        checked_items.append(attach_impact(item))
    items = checked_items
    meta["translation_review_count"] = sum(item.get("translationStatus") == "review" for item in items)
    meta["translation_failed_count"] = sum(item.get("translationStatus") == "failed" for item in items)
    meta["translation_pending_count"] = sum(item.get("translationStatus") == "pending" for item in items)
    meta["final_count"] = len(items)
    meta["checkedAt"] = now_iso()
    payload: dict[str, Any] = {"updatedAt": updated_at, "items": items, "meta": meta}
    atomic_write_json(out_path, payload)


def resolve_output_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def clone_args_for_feed(args: argparse.Namespace, provider_name: str) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.provider = provider_name
    if provider_name != "combined":
        cloned.min_reuters_items = 0
    return cloned


def provider_filtered_items(items: list[dict[str, Any]], provider_name: str) -> list[dict[str, Any]]:
    return [item for item in items if source_provider_name(item) == provider_name]


def select_items_for_feed(items: list[dict[str, Any]], args: argparse.Namespace, provider_name: str) -> list[dict[str, Any]]:
    feed_args = clone_args_for_feed(args, provider_name)
    return select_final_items(items, feed_args)


def translated_item_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item_identity(item)
        if key:
            mapped[key] = item
    return mapped


def unique_items_for_translation(feed_items: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for key in TAB_FEED_KEYS:
        for item in feed_items.get(key, []):
            identity = item_identity(item)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            unique.append(item)
    return unique


def apply_translations_to_feed(
    items: list[dict[str, Any]],
    translated_by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        translated = translated_by_key.get(item_identity(item))
        out.append(translated if translated is not None else item)
    return out


def source_meta_for_provider(result_meta: dict[str, Any], provider_name: str) -> dict[str, Any]:
    for entry in result_meta.get("sources") or []:
        if not isinstance(entry, dict):
            continue
        if clean_text((entry or {}).get("provider")).lower() == provider_name:
            return dict(entry or {})
    return {}


def build_output_meta(
    result_meta: dict[str, Any],
    *,
    feed_key: str,
    candidate_items: list[dict[str, Any]],
    filtered_items: list[dict[str, Any]],
    final_items: list[dict[str, Any]],
    args: argparse.Namespace,
    billed_chars: int = 0,
) -> dict[str, Any]:
    result_provider = clean_text(result_meta.get("provider")).lower()
    if feed_key == "combined" or result_provider == feed_key:
        meta = dict(result_meta)
    else:
        meta = {
            "provider": feed_key,
            "generated_from": "combined",
            "source": source_meta_for_provider(result_meta, feed_key),
        }
    statuses = result_meta.get("source_statuses") or {}
    if feed_key != "combined":
        statuses = {key: value for key, value in statuses.items() if key == feed_key or value.get("group") == feed_key}
    if feed_key != "combined" and not statuses:
        statuses = {feed_key: {"status": "disabled", "label": feed_key}}
    meta["source_statuses"] = statuses
    if statuses:
        meta["errors"] = [f"{name}: {value['error']}" for name, value in statuses.items() if value.get("error")]
    meta["staleAfterSeconds"] = max(180, args.watch * 3)

    meta["feed_key"] = feed_key
    meta["candidate_source_counts"] = count_source_providers(candidate_items)
    meta["filtered_source_counts"] = count_source_providers(filtered_items)
    meta["final_source_counts"] = count_source_providers(final_items)
    meta["candidate_time_precision_counts"] = count_time_precision(candidate_items)
    meta["filtered_time_precision_counts"] = count_time_precision(filtered_items)
    meta["final_time_precision_counts"] = count_time_precision(final_items)
    meta["candidate_count"] = len(candidate_items)
    meta["filtered_count"] = len(filtered_items)
    meta["final_count"] = len(final_items)
    meta["rank_mode"] = clean_text(getattr(args, "rank_mode", "fresh") or "fresh")
    if feed_key == "official":
        meta["articleStaleAfterSeconds"] = args.official_lookback_hours * 3600
    if feed_key == "combined":
        meta["min_reuters_items"] = max(int(getattr(args, "min_reuters_items", 0) or 0), 0)
    meta["translate_provider"] = clean_text(args.translate_provider or "none")
    if billed_chars:
        meta["deepl_billed_characters"] = billed_chars
    return meta


def debug_print_items(label: str, items: list[dict[str, Any]], args: argparse.Namespace) -> None:
    print(f"[news_relay][debug] final feed={label} count={len(items)}")
    for index, item in enumerate(items[:5], start=1):
        score = item.get("_score")
        age_note = item_age_label(item)
        time_note = clean_text(item.get("publishedAt") or item.get("publishedDate") or "")
        detail_note = f" time={time_note or '-'} precision={item_time_precision_label(item)}"
        if age_note:
            detail_note += f" age={age_note}"
        print(f"[news_relay][debug] {label} {index}. score={score}{detail_note} {item.get('title')} | {item.get('source')}")
        if clean_text(args.translate_provider).lower() == "deepl":
            print(f"[news_relay][debug]    ko= {item.get('titleKo')}")


def write_single_feed(result: ProviderResult, candidate_items: list[dict[str, Any]], filtered_items: list[dict[str, Any]], args: argparse.Namespace) -> None:
    items = select_final_items(filtered_items, args)
    translated_items, billed_chars = translate_items(items, args)
    cleaned_items = strip_internal_fields(translated_items)

    meta = build_output_meta(
        dict(result.meta),
        feed_key=clean_text(args.provider or "combined"),
        candidate_items=candidate_items,
        filtered_items=filtered_items,
        final_items=items,
        args=args,
        billed_chars=billed_chars,
    )

    write_payload(resolve_output_path(args.out), cleaned_items, meta=meta)
    print(f"[news_relay] wrote {len(cleaned_items)} items -> {args.out}")

    if args.provider == "combined":
        candidate_counts = meta.get("candidate_source_counts", {})
        filtered_counts = meta.get("filtered_source_counts", {})
        final_counts = meta.get("final_source_counts", {})
        final_precision = meta.get("final_time_precision_counts", {})
        print(
            "[news_relay] sources "
            f"candidates marketaux={candidate_counts.get('marketaux', 0)} reuters={candidate_counts.get('reuters', 0)}; "
            f"eligible marketaux={filtered_counts.get('marketaux', 0)} reuters={filtered_counts.get('reuters', 0)}; "
            f"final marketaux={final_counts.get('marketaux', 0)} reuters={final_counts.get('reuters', 0)}; "
            f"time exact={final_precision.get('exact', 0)} date-only={final_precision.get('date-only', 0)} missing={final_precision.get('missing', 0)}"
        )

    for error in (meta.get("errors") or [])[:3]:
        print(f"[news_relay][warn] source error: {error}", file=sys.stderr)

    if args.debug:
        print(f"[news_relay][debug] translate_provider= {clean_text(args.translate_provider or 'none')}")
        print(f"[news_relay][debug] rank_mode= {clean_text(getattr(args, 'rank_mode', 'fresh'))} marketaux_sort= {clean_text(getattr(args, 'sort', '')) or '(none)'}")
        if args.file_marketaux_api_token_path:
            print(f"[news_relay][debug] marketaux_key_file= {args.file_marketaux_api_token_path}")
        if args.file_deepl_api_key_path and clean_text(args.translate_provider).lower() == "deepl":
            print(f"[news_relay][debug] deepl_key_file= {args.file_deepl_api_key_path}")
        if billed_chars:
            print(f"[news_relay][debug] deepl billed_characters= {billed_chars}")
        debug_print_items(clean_text(args.provider or "feed"), translated_items, args)


def write_tab_feeds(result: ProviderResult, raw_items: list[dict[str, Any]], args: argparse.Namespace) -> None:
    cutoff = parse_iso_datetime(args.published_after)
    # Keep the official archive in its own tab without passing old releases off as current news.
    combined_candidates = dedupe_items([item for item in raw_items if cutoff is None or ReutersProvider.is_recent_enough(item, cutoff)])
    feed_candidates = {key: dedupe_items(provider_filtered_items(raw_items, key)) for key in TAB_FEED_KEYS if key != "combined"}
    feed_candidates["combined"] = combined_candidates
    feed_filtered = {key: apply_broad_market_filter(items, clone_args_for_feed(args, key)) for key, items in feed_candidates.items() if key != "combined"}
    feed_filtered["combined"] = dedupe_items([item for items in feed_filtered.values() for item in items
                                             if cutoff is None or ReutersProvider.is_recent_enough(item, cutoff)])
    feed_selected = {
        key: select_items_for_feed(feed_filtered[key], args, key)
        for key in TAB_FEED_KEYS
    }

    unique_selected = unique_items_for_translation(feed_selected)
    translated_unique, billed_chars = translate_items(unique_selected, args)
    translated_by_key = translated_item_map(translated_unique)

    feed_translated = {
        key: apply_translations_to_feed(feed_selected[key], translated_by_key)
        for key in TAB_FEED_KEYS
    }

    outputs = {
        "combined": args.out,
        "reuters": args.reuters_out,
        "marketaux": args.marketaux_out,
        "official": args.official_out,
        "rss": args.rss_out,
        "benzinga": args.benzinga_out,
    }

    for key in TAB_FEED_KEYS:
        meta = build_output_meta(
            dict(result.meta),
            feed_key=key,
            candidate_items=feed_candidates[key],
            filtered_items=feed_filtered[key],
            final_items=feed_selected[key],
            args=args,
            billed_chars=billed_chars if key == "combined" else 0,
        )
        write_payload(resolve_output_path(outputs[key]), strip_internal_fields(feed_translated[key]), meta=meta)

    report = {
        "counts": {key: len(value) for key, value in feed_translated.items()},
        "sources": {key: {"status": value.get("status"), "interval": value.get("effectiveIntervalSeconds"),
                          "daily_limit": (value.get("usage") or {}).get("daily_limit")}
                    for key, value in result.meta.get("source_statuses", {}).items()},
    }
    signature = json.dumps(report, sort_keys=True)
    if signature != getattr(args, "_last_report", None):
        print("[news_relay] " + signature)
        args._last_report = signature

    if args.debug:
        print(f"[news_relay][debug] translate_provider= {clean_text(args.translate_provider or 'none')}")
        print(f"[news_relay][debug] rank_mode= {clean_text(getattr(args, 'rank_mode', 'fresh'))} marketaux_sort= {clean_text(getattr(args, 'sort', '')) or '(none)'}")
        if args.file_marketaux_api_token_path:
            print(f"[news_relay][debug] marketaux_key_file= {args.file_marketaux_api_token_path}")
        if args.file_deepl_api_key_path and clean_text(args.translate_provider).lower() == "deepl":
            print(f"[news_relay][debug] deepl_key_file= {args.file_deepl_api_key_path}")
        if billed_chars:
            print(f"[news_relay][debug] deepl billed_characters= {billed_chars}")
        for key in TAB_FEED_KEYS:
            debug_print_items(key, feed_translated[key], args)


def refresh_once(provider: Provider, args: argparse.Namespace) -> None:
    if not args.explicit_published_after:
        args.published_after = (now_utc() - timedelta(hours=max(args.lookback_hours, 1))).strftime("%Y-%m-%dT%H:%M")
    result = provider.fetch(args)
    raw_items = [item for item in result.items if not parse_iso_datetime(item.get("publishedAt"))
                 or parse_iso_datetime(item["publishedAt"]) <= now_utc() + timedelta(minutes=5)]

    if getattr(args, "tab_feeds", False) and args.provider == "combined":
        write_tab_feeds(result, raw_items, args)
        return

    cutoff = parse_iso_datetime(args.published_after)
    raw_items = [item for item in raw_items if cutoff is None or ReutersProvider.is_recent_enough(item, cutoff)]
    candidate_items = dedupe_items(raw_items)
    filtered_items = apply_provider_aware_market_filter(candidate_items, args)
    write_single_feed(result, candidate_items, filtered_items, args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="marketaux/Reuters 뉴스를 오버레이용 JSON으로 정규화하고, 필요시 DeepL로 한국어 헤드라인을 생성합니다. API 키는 keys/*.txt 에서 자동으로 읽을 수 있습니다.")

    parser.add_argument("--provider", choices=["marketaux", "reuters", "massive", "combined"], default="combined")
    parser.add_argument("--out", default="data/news.live.json")
    parser.add_argument("--tab-feeds", action="store_true", help="combined 모드에서 통합/Reuters/Marketaux 탭용 JSON을 한 번에 생성합니다")
    parser.add_argument("--reuters-out", default=DEFAULT_REUTERS_OUT, help="--tab-feeds 사용 시 Reuters 탭 JSON 출력 경로")
    parser.add_argument("--marketaux-out", default=DEFAULT_MARKETAUX_OUT, help="--tab-feeds 사용 시 Marketaux 탭 JSON 출력 경로")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--fetch-limit", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--request-retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=1.5)
    parser.add_argument("--watch", type=int, default=0)
    parser.add_argument("--marketaux-interval", type=int, default=15)
    parser.add_argument("--no-marketaux", action="store_true", help="통합 수집에서 Marketaux를 제외")
    parser.add_argument("--massive-key-file", default="")
    parser.add_argument("--massive-interval", type=int, default=10)
    parser.add_argument("--massive-fetch-limit", type=int, default=200, help="매 조회 시 확인할 최신 Benzinga 뉴스 수 (1~1000)")
    parser.add_argument("--no-massive", action="store_true")
    parser.add_argument("--reuters-interval", type=int, default=45)
    parser.add_argument("--state-dir", default="data/.relay-cache")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--official-out", default="data/news.official.json")
    parser.add_argument("--rss-out", default="data/news.rss.json")
    parser.add_argument("--rss-interval", type=int, default=120, help="공개 RSS 조회 간격. 소스 권장 간격/TTL이 더 길면 우선 적용")
    parser.add_argument("--no-public-rss", action="store_true")
    parser.add_argument("--benzinga-out", default="data/news.benzinga.json")
    parser.add_argument("--official-interval", type=int, default=60)
    parser.add_argument("--official-lookback-hours", type=int, default=168)
    parser.add_argument("--no-official-feeds", action="store_true")
    parser.add_argument("--no-benzinga", action="store_true")
    parser.add_argument("--benzinga-key-file", default="")

    parser.add_argument("--api-key", default="")
    parser.add_argument("--key-dir", default="keys")
    parser.add_argument("--marketaux-key-file", default="")
    parser.add_argument("--provider-label", default="")
    parser.add_argument("--category-label", default="")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--query", default="")
    parser.add_argument("--country", default="")  # compatibility only

    parser.add_argument("--marketaux-mode", choices=["broad", "indices", "watchlist"], default="broad")
    parser.add_argument("--countries", default="", help="기본 전세계: 미국 증시에 영향을 주는 해외 사건도 수집")
    parser.add_argument("--domains", default="")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--must-have-entities", action="store_true", default=False)
    parser.add_argument("--no-must-have-entities", action="store_false", dest="must_have_entities")
    parser.add_argument("--group-similar", action="store_true", default=True)
    parser.add_argument("--no-group-similar", action="store_false", dest="group_similar")
    parser.add_argument("--filter-entities", action="store_true", default=True)
    parser.add_argument("--no-filter-entities", action="store_false", dest="filter_entities")
    parser.add_argument("--lookback-hours", type=int, default=48)
    parser.add_argument("--use-preferred-domains", action="store_true")
    parser.add_argument("--sort", default="")
    parser.add_argument("--rank-mode", choices=["fresh", "balanced", "score"], default="fresh", help="최종 카드 선정 방식: fresh=최신순, balanced=점수+최신성, score=기존 점수순")
    parser.add_argument("--min-overlay-score", type=float, default=6.5)

    parser.add_argument("--reuters-feed", action="append", default=[], help="Reuters RSS/Atom/news sitemap URL 또는 로컬 XML 파일. 여러 번 지정 가능")
    parser.add_argument("--no-reuters-default-feed", action="store_true", help="--reuters-feed 가 없을 때 기본 Reuters news sitemap을 사용하지 않습니다")
    parser.add_argument("--reuters-fetch-limit", type=int, default=120, help="Reuters 원시 후보 기사 최대 개수")
    parser.add_argument("--reuters-sitemap-limit", type=int, default=3, help="sitemap index에서 읽을 최신 하위 sitemap 개수")
    parser.add_argument("--reuters-request-delay", type=float, default=0.2, help="Reuters 하위 sitemap 요청 사이 대기 시간(초)")
    parser.add_argument("--reuters-source-label", default="Reuters")
    parser.add_argument("--reuters-lang", default="", help="Reuters sitemap/RSS/Atom 언어 필터입니다. 기본값은 --lang 값입니다. 예: en 또는 en,ko")
    parser.add_argument("--min-reuters-items", type=int, default=0, help="combined 모드에서 Reuters 후보를 최종 카드에 최소 몇 개 강제로 포함할지 지정합니다. 기본 0은 순수 최신순입니다")
    parser.add_argument("--allow-undated-reuters", action="store_true", help="Reuters 항목에 publication_date, lastmod, URL 날짜가 모두 없더라도 포함합니다. 기본값은 제외입니다.")

    parser.add_argument("--translate-provider", choices=["none", "deepl"], default="none")
    parser.add_argument("--translate-target", default="ko")
    parser.add_argument("--translate-source", default="en")
    parser.add_argument("--translate-cache", default="data/translate_cache.ko.json")
    parser.add_argument("--translate-context-chars", type=int, default=1500)
    parser.add_argument("--translate-terms-file", default="tools/ko_terms.sample.json")
    parser.add_argument("--translate-instructions", default="")
    parser.add_argument("--translate-api-key", default="")  # generic alias

    parser.add_argument("--deepl-api-key", default="")
    parser.add_argument("--deepl-key-file", default="")
    parser.add_argument("--deepl-api-url", default="")
    parser.add_argument("--deepl-glossary-id", default="")

    parser.add_argument("--aws-region", default="", help=argparse.SUPPRESS)  # backward compatibility

    args = parser.parse_args()
    if min(args.marketaux_interval, args.massive_interval, args.reuters_interval, args.official_interval, args.rss_interval, args.official_lookback_hours, args.limit, args.timeout) <= 0 or args.watch < 0:
        parser.error("요청 간격과 --limit 은 양수여야 합니다")
    if not 1 <= args.massive_fetch_limit <= 1000:
        parser.error("--massive-fetch-limit 은 1~1000 이어야 합니다")
    args.explicit_published_after = args.published_after

    load_env_file(PROJECT_ROOT / ".env.local")
    load_env_file(PROJECT_ROOT / ".env")

    args.env_marketaux_api_token = os.getenv("MARKETAUX_API_TOKEN", "")
    args.env_deepl_api_key = os.getenv("DEEPL_API_KEY", "")

    key_dir = resolve_path(args.key_dir)
    marketaux_candidates: list[Path] = []
    if clean_text(args.marketaux_key_file):
        marketaux_candidates.append(resolve_path(args.marketaux_key_file))
    marketaux_candidates.extend(DEFAULT_MARKETAUX_KEY_FILES)
    if key_dir != DEFAULT_KEYS_DIR:
        marketaux_candidates.extend([key_dir / "marketaux_token.txt", key_dir / "marketaux.txt"])

    deepl_candidates: list[Path] = []
    if clean_text(args.deepl_key_file):
        deepl_candidates.append(resolve_path(args.deepl_key_file))
    deepl_candidates.extend(DEFAULT_DEEPL_KEY_FILES)
    if key_dir != DEFAULT_KEYS_DIR:
        deepl_candidates.extend([key_dir / "deepl_api_key.txt", key_dir / "deepl_key.txt", key_dir / "deepl.txt"])

    args.file_marketaux_api_token, args.file_marketaux_api_token_path = first_existing_secret(marketaux_candidates)
    args.file_deepl_api_key, args.file_deepl_api_key_path = first_existing_secret(deepl_candidates)
    benzinga_candidates = [resolve_path(args.benzinga_key_file)] if args.benzinga_key_file else []
    benzinga_candidates.extend([key_dir / "benzinga_token.txt", key_dir / "benzinga.txt"])
    args.benzinga_token = os.getenv("BENZINGA_API_KEY", "") or first_existing_secret(benzinga_candidates)[0]
    massive_candidates = [key_dir / "massive_api_key.txt", key_dir / "massive_token.txt"]
    if args.massive_key_file:
        args.massive_token = load_secret_from_file(resolve_path(args.massive_key_file))[0]
    else:
        args.massive_token = os.getenv("MASSIVE_API_KEY", "") or first_existing_secret(massive_candidates)[0]

    if args.use_preferred_domains and not args.domains:
        args.domains = DEFAULT_PREFERRED_DOMAINS
    if not args.published_after:
        args.published_after = (now_utc() - timedelta(hours=max(args.lookback_hours, 1))).strftime("%Y-%m-%dT%H:%M")
    if not clean_text(args.reuters_lang):
        args.reuters_lang = clean_text(args.lang)
    if not args.sort and (args.query or args.marketaux_mode == "broad"):
        # With a search query Marketaux otherwise tends to rank by relevance;
        # for a live overlay we prefer the newest matching market headlines.
        args.sort = "published_on"

    return args


def build_provider(args: argparse.Namespace) -> Provider:
    provider_name = clean_text(args.provider).lower()
    if provider_name == "marketaux":
        return ManagedProvider("marketaux", MarketauxProvider())
    if provider_name == "reuters":
        return ManagedProvider("reuters", ReutersProvider())
    if provider_name == "massive":
        return ManagedProvider("massive", MassiveNewsProvider())
    if provider_name == "combined":
        return CombinedProvider(args)
    raise RelayError(f"지원하지 않는 provider 입니다: {args.provider}")


def main() -> int:
    args = parse_args()
    provider = build_provider(args)

    try:
        if args.watch > 0:
            if args.translate_provider != "none":
                args.live_translator = BackgroundTranslator(args)
            while True:
                started = time.monotonic()
                try:
                    refresh_once(provider, args)
                except Exception as exc:  # noqa: BLE001
                    print(f"[news_relay] refresh failed: {safe_error(exc, args)}", file=sys.stderr)
                time.sleep(max(0.05, args.watch - (time.monotonic() - started)))
        else:
            refresh_once(provider, args)
            saved = read_json(resolve_output_path(args.out), {})
            statuses = (saved.get("meta") or {}).get("source_statuses") or {}
            if statuses and all(value.get("status") == "error" for value in statuses.values()):
                return 1
    except KeyboardInterrupt:
        print("\n[news_relay] stopped")
        return 0
    finally:
        if hasattr(provider, "close"):
            provider.close()
        if getattr(args, "live_translator", None) is not None:
            args.live_translator.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
