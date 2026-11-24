"""User-initiated Windows updates from this project's stable GitHub releases.

No network or installation work runs at import/startup. The launcher first asks
the user to check, then separately asks to install the selected release.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid
from zipfile import ZipFile

from .instance_lock import InstanceLock
from .storage import atomic_write_json

REPOSITORY = 'Brandon-jlim/news_widget'
LATEST_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_DOWNLOAD = 160 * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024
# These are the only entries ever replaced. Keys, data, logs and user files stay.
APP_ENTRIES = ('_internal', 'licenses', '처음 읽어주세요.txt', 'VERSION.txt', 'MarketBrief.exe')
DOWNLOAD_HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                  'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


class UpdateError(Exception):
    pass


class RollbackError(UpdateError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})', value):
        raise UpdateError('업데이트 버전 정보를 확인할 수 없습니다.')
    return tuple(map(int, value.split('.')))


def checked_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in DOWNLOAD_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError('업데이트 다운로드 주소가 올바르지 않습니다.')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url: str):
    return build_opener(SafeRedirect()).open(Request(checked_url(url), headers={
        'User-Agent': 'MarketBrief-ManualUpdater', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }), timeout=25)


def read_small(url: str, limit: int) -> bytes:
    with open_url(url) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise UpdateError('업데이트 응답이 너무 큽니다.')
    return body


@dataclass(frozen=True)
class Release:
    version: str
    archive_url: str
    checksum_url: str
    size: int

    @property
    def filename(self):
        return f'MarketBrief-v{self.version}-Windows-x64.zip'


def parse_release(payload: dict, current: str) -> Release | None:
    version_tuple(current)
    if not isinstance(payload, dict) or payload.get('draft') is not False or payload.get('prerelease') is not False:
        raise UpdateError('정식 배포 버전 정보를 확인할 수 없습니다.')
    tag = payload.get('tag_name', '')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise UpdateError('정식 배포 버전 이름이 올바르지 않습니다.')
    version = tag[1:]
    if version_tuple(version) <= version_tuple(current):
        return None
    archive = f'MarketBrief-v{version}-Windows-x64.zip'
    checksum = archive.removesuffix('.zip') + '.sha256'
    assets = payload.get('assets', [])
    if not isinstance(assets, list):
        raise UpdateError('업데이트 파일 목록이 올바르지 않습니다.')
    chosen = {}
    for name in [archive, checksum]:
        matches = [a for a in assets if isinstance(a, dict) and a.get('name') == name]
        expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
        if len(matches) != 1 or matches[0].get('browser_download_url') != expected or matches[0].get('state') != 'uploaded':
            raise UpdateError('검증된 Windows 업데이트 파일이 아직 준비되지 않았습니다.')
        chosen[name] = matches[0]
    size = chosen[archive].get('size')
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
        raise UpdateError('업데이트 파일 크기가 올바르지 않습니다.')
    return Release(version, chosen[archive]['browser_download_url'], chosen[checksum]['browser_download_url'], size)


def check_for_update(current: str) -> Release | None:
    try:
        return parse_release(json.loads(read_small(LATEST_URL, 1024 * 1024)), current)
    except HTTPError as exc:
        if exc.code == 404:
            return None  # There may be no stable release yet.
        if exc.code in (403, 429):
            raise UpdateError('GitHub 확인 요청이 잠시 제한되었습니다. 나중에 버튼을 다시 눌러 주세요.') from exc
        raise UpdateError('GitHub에 연결하지 못했습니다. 잠시 후 다시 확인해 주세요.') from exc
    except (URLError, OSError, ValueError) as exc:
        raise UpdateError('업데이트를 확인하지 못했습니다. 인터넷 연결을 확인해 주세요.') from exc


def ensure_plain(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise UpdateError('연결된 폴더에서는 업데이트할 수 없습니다. 일반 폴더에 압축을 풀어 주세요.')


def new_job(target: Path) -> Path:
    ensure_plain(target)
    base = target / 'data' / '.updates'
    ensure_plain(base)
    base.mkdir(parents=True, exist_ok=True)
    # Only remove our completed, older jobs on the next explicit update attempt.
    for old in base.iterdir():
        if re.fullmatch(r'[0-9a-f]{32}', old.name) and not old.is_symlink() and (old / 'complete').is_file():
            ensure_plain(old)
            shutil.rmtree(old, ignore_errors=True)
    job = base / uuid.uuid4().hex
    job.mkdir()
    return job


def unpack_release(archive: Path, destination: Path, version: str):
    version_tuple(version)
    prefix = f'MarketBrief-v{version}-Windows-x64'
    total = 0
    seen = set()
    with ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 15000:
            raise UpdateError('업데이트 ZIP에 파일이 너무 많습니다.')
        for entry in entries:
            name = entry.filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != prefix or '\\' in name or name.startswith('/')
                    or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))
                    or any(p in ('.', '..') or ':' in p or p.endswith((' ', '.')) or
                           re.match(r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)', p, re.I) for p in parts)
                    or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                raise UpdateError('업데이트 ZIP의 파일 경로가 올바르지 않습니다.')
            normalized = name.rstrip('/').casefold()
            if normalized in seen:
                raise UpdateError('업데이트 ZIP에 중복 파일이 있습니다.')
            seen.add(normalized)
            total += entry.file_size
            if total > MAX_EXPANDED:
                raise UpdateError('업데이트 압축 해제 크기가 너무 큽니다.')
            if len(parts) == 1 and entry.is_dir():
                continue
            if len(parts) < 2 or parts[1] not in (*APP_ENTRIES, 'keys'):
                raise UpdateError('업데이트 ZIP에 예상하지 않은 파일이 있습니다.')
            if parts[1] in ('MarketBrief.exe', 'VERSION.txt', '처음 읽어주세요.txt') and len(parts) != 2:
                raise UpdateError('업데이트 파일 구조가 올바르지 않습니다.')
            if parts[1] == 'keys':
                if entry.is_dir() and len(parts) == 2:
                    continue
                if len(parts) != 3 or parts[2] not in ('massive_api_key.txt', 'deepl_api_key.txt') or entry.file_size > 512 or source.read(entry).strip():
                    raise UpdateError('업데이트 파일에 개인 API 키가 포함되어 있습니다.')
                continue
        if shutil.disk_usage(destination.parent).free < total + 100 * 1024 * 1024:
            raise UpdateError('업데이트에 필요한 디스크 공간이 부족합니다.')
        for filename in ('VERSION.txt', '_internal/VERSION'):
            if source.read(prefix + '/' + filename).decode('utf-8').strip() != version:
                raise UpdateError('업데이트 파일의 버전이 일치하지 않습니다.')
        with source.open(prefix + '/MarketBrief.exe') as executable:
            if executable.read(2) != b'MZ':
                raise UpdateError('Windows 실행 파일을 확인할 수 없습니다.')
        destination.mkdir(exist_ok=False)
        for entry in entries:
            parts = PurePosixPath(entry.filename).parts[1:]
            if not parts or parts[0] == 'keys':
                continue
            target = destination.joinpath(*parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as content, target.open('xb') as output:
                    shutil.copyfileobj(content, output, 128 * 1024)
        if not all((destination / name).exists() for name in APP_ENTRIES):
            raise UpdateError('업데이트 파일이 일부 누락되었습니다.')


def prepare_update(release: Release, target: Path, progress=lambda message: None) -> Path:
    job = new_job(target)
    try:
        progress('업데이트 파일을 확인하고 있습니다…')
        checksum = read_small(release.checksum_url, 1024).decode('ascii').strip().split()
        if len(checksum) != 2 or not re.fullmatch('[0-9a-fA-F]{64}', checksum[0]) or checksum[1] != release.filename:
            raise UpdateError('업데이트 검증 정보를 확인할 수 없습니다.')
        archive = job / 'download.zip'
        digest = hashlib.sha256()
        downloaded = 0
        last_percent = -1
        deadline = time.monotonic() + 300
        with open_url(release.archive_url) as response, archive.open('xb') as output:
            while chunk := response.read(128 * 1024):
                if time.monotonic() > deadline:
                    raise UpdateError('다운로드 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.')
                downloaded += len(chunk)
                if downloaded > release.size or downloaded > MAX_DOWNLOAD:
                    raise UpdateError('업데이트 파일 크기가 배포 정보와 다릅니다.')
                output.write(chunk)
                digest.update(chunk)
                percent = downloaded * 100 // release.size
                if percent != last_percent:
                    progress(f'업데이트 다운로드 중… {percent}%')
                    last_percent = percent
        if downloaded != release.size or digest.hexdigest() != checksum[0].lower():
            raise UpdateError('업데이트 파일 검증에 실패했습니다. 현재 버전은 그대로 유지됩니다.')
        progress('다운로드 완료 · 설치를 준비하고 있습니다…')
        unpack_release(archive, job / 'incoming', release.version)
        return job
    except Exception as exc:
        shutil.rmtree(job, ignore_errors=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError('업데이트 파일을 준비하지 못했습니다. 인터넷 연결과 저장 공간을 확인해 주세요.') from exc


def detached_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('_PYI_', 'PYINSTALLER_')) or key in ('NEWS_WIDGET_PARENT_PID', 'MASSIVE_API_KEY', 'DEEPL_API_KEY', 'BENZINGA_API_KEY', 'MARKETAUX_API_TOKEN'):
            env.pop(key, None)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def launch_helper(job: Path, target: Path, version: str, port: int, *, test_restart=False):
    """Copy the currently running runtime out of the directory being replaced."""
    try:
        _launch_helper(job, target, version, port, test_restart=test_restart)
    except Exception:
        # The helper has either not started or has been stopped before returning
        # an error. Never leave a failed download/runtime copy behind.
        shutil.rmtree(job, ignore_errors=True)
        raise


def _launch_helper(job: Path, target: Path, version: str, port: int, *, test_restart=False):
    runner = job / 'runner'
    runner.mkdir()
    shutil.copy2(target / 'MarketBrief.exe', runner / 'MarketBrief.exe')
    shutil.copytree(target / '_internal', runner / '_internal')
    plan = job / 'plan.json'
    atomic_write_json(plan, {'version': version, 'parent': os.getpid(), 'port': port, 'testRestart': test_restart})
    proc = subprocess.Popen([str(runner / 'MarketBrief.exe'), '--internal-update', str(plan)],
                            cwd=runner, env=detached_environment(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (job / 'ready').exists():
            return
        if proc.poll() is not None:
            raise UpdateError('업데이트 도우미를 실행하지 못했습니다. 현재 버전을 계속 사용합니다.')
        time.sleep(.1)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    raise UpdateError('업데이트 준비 시간이 초과되었습니다. 현재 버전을 계속 사용합니다.')


def move(source: Path, target: Path):
    deadline = time.monotonic() + 12
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)


def replace_application(target: Path, incoming: Path, backup: Path):
    """Replace only application entries; restore all originals on a move failure."""
    ensure_plain(target)
    ensure_plain(incoming)
    backup.mkdir(exist_ok=False)
    saved, installed = [], []
    try:
        for name in APP_ENTRIES:
            ensure_plain(target / name)
            if (target / name).exists():
                move(target / name, backup / name)
                saved.append(name)
            move(incoming / name, target / name)
            installed.append(name)
    except Exception as exc:
        try:
            for name in reversed(installed):
                move(target / name, incoming / name)
            for name in reversed(saved):
                move(backup / name, target / name)
        except Exception as restore_error:
            raise RollbackError('기존 파일을 모두 복원하지 못했습니다. 업데이트 백업 폴더를 보관하고 지원을 요청해 주세요.') from restore_error
        raise UpdateError('업데이트에 실패해 기존 버전을 복원했습니다.') from exc


def wait_for_parent(pid: int, ready: Path):
    if os.name != 'nt':
        raise UpdateError('실행 파일 업데이트는 Windows에서 지원합니다.')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        raise UpdateError('실행 중인 앱을 확인하지 못했습니다.')
    try:
        ready.write_text('ready', encoding='ascii')
        if kernel.WaitForSingleObject(handle, 60000) != 0:
            raise UpdateError('기존 앱이 종료되지 않아 업데이트를 중단했습니다.')
    finally:
        kernel.CloseHandle(handle)


def helper_main(plan_path: Path) -> int:
    job = plan_path.absolute().parent
    ensure_plain(job)
    if job.parent.name != '.updates' or job.parent.parent.name != 'data' or not re.fullmatch('[0-9a-f]{32}', job.name):
        raise UpdateError('업데이트 작업 폴더가 올바르지 않습니다.')
    target = job.parent.parent.parent
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    version_tuple(plan['version'])
    if type(plan['parent']) is not int or plan['parent'] <= 0 or type(plan['port']) is not int or not 1 <= plan['port'] <= 65535:
        raise UpdateError('업데이트 실행 정보가 올바르지 않습니다.')
    guard = InstanceLock(target / 'data/.update.lock')
    instance = None
    restart = False
    result = {'status': 'failed', 'version': plan['version']}
    try:
        if not guard.handle:
            raise UpdateError('다른 업데이트가 진행 중입니다.')
        wait_for_parent(plan['parent'], job / 'ready')
        instance = InstanceLock(target / 'data/.desktop.lock')
        if not instance.handle:
            raise UpdateError('다른 Market Brief가 실행 중이어서 업데이트를 중단했습니다.')
        if (job / 'incoming/_internal/VERSION').read_text().strip() != plan['version']:
            raise UpdateError('설치할 버전을 다시 확인하지 못했습니다.')
        replace_application(target, job / 'incoming', job / 'backup')
        result['status'] = 'installed'
        restart = True
        (job / 'complete').write_text('installed', encoding='ascii')
    except RollbackError as exc:
        result['message'] = str(exc)
    except Exception as exc:
        result['message'] = str(exc) if isinstance(exc, UpdateError) else '업데이트를 완료하지 못했습니다. 기존 프로그램 폴더를 확인해 주세요.'
        restart = bool(instance and instance.handle and (target / 'MarketBrief.exe').exists())
    finally:
        if instance:
            instance.close()
        guard.close()
        record_result(job, target, result)
    if restart:
        args = [str(target / 'MarketBrief.exe'), '--port', str(plan['port'])]
        if plan.get('testRestart') is True:
            args += ['--self-test', str(target / 'data/.update-smoke.json'), '--test-stay-open']
        try:
            subprocess.Popen(args, cwd=target, env=detached_environment(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            restart = False
            result['message'] = '앱을 다시 열지 못했습니다. 폴더의 MarketBrief.exe를 직접 실행해 주세요.'
            record_result(job, target, result)
    if not restart and not plan.get('testRestart'):
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror('Market Brief 업데이트', result.get('message', '업데이트에 실패했습니다.'))
        window.destroy()
    return 0 if result['status'] == 'installed' else 1


def record_result(job: Path, target: Path, result: dict):
    # A full/read-only disk must not prevent releasing locks or restarting a
    # successfully restored app. Either record is sufficient for diagnostics.
    for path in (job / 'result.json', target / 'data/update-result.json'):
        try:
            atomic_write_json(path, result)
        except OSError:
            pass
