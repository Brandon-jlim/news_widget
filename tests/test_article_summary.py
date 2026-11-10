import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit

from test_pipeline import relay, serve_overlay, storage, run_overlay
from tools import article_summary as summary

FETCH_MASSIVE = summary.fetch_massive_article


class ArticleSummaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.feed = self.root / 'feed.json'
        self.item = {'id': 'benzinga:123', 'title': 'Blinken criticizes nuclear deal exit',
                     'titleKo': '블링컨, 핵합의 탈퇴 비판', 'url': 'https://www.benzinga.com/news/123/story',
                     'publishedAt': '2026-11-10T07:34:38Z', 'snippet': 'Blinken warned further escalation would be a disaster.'}
        self.write_feed()
        self.args = SimpleNamespace(massive_token='fixture', translate_provider='deepl')
        self.parse = patch.object(summary.relay, 'parse_args', side_effect=lambda *_: SimpleNamespace(**vars(self.args))).start()
        self.fetch = patch.object(summary, 'fetch_massive_article', return_value={
            'body': '<p>On Monday, Antony Blinken blamed the exit for the crisis.</p><p>He urged diplomacy.</p>'}).start()
        self.translator = Mock()
        self.translator.translate_item.return_value = relay.TranslationResult('블링컨은 탈퇴가 위기의 원인이라고 비판했다.\n\n그는 외교적 해결을 촉구했다.', 'deepl', False, 100)
        patch.object(summary.relay, 'DeepLTranslator', return_value=self.translator).start()
        self.addCleanup(patch.stopall)
        self.service = summary.ArticleSummaryService(self.root / 'private.json', {'/data/feed.json': self.feed})
        self.addCleanup(self.service.close)

    def write_feed(self):
        storage.atomic_write_json(self.feed, {'items': [self.item]})

    def open(self):
        return self.service.start(self.item['id'], self.item['url'])

    def wait(self, job):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = self.service.snapshot(job)
            if value['status'] != 'pending':
                return value
            time.sleep(.005)
        self.fail('Article request did not finish')

    def test_body_is_translated_with_attribution_and_private_fields_are_not_returned(self):
        result = self.wait(self.open()['job'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['kind'], 'body')
        args = self.translator.translate_item.call_args
        self.assertIn('Antony Blinken', args.args[0]['title'])
        self.assertIn('\n\n', args.args[0]['title'])
        self.assertTrue(args.kwargs['preserve_paragraphs'])
        self.assertIn('speaker', args.args[1].translate_instructions)
        self.assertNotIn('excerpt', result)
        self.assertNotIn('article', result)

    def test_simultaneous_opens_reuse_one_job_and_persist_across_restart_and_pinning(self):
        entered, release = threading.Event(), threading.Event()
        def translate(*args, **kwargs):
            entered.set()
            release.wait(2)
            return relay.TranslationResult('블링컨은 외교적 해결을 촉구했다.', 'deepl', False, 100)
        self.translator.translate_item.side_effect = translate
        first = self.open()
        self.assertTrue(entered.wait(1))
        second = self.open()
        self.assertEqual(first['job'], second['job'])
        release.set()
        self.assertEqual(self.wait(first['job'])['status'], 'ready')
        self.assertEqual(self.open()['status'], 'ready')
        self.service.close()
        storage.atomic_write_json(self.feed, {'items': []})
        restored = summary.ArticleSummaryService(self.service.path, self.service.feeds)
        try:
            self.assertEqual(restored.start(self.item['id'], self.item['url'])['status'], 'ready')
        finally:
            restored.close()
        self.fetch.assert_called_once()
        self.translator.translate_item.assert_called_once()

    def test_updated_story_invalidates_old_brief(self):
        first = self.open()
        self.wait(first['job'])
        self.item['updatedAt'] = '2026-11-10T08:00:00Z'
        self.write_feed()
        second = self.open()
        self.assertNotEqual(first['job'], second['job'])
        self.wait(second['job'])
        self.assertEqual(self.translator.translate_item.call_count, 2)

    def test_teaser_and_headline_only_are_labelled_honestly(self):
        self.fetch.return_value = {'teaser': 'Blinken urged diplomacy.'}
        self.assertEqual(self.wait(self.open()['job'])['kind'], 'teaser')
        self.item['id'] = 'benzinga:124'
        self.item['snippet'] = 'https://example.test/another-story'
        self.write_feed()
        self.fetch.return_value = {}
        result = self.wait(self.open()['job'])
        self.assertEqual(result['kind'], 'headline')
        self.assertEqual(result['text'], self.item['titleKo'])
        self.assertEqual(self.translator.translate_item.call_count, 1)

    def test_unknown_articles_and_url_mismatches_never_start_paid_work(self):
        self.assertEqual(self.service.start(self.item['id'], 'http://localhost/private')['status'], 'missing')
        self.assertEqual(self.service.start('arbitrary text', self.item['url'])['status'], 'missing')
        self.assertEqual(self.service.snapshot('bad')['status'], 'missing')
        self.fetch.assert_not_called()
        self.translator.translate_item.assert_not_called()

    def test_translation_disabled_does_not_call_deepl(self):
        self.args.translate_provider = 'none'
        self.assertEqual(self.wait(self.open()['job'])['status'], 'error')
        self.translator.translate_item.assert_not_called()

    def test_rejected_mixed_script_is_cached_without_rebilling(self):
        self.translator.translate_item.return_value = relay.TranslationResult('블링컨姆의 경고', 'deepl', False)
        result = self.wait(self.open()['job'])
        self.assertEqual(result['status'], 'review')
        self.assertNotIn('text', result)
        self.assertEqual(self.open()['status'], 'review')
        self.translator.translate_item.assert_called_once()

    def test_quota_backoff_retains_excerpt_and_does_not_rebill_other_articles(self):
        self.translator.translate_item.side_effect = relay.RequestFailure(429, retry_after=3600)
        result = self.wait(self.open()['job'])
        self.assertEqual(result['status'], 'error')
        self.assertGreater(result['retryAt'], time.time() + 3590)
        self.assertEqual(self.open()['status'], 'error')
        self.item['id'] = 'benzinga:125'
        self.write_feed()
        self.assertEqual(self.wait(self.open()['job'])['status'], 'error')
        self.translator.translate_item.assert_called_once()
        self.fetch.assert_called_once()

    def test_body_outage_does_not_claim_no_additional_body(self):
        self.fetch.side_effect = relay.RequestFailure(503)
        self.item['snippet'] = ''
        self.write_feed()
        result = self.wait(self.open()['job'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('본문을 불러오지 못', result['message'])
        self.translator.translate_item.assert_not_called()

    def test_html_extraction_omits_scripts_urls_and_related_links_and_keeps_sentence_boundaries(self):
        body = '<script>steal secrets</script><p><b>Blinken</b> said Iran should not escalate.</p><p>Read Also: unrelated story</p><p>https://example.test</p><p>Photo by someone</p>'
        self.assertEqual(summary.excerpt(body), 'Blinken said Iran should not escalate.')
        text = summary.excerpt('One sentence. ' * 300)
        self.assertLessEqual(len(text), summary.MAX_CHARS)
        self.assertTrue(text.endswith('.'))

    def test_massive_time_filter_requires_exact_id(self):
        # Exercise the actual fixed-host request, never an arbitrary input URL.
        rows = [{'benzinga_id': 124, 'body': 'Other story.'}, {'benzinga_id': 123, 'body': 'Correct story.'}]
        with patch.object(relay, 'request_json', return_value=relay.HttpResponse({'results': rows}, {})) as request:
            self.assertEqual(FETCH_MASSIVE(self.item, self.args)['body'], 'Correct story.')
            url = urlsplit(request.call_args.args[0])
            self.assertEqual(url.hostname, 'api.massive.com')
            self.assertEqual(parse_qs(url.query)['published.lte'], ['2026-11-10T07:34:39+00:00'])
            request.return_value.payload['results'] = rows[:1]
            self.assertEqual(FETCH_MASSIVE(self.item, self.args), {})

    def test_reader_settings_forwarded_to_server(self):
        args = run_overlay.build_parser().parse_args(['--translate-provider', 'none', '--massive-key-file', 'keys/custom.txt'])
        cmd = run_overlay.build_server_cmd(args)
        self.assertEqual(cmd[cmd.index('--translate-provider') + 1], 'none')
        self.assertEqual(cmd[cmd.index('--massive-key-file') + 1], 'keys/custom.txt')

    def test_http_requires_same_origin_json_and_known_feed_article(self):
        server = serve_overlay.build_server('127.0.0.1', 0, self.root, {'/data/feed.json': self.feed}, {})
        server.article_summary.close()
        server.article_summary = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with urlopen(base + '/api/article-summary?url=http://localhost/private') as response:
                self.assertEqual(json.load(response)['status'], 'missing')
            self.fetch.assert_not_called()
            body = json.dumps({'id': self.item['id'], 'url': self.item['url']}).encode()
            for headers in [{'Content-Type': 'text/plain'}, {'Content-Type': 'application/json', 'Origin': 'https://evil.test'}]:
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(base + '/api/article-summary', body, headers))
                self.assertEqual(failure.exception.code, 403)
            with urlopen(Request(base + '/api/article-summary', body, {'Content-Type': 'application/json', 'Origin': base})) as response:
                job = json.load(response)['job']
            self.assertEqual(self.wait(job)['status'], 'ready')
            with urlopen(base + '/api/article-summary?job=' + job) as response:
                self.assertEqual(json.load(response)['kind'], 'body')
            with self.assertRaises(HTTPError):
                urlopen(base + '/data/.relay-cache/article-summaries.ko.json')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
