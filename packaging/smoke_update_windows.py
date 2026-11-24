"""Replace the real frozen app from a local fixture; no GitHub/paid API calls."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import time
from zipfile import ZipFile, ZIP_DEFLATED

from smoke_windows import free_port, is_alive, print_logs

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != 'nt':
        raise SystemExit('Run the update smoke test on Windows.')
    with tempfile.TemporaryDirectory(prefix='MarketBrief-update-') as directory:
        directory = Path(directory)
        target = directory / '한글 경로' / 'Market Brief'
        shutil.copytree(ROOT / 'dist/MarketBrief', target)
        keys = ['massive_api_key.txt', 'deepl_api_key.txt']
        for name in keys:
            (target / 'keys' / name).write_text('fixture-not-a-real-key\n', encoding='utf-8-sig')
        (target / 'data').mkdir()
        (target / 'data/keep.txt').write_text('preserve-news-and-cache', encoding='utf-8')
        (target / 'custom-note.txt').write_text('user-file', encoding='utf-8')
        # The same EXE is re-bundled with a distinct resource version. This tests
        # real file locks and restarting the downloaded runtime, without publishing
        # a fake update or requiring a second product release.
        version = '999.0.0'
        fixture = directory / 'update.zip'
        prefix = f'MarketBrief-v{version}-Windows-x64/'
        with ZipFile(fixture, 'w', ZIP_DEFLATED) as archive:
            for path in sorted((ROOT / 'dist/MarketBrief').rglob('*')):
                if path.is_file():
                    relative = path.relative_to(ROOT / 'dist/MarketBrief').as_posix()
                    data = (version + '\n').encode() if relative in ('VERSION.txt', '_internal/VERSION') else path.read_bytes()
                    archive.writestr(prefix + relative, data)
        env = os.environ.copy()
        system_root = env.get('SystemRoot', r'C:\Windows')
        env['PATH'] = os.pathsep.join([system_root, str(Path(system_root) / 'System32')])
        for key in list(env):
            if key.startswith(('PYTHON', 'PYINSTALLER', '_PYI_')) or key in ('GH_TOKEN', 'GITHUB_TOKEN'):
                env.pop(key, None)
        report = target / 'before-update.json'
        restarted_report = target / 'data/.update-smoke.json'
        port = free_port()
        parent = subprocess.Popen([str(target / 'MarketBrief.exe'), '--self-test', str(report),
                                   '--self-test-update', str(fixture), '--port', str(port)], cwd=directory, env=env)
        new_pid = None
        try:
            assert parent.wait(timeout=90) == 0
            old = json.loads(report.read_text(encoding='utf-8'))
            assert old['stopped'] and old['updateJob']
            deadline = time.monotonic() + 90
            while not restarted_report.exists() and time.monotonic() < deadline:
                time.sleep(.2)
            if not restarted_report.exists():
                print_logs(target)
                for result in (target / 'data/.updates').rglob('result.json'):
                    print(result.read_text())
                raise AssertionError('Updated EXE did not restart')
            new = json.loads(restarted_report.read_text(encoding='utf-8'))
            new_pid = new['pid']
            assert new['status'] == 'ready' and new['frozen'] and new['version'] == version
            assert new['port'] == port and new['bundledFontsServed']
            assert not is_alive(old['serverPid']) and not is_alive(old['relayPid'])
            assert (target / '_internal/VERSION').read_text().strip() == version
            for name in keys:
                assert (target / 'keys' / name).read_text(encoding='utf-8-sig').strip() == 'fixture-not-a-real-key'
            assert (target / 'data/keep.txt').read_text() == 'preserve-news-and-cache'
            assert (target / 'custom-note.txt').read_text() == 'user-file'
            result = json.loads((target / 'data/update-result.json').read_text(encoding='utf-8'))
            assert result['status'] == 'installed'
            summary = dict(realFrozenReplacement=True, restartedWithNewVersion=True, samePort=True,
                           keysAndDataPreserved=True, unicodePath=True, withoutPythonOnPath=True,
                           oldChildrenStopped=True, bundledFontsServed=True, offline=True)
            (ROOT / 'dist/windows-update-report.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
            print(json.dumps(summary))
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)
            if new_pid:
                subprocess.run(['taskkill', '/PID', str(new_pid), '/T', '/F'], stdout=subprocess.DEVNULL, check=False)
                deadline = time.monotonic() + 12
                while is_alive(new_pid) and time.monotonic() < deadline:
                    time.sleep(.2)


if __name__ == '__main__':
    main()
