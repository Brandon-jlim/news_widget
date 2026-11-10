from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from test_pipeline import relay, run_overlay, serve_overlay
from tools import desktop, app_paths


class DesktopTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='Market Brief ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'keys').mkdir()
        for name in ['massive_api_key.txt', 'deepl_api_key.txt']:
            (self.root / 'keys' / name).write_text('fixture-key\n', encoding='utf-8-sig')

    def test_portable_settings_use_external_keys_and_persistent_data(self):
        session = desktop.DesktopSession(self.root)
        args = session.prepare()
        self.assertEqual(args.limit, 30)
        self.assertEqual(args.massive_interval, 10)
        self.assertEqual(Path(args.massive_key_file), self.root / 'keys/massive_api_key.txt')
        self.assertEqual(Path(args.out), self.root / 'data/news.live.json')
        self.assertEqual(Path(args.translate_cache), self.root / 'data/translate_cache.ko.json')
        self.assertTrue(Path(args.translate_terms_file).is_file())
        self.assertTrue(args.no_marketaux)
        self.assertTrue(args.no_reuters_default_feed)
        self.assertIn('?view=widget&tab=combined', session.url)

    def test_missing_keys_and_occupied_ports_never_spawn_collectors(self):
        (self.root / 'keys/deepl_api_key.txt').write_text('# put a key here\n', encoding='utf-8')
        session = desktop.DesktopSession(self.root)
        with patch.object(desktop.subprocess, 'Popen') as spawn:
            with self.assertRaises(desktop.StartupError) as error:
                session.start()
            self.assertIn('deepl_api_key.txt', str(error.exception))
            spawn.assert_not_called()
        (self.root / 'keys/deepl_api_key.txt').write_text('fixture-key', encoding='utf-8')
        with patch.object(run_overlay, 'check_server_port', return_value=False), patch.object(desktop.subprocess, 'Popen') as spawn:
            with self.assertRaises(desktop.StartupError):
                session.start()
            spawn.assert_not_called()

    def test_lock_blocks_duplicate_and_is_released_on_close(self):
        path = self.root / 'data/lock'
        first = desktop.InstanceLock(path)
        second = desktop.InstanceLock(path)
        self.assertIsNotNone(first.handle)
        self.assertIsNone(second.handle)
        first.close()
        third = desktop.InstanceLock(path)
        self.assertIsNotNone(third.handle)
        third.close()

    def test_frozen_children_dispatch_through_same_exe(self):
        with patch.object(app_paths.sys, 'frozen', True, create=True), patch.object(app_paths.sys, 'executable', 'C:/한글 폴더/MarketBrief.exe'):
            args = desktop.DesktopSession(self.root).prepare()
            self.assertEqual(run_overlay.build_server_cmd(args)[:2], ['C:/한글 폴더/MarketBrief.exe', '--internal-server'])
            self.assertEqual(run_overlay.build_relay_cmd(args)[:2], ['C:/한글 폴더/MarketBrief.exe', '--internal-relay'])

    def test_close_prevents_late_worker_spawn_and_stops_both_children(self):
        session = desktop.DesktopSession(self.root)
        session.server, session.relay = Mock(), Mock()
        with patch.object(run_overlay, 'terminate_process') as stop:
            session.close()
            self.assertEqual(stop.call_count, 2)
        with patch.object(desktop.subprocess, 'Popen') as spawn:
            with self.assertRaises(desktop.StartupError):
                session._spawn('server', ['never'])
            spawn.assert_not_called()

    def test_child_inherits_parent_watch_but_not_developer_api_credentials(self):
        session = desktop.DesktopSession(self.root, offline=True)
        args = session.prepare()
        self.assertTrue(all([args.no_massive, args.no_benzinga, args.no_official_feeds, args.no_public_rss]))
        self.assertEqual(args.translate_provider, 'none')
        with patch.dict(desktop.os.environ, {'MASSIVE_API_KEY': 'do-not-inherit', 'DEEPL_API_KEY': 'do-not-inherit'}), patch.object(desktop.subprocess, 'Popen') as spawn:
            try:
                session._spawn('server', ['fixture'])
                env = spawn.call_args.kwargs['env']
                self.assertNotIn('MASSIVE_API_KEY', env)
                self.assertNotIn('DEEPL_API_KEY', env)
                self.assertEqual(env['NEWS_WIDGET_PARENT_PID'], str(desktop.os.getpid()))
            finally:
                session.close()

    def test_notepad_key_encodings_and_comment_only_templates(self):
        path = self.root / 'keys/deepl_api_key.txt'
        for encoding in ['utf-8', 'utf-8-sig', 'utf-16']:
            path.write_text('fixture-key\n', encoding=encoding)
            self.assertEqual(relay.load_secret_from_file(path)[0], 'fixture-key')
        path.write_text('# no key\n\n', encoding='utf-8')
        self.assertEqual(relay.load_secret_from_file(path)[0], '')

    def test_bundle_assets_and_runtime_cache_can_live_in_separate_roots(self):
        assets, writable = self.root / 'assets', self.root / 'portable'
        server = serve_overlay.build_server('127.0.0.1', 0, assets, {}, {}, cache_root=writable)
        try:
            self.assertTrue(server.article_summary.path.is_relative_to(writable))
            self.assertTrue(server.korean_index.path.is_relative_to(writable))
        finally:
            server.server_close()


if __name__ == '__main__':
    unittest.main()
