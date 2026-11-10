import argparse
from datetime import timedelta
from email.message import Message
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import test_pipeline as fixture
from test_pipeline import relay, serve_overlay, storage


class LiveSourceTests(unittest.TestCase):
    setUp = fixture.PipelineTests.setUp
    managed = fixture.PipelineTests.managed
    def test_subscription_upgrade_and_downgrade_follow_response_headers(self):
        source = self.managed()
        free = source.fetch(self.args)
        self.assertGreaterEqual(free.meta['source_statuses']['marketaux']['effectiveIntervalSeconds'], 899)
        self.clock.return_value += timedelta(seconds=901)
        self.provider.fetch.return_value.meta['usage'] = {'daily_limit':'10000','daily_remaining':'9900','rate_limit':'60'}
        paid = source.fetch(self.args)
        self.assertEqual(paid.meta['source_statuses']['marketaux']['effectiveIntervalSeconds'], 15)
        self.clock.return_value += timedelta(seconds=14)
        source.fetch(self.args)
        self.assertEqual(self.provider.fetch.call_count, 2)
        self.clock.return_value += timedelta(seconds=1)
        self.provider.fetch.return_value.meta['usage']['daily_limit'] = '100'
        free_again = source.fetch(self.args)
        self.assertGreaterEqual(free_again.meta['source_statuses']['marketaux']['effectiveIntervalSeconds'], 899)

    def test_minute_quota_and_unknown_quota_are_conservative(self):
        source = self.managed()
        self.assertGreaterEqual(source.interval(self.args, {}), 899)
        self.assertEqual(source.interval(self.args, {'daily_limit':'10000', 'rate_limit':'2'}), 32)

    def test_us_broadcast_filter_keeps_megacaps_and_geopolitics_without_unrelated_fallback(self):
        examples = [('Nvidia announces new chips', True), ('Iran launches missile attack', True),
                    ('Federal Reserve leaves rates unchanged', True), ('Film festival presents award', False),
                    ('Garden soil wins a local award', False)]
        for title, wanted in examples:
            article = relay.MarketauxProvider().normalize_article({'title':title,'published_at':self.now.isoformat()}, self.args)
            self.assertEqual(bool(relay.apply_broad_market_filter([article], self.args)), wanted, title)

    def test_official_conditional_304_reuses_original_publication_time(self):
        spec = dict(relay.OFFICIAL_FEEDS[0])
        source = relay.OfficialFeedProvider(spec)
        response = io.BytesIO(b'<rss><channel><title>Federal Reserve</title><item><title>FOMC statement</title><link>https://example.test/fomc</link><pubDate>Mon, 09 Nov 2026 18:00:00 GMT</pubDate></item></channel></rss>')
        response.headers = {'ETag':'"v1"'}
        with patch.object(relay, 'urlopen', return_value=response):
            first = source.fetch(self.args)
        error = HTTPError(spec['url'], 304, 'Not Modified', Message(), io.BytesIO())
        with patch.object(relay, 'urlopen', side_effect=error) as get:
            second = source.fetch(self.args)
        self.assertEqual(get.call_args.args[0].get_header('If-none-match'), '"v1"')
        self.assertEqual(first.items, second.items)
        self.assertEqual(second.items[0]['_source_provider'], 'official')
        self.assertEqual(second.items[0]['source'], spec['label'])
        self.assertEqual(second.items[0]['publishedAt'], '2026-11-09T18:00:00+00:00')

    def test_official_403_preserves_snapshot_and_backs_off(self):
        source = relay.ManagedProvider('bls', relay.OfficialFeedProvider(relay.OFFICIAL_FEEDS[2]))
        source.provider.fetch = Mock(return_value=relay.ProviderResult([dict(self.item, _source_provider='official')], {}))
        source.fetch(self.args)
        self.clock.return_value += timedelta(seconds=61)
        source.provider.fetch.side_effect = relay.RequestFailure(403)
        result = source.fetch(self.args)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.meta['source_statuses']['bls']['group'], 'official')
        self.assertEqual(relay.parse_iso_datetime(result.meta['source_statuses']['bls']['nextFetchAt']), self.clock.return_value + timedelta(hours=1))

    def test_public_rss_keeps_publisher_and_honors_ttl_across_restart(self):
        spec = dict(relay.PUBLIC_RSS_FEEDS[2])
        source = relay.ManagedProvider(spec['id'], relay.PublicRSSFeedProvider(spec))
        response = io.BytesIO(b'<rss><channel><ttl>60</ttl><item><title>Nvidia announces earnings</title><link>https://example.test/pr</link><pubDate>Mon, 09 Nov 2026 23:00:00 GMT</pubDate></item></channel></rss>')
        response.headers = {}
        with patch.object(relay, 'urlopen', return_value=response):
            first = source.fetch(self.args)
        item = first.items[0]
        self.assertEqual(item['source'], spec['label'])
        self.assertEqual(item['_source_provider'], 'rss')
        self.assertFalse(item['official'])
        self.assertEqual(item['contentKind'], 'press_release')
        self.assertEqual(first.meta['source_statuses'][spec['id']]['effectiveIntervalSeconds'], 3600)
        self.clock.return_value += timedelta(seconds=121)
        with patch.object(relay, 'urlopen') as request:
            restarted = relay.ManagedProvider(spec['id'], relay.PublicRSSFeedProvider(spec)).fetch(self.args)
        request.assert_not_called()
        self.assertEqual(restarted.items, first.items)
        self.assertEqual(restarted.meta['source_statuses'][spec['id']]['group'], 'rss')

    def test_press_release_boilerplate_does_not_turn_unrelated_promo_into_market_news(self):
        item = dict(self.item, contentKind='press_release', title='Travel app launches a holiday promotion',
                    snippet='Forward-looking statements: inflation, Nvidia, Federal Reserve, earnings risks.')
        self.assertEqual(relay.apply_broad_market_filter([item], self.args), [])
        item['title'] = 'Nvidia raises earnings guidance'
        self.assertEqual(relay.apply_broad_market_filter([item], self.args), [item])

    def test_public_rss_reaches_combined_and_its_own_tab_with_independent_health(self):
        item = dict(self.item, id='rss', url='https://example.test/pr', _source_provider='rss',
                    contentKind='press_release', title='Nvidia raises earnings guidance')
        result = relay.ProviderResult([item], {'provider':'combined', 'source_statuses':{
            'prn_finance':{'status':'ok','group':'rss','lastSuccessAt':self.now.isoformat()},
            'bea':{'status':'error','group':'official','error':'fixture'}}})
        relay.write_tab_feeds(result, result.items, self.args)
        rss = storage.read_json(Path(self.args.rss_out))
        combined = storage.read_json(Path(self.args.out))
        self.assertEqual([x['id'] for x in rss['items']], ['rss'])
        self.assertEqual([x['id'] for x in combined['items']], ['rss'])
        self.assertEqual(set(rss['meta']['source_statuses']), {'prn_finance'})

    def test_old_official_release_only_appears_in_official_archive(self):
        old = dict(self.item, id='official', url='https://example.test/official', _source_provider='official',
                   official=True, publishedAt=(self.now-timedelta(days=3)).isoformat())
        result = relay.ProviderResult([self.item, old], {'provider':'combined', 'source_statuses':{
            'marketaux':{'status':'ok','lastSuccessAt':self.now.isoformat()},
            'bea':{'status':'ok','group':'official','lastSuccessAt':self.now.isoformat()}}})
        self.args.published_after = (self.now-timedelta(days=2)).isoformat()
        relay.write_tab_feeds(result, result.items, self.args)
        combined = storage.read_json(Path(self.args.out))
        official = storage.read_json(Path(self.args.official_out))
        self.assertEqual([x['id'] for x in combined['items']], ['m'])
        self.assertEqual([x['id'] for x in official['items']], ['official'])
        self.assertEqual(set(official['meta']['source_statuses']), {'bea'})

    def test_cached_items_age_out_without_an_upstream_request(self):
        self.managed().fetch(self.args)
        self.args.published_after = (self.now+timedelta(hours=1)).isoformat()
        result = self.managed().fetch(self.args)
        self.assertEqual(result.items, [])
        self.assertEqual(self.provider.fetch.call_count, 1)

    def test_no_tabs_does_not_put_old_official_archive_in_live_feed(self):
        self.args.tab_feeds = False
        old = dict(self.item, _source_provider='official', official=True,
                   publishedAt=(self.now-timedelta(days=3)).isoformat())
        self.provider.fetch.return_value = relay.ProviderResult([old], {})
        relay.refresh_once(self.provider, self.args)
        self.assertEqual(storage.read_json(Path(self.args.out))['items'], [])

    def test_slow_source_does_not_block_fast_source_in_watch_mode(self):
        combined = relay.CombinedProvider()
        release = threading.Event()
        fast_done = threading.Event()
        self.args.watch = 1
        slow = Mock()
        slow.fetch.side_effect = lambda args: (release.wait(5), relay.ProviderResult([], {}))[1]
        fast = Mock()
        def fetch(args):
            fast_done.set()
            return relay.ProviderResult([self.item], {})
        fast.fetch.side_effect = fetch
        combined.providers = [('slow', slow), ('fast', fast)]
        try:
            started = time.monotonic()
            combined.fetch(self.args)
            self.assertLess(time.monotonic()-started, .5)
            self.assertTrue(fast_done.wait(1))
            # Wait for just the fast future, while the slow source remains blocked.
            combined.pending['fast'].result(timeout=1)
            self.assertEqual(combined.fetch(self.args).items, [self.item])
            self.assertFalse(release.is_set())
        finally:
            release.set()
            combined.close()

    def test_translation_is_incremental_and_reuses_cache(self):
        release = threading.Event()
        self.args.translate_provider = 'deepl'
        translator = relay.BackgroundTranslator(self.args)
        def translate(item, args):
            release.wait(3)
            return relay.TranslationResult('연준 금리 인하', 'deepl', False, 10)
        try:
            with patch.object(relay.DeepLTranslator, 'translate_item', side_effect=translate) as call:
                first, _ = translator.translate([self.item], self.args)
                self.assertEqual(first[0]['translationStatus'], 'pending')
                self.assertEqual(first[0]['title'], 'Fed cuts rates')
                self.assertEqual(first[0]['titleKo'], '')
                release.set()
                translator.future.result(timeout=2)
                second, billed = translator.translate([self.item], self.args)
                self.assertEqual(second[0]['titleKo'], '연준 금리 인하')
                self.assertEqual(billed, 10)
                translator.translate([self.item], self.args)
                self.assertEqual(call.call_count, 1)
        finally:
            release.set()
            translator.close()

    def test_stream_updates_and_deletes_preserve_publication_time(self):
        stream = relay.BenzingaStreamProvider('fixture')
        content = {'id':42,'title':'Nvidia reports earnings','created':'Mon, 09 Nov 2026 12:00:00 -0500',
                   'updated':'Mon, 09 Nov 2026 12:00:01 -0500','url':'https://example.test/42','stocks':[{'name':'NVDA'}]}
        def event(action):
            return json.dumps({'kind':'news','data':{'action':action,'id':42,'content':content}})
        stream.handle_message(event('created'), self.args)
        self.assertEqual(len(stream.items), 1)
        self.assertEqual(stream.items['42']['publishedAt'], '2026-11-09T17:00:00+00:00')
        content['title'] = 'Nvidia corrects earnings outlook'
        content['updated'] = 'Mon, 09 Nov 2026 12:01:00 -0500'
        stream.handle_message(event('updated'), self.args)
        self.assertEqual(stream.items['42']['title'], content['title'])
        self.assertEqual(len(stream.items), 1)
        content['title'] = 'Old replay'
        content['updated'] = 'Mon, 09 Nov 2026 12:00:01 -0500'
        stream.handle_message(event('updated'), self.args)
        self.assertEqual(stream.items['42']['title'], 'Nvidia corrects earnings outlook')
        stream.handle_message(event('deleted'), self.args)
        self.assertEqual(stream.items, {})


class EventServerTests(unittest.TestCase):
    def test_sse_announces_atomic_feed_changes_and_rejects_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root/'feed.json'
            storage.atomic_write_json(feed, {'items':[]})
            server = serve_overlay.build_server('127.0.0.1', 0, root, {'/data/news.live.json':feed}, {})
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                conn.request('GET', '/events?feed=/keys/marketaux_token.txt')
                self.assertEqual(conn.getresponse().status, 404)
                conn.close()
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                conn.request('GET', '/events?feed=/data/news.live.json')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'text/event-stream; charset=utf-8')
                self.assertEqual(response.fp.readline(), b'event: feed\n')
                response.fp.readline(); response.fp.readline()
                storage.atomic_write_json(feed, {'items':[{'title':'new'}]})
                self.assertEqual(response.fp.readline(), b'event: feed\n')
                conn.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
