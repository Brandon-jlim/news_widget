#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    from .app_paths import DATA_ROOT, child_command
except ImportError:
    from app_paths import DATA_ROOT, child_command

PROJECT_ROOT = DATA_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="뉴스 수집과 공개 파일만 제공하는 위젯 서버를 실행합니다."
    )
    parser.add_argument("--host", default="127.0.0.1", help="로컬 서버 바인드 주소")
    parser.add_argument("--port", type=int, default=8787, help="로컬 서버 포트")
    parser.add_argument("--watch", type=int, default=1, help="로컬 수집 상태/화면 발행 주기(초), API 호출 주기와 별개")
    parser.add_argument("--marketaux-interval", type=int, default=15, help="Marketaux 목표 요청 간격(초). 실제 응답 한도가 우선합니다")
    parser.add_argument("--no-marketaux", action="store_true")
    parser.add_argument("--massive-key-file", default="")
    parser.add_argument("--massive-interval", type=int, default=10)
    parser.add_argument("--massive-fetch-limit", type=int, default=200)
    parser.add_argument("--no-massive", action="store_true")
    parser.add_argument("--reuters-interval", type=int, default=45, help="Reuters 최소 요청 간격(초)")
    parser.add_argument("--state-dir", default="data/.relay-cache", help="재시작 시에도 유지할 소스별 요청 상태")
    parser.add_argument("--limit", type=int, default=5, help="최종 카드 수")
    parser.add_argument("--fetch-limit", type=int, default=50, help="marketaux에서 가져올 원시 기사 수")
    parser.add_argument("--timeout", type=float, default=20.0, help="API 요청 타임아웃(초)")
    parser.add_argument("--request-retries", type=int, default=2, help="실패 시 재시도 횟수")
    parser.add_argument("--retry-backoff", type=float, default=1.5, help="재시도 대기 배수")
    parser.add_argument("--provider", choices=["marketaux", "reuters", "massive", "combined"], default="combined")
    parser.add_argument("--marketaux-mode", choices=["broad", "indices", "watchlist"], default="broad")
    parser.add_argument("--countries", default="")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--sort", default="")
    parser.add_argument("--rank-mode", choices=["fresh", "balanced", "score"], default="fresh", help="최종 카드 선정 방식: fresh=최신순, balanced=점수+최신성, score=기존 점수순")
    parser.add_argument("--reuters-feed", action="append", default=[], help="Reuters RSS/Atom/news sitemap URL 또는 로컬 XML 파일. 여러 번 지정 가능")
    parser.add_argument("--no-reuters-default-feed", action="store_true", help="--reuters-feed 가 없을 때 기본 Reuters news sitemap을 사용하지 않습니다")
    parser.add_argument("--reuters-fetch-limit", type=int, default=120, help="Reuters 원시 후보 기사 최대 개수")
    parser.add_argument("--reuters-sitemap-limit", type=int, default=3, help="sitemap index에서 읽을 최신 하위 sitemap 개수")
    parser.add_argument("--reuters-request-delay", type=float, default=0.2, help="Reuters 하위 sitemap 요청 사이 대기 시간(초)")
    parser.add_argument("--reuters-source-label", default="Reuters")
    parser.add_argument("--reuters-lang", default="", help="Reuters 언어 필터입니다. 기본값은 --lang 값입니다. 예: en 또는 en,ko")
    parser.add_argument("--min-reuters-items", type=int, default=0, help="combined 모드에서 Reuters 후보를 최종 카드에 최소 몇 개 강제로 포함합니다. 기본 0은 순수 최신순입니다")
    parser.add_argument("--allow-undated-reuters", action="store_true", help="Reuters 날짜 정보가 전혀 없는 항목도 포함합니다. 기본값은 제외입니다.")
    parser.add_argument("--translate-provider", choices=["none", "deepl"], default="deepl")
    parser.add_argument("--translate-target", default="ko")
    parser.add_argument("--translate-source", default="en")
    parser.add_argument("--translate-cache", default="data/translate_cache.ko.json")
    parser.add_argument("--translate-terms-file", default="tools/ko_terms.sample.json")
    parser.add_argument("--out", default="data/news.live.json", help="통합 탭/기본 JSON 출력 경로")
    parser.add_argument("--tabs", action="store_true", default=True, help="통합/Benzinga/공식 발표/기사·보도자료 등 탭용 JSON을 함께 생성합니다")
    parser.add_argument("--no-tabs", action="store_false", dest="tabs", help="탭용 보조 JSON 생성을 끕니다")
    parser.add_argument("--reuters-out", default="data/news.reuters.json", help="Reuters 탭 JSON 출력 경로")
    parser.add_argument("--marketaux-out", default="data/news.marketaux.json", help="Marketaux 탭 JSON 출력 경로")
    parser.add_argument("--min-overlay-score", type=float, default=6.5)
    parser.add_argument("--use-preferred-domains", action="store_true", default=False)
    parser.add_argument("--no-use-preferred-domains", action="store_false", dest="use_preferred_domains")
    parser.add_argument("--must-have-entities", action="store_true", default=False)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--open-browser", action="store_true", help="서버가 뜨면 기본 브라우저로 URL 열기")
    parser.add_argument("--official-out", default="data/news.official.json")
    parser.add_argument("--rss-out", default="data/news.rss.json")
    parser.add_argument("--rss-interval", type=int, default=120)
    parser.add_argument("--no-public-rss", action="store_true")
    parser.add_argument("--benzinga-out", default="data/news.benzinga.json")
    parser.add_argument("--official-interval", type=int, default=60)
    parser.add_argument("--official-lookback-hours", type=int, default=168)
    parser.add_argument("--no-official-feeds", action="store_true")
    parser.add_argument("--no-benzinga", action="store_true")
    parser.add_argument("--benzinga-key-file", default="")
    return parser


def build_relay_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        *child_command('relay'),
        "--provider",
        args.provider,
        "--marketaux-mode",
        args.marketaux_mode,
        "--countries",
        args.countries,
        "--lang",
        args.lang,
        "--fetch-limit",
        str(args.fetch_limit),
        "--limit",
        str(args.limit),
        "--translate-provider",
        args.translate_provider,
        "--translate-target",
        args.translate_target,
        "--translate-source",
        args.translate_source,
        "--translate-cache",
        args.translate_cache,
        "--translate-terms-file",
        args.translate_terms_file,
        "--out",
        args.out,
        "--reuters-out",
        args.reuters_out,
        "--marketaux-out",
        args.marketaux_out,
        "--timeout",
        str(args.timeout),
        "--request-retries",
        str(args.request_retries),
        "--retry-backoff",
        str(args.retry_backoff),
        "--watch",
        str(args.watch),
        "--marketaux-interval", str(args.marketaux_interval),
        "--massive-key-file", args.massive_key_file,
        "--massive-interval", str(args.massive_interval),
        "--massive-fetch-limit", str(args.massive_fetch_limit),
        "--reuters-interval", str(args.reuters_interval),
        "--state-dir", args.state_dir,
        "--official-out", args.official_out,
        "--rss-out", args.rss_out,
        "--rss-interval", str(args.rss_interval),
        "--benzinga-out", args.benzinga_out,
        "--official-interval", str(args.official_interval),
        "--official-lookback-hours", str(args.official_lookback_hours),
        "--benzinga-key-file", args.benzinga_key_file,
        "--min-overlay-score",
        str(args.min_overlay_score),
        "--rank-mode",
        args.rank_mode,
        "--reuters-fetch-limit",
        str(args.reuters_fetch_limit),
        "--reuters-sitemap-limit",
        str(args.reuters_sitemap_limit),
        "--reuters-request-delay",
        str(args.reuters_request_delay),
        "--reuters-source-label",
        args.reuters_source_label,
        "--reuters-lang",
        args.reuters_lang,
        "--min-reuters-items",
        str(args.min_reuters_items),
    ]

    for feed in args.reuters_feed:
        cmd.extend(["--reuters-feed", feed])
    if args.no_official_feeds:
        cmd.append("--no-official-feeds")
    if args.no_public_rss:
        cmd.append("--no-public-rss")
    if args.no_marketaux:
        cmd.append("--no-marketaux")
    if args.no_massive:
        cmd.append("--no-massive")
    if args.no_benzinga:
        cmd.append("--no-benzinga")
    if args.no_reuters_default_feed:
        cmd.append("--no-reuters-default-feed")
    if args.allow_undated_reuters:
        cmd.append("--allow-undated-reuters")

    if args.tabs and args.provider == "combined":
        cmd.append("--tab-feeds")

    if args.use_preferred_domains:
        cmd.append("--use-preferred-domains")
    if args.must_have_entities:
        cmd.append("--must-have-entities")
    if args.debug:
        cmd.append("--debug")
    if args.published_after:
        cmd.extend(["--published-after", args.published_after])
    if args.query:
        cmd.extend(["--query", args.query])
    if args.sort:
        cmd.extend(["--sort", args.sort])
    return cmd


def build_server_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        *child_command('server'),
        "--host", args.host, "--port", str(args.port),
        "--out", args.out, "--reuters-out", args.reuters_out,
        "--marketaux-out", args.marketaux_out, "--limit", str(args.limit),
        "--official-out", args.official_out, "--benzinga-out", args.benzinga_out,
        "--rss-out", args.rss_out,
        "--translate-provider", args.translate_provider, "--translate-source", args.translate_source,
        "--translate-terms-file", args.translate_terms_file, "--massive-key-file", args.massive_key_file,
    ]
    if not args.tabs or args.provider != "combined":
        cmd.append("--no-tabs")
    return cmd


def terminate_process(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except ProcessLookupError:
        return


def wait_for_exit(relay_proc: subprocess.Popen[bytes], server_proc: subprocess.Popen[bytes]) -> int:
    try:
        while True:
            relay_code = relay_proc.poll()
            server_code = server_proc.poll()

            if relay_code is not None:
                terminate_process(server_proc)
                return relay_code
            if server_code is not None:
                terminate_process(relay_proc)
                return server_code

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[run_overlay] stopping...")
        terminate_process(relay_proc)
        terminate_process(server_proc)
        return 0


def check_server_port(host: str, port: int) -> bool:
    """Reject duplicate launches before they can start another paid API collector."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE and getattr(exc, 'winerror', None) not in {10013, 10048}:
                raise
            print(f"[run_overlay] 포트 {port}가 이미 사용 중입니다. 추가 수집기는 시작하지 않았습니다.")
            print(f"[run_overlay] 기존 위젯 확인: http://{host}:{port}/?tab=benzinga")
            print("[run_overlay] 설정을 바꾸려면 기존 실행 터미널에서 Ctrl+C로 종료한 뒤 다시 실행하세요.")
            return False
    return True


def wait_for_server(server_proc: subprocess.Popen[bytes], host: str, port: int) -> bool:
    """Start collection only after the HTTP server is ready."""
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server_proc.poll() is not None:
            return False
        try:
            with urlopen(f"http://{connect_host}:{port}/widget-runtime.js", timeout=0.5) as response:
                if response.status == 200 and server_proc.poll() is None:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    return False


def main() -> int:
    args = build_parser().parse_args()
    if args.provider != "combined":
        args.tabs = False
    if args.watch <= 0 or args.limit <= 0:
        raise SystemExit("--watch 와 --limit 은 양수여야 합니다. 1회 수집은 news_relay.py를 사용하세요.")
    if not check_server_port(args.host, args.port):
        return 1
    relay_cmd = build_relay_cmd(args)
    server_cmd = build_server_cmd(args)

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    if args.debug:
        print("[run_overlay][debug] project_root=", PROJECT_ROOT)
        print("[run_overlay][debug] relay_cmd=", " ".join(relay_cmd))
        print("[run_overlay][debug] server_cmd=", " ".join(server_cmd))

    relay_proc = None
    server_proc = None
    try:
        server_proc = subprocess.Popen(server_cmd, cwd=PROJECT_ROOT, env=env)
        if not wait_for_server(server_proc, args.host, args.port):
            print("[run_overlay] 위젯 서버를 시작하지 못했습니다. 수집기는 시작하지 않았습니다.")
            terminate_process(server_proc)
            return 1
        relay_proc = subprocess.Popen(relay_cmd, cwd=PROJECT_ROOT, env=env)
    except BaseException:
        terminate_process(relay_proc)
        terminate_process(server_proc)
        raise

    base_url = f"http://{args.host}:{args.port}/"
    overlay_url = base_url if args.tabs else f"{base_url}?tabs=0"
    edit_url = f"{base_url}?edit=1" if args.tabs else f"{base_url}?tabs=0&edit=1"
    print(f"[run_overlay] overlay url: {overlay_url}")
    print(f"[run_overlay] edit mode url: {edit_url}")
    if args.tabs and args.provider == "combined":
        tabs = [("combined", args.out), ("benzinga", args.benzinga_out),
                ("official", args.official_out), ("rss", args.rss_out)]
        print("[run_overlay] tabs: " + " ".join(f"{key}={path}" for key, path in tabs))
        print("[run_overlay] tab urls: " + " | ".join(f"{base_url}?tab={key}" for key, _ in tabs))
    print(f"[run_overlay] 로컬 화면 발행: {args.watch}초 간격 (뉴스 API 호출 간격과 별개)")
    print("[run_overlay] stop: Ctrl+C")

    if args.open_browser:
        try:
            webbrowser.open(overlay_url)
        except Exception:
            pass

    return wait_for_exit(relay_proc, server_proc)


if __name__ == "__main__":
    raise SystemExit(main())
