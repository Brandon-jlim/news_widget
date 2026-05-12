#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="뉴스 수집(news_relay)과 로컬 웹서버(http.server)를 한 번에 실행합니다."
    )
    parser.add_argument("--host", default="127.0.0.1", help="로컬 서버 바인드 주소")
    parser.add_argument("--port", type=int, default=8787, help="로컬 서버 포트")
    parser.add_argument("--watch", type=int, default=45, help="뉴스 갱신 주기(초)")
    parser.add_argument("--limit", type=int, default=5, help="최종 카드 수")
    parser.add_argument("--fetch-limit", type=int, default=20, help="marketaux에서 가져올 원시 기사 수")
    parser.add_argument("--timeout", type=float, default=20.0, help="API 요청 타임아웃(초)")
    parser.add_argument("--request-retries", type=int, default=2, help="실패 시 재시도 횟수")
    parser.add_argument("--retry-backoff", type=float, default=1.5, help="재시도 대기 배수")
    parser.add_argument("--provider", choices=["marketaux"], default="marketaux")
    parser.add_argument("--marketaux-mode", choices=["broad", "indices", "watchlist"], default="broad")
    parser.add_argument("--countries", default="us")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--published-after", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--sort", default="")
    parser.add_argument("--translate-provider", choices=["none", "deepl"], default="deepl")
    parser.add_argument("--translate-target", default="ko")
    parser.add_argument("--translate-source", default="en")
    parser.add_argument("--translate-cache", default="data/translate_cache.ko.json")
    parser.add_argument("--translate-terms-file", default="tools/ko_terms.sample.json")
    parser.add_argument("--out", default="data/news.live.json")
    parser.add_argument("--min-overlay-score", type=float, default=6.5)
    parser.add_argument("--use-preferred-domains", action="store_true", default=True)
    parser.add_argument("--no-use-preferred-domains", action="store_false", dest="use_preferred_domains")
    parser.add_argument("--must-have-entities", action="store_true", default=False)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--open-browser", action="store_true", help="서버가 뜨면 기본 브라우저로 URL 열기")
    return parser


def build_relay_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "news_relay.py"),
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
        "--timeout",
        str(args.timeout),
        "--request-retries",
        str(args.request_retries),
        "--retry-backoff",
        str(args.retry_backoff),
        "--watch",
        str(args.watch),
        "--min-overlay-score",
        str(args.min_overlay_score),
    ]

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
    return [
        sys.executable,
        "-m",
        "http.server",
        str(args.port),
        "--bind",
        args.host,
        "--directory",
        str(PROJECT_ROOT),
    ]


def terminate_process(proc: subprocess.Popen[bytes] | None, *, sig: int = signal.SIGTERM) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
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
        time.sleep(0.5)
        terminate_process(relay_proc, sig=signal.SIGKILL)
        terminate_process(server_proc, sig=signal.SIGKILL)
        return 0


def main() -> int:
    args = build_parser().parse_args()
    relay_cmd = build_relay_cmd(args)
    server_cmd = build_server_cmd(args)

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    if args.debug:
        print("[run_overlay][debug] project_root=", PROJECT_ROOT)
        print("[run_overlay][debug] relay_cmd=", " ".join(relay_cmd))
        print("[run_overlay][debug] server_cmd=", " ".join(server_cmd))

    relay_proc = subprocess.Popen(relay_cmd, cwd=PROJECT_ROOT, env=env)
    # relay가 첫 fetch를 시작할 시간을 아주 조금 준다.
    time.sleep(0.6)
    server_proc = subprocess.Popen(server_cmd, cwd=PROJECT_ROOT, env=env)

    base_url = f"http://{args.host}:{args.port}/"
    print(f"[run_overlay] overlay url: {base_url}")
    print(f"[run_overlay] edit mode url: {base_url}?edit=1")
    print(f"[run_overlay] relay refresh: every {args.watch}s")
    print("[run_overlay] stop: Ctrl+C")

    if args.open_browser:
        try:
            webbrowser.open(base_url)
        except Exception:
            pass

    return wait_for_exit(relay_proc, server_proc)


if __name__ == "__main__":
    raise SystemExit(main())
