#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

USER_AGENT = "NewsOverlayWidget/0.1"
HTTP_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def child_text(element: ET.Element | None, local_name: str) -> str:
    if element is None:
        return ""

    for child in list(element):
        if strip_ns(child.tag) == local_name:
            return clean_text("".join(child.itertext()))

    return ""


def children(element: ET.Element | None, local_name: str) -> Iterable[ET.Element]:
    if element is None:
        return []

    return [child for child in list(element) if strip_ns(child.tag) == local_name]


def parse_datetime(value: str) -> str | None:
    raw = clean_text(value)
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().isoformat()
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(raw).astimezone().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def read_source(source: str, timeout: float) -> bytes:
    if HTTP_PATTERN.match(source):
        request = Request(
            source,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            return response.read()

    path = Path(source)
    return path.read_bytes()


def fallback_source_label(source: str, feed_title: str) -> str:
    title = clean_text(feed_title)
    if title:
        return title

    if HTTP_PATTERN.match(source):
        parsed = urlparse(source)
        return parsed.netloc or "RSS Feed"

    return Path(source).stem or "RSS Feed"


def parse_rss_item(item: ET.Element, source: str, feed_title: str) -> dict:
    title = child_text(item, "title")
    link = child_text(item, "link")
    category = child_text(item, "category")
    published_at = parse_datetime(child_text(item, "pubDate") or child_text(item, "published") or child_text(item, "date"))
    source_label = child_text(item, "source") or fallback_source_label(source, feed_title)
    guid = child_text(item, "guid") or link or title

    return {
        "id": clean_text(guid),
        "title": title,
        "publishedAt": published_at,
        "source": clean_text(source_label),
        "category": category,
        "url": link,
    }


def parse_atom_entry(entry: ET.Element, source: str, feed_title: str) -> dict:
    title = child_text(entry, "title")
    published_at = parse_datetime(child_text(entry, "updated") or child_text(entry, "published"))
    category = ""
    term = ""

    for category_el in children(entry, "category"):
        term = clean_text(category_el.attrib.get("term"))
        if term:
            break

    link = ""
    for link_el in children(entry, "link"):
        rel = clean_text(link_el.attrib.get("rel"))
        href = clean_text(link_el.attrib.get("href"))
        if href and (not rel or rel == "alternate"):
            link = href
            break

    if not link:
        link = clean_text(child_text(entry, "id"))

    source_label = fallback_source_label(source, feed_title)
    category = term
    guid = clean_text(child_text(entry, "id")) or link or title

    return {
        "id": guid,
        "title": title,
        "publishedAt": published_at,
        "source": source_label,
        "category": category,
        "url": link,
    }


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    root_tag = strip_ns(root.tag)

    if root_tag == "rss":
        channel = root.find("channel")
        feed_title = child_text(channel, "title")
        return [parse_rss_item(item, source, feed_title) for item in children(channel, "item")]

    if root_tag == "feed":
        feed_title = child_text(root, "title")
        return [parse_atom_entry(entry, source, feed_title) for entry in children(root, "entry")]

    channel = root.find("channel")
    if channel is not None:
        feed_title = child_text(channel, "title")
        return [parse_rss_item(item, source, feed_title) for item in children(channel, "item")]

    raise ValueError("지원하지 않는 RSS/Atom 형식입니다.")


def sort_key(item: dict) -> tuple[int, str]:
    published_at = item.get("publishedAt")
    if not published_at:
        return (1, "")
    return (0, published_at)


def write_payload(items: list[dict], out_path: Path) -> None:
    payload = {
        "updatedAt": now_iso(),
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_once(rss_list: list[str], out_path: Path, limit: int, timeout: float) -> None:
    combined: list[dict] = []
    errors: list[str] = []

    for source in rss_list:
        try:
            xml_bytes = read_source(source, timeout=timeout)
            feed_items = parse_feed(xml_bytes, source)
            combined.extend([item for item in feed_items if clean_text(item.get("title"))])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {exc}")

    combined.sort(key=sort_key)
    combined.reverse()
    limited_items = combined[:limit]
    write_payload(limited_items, out_path)

    if errors:
        print("[rss_to_json] warnings:", file=sys.stderr)
        for message in errors:
            print(f"  - {message}", file=sys.stderr)

    print(f"[rss_to_json] wrote {len(limited_items)} items -> {out_path}")

    if errors and not limited_items:
        raise RuntimeError("모든 피드 갱신에 실패했습니다.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RSS/Atom 피드를 뉴스 오버레이용 JSON으로 변환합니다.")
    parser.add_argument("--rss", action="append", required=True, help="RSS/Atom URL 또는 로컬 XML 파일 경로. 여러 번 지정 가능")
    parser.add_argument("--out", default="data/news.live.json", help="출력 JSON 경로")
    parser.add_argument("--limit", type=int, default=5, help="최대 뉴스 개수")
    parser.add_argument("--timeout", type=float, default=8.0, help="피드 요청 타임아웃(초)")
    parser.add_argument("--watch", type=int, default=0, help="0이면 1회 실행, 양수면 N초마다 반복 갱신")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_path = Path(args.out)

    try:
        if args.watch > 0:
            while True:
                try:
                    refresh_once(args.rss, out_path, args.limit, args.timeout)
                except Exception as exc:  # noqa: BLE001
                    print(f"[rss_to_json] refresh failed: {exc}", file=sys.stderr)
                time.sleep(args.watch)
        else:
            refresh_once(args.rss, out_path, args.limit, args.timeout)
    except KeyboardInterrupt:
        print("\n[rss_to_json] stopped")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
