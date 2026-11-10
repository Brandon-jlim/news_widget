from datetime import timedelta
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import unittest

import test_pipeline as fixture
from test_pipeline import relay, run_overlay, storage


class MassiveTests(unittest.TestCase):
    setUp = fixture.PipelineTests.setUp

    def article(self, **changes):
        return dict({"benzinga_id": 42, "title": "Nvidia&#39;s earnings &amp; guidance",
                     "published": self.now.isoformat(), "last_updated": self.now.isoformat(),
                     "tickers": ["NVDA"], "channels": ["earnings"], "url": "https://example.test/42"}, **changes)

    def response(self, rows=None, **changes):
        return relay.HttpResponse({"status": "OK", "results": rows if rows is not None else [self.article()], **changes}, {})

    def source(self):
        self.args.massive_token = "fixture-massive-secret"
        return relay.ManagedProvider("massive", relay.MassiveNewsProvider())

    def test_headline_only_articles_use_header_auth_and_never_persist_pagination_credentials(self):
        source = self.source()
        self.args.published_after = "2026-11-09T21:00"
        with patch.object(relay, "request_json", return_value=self.response(next_url="https://example.test/?apiKey=fixture-massive-secret")) as request:
            result = source.fetch(self.args)
        url = request.call_args.args[0]
        self.assertEqual(urlsplit(url).netloc, "api.massive.com")
        self.assertNotIn(self.args.massive_token, url)
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer " + self.args.massive_token})
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["sort"], ["published.desc"])
        self.assertEqual(query["published.gte"], ["2026-11-09T21:00:00+00:00"])
        self.assertNotIn("body", query["fields"][0].split(","))
        self.assertEqual(result.items[0]["title"], "Nvidia's earnings & guidance")
        self.assertEqual(result.items[0]["id"], "benzinga:42")
        self.assertEqual(result.items[0]["_source_provider"], "benzinga")
        self.assertEqual(result.items[0]["source"], "Benzinga")
        self.assertEqual(result.meta["source_statuses"]["massive"]["group"], "benzinga")
        self.assertTrue(result.meta["moreAvailable"])
        state = Path(self.args.state_dir, "massive.json").read_text()
        self.assertNotIn(self.args.massive_token, state)
        self.assertNotIn("next_url", state)
        self.assertEqual(request.call_count, 1)

    def test_updates_keep_original_time_and_first_seen_and_restart_obeys_poll_deadline(self):
        source = self.source()
        with patch.object(relay, "request_json", return_value=self.response()) as request:
            first = source.fetch(self.args)
            self.clock.return_value += timedelta(seconds=9)
            self.source().fetch(self.args)
            self.assertEqual(request.call_count, 1)
            self.clock.return_value += timedelta(seconds=2)
            request.return_value = self.response([self.article(title="Nvidia raises earnings outlook", last_updated=self.clock.return_value.isoformat())])
            updated = self.source().fetch(self.args)
        self.assertEqual(updated.items[0]["publishedAt"], first.items[0]["publishedAt"])
        self.assertEqual(updated.items[0]["firstSeenAt"], first.items[0]["firstSeenAt"])
        self.assertNotEqual(updated.items[0]["updatedAt"], first.items[0]["updatedAt"])
        self.assertEqual(updated.items[0]["title"], "Nvidia raises earnings outlook")

    def test_malformed_response_preserves_snapshot_and_rate_limit_does_not_retry(self):
        source = self.source()
        with patch.object(relay, "request_json", return_value=self.response()) as request:
            first = source.fetch(self.args)
            self.clock.return_value += timedelta(seconds=11)
            request.return_value = relay.HttpResponse({"status": "OK", "results": {}}, {})
            failed = source.fetch(self.args)
            self.assertEqual(failed.items, first.items)
            self.assertEqual(failed.meta["source_statuses"]["massive"]["status"], "error")
            self.clock.return_value += timedelta(seconds=301)
            request.side_effect = relay.RequestFailure(429, retry_after=120)
            limited = source.fetch(self.args)
            deadline = relay.parse_iso_datetime(limited.meta["source_statuses"]["massive"]["nextFetchAt"])
            self.assertEqual(deadline, self.clock.return_value + timedelta(seconds=120))
            self.assertEqual(request.call_count, 3)

    def test_empty_success_clears_news_but_auth_error_retains_snapshot_without_secret(self):
        source = self.source()
        with patch.object(relay, "request_json", return_value=self.response()) as request:
            first = source.fetch(self.args)
            self.clock.return_value += timedelta(seconds=11)
            request.side_effect = relay.RequestFailure(403)
            failed = source.fetch(self.args)
            self.assertEqual(failed.items, first.items)
            self.clock.return_value += timedelta(hours=1, seconds=1)
            request.side_effect = None
            request.return_value = self.response([])
            self.assertEqual(source.fetch(self.args).items, [])
        self.assertNotIn(self.args.massive_token, relay.safe_error(Exception("Bearer " + self.args.massive_token), self.args))

    def test_combined_prefers_massive_and_routes_it_to_benzinga_tab(self):
        self.args.no_marketaux = True
        self.args.no_reuters_default_feed = True
        self.args.no_official_feeds = True
        self.args.no_public_rss = True
        self.args.massive_token = "fixture-massive-secret"
        self.args.benzinga_token = "fixture-direct-secret"
        source = relay.CombinedProvider(self.args)
        try:
            self.assertEqual([name for name, _ in source.providers], ["massive"])
            with patch.object(relay, "request_json", return_value=self.response()):
                result = source.fetch(self.args)
                relay.write_tab_feeds(result, result.items, self.args)
            for path in [self.args.out, self.args.benzinga_out]:
                payload = storage.read_json(Path(path))
                self.assertEqual(payload["items"][0]["id"], "benzinga:42")
                self.assertEqual(set(payload["meta"]["source_statuses"]), {"massive"})
                self.assertNotIn(self.args.massive_token, json.dumps(payload))
        finally:
            source.close()

    def test_launcher_forwards_massive_options_without_key_content(self):
        args = run_overlay.build_parser().parse_args(["--massive-key-file", "/tmp/key.txt", "--massive-interval", "15", "--no-marketaux"])
        cmd = run_overlay.build_relay_cmd(args)
        self.assertEqual(cmd[cmd.index("--massive-key-file") + 1], "/tmp/key.txt")
        self.assertEqual(cmd[cmd.index("--massive-interval") + 1], "15")
        self.assertIn("--no-marketaux", cmd)


if __name__ == "__main__":
    unittest.main()
