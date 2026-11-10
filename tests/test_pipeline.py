import argparse
import contextlib
from datetime import datetime, timedelta, timezone
from email.message import Message
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import news_relay as relay
from tools import rss_to_json, run_overlay, serve_overlay, storage


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(relay, "PROJECT_ROOT", self.root), patch.object(sys, "argv", ["test"]), \
             patch.object(relay, "first_existing_secret", return_value=("", "")), patch.dict(relay.os.environ, {}, clear=True):
            self.args = relay.parse_args()
        self.args.state_dir = str(self.root / "state")
        self.args.translate_cache = str(self.root / "translate.json")
        self.args.translate_terms_file = ""
        self.args.out = str(self.root / "combined.json")
        self.args.reuters_out = str(self.root / "reuters.json")
        self.args.marketaux_out = str(self.root / "marketaux.json")
        self.args.official_out = str(self.root / "official.json")
        self.args.rss_out = str(self.root / "rss.json")
        self.args.benzinga_out = str(self.root / "benzinga.json")
        self.args.tab_feeds = True
        self.args.request_retries = 0
        self.args.file_marketaux_api_token = "fixture"
        self.args.file_deepl_api_key = "fixture"
        self.now = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
        self.clock = patch.object(relay, "now_utc", return_value=self.now).start()
        self.addCleanup(patch.stopall)
        self.item = {"id": "m", "url": "https://example.test/market", "title": "Fed cuts rates", "publishedAt": self.now.isoformat(), "_score": 20}
        self.provider = Mock()
        self.provider.fetch.return_value = relay.ProviderResult([self.item], {"usage": {"daily_limit": "100", "daily_remaining": "99"}})

    def managed(self, name="marketaux"):
        return relay.ManagedProvider(name, self.provider)

    def test_default_quota_and_restart_do_not_refetch(self):
        first = self.managed().fetch(self.args)
        self.clock.return_value = self.now + timedelta(seconds=45)
        second = self.managed().fetch(self.args)
        self.assertEqual(self.provider.fetch.call_count, 1)
        self.assertEqual(first.items, second.items)
        self.assertEqual(second.meta["source_statuses"]["marketaux"]["lastSuccessAt"], self.now.isoformat())
        self.clock.return_value = self.now + timedelta(seconds=900)
        self.managed().fetch(self.args)
        self.assertEqual(self.provider.fetch.call_count, 2)

    def test_paid_interval_still_obeys_observed_daily_limit(self):
        self.args.marketaux_interval = 45
        result = self.managed().fetch(self.args)
        self.assertGreaterEqual(result.meta["source_statuses"]["marketaux"]["effectiveIntervalSeconds"], 864)
        self.clock.return_value = self.now + timedelta(seconds=45)
        self.managed().fetch(self.args)
        self.assertEqual(self.provider.fetch.call_count, 1)

    def test_quota_exhaustion_keeps_last_result_and_waits_a_day(self):
        self.provider.fetch.return_value.meta["usage"]["daily_remaining"] = "0"
        result = self.managed().fetch(self.args)
        status = result.meta["source_statuses"]["marketaux"]
        self.assertEqual(status["status"], "limited")
        self.assertEqual(relay.parse_iso_datetime(status["nextFetchAt"]), self.now + timedelta(days=1))
        self.assertEqual(result.items, [self.item])

    def test_rate_limit_does_not_retry_immediately(self):
        headers = Message()
        headers["Retry-After"] = "3600"
        error = HTTPError("https://example.test", 429, "limit", headers, io.BytesIO(b"{}"))
        with patch.object(relay, "urlopen", side_effect=error) as fetch, patch.object(relay.time, "sleep") as sleep:
            with self.assertRaises(relay.RequestFailure) as caught:
                relay.request_json("https://example.test", timeout=1, retries=2)
            self.assertEqual(caught.exception.retry_after, 3600)
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()

    def test_retry_after_date(self):
        self.assertEqual(relay.retry_after_seconds({"Retry-After": "Tue, 10 Nov 2026 01:00:00 GMT"}), 3600)

    def test_provider_failure_retains_cache_and_honors_retry_after(self):
        self.managed().fetch(self.args)
        self.clock.return_value = self.now + timedelta(seconds=901)
        self.provider.fetch.side_effect = relay.RequestFailure(429, retry_after=7200)
        result = self.managed().fetch(self.args)
        status = result.meta["source_statuses"]["marketaux"]
        self.assertEqual(result.items, [self.item])
        self.assertEqual(status["status"], "error")
        self.assertEqual(relay.parse_iso_datetime(status["nextFetchAt"]), self.clock.return_value + timedelta(seconds=7200))

    def test_reuters_block_cools_down_for_one_hour(self):
        self.provider.fetch.side_effect = relay.RequestFailure(401)
        result = self.managed("reuters").fetch(self.args)
        self.assertEqual(relay.parse_iso_datetime(result.meta["source_statuses"]["reuters"]["nextFetchAt"]), self.now + timedelta(hours=1))
        self.clock.return_value += timedelta(seconds=45)
        self.managed("reuters").fetch(self.args)
        self.assertEqual(self.provider.fetch.call_count, 1)

    def test_partial_xml_source_failure_remains_visible(self):
        self.provider.fetch.return_value = relay.ProviderResult([dict(self.item, _source_provider="reuters")], {"errors":["one XML source failed"]})
        result = self.managed("reuters").fetch(self.args)
        self.assertEqual(result.meta["source_statuses"]["reuters"]["status"], "degraded")
        self.assertTrue(result.items)
        self.assertTrue(result.meta["errors"])

    def test_changed_query_does_not_reuse_wrong_articles_or_bypass_deadline(self):
        self.managed().fetch(self.args)
        self.args.query = "different topic"
        result = self.managed().fetch(self.args)
        self.assertEqual(result.items, [])
        self.assertEqual(result.meta["source_statuses"]["marketaux"]["status"], "waiting")
        self.assertEqual(self.provider.fetch.call_count, 1)

    def test_failed_tab_preserves_articles_and_timestamp(self):
        old = {"updatedAt": "2026-11-09T22:00:00Z", "items": [{"title": "Last good headline", "titleKo": "이전 정상 뉴스"}]}
        Path(self.args.reuters_out).write_text(json.dumps(old))
        result = relay.ProviderResult([self.item], {"provider": "combined", "source_statuses": {
            "marketaux": {"status": "ok", "lastSuccessAt": self.now.isoformat()},
            "reuters": {"status": "error", "error": "HTTP 401"}}})
        relay.write_tab_feeds(result, result.items, self.args)
        saved = storage.read_json(Path(self.args.reuters_out))
        self.assertEqual(saved["items"], old["items"])
        self.assertEqual(saved["updatedAt"], old["updatedAt"])
        self.assertTrue(saved["meta"]["retained"])
        market = storage.read_json(Path(self.args.marketaux_out))
        self.assertEqual(market["meta"]["errors"], [])
        self.assertEqual(len(storage.read_json(Path(self.args.out))["items"]), 1)

    def test_all_sources_fail_on_first_start_writes_error_feeds(self):
        combined = relay.CombinedProvider()
        self.addCleanup(combined.close)
        for _, source in combined.providers:
            source.provider = Mock()
            source.provider.fetch.side_effect = relay.RequestFailure(401)
        relay.refresh_once(combined, self.args)
        for filename in [self.args.out, self.args.reuters_out, self.args.marketaux_out]:
            saved = storage.read_json(Path(filename))
            self.assertEqual(saved["items"], [])
            self.assertIsNone(saved["updatedAt"])
            self.assertTrue(saved["meta"]["errors"])

    def test_one_shot_reports_total_collection_failure(self):
        self.args.provider = "marketaux"
        self.args.tab_feeds = False
        self.provider.fetch.side_effect = relay.RequestFailure(401)
        with patch.object(relay, "parse_args", return_value=self.args), patch.object(relay, "build_provider", return_value=self.managed()):
            self.assertEqual(relay.main(), 1)
        self.assertTrue(storage.read_json(Path(self.args.out))["meta"]["errors"])

    def test_successful_empty_feed_clears_old_items(self):
        path = Path(self.args.out)
        path.write_text('{"items":[{"title":"old"}]}')
        relay.write_payload(path, [], {"source_statuses": {"marketaux": {"status":"ok","lastSuccessAt":self.now.isoformat()}}})
        self.assertEqual(storage.read_json(path)["items"], [])

    def test_missing_deepl_key_uses_cached_translation_and_marks_uncached_original(self):
        self.args.translate_provider = "deepl"
        self.args.file_deepl_api_key = ""
        storage.atomic_write_json(Path(self.args.translate_cache), {relay.translation_cache_key(self.item, self.args): "연준 금리 인하"})
        other = dict(self.item, title="Stocks rise")
        items, _ = relay.translate_items([self.item, other], self.args)
        self.assertEqual(items[0]["titleKo"], "연준 금리 인하")
        self.assertEqual(items[1]["titleKo"], "")
        self.assertEqual(items[1]["translationStatus"], "failed")
        self.assertEqual(items[1]["title"], "Stocks rise")

    def test_translation_failure_and_recovery_cache(self):
        self.args.translate_provider = "deepl"
        with patch.object(relay.DeepLTranslator, "translate_item", side_effect=relay.RequestFailure(403)):
            items, _ = relay.translate_items([self.item], self.args)
            self.assertEqual(items[0]["translationStatus"], "failed")
        with patch.object(relay.DeepLTranslator, "translate_item", return_value=relay.TranslationResult("연준 금리 인하", "deepl", False, 10)) as translate:
            relay.translate_items([self.item], self.args)
            items, _ = relay.translate_items([self.item], self.args)
            self.assertEqual(items[0]["translationStatus"], "cached")
            self.assertEqual(translate.call_count, 1)

    def test_rolling_cutoff_moves_but_explicit_cutoff_stays(self):
        provider = Mock()
        provider.fetch.return_value = relay.ProviderResult([], {})
        relay.refresh_once(provider, self.args)
        cutoff = self.args.published_after
        self.clock.return_value += timedelta(days=3)
        relay.refresh_once(provider, self.args)
        self.assertNotEqual(cutoff, self.args.published_after)
        self.assertAlmostEqual((self.clock.return_value - relay.parse_iso_datetime(self.args.published_after)).total_seconds(), 48*3600)
        self.args.explicit_published_after = self.args.published_after
        self.clock.return_value += timedelta(days=3)
        relay.refresh_once(provider, self.args)
        self.assertEqual(self.args.published_after, self.args.explicit_published_after)

    def test_atomic_replace_failure_keeps_previous_valid_file(self):
        path = self.root / "atomic.json"
        storage.atomic_write_json(path, {"old": True})
        with patch.object(storage.os, "replace", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                storage.atomic_write_json(path, {"new": True})
        self.assertEqual(storage.read_json(path), {"old": True})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_legacy_rss_failure_keeps_last_success(self):
        path = self.root / "rss.json"
        storage.atomic_write_json(path, {"items": [{"title": "saved"}]})
        with self.assertRaises(RuntimeError):
            rss_to_json.refresh_once([str(self.root / "missing.xml")], path, 5, 1)
        self.assertEqual(storage.read_json(path)["items"][0]["title"], "saved")

    def test_server_command_carries_custom_outputs(self):
        args = run_overlay.build_parser().parse_args(["--out", "data/custom.json", "--limit", "7", "--provider", "reuters"])
        cmd = run_overlay.build_server_cmd(args)
        self.assertIn("data/custom.json", cmd)
        self.assertIn("--no-tabs", cmd)
        self.assertEqual(args.marketaux_interval, 15)

    def test_shutdown_uses_portable_process_methods(self):
        proc = Mock()
        proc.poll.return_value = None
        run_overlay.terminate_process(proc)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()


class ServerTests(unittest.TestCase):
    def test_only_public_assets_and_explicit_feeds_are_served(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keys").mkdir()
            (root / "keys/marketaux_token.txt").write_text("fixture-secret")
            (root / ".env").write_text("fixture-secret")
            (root / "index.html").write_text("widget")
            (root / "custom.json").write_text('{"items":[]}')
            (root / "styles.css").symlink_to(root / "keys/marketaux_token.txt")
            server = serve_overlay.build_server("127.0.0.1", 0, root, {"/data/news.live.json":root/"custom.json"}, {"maxItems":7,"tabsEnabled":False})
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for method in ["GET", "HEAD"]:
                    for path, expected in [("/",200),("/data/news.live.json",200),("/widget-runtime.js",200),
                        ("/keys/marketaux_token.txt",404),("/keys/massive_api_key.txt",404),("/.env",404),("/data/",404),("/tools/news_relay.py",404),
                        ("/data/translate_cache.ko.json",404),("/data/.relay-cache/marketaux.json",404),
                        ("/%2e%2e/keys/marketaux_token.txt",404),("/styles.css",404)]:
                        with self.subTest(method=method,path=path):
                            conn = http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=2)
                            conn.request(method,path)
                            response = conn.getresponse()
                            body = response.read()
                            self.assertEqual(response.status, expected)
                            self.assertNotIn(b"fixture-secret", body)
                            self.assertEqual(response.getheader("Cache-Control"),"no-store")
                            if path == "/widget-runtime.js" and method == "GET": self.assertIn(b'"maxItems": 7',body)
                            conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
