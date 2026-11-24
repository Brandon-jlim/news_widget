import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

from test_pipeline import serve_overlay  # Add the project root to sys.path.
from tools import updater, update_ui


def release_payload(version='1.12.0'):
    prefix = f'MarketBrief-v{version}-Windows-x64'
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'assets': [{'name': prefix + suffix, 'size': 1024, 'state': 'uploaded',
                        'browser_download_url': f'https://github.com/{updater.REPOSITORY}/releases/download/v{version}/{prefix}{suffix}'}
                       for suffix in ('.zip', '.sha256')]}


def fixture_zip(path, version='1.12.0', extra=None):
    prefix = f'MarketBrief-v{version}-Windows-x64/'
    content = {'MarketBrief.exe': b'MZ-new-executable', '_internal/VERSION': version.encode(),
               '_internal/runtime.dll': b'new-runtime', 'VERSION.txt': version.encode(),
               '처음 읽어주세요.txt': b'guide', 'licenses/license.txt': b'license',
               'keys/massive_api_key.txt': b'', 'keys/deepl_api_key.txt': b''}
    if extra:
        content.update(extra)
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, value in content.items():
            archive.writestr(prefix + name, value)
    return path.read_bytes()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_only_new_stable_complete_releases_are_offered(self):
        self.assertEqual(updater.parse_release(release_payload(), '1.11.0').version, '1.12.0')
        self.assertIsNone(updater.parse_release(release_payload(), '1.12.0'))
        self.assertIsNone(updater.parse_release(release_payload(), '2.0.0'))
        self.assertGreater(updater.version_tuple('1.12.0'), updater.version_tuple('1.9.9'))
        for change in [{'draft': True}, {'prerelease': True}, {'tag_name': 'v1.12.0-beta'}, {'assets': []}]:
            with self.subTest(change=change), self.assertRaises(updater.UpdateError):
                updater.parse_release({**release_payload(), **change}, '1.11.0')
        for version in ['', 'v1.2.3', '1.2', '01.2.3', '../1.2.3']:
            with self.assertRaises(updater.UpdateError):
                updater.version_tuple(version)

    def test_downloads_are_pinned_to_repository_and_redirects_stay_on_github_https(self):
        for bad in ['https://example.com/app.zip', 'https://github.com/other/project/releases/app.zip']:
            payload = release_payload()
            payload['assets'][0]['browser_download_url'] = bad
            with self.assertRaises(updater.UpdateError):
                updater.parse_release(payload, '1.11.0')
        for bad in ['http://github.com/file', 'https://github.com.evil.test/file', 'https://github.com@evil.test/file', 'https://github.com:444/file']:
            with self.assertRaises(updater.UpdateError):
                updater.checked_url(bad)
        self.assertEqual(updater.checked_url('https://release-assets.githubusercontent.com/file?signature=fixture'),
                         'https://release-assets.githubusercontent.com/file?signature=fixture')

    def test_explicit_check_handles_missing_release_rate_limit_and_network_errors(self):
        with patch.object(updater, 'read_small', return_value=json.dumps(release_payload()).encode()) as read:
            self.assertEqual(updater.check_for_update('1.11.0').version, '1.12.0')
            self.assertEqual(read.call_args.args[0], updater.LATEST_URL)
        for code in [404, 403, 429, 500]:
            with patch.object(updater, 'read_small', side_effect=updater.HTTPError(updater.LATEST_URL, code, '', {}, None)):
                if code == 404:
                    self.assertIsNone(updater.check_for_update('1.11.0'))
                else:
                    with self.assertRaises(updater.UpdateError):
                        updater.check_for_update('1.11.0')

    def test_verified_download_stages_app_only_and_never_touches_keys(self):
        data = fixture_zip(self.root / 'source.zip')
        payload = release_payload()
        payload['assets'][0]['size'] = len(data)
        release = updater.parse_release(payload, '1.11.0')
        keys = self.root / 'keys'
        keys.mkdir()
        (keys / 'massive_api_key.txt').write_text('existing-private-key')
        checksum = hashlib.sha256(data).hexdigest() + '  ' + release.filename
        with patch.object(updater, 'read_small', return_value=checksum.encode()), patch.object(updater, 'open_url', return_value=io.BytesIO(data)):
            job = updater.prepare_update(release, self.root)
        self.assertTrue((job / 'incoming/MarketBrief.exe').is_file())
        self.assertFalse((job / 'incoming/keys').exists())
        self.assertEqual((keys / 'massive_api_key.txt').read_text(), 'existing-private-key')

    def test_hash_size_and_truncated_download_failure_leave_current_app_untouched(self):
        data = fixture_zip(self.root / 'source.zip')
        payload = release_payload()
        payload['assets'][0]['size'] = len(data)
        release = updater.parse_release(payload, '1.11.0')
        (self.root / 'MarketBrief.exe').write_bytes(b'MZ-old')
        valid = hashlib.sha256(data).hexdigest() + '  ' + release.filename
        for checksum, body in [('0' * 64 + '  ' + release.filename, data), (valid, data[:-5]), (valid, data + b'extra')]:
            with patch.object(updater, 'read_small', return_value=checksum.encode()), patch.object(updater, 'open_url', return_value=io.BytesIO(body)):
                with self.assertRaises(updater.UpdateError):
                    updater.prepare_update(release, self.root)
            self.assertEqual((self.root / 'MarketBrief.exe').read_bytes(), b'MZ-old')
            self.assertEqual(list((self.root / 'data/.updates').iterdir()), [])

    def test_zip_rejects_traversal_duplicates_links_private_data_and_wrong_versions(self):
        for i, extra in enumerate([{'../outside': b'x'}, {'_internal/../../outside': b'x'},
                {'_internal/./file': b'x'}, {'_internal//file': b'x'},
                {'_internal/CON.txt': b'x'}, {'_internal/file:stream': b'x'}, {'data/cache': b'x'},
                {'keys/massive_api_key.txt': b'private'}, {'_internal/VERSION': b'2.0.0'},
                {'MARKETBRIEF.EXE': b'MZx'}]):
            archive = self.root / f'bad-{i}.zip'
            fixture_zip(archive, extra=extra)
            with self.subTest(extra=extra), self.assertRaises((updater.UpdateError, KeyError)):
                updater.unpack_release(archive, self.root / f'in-{i}', '1.12.0')
        archive = self.root / 'link.zip'
        fixture_zip(archive)
        with ZipFile(archive, 'a') as z:
            info = ZipInfo('MarketBrief-v1.12.0-Windows-x64/_internal/link')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '../../outside')
        with self.assertRaises(updater.UpdateError):
            updater.unpack_release(archive, self.root / 'in-link', '1.12.0')

    def application_dirs(self):
        target, incoming = self.root / 'app', self.root / 'incoming'
        for folder, contents in [(target, b'old'), (incoming, b'new')]:
            folder.mkdir()
            for name in updater.APP_ENTRIES:
                entry = folder / name
                if name in ('_internal', 'licenses'):
                    entry.mkdir()
                    (entry / 'item').write_bytes(contents)
                else:
                    entry.write_bytes(contents)
        for name in ['keys', 'data', 'logs']:
            (target / name).mkdir()
            (target / name / 'sentinel').write_bytes(b'preserve')
        (target / 'my-notes.txt').write_bytes(b'preserve')
        return target, incoming

    def test_replacement_preserves_user_files_and_retains_backup(self):
        target, incoming = self.application_dirs()
        backup = self.root / 'backup'
        updater.replace_application(target, incoming, backup)
        self.assertEqual((target / 'MarketBrief.exe').read_bytes(), b'new')
        self.assertEqual((backup / 'MarketBrief.exe').read_bytes(), b'old')
        for name in ['keys/sentinel', 'data/sentinel', 'logs/sentinel', 'my-notes.txt']:
            self.assertEqual((target / name).read_bytes(), b'preserve')

    def test_failure_mid_install_restores_all_original_files(self):
        target, incoming = self.application_dirs()
        move = updater.move
        def fail(source, destination):
            if source == incoming / 'MarketBrief.exe':
                raise OSError('fixture disk failure')
            move(source, destination)
        with patch.object(updater, 'move', side_effect=fail):
            with self.assertRaisesRegex(updater.UpdateError, '복원'):
                updater.replace_application(target, incoming, self.root / 'backup')
        self.assertEqual((target / 'MarketBrief.exe').read_bytes(), b'old')
        self.assertEqual((target / '_internal/item').read_bytes(), b'old')
        self.assertEqual((target / 'keys/sentinel').read_bytes(), b'preserve')

    def test_restart_does_not_inherit_parent_watch_or_private_provider_credentials(self):
        with patch.dict(updater.os.environ, {'NEWS_WIDGET_PARENT_PID': '999', 'DEEPL_API_KEY': 'fixture', '_PYI_PARENT_PROCESS_LEVEL': '1'}):
            env = updater.detached_environment()
            self.assertNotIn('NEWS_WIDGET_PARENT_PID', env)
            self.assertNotIn('DEEPL_API_KEY', env)
            self.assertNotIn('_PYI_PARENT_PROCESS_LEVEL', env)
            self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_failed_rollback_retains_original_backup_for_recovery(self):
        target, incoming = self.application_dirs()
        backup = self.root / 'backup'
        move = updater.move
        def fail(source, destination):
            if source == incoming / 'MarketBrief.exe' or source == backup / 'MarketBrief.exe':
                raise OSError('fixture disk failure')
            move(source, destination)
        with patch.object(updater, 'move', side_effect=fail), self.assertRaises(updater.RollbackError):
            updater.replace_application(target, incoming, backup)
        self.assertEqual((backup / 'MarketBrief.exe').read_bytes(), b'old')
        self.assertEqual((target / 'keys/sentinel').read_bytes(), b'preserve')

    def test_helper_records_restart_failure_and_releases_locks(self):
        target, incoming = self.application_dirs()
        (incoming / '_internal/VERSION').write_text('1.12.0')
        job = updater.new_job(target)
        incoming.rename(job / 'incoming')
        plan = job / 'plan.json'
        plan.write_text(json.dumps(dict(version='1.12.0', parent=123, port=8787, testRestart=True)))
        with patch.object(updater, 'wait_for_parent'), patch.object(updater.subprocess, 'Popen', side_effect=OSError('fixture restart failure')):
            self.assertEqual(updater.helper_main(plan), 0)
        result = json.loads((target / 'data/update-result.json').read_text())
        self.assertEqual(result['status'], 'installed')
        self.assertIn('직접 실행', result['message'])
        self.assertEqual((job / 'backup/MarketBrief.exe').read_bytes(), b'old')
        for name in ['.update.lock', '.desktop.lock']:
            lock = updater.InstanceLock(target / 'data' / name)
            self.assertIsNotNone(lock.handle)
            lock.close()

    def test_helper_runtime_copy_failure_keeps_current_app_and_cleans_job(self):
        target, incoming = self.application_dirs()
        job = updater.new_job(target)
        with patch.object(updater.shutil, 'copytree', side_effect=OSError('fixture disk full')), self.assertRaises(OSError):
            updater.launch_helper(job, target, '1.12.0', 8787)
        self.assertFalse(job.exists())
        self.assertEqual((target / 'MarketBrief.exe').read_bytes(), b'old')


class FakeControl:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get('value')
    def set(self, value): self.value = value
    def pack(self, **kwargs): pass
    def configure(self, **kwargs): pass


class InlineThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


class ManualUpdateUITests(unittest.TestCase):
    def test_startup_and_timer_do_not_check_and_declining_does_not_download(self):
        messagebox = types.SimpleNamespace(askyesno=Mock(return_value=False))
        tk = types.SimpleNamespace(StringVar=FakeControl, ttk=types.SimpleNamespace(Button=FakeControl, Label=FakeControl), messagebox=messagebox)
        root = Mock()
        with patch.dict('sys.modules', {'tkinter': tk}), patch.object(update_ui.threading, 'Thread', InlineThread), \
                patch.object(updater, 'check_for_update', return_value=updater.parse_release(release_payload(), '1.11.0')) as check, \
                patch.object(updater, 'prepare_update') as download:
            ui = update_ui.UpdateControls(root, Mock(), 8787, Mock())
            for _ in range(5): ui.poll()
            check.assert_not_called()
            download.assert_not_called()
            ui.check()
            ui.poll()
            check.assert_called_once()
            messagebox.askyesno.assert_called_once()
            download.assert_not_called()

    def test_confirming_install_hands_off_once_and_uses_selected_release(self):
        messagebox = types.SimpleNamespace(askyesno=Mock(return_value=True))
        tk = types.SimpleNamespace(StringVar=FakeControl, ttk=types.SimpleNamespace(Button=FakeControl, Label=FakeControl), messagebox=messagebox)
        release = updater.parse_release(release_payload(), '1.11.0')
        handoff = Mock()
        with patch.dict('sys.modules', {'tkinter': tk}), patch.object(update_ui.threading, 'Thread', InlineThread), \
                patch.object(updater, 'check_for_update', return_value=release), \
                patch.object(updater, 'prepare_update', return_value=Path('/fixture/job')) as download, \
                patch.object(updater, 'launch_helper') as helper:
            ui = update_ui.UpdateControls(Mock(), Mock(), 8787, handoff)
            ui.check()
            ui.poll()
            self.assertEqual(download.call_args.args[0], release)
            self.assertEqual(helper.call_args.args[2:], ('1.12.0', 8787))
            handoff.assert_called_once()


if __name__ == '__main__':
    unittest.main()
