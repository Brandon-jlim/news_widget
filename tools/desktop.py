"""Portable Windows launcher; the same module can be exercised from source."""
from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

from .app_paths import DATA_ROOT, RESOURCE_ROOT
from . import run_overlay
from .storage import atomic_write_json


class StartupError(Exception):
    pass


class InstanceLock:
    """OS-managed file lock; crashes do not leave a stale PID lock behind."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open('a+b')
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def watch_parent() -> None:
    """Ensure hidden children stop even if the user force-closes the launcher."""
    pid = int(os.environ.get('NEWS_WIDGET_PARENT_PID', '0'))
    if not pid:
        return
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            os._exit(0)
        def wait():
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel.CloseHandle(handle)
            os._exit(0)
    else:
        def wait():
            while os.getppid() == pid:
                time.sleep(.25)
            os._exit(0)
    threading.Thread(target=wait, daemon=True, name='launcher-watch').start()


def open_external(target: str) -> None:
    # Avoid passing PyInstaller's DLL search path to an installed browser/Explorer.
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        if target.startswith(('http://', 'https://')):
            webbrowser.open(target)
        elif os.name == 'nt':
            os.startfile(target)
    finally:
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            ctypes.windll.kernel32.SetDllDirectoryW(str(RESOURCE_ROOT))


class DesktopSession:
    def __init__(self, root: Path = DATA_ROOT, port: int = 8787, offline: bool = False):
        self.root, self.port = root, port
        self.url = f'http://127.0.0.1:{port}/?view=widget&tab=combined'
        self.offline = offline
        self.server = self.relay = None
        self.logs = []
        self.closed = False
        self.lock = threading.Lock()

    def prepare(self):
        from .news_relay import load_secret_from_file
        for name in ['keys', 'data', 'logs']:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        missing = [name for name in ['massive_api_key.txt', 'deepl_api_key.txt']
                   if not load_secret_from_file(self.root / 'keys' / name)[0]]
        if missing:
            raise StartupError('API 키 파일이 비어 있거나 없습니다.\nkeys 폴더의 다음 파일을 확인한 뒤 다시 실행해 주세요.\n\n' + '\n'.join(missing))
        # Verify the directory is writable before spawning any collector.
        probe = self.root / 'data' / '.write-check'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        args = run_overlay.build_parser().parse_args([
            '--no-marketaux', '--no-reuters-default-feed', '--limit', '30',
            '--port', str(self.port), '--massive-key-file', str(self.root / 'keys/massive_api_key.txt'),
            '--translate-terms-file', str(RESOURCE_ROOT / 'tools/ko_terms.sample.json'),
        ] + (['--no-massive', '--no-benzinga', '--no-official-feeds', '--no-public-rss', '--translate-provider', 'none'] if self.offline else []))
        for attr in ['out', 'marketaux_out', 'reuters_out', 'official_out', 'rss_out', 'benzinga_out', 'translate_cache', 'state_dir']:
            setattr(args, attr, str(self.root / getattr(args, attr)))
        return args

    def _spawn(self, role, cmd):
        with self.lock:
            if self.closed:
                raise StartupError('실행을 취소했습니다.')
            path = self.root / 'logs' / (role + '.log')
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix('.previous.log'))
            log = path.open('a', encoding='utf-8')
            self.logs.append(log)
            env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8',
                   'NEWS_WIDGET_PARENT_PID': str(os.getpid())}
            # Do not accidentally inherit a developer machine's provider credentials.
            for name in ['MASSIVE_API_KEY', 'DEEPL_API_KEY', 'MARKETAUX_API_TOKEN', 'BENZINGA_API_KEY']:
                env.pop(name, None)
            proc = subprocess.Popen(cmd, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            setattr(self, role, proc)
            return proc

    def start(self):
        args = self.prepare()
        if not run_overlay.check_server_port('127.0.0.1', self.port):
            raise StartupError('위젯 연결 주소를 다른 프로그램이 사용 중입니다.\n이미 실행 중인 Market Brief 창을 확인해 주세요.')
        self._spawn('server', run_overlay.build_server_cmd(args))
        if not run_overlay.wait_for_server(self.server, '127.0.0.1', self.port):
            raise StartupError('위젯을 시작하지 못했습니다.\n실행 폴더의 logs/server.log를 확인해 주세요.')
        self._spawn('relay', run_overlay.build_relay_cmd(args))

    def close(self):
        with self.lock:
            self.closed = True
            processes, logs = [self.relay, self.server], self.logs[:]
            self.logs.clear()
        for process in processes:
            run_overlay.terminate_process(process)
        for log in logs:
            log.close()


def self_test(report: Path, port: int, stay_open: bool) -> int:
    """Exercise the real frozen EXE without keys or requests to paid services."""
    import ssl
    import tkinter as tk
    from urllib.error import HTTPError
    # A build test must prove the packaged Tcl/Tk and SSL runtime load too.
    root = tk.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    ssl.create_default_context()
    session = DesktopSession(port=port, offline=True)
    result = {'status': 'failed'}
    try:
        session.start()
        for name in ['/', '/styles.css', '/js/app.js', '/js/article-reader.js', '/widget-runtime.js']:
            with urlopen(f'http://127.0.0.1:{port}' + name, timeout=5) as response:
                assert response.status == 200
                assert response.read()
        for name in ['/keys/massive_api_key.txt', '/keys/deepl_api_key.txt', '/data/.desktop.lock', '/logs/relay.log']:
            try:
                urlopen(f'http://127.0.0.1:{port}' + name, timeout=5)
                raise AssertionError('Private file was served')
            except HTTPError as exc:
                assert exc.code == 404
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (DATA_ROOT / 'data/news.live.json').exists():
            time.sleep(.1)
        payload = json.loads((DATA_ROOT / 'data/news.live.json').read_text(encoding='utf-8'))
        assert payload['items'] == []
        result = {'status': 'ready', 'frozen': bool(getattr(sys, 'frozen', False)),
                  'version': (RESOURCE_ROOT / 'VERSION').read_text().strip(),
                  'pid': os.getpid(), 'serverPid': session.server.pid, 'relayPid': session.relay.pid,
                  'port': port, 'privateFilesBlocked': True, 'tkAndSslLoaded': True,
                  'offline': True, 'resourceRoot': str(RESOURCE_ROOT), 'dataRoot': str(DATA_ROOT)}
        atomic_write_json(report, result)
        if stay_open:
            while True:
                time.sleep(1)
    finally:
        session.close()
    result['stopped'] = session.server.poll() is not None and session.relay.poll() is not None
    atomic_write_json(report, result)
    return 0


def run_gui(port: int, test_report: Path | None = None) -> int:
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title('Market Brief · 뉴스 위젯')
    root.geometry('500x340')
    root.resizable(False, False)
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background='#121720')
    style.configure('TFrame', background='#121720')
    style.configure('TLabel', background='#121720', foreground='#eef2f8', font=('Malgun Gothic', 10))
    style.configure('TButton', font=('Malgun Gothic', 10), padding=8)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='MARKET BRIEF', font=('Malgun Gothic', 17, 'bold')).pack(anchor='w')
    status = tk.StringVar(value='뉴스 위젯을 준비하고 있습니다…')
    ttk.Label(frame, textvariable=status, wraplength=410).pack(anchor='w', pady=(16, 12))
    ttk.Label(frame, text='위젯은 브라우저에서 자동으로 열립니다.\n이 창을 닫거나 종료를 누르면 뉴스 수집도 끝납니다.', wraplength=410).pack(anchor='w')
    controls = ttk.Frame(frame)
    controls.pack(side='bottom', fill='x', pady=(16, 0))
    session = DesktopSession(port=port, offline=test_report is not None)
    atexit.register(session.close)
    opened = [False]
    messages = []
    def open_widget():
        open_external(session.url)
    open_button = ttk.Button(controls, text='위젯 열기', command=open_widget, state='disabled')
    open_button.pack(side='left')
    ttk.Button(controls, text='API 키 폴더', command=lambda: open_external(str(DATA_ROOT / 'keys'))).pack(side='left', padx=7)
    def close():
        root.destroy()
        session.close()
    ttk.Button(controls, text='종료', command=close).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', close)
    def launch():
        try:
            session.start()
            messages.append(('ready', '실행 중 · 첫 뉴스는 잠시 후 표시됩니다.'))
        except Exception as exc:
            session.close()
            message = str(exc) if isinstance(exc, StartupError) else '실행 폴더에 쓸 수 없습니다. ZIP을 바탕 화면에 모두 풀고 다시 실행해 주세요.'
            messages.append(('error', message))
    def poll():
        if messages:
            kind, text = messages.pop(0)
            status.set(text)
            if kind == 'ready':
                open_button.configure(state='normal')
                opened[0] = True
                if test_report:
                    atomic_write_json(test_report, {'status': 'ready', 'window': root.winfo_id(),
                                      'pid': os.getpid(), 'serverPid': session.server.pid, 'relayPid': session.relay.pid})
                else:
                    open_widget()
        if opened[0] and any(proc.poll() is not None for proc in [session.server, session.relay]):
            opened[0] = False
            status.set('뉴스 연결이 종료되었습니다. 이 창을 닫고 다시 실행해 주세요.')
            open_button.configure(state='disabled')
            session.close()
        root.after(400, poll)
    threading.Thread(target=launch, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--self-test', type=Path)
    parser.add_argument('--test-stay-open', action='store_true')
    parser.add_argument('--ui-test', type=Path)
    args = parser.parse_args(argv)
    lock = InstanceLock(DATA_ROOT / 'data/.desktop.lock')
    if not lock.handle:
        if args.self_test:
            atomic_write_json(args.self_test, {'status': 'already_running'})
        else:
            open_external(f'http://127.0.0.1:{args.port}/?view=widget&tab=combined')
        return 0
    try:
        if args.self_test:
            return self_test(args.self_test, args.port, args.test_stay_open)
        return run_gui(args.port, args.ui_test)
    finally:
        lock.close()
