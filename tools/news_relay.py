#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = "NewsOverlayWidget/0.8"
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
    'pmi | "retail sales" | "u.s. stocks" | "wall street" | nasdaq | "s&p 500" | dow'
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

INDEX_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM", "VIX", "TLT", "USO", "GLD"}
BROAD_ENTITY_TYPES = {"country", "currency", "commodity", "etf", "index", "macro"}

DEFAULT_DEEPL_INSTRUCTIONS = [
    "Translate into concise, natural Korean financial-news headline style.",
    "Prefer familiar Korean market terms such as 연준, 미 국채금리, 월가, S&P 500, 나스닥.",
    "Avoid honorifics, keep the meaning faithful, and make the headline readable in a small broadcast overlay.",
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

SENTIMENT_POSITIVE_TERMS = (
    "rally", "rallies", "gain", "gains", "advance", "advances", "rebound", "rebounds", "rise", "rises",
    "record high", "record highs", "optimism", "optimistic", "boost", "beats", "beat", "ceasefire",
    "peace hopes", "progress", "rate cuts", "cooling inflation", "easing", "soft landing", "stimulus",
)

SENTIMENT_NEGATIVE_TERMS = (
    "selloff", "falls", "fall", "slump", "slumps", "drop", "drops", "decline", "declines", "crash",
    "concern", "concerns", "worry", "worries", "fear", "fears", "hawkish", "hot inflation",
    "sticky inflation", "war", "tariff", "tariffs", "sanction", "sanctions", "volatility", "uncertainty",
    "recession", "teeter", "tension", "tensions",
)

SENTIMENT_UP_WORDS = ("rise", "rises", "rose", "up", "jump", "jumps", "surge", "surges", "spike", "spikes")
SENTIMENT_DOWN_WORDS = ("fall", "falls", "fell", "down", "drop", "drops", "slip", "slips", "easing", "ease", "eases", "cool", "cools", "cooling")


class RelayError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    items: list[dict[str, Any]]
    meta: dict[str, Any]


@dataclass
class HttpResponse:
    payload: dict[str, Any]
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

        request_url = f"{self.BASE}?{urlencode({k: v for k, v in params.items() if v != ''})}"
        if args.debug:
            print(f"[news_relay][debug] marketaux request_url= {request_url}")

        response = request_json(
            request_url,
            timeout=args.timeout,
            retries=args.request_retries,
            retry_backoff=args.retry_backoff,
            debug=args.debug,
        )
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
            },
        )

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
        }


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def as_bool_param(value: bool) -> str:
    return "true" if value else "false"


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
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                detail_payload = json.loads(body)
                detail = json.dumps(detail_payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

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
        except json.JSONDecodeError as exc:
            raise RelayError(f"JSON 파싱 실패: {exc}") from exc

    assert last_error is not None
    raise last_error


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
    }
    for keyword, weight in weights.items():
        if keyword in text:
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

    equity_symbols = [symbol for symbol in symbols if symbol and symbol not in INDEX_SYMBOLS]
    if len(equity_symbols) == 1 and score < 10:
        score -= 4

    published_at = parse_iso_datetime(article.get("published_at"))
    if published_at is not None:
        age_hours = max((now_utc() - published_at).total_seconds() / 3600, 0)
        score += max(0.0, 6 - age_hours * 0.2)

    return round(score, 3)



def classify_market_sentiment(title: str, snippet: str, entities: list[dict[str, Any]]) -> str:
    text = f"{clean_text(title)} {clean_text(snippet)}".lower()
    entity_names = " ".join(clean_text((entity or {}).get("name")) for entity in (entities or []))
    if entity_names:
        text = f"{text} {entity_names.lower()}"

    score = 0
    for term in SENTIMENT_POSITIVE_TERMS:
        if term in text:
            score += 1
    for term in SENTIMENT_NEGATIVE_TERMS:
        if term in text:
            score -= 1

    yield_context = any(term in text for term in ("yield", "yields", "treasury", "bond"))
    oil_context = any(term in text for term in ("oil", "crude", "wti", "brent", "opec"))
    inflation_context = any(term in text for term in ("inflation", "cpi", "pce", "ppi"))

    if yield_context:
        if any(term in text for term in SENTIMENT_DOWN_WORDS):
            score += 2
        if any(term in text for term in SENTIMENT_UP_WORDS):
            score -= 2
    if oil_context:
        if any(term in text for term in SENTIMENT_DOWN_WORDS):
            score += 2
        if any(term in text for term in SENTIMENT_UP_WORDS):
            score -= 2
    if inflation_context:
        if any(term in text for term in ("cool", "cooling", "ease", "easing", "soft", "slower", "slowdown")):
            score += 2
        if any(term in text for term in ("hot", "sticky", "surge", "reaccelerat", "faster", "higher")):
            score -= 2

    if score >= 2:
        return "positive"
    if score <= -2:
        return "negative"
    return "neutral"

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
    if args.provider != "marketaux" or args.marketaux_mode == "watchlist":
        return items
    filtered = [item for item in items if float(item.get("_score") or 0) >= args.min_overlay_score]
    return filtered or items


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[float, float]:
        score = float(item.get("_score") or 0)
        published = parse_iso_datetime(item.get("publishedAt") or item.get("published_at"))
        timestamp = published.timestamp() if published else 0.0
        return (score, timestamp)

    return sorted(items, key=key, reverse=True)


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


def save_translation_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


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


def translate_items(items: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    translator = build_translator(args)
    if isinstance(translator, NullTranslator):
        return items, 0

    replacements = load_replacements(args.translate_terms_file)
    cache_path, cache = load_translation_cache(args.translate_cache)
    total_billed = 0
    translated_items: list[dict[str, Any]] = []

    for item in items:
        title = clean_text(item.get("title"))
        cache_key = translation_cache_key(item, args)
        translated_text = ""
        from_cache = False

        if cache_key in cache:
            translated_text = clean_text(cache.get(cache_key))
            from_cache = True
        else:
            try:
                result = translator.translate_item(item, args)
                translated_text = result.text
                total_billed += result.billed_characters
                cache[cache_key] = translated_text
                from_cache = result.from_cache
            except Exception as exc:  # noqa: BLE001
                if args.debug:
                    print(f"[news_relay][warn] translate failed: {exc}", file=sys.stderr)
                translated_text = title

        translated_text = apply_replacements(translated_text, replacements)
        item_copy = dict(item)
        item_copy["titleKo"] = translated_text or title
        item_copy["_translated_from_cache"] = from_cache
        translated_items.append(item_copy)

    save_translation_cache(cache_path, cache)
    return translated_items, total_billed


def strip_internal_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in items]


def write_payload(out_path: Path, items: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"updatedAt": now_iso(), "items": items}
    if meta:
        payload["meta"] = meta
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_once(provider: Provider, args: argparse.Namespace) -> None:
    result = provider.fetch(args)
    items = dedupe_items(result.items)
    items = apply_broad_market_filter(items, args)
    items = sort_items(items)
    items = items[: args.limit]

    translated_items, billed_chars = translate_items(items, args)
    cleaned_items = strip_internal_fields(translated_items)

    meta = dict(result.meta)
    meta["translate_provider"] = clean_text(args.translate_provider or "none")
    if billed_chars:
        meta["deepl_billed_characters"] = billed_chars

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    write_payload(out_path, cleaned_items, meta=meta)
    print(f"[news_relay] wrote {len(cleaned_items)} items -> {args.out}")

    if args.debug:
        print(f"[news_relay][debug] translate_provider= {clean_text(args.translate_provider or 'none')}")
        if args.file_marketaux_api_token_path:
            print(f"[news_relay][debug] marketaux_key_file= {args.file_marketaux_api_token_path}")
        if args.file_deepl_api_key_path and clean_text(args.translate_provider).lower() == "deepl":
            print(f"[news_relay][debug] deepl_key_file= {args.file_deepl_api_key_path}")
        if billed_chars:
            print(f"[news_relay][debug] deepl billed_characters= {billed_chars}")
        for index, item in enumerate(translated_items[:5], start=1):
            score = item.get("_score")
            print(f"[news_relay][debug] {index}. score={score} {item.get('title')} | {item.get('source')}")
            if clean_text(args.translate_provider).lower() == "deepl":
                print(f"[news_relay][debug]    ko= {item.get('titleKo')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="marketaux 뉴스를 오버레이용 JSON으로 정규화하고, 필요시 DeepL로 한국어 헤드라인을 생성합니다. API 키는 keys/*.txt 에서 자동으로 읽을 수 있습니다.")

    parser.add_argument("--provider", choices=["marketaux"], required=True)
    parser.add_argument("--out", default="data/news.live.json")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--fetch-limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--request-retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=1.5)
    parser.add_argument("--watch", type=int, default=0)
    parser.add_argument("--debug", action="store_true")

    parser.add_argument("--api-key", default="")
    parser.add_argument("--key-dir", default="keys")
    parser.add_argument("--marketaux-key-file", default="")
    parser.add_argument("--provider-label", default="")
    parser.add_argument("--category-label", default="")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--query", default="")
    parser.add_argument("--country", default="")  # compatibility only

    parser.add_argument("--marketaux-mode", choices=["broad", "indices", "watchlist"], default="broad")
    parser.add_argument("--countries", default="us")
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
    parser.add_argument("--min-overlay-score", type=float, default=6.5)

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

    if args.use_preferred_domains and not args.domains:
        args.domains = DEFAULT_PREFERRED_DOMAINS
    if not args.published_after:
        args.published_after = (now_utc() - timedelta(hours=max(args.lookback_hours, 1))).strftime("%Y-%m-%dT%H:%M")
    if not args.sort and (args.query or args.marketaux_mode == "broad"):
        args.sort = "relevance_score"

    return args


def main() -> int:
    args = parse_args()
    provider = MarketauxProvider()

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
