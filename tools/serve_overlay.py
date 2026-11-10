#!/usr/bin/env python3
"""Serve only the widget assets and explicitly configured public feeds."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
try:
    from .translation_quality import POLICY
    from .korean_links import KoreanArticleIndex
    from .article_summary import ArticleSummaryService
    from .app_paths import DATA_ROOT, RESOURCE_ROOT
except ImportError:
    from translation_quality import POLICY
    from korean_links import KoreanArticleIndex
    from article_summary import ArticleSummaryService
    from app_paths import DATA_ROOT, RESOURCE_ROOT

PROJECT_ROOT = RESOURCE_ROOT
ASSETS = ("index.html", "styles.css", "js/config.js", "js/appearance.js", "js/market-ticker.js", "js/translation-quality.js", "js/market-impact.js", "js/article-links.js", "js/news-source.js", "js/news-context.js", "js/feed-health.js", "js/app.js")
ASSETS += ("js/article-reader.js",)


def regular_file(path: Path) -> bool:
    # Windows 8.3 aliases can differ from resolve() without being symlinks.
    # Inspect actual links/junctions instead of comparing their path spellings.
    absolute = path.absolute()
    return absolute.is_file() and not any(
        part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
        for part in (absolute, *absolute.parents)
    )


class WidgetHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, root: Path, feeds: dict[str, Path], runtime: dict, **kwargs):
        self.root = root.resolve()
        self.routes = {"/" + name: self.root / name for name in ASSETS}
        self.routes["/"] = self.root / "index.html"
        self.routes.update(feeds)
        self.feeds = feeds
        self.runtime = runtime
        super().__init__(*args, directory=str(self.root), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == '/api/article-summary':
            job = parse_qs(urlsplit(self.path).query).get('job', [''])[0]
            return self.send_json(self.server.article_summary.snapshot(job))
        if urlsplit(self.path).path != "/events":
            return super().do_GET()
        route = parse_qs(urlsplit(self.path).query).get("feed", ["/data/news.live.json"])[0]
        target = self.feeds.get(route)
        if target is None:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.connection.settimeout(5)
        previous = None
        heartbeat = 0
        try:
            while True:
                try:
                    stat = target.stat() if regular_file(target) else None
                    version = (stat.st_mtime_ns, stat.st_size) if stat else None
                except OSError:
                    version = None
                if version != previous:
                    self.wfile.write(b"event: feed\ndata: changed\n\n")
                    previous = version
                elif heartbeat % 15 == 0:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                heartbeat += 1
                time.sleep(1)
        except (OSError, TimeoutError):
            pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlsplit(self.path).path != '/api/article-summary':
            return self.send_error(404, 'Not found')
        # A JSON POST from this widget is required before any paid API work starts.
        # Cross-site forms/GETs and arbitrary text/URLs cannot trigger translation.
        origin = self.headers.get('Origin')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json' or
                self.headers.get('Sec-Fetch-Site') == 'cross-site' or
                (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
            return self.send_json({'status': 'error', 'message': '위젯에서 기사를 열어 주세요.'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, OSError):
            return self.send_json({'status': 'error', 'message': '기사 요청을 확인할 수 없습니다.'}, 400)
        self.send_json(self.server.article_summary.start(payload.get('id'), payload.get('url', '')))

    def send_head(self):
        route = unquote(urlsplit(self.path).path)
        if route == '/api/benzinga-korean':
            import io
            body = json.dumps(self.server.korean_index.snapshot(), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        if route == "/widget-runtime.js":
            import io
            body = ("window.NEWS_WIDGET_RUNTIME = " + json.dumps(self.runtime, ensure_ascii=False) + ";").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        target = self.routes.get(route)
        # Exact routes only; reject symlinks even when they point inside the root.
        if target is None or not regular_file(target):
            self.send_error(404, "Not found")
            return None
        try:
            handle = target.open("rb")
        except OSError:
            self.send_error(404, "Not found")
            return None
        import os
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(target)))
        self.send_header("Content-Length", str(os.fstat(handle.fileno()).st_size))
        self.end_headers()
        return handle

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


class WidgetServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        super().server_close()
        if hasattr(self, 'korean_index'):
            self.korean_index.close()
        if hasattr(self, 'article_summary'):
            self.article_summary.close()


def build_server(host: str, port: int, root: Path, feeds: dict[str, Path], runtime: dict,
                 summary_options: list[str] | None = None, *, cache_root: Path | None = None) -> ThreadingHTTPServer:
    handler = partial(WidgetHandler, root=root, feeds=feeds, runtime={**runtime, "translationPolicy": POLICY})
    server = WidgetServer((host, port), handler)
    cache_root = cache_root or root
    server.korean_index = KoreanArticleIndex(cache_root / 'data/.relay-cache/benzinga-korean-links.json')
    server.article_summary = ArticleSummaryService(cache_root / 'data/.relay-cache/article-summaries.ko.json', feeds, summary_options)
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--out", default="data/news.live.json")
    parser.add_argument("--reuters-out", default="data/news.reuters.json")
    parser.add_argument("--marketaux-out", default="data/news.marketaux.json")
    parser.add_argument("--official-out", default="data/news.official.json")
    parser.add_argument("--rss-out", default="data/news.rss.json")
    parser.add_argument("--benzinga-out", default="data/news.benzinga.json")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-tabs", action="store_true")
    parser.add_argument("--translate-provider", choices=['none', 'deepl'], default='none')
    parser.add_argument("--translate-source", default='en')
    parser.add_argument("--translate-terms-file", default='tools/ko_terms.sample.json')
    parser.add_argument("--massive-key-file", default='')
    args = parser.parse_args()
    feeds = {}
    for route, value in [("news.live.json", args.out), ("news.reuters.json", args.reuters_out), ("news.marketaux.json", args.marketaux_out),
                         ("news.official.json", args.official_out), ("news.benzinga.json", args.benzinga_out),
                         ("news.rss.json", args.rss_out)]:
        path = Path(value)
        path = path if path.is_absolute() else DATA_ROOT / path
        if path.suffix != ".json":
            parser.error("피드 출력 경로는 .json 파일이어야 합니다")
        feeds["/data/" + route] = path.absolute()
    server = build_server(args.host, args.port, PROJECT_ROOT, feeds,
                          {"maxItems": max(1, args.limit), "tabsEnabled": not args.no_tabs},
                          ['--translate-provider', args.translate_provider, '--translate-source', args.translate_source,
                           '--translate-terms-file', args.translate_terms_file, '--massive-key-file', args.massive_key_file],
                          cache_root=DATA_ROOT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
