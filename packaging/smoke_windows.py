"""Run the actual windowed EXE from a Unicode path without any real API keys."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def print_logs(root):
    for path in sorted((root / 'logs').glob('*.log')):
        print(path.name + ':\n' + path.read_text(encoding='utf-8', errors='replace')[-6000:])


def wait_report(path, proc):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        if proc.poll() is not None:
            print_logs(path.parent)
            raise AssertionError('EXE exited before reporting readiness: ' + str(proc.returncode))
        time.sleep(.2)
    print_logs(path.parent)
    raise AssertionError('EXE startup timed out')


def is_alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)


def main():
    if os.name != 'nt':
        raise SystemExit('This test runs on Windows.')
    with tempfile.TemporaryDirectory(prefix='MarketBrief-') as directory:
        target = Path(directory) / '한글 경로' / 'Market Brief'
        shutil.copytree(ROOT / 'dist/MarketBrief', target)
        for name in ['massive_api_key.txt', 'deepl_api_key.txt']:
            (target / 'keys' / name).write_text('fixture-not-a-real-key\n', encoding='utf-8-sig')
        env = os.environ.copy()
        # A recipient does not have Python installed or a development PATH.
        system_root = os.environ.get('SystemRoot', r'C:\Windows')
        env['PATH'] = os.pathsep.join([system_root, str(Path(system_root) / 'System32')])
        for key in list(env):
            if key.startswith(('PYTHON', 'PYINSTALLER')) or key in ['GH_TOKEN', 'GITHUB_TOKEN']:
                env.pop(key, None)
        executable = str(target / 'MarketBrief.exe')
        report = target / 'test-result.json'
        proc = subprocess.Popen([executable, '--self-test', str(report), '--port', str(free_port())], cwd=directory, env=env)
        try:
            exit_code = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            print_logs(target)
            raise
        if exit_code:
            print_logs(target)
        assert exit_code == 0
        result = json.loads(report.read_text(encoding='utf-8'))
        assert result['status'] == 'ready' and result['frozen'] and result['stopped']
        assert '한글 경로' in result['dataRoot']
        assert not is_alive(result['serverPid']) and not is_alive(result['relayPid'])
        report.unlink()
        proc = subprocess.Popen([executable, '--self-test', str(report), '--test-stay-open', '--port', str(free_port())], cwd=directory, env=env)
        try:
            running = wait_report(report, proc)
            duplicate_report = target / 'duplicate.json'
            duplicate = subprocess.run([executable, '--self-test', str(duplicate_report)], cwd=directory, env=env, timeout=30)
            assert duplicate.returncode == 0
            assert json.loads(duplicate_report.read_text())['status'] == 'already_running'
            proc.kill()
            proc.wait(timeout=10)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and any(is_alive(running[key]) for key in ['serverPid', 'relayPid']):
                time.sleep(.2)
            assert not is_alive(running['serverPid']) and not is_alive(running['relayPid'])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        summary = {k: v for k, v in result.items() if k not in ['pid', 'serverPid', 'relayPid', 'resourceRoot', 'dataRoot', 'port']}
        summary.update(unicodePath=True, withoutPythonOnPath=True, duplicateLaunchBlocked=True, forcedExitStopsChildren=True)
        ui_report = target / 'ui-result.json'
        gui = subprocess.Popen([executable, '--ui-test', str(ui_report), '--port', str(free_port())], cwd=directory, env=env)
        try:
            ui = wait_report(ui_report, gui)
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            window = user.GetAncestor(ui['window'], 2)
            assert window and user.PostMessageW(window, 0x0010, 0, 0)  # Normal window close
            assert gui.wait(timeout=15) == 0
            assert not is_alive(ui['serverPid']) and not is_alive(ui['relayPid'])
            summary['guiWindowCloseStopsCollection'] = True
        finally:
            if gui.poll() is None:
                gui.kill()
                gui.wait(timeout=10)
        (ROOT / 'dist/windows-smoke-report.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))


if __name__ == '__main__':
    main()
