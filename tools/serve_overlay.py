#!/usr/bin/env python3
"""Serve only the widget assets and explicitly configured public feeds."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
try:
    from .translation_quality import POLICY
except ImportError:
    from translation_quality import POLICY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "styles.css", "js/config.js", "js/translation-quality.js", "js/market-impact.js", "js/news-source.js", "js/news-context.js", "js/feed-health.js", "js/app.js")


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
                    stat = target.stat() if target.resolve() == target.absolute() else None
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

    def send_head(self):
        route = unquote(urlsplit(self.path).path)
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
        if target is None or target.resolve() != target.absolute() or not target.is_file():
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


def build_server(host: str, port: int, root: Path, feeds: dict[str, Path], runtime: dict) -> ThreadingHTTPServer:
    handler = partial(WidgetHandler, root=root, feeds=feeds, runtime={**runtime, "translationPolicy": POLICY})
    return ThreadingHTTPServer((host, port), handler)


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
    args = parser.parse_args()
    feeds = {}
    for route, value in [("news.live.json", args.out), ("news.reuters.json", args.reuters_out), ("news.marketaux.json", args.marketaux_out),
                         ("news.official.json", args.official_out), ("news.benzinga.json", args.benzinga_out),
                         ("news.rss.json", args.rss_out)]:
        path = Path(value)
        path = path if path.is_absolute() else PROJECT_ROOT / path
        if path.suffix != ".json":
            parser.error("피드 출력 경로는 .json 파일이어야 합니다")
        feeds["/data/" + route] = path.absolute()
    server = build_server(args.host, args.port, PROJECT_ROOT, feeds,
                          {"maxItems": max(1, args.limit), "tabsEnabled": not args.no_tabs})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
