from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from test_pipeline import serve_overlay, storage
from tools import korean_links as links

RSS = b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:sy="http://purl.org/rss/1.0/modules/syndication/"><channel>
<sy:updatePeriod>hourly</sy:updatePeriod><sy:updateFrequency>1</sy:updateFrequency>
<item><title>AMD 13% gain</title><link>https://kr.benzinga.com/news/usa/stocks/example/</link>
<pubDate>Tue, 10 Nov 2026 06:59:12 +0000</pubDate><content:encoded><![CDATA[<p>NASDAQ: <a href="https://kr.benzinga.com/quote/AMD">AMD</a></p><a href="https://www.benzinga.com/news/26/11/10000099/related-story">Related story</a>]]></content:encoded></item>
<item><title>Bad host</title><link>https://kr.benzinga.com.evil.test/news/usa/stocks/example/</link><pubDate>Tue, 10 Nov 2026 06:59:12 +0000</pubDate></item>
</channel></rss>'''


class KoreanLinksTests(unittest.TestCase):
    def test_rss_metadata_only_and_related_ids_are_never_used_as_identity(self):
        items, interval = links.parse_feed(RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['symbols'], ['AMD'])
        self.assertEqual(interval, 3600)
        self.assertNotIn('10000099', json.dumps(items))
        self.assertEqual(set(items[0]), {'title','url','publishedAt','symbols'})

    def test_url_validation_rejects_impostors_credentials_and_nonarticle_paths(self):
        for url in ['https://kr.benzinga.com/', 'https://evil.test/news/usa/article/',
                    'https://kr.benzinga.com.evil.test/news/usa/article/',
                    'http://kr.benzinga.com/news/usa/article/',
                    'https://user:pass@kr.benzinga.com/news/usa/article/',
                    'https://kr.benzinga.com:8787/news/usa/article/', None]:
            with self.subTest(url=url):
                self.assertEqual(links.korean_article_url(url), '')
        self.assertIn('%', links.korean_article_url('https://kr.benzinga.com/news/usa/기사/'))

    def test_pending_refresh_is_nonblocking_coalesced_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            index = links.KoreanArticleIndex(Path(directory)/'cache.json')
            started, release = threading.Event(), threading.Event()
            item = links.parse_feed(RSS)[0][0]
            item['publishedAt'] = datetime.now(timezone.utc).isoformat()
            def fetch():
                started.set()
                release.wait(2)
                return [item],3600
            try:
                with patch.object(links, 'fetch_index', side_effect=fetch) as request:
                    self.assertEqual(index.snapshot()['status'], 'loading')
                    self.assertTrue(started.wait(1))
                    self.assertEqual(index.snapshot()['status'], 'loading')
                    self.assertEqual(request.call_count, 1)
                    release.set()
                    index.future.result(timeout=2)
                    self.assertEqual(index.snapshot()['items'], [item])
                    self.assertEqual(index.snapshot()['status'], 'ok')
                    self.assertEqual(request.call_count, 1)
                    self.assertEqual(storage.read_json(index.path)['items'], [item])
            finally:
                release.set()
                index.close()

    def test_source_failure_retains_cache_and_respects_retry_after(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cache.json'
            items=links.parse_feed(RSS)[0]
            storage.atomic_write_json(path, {'items':items})
            index=links.KoreanArticleIndex(path)
            try:
                failure=HTTPError(links.FEED_URL,429,'rate limit',{'Retry-After':'7200'},None)
                with patch.object(links,'fetch_index',side_effect=failure) as request:
                    index.snapshot()
                    try:index.future.result(timeout=1)
                    except HTTPError:pass
                    result=index.snapshot()
                    self.assertEqual(result['status'],'unavailable')
                    self.assertEqual(result['items'],items)
                    self.assertGreater(index.next_check-links.time.time(),7190)
                    index.snapshot()
                    self.assertEqual(request.call_count,1)
            finally:index.close()

    def test_endpoint_is_fixed_source_and_private_cache_remains_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            server=serve_overlay.build_server('127.0.0.1',0,root,{}, {})
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                with patch.object(links,'fetch_index',return_value=([],3600)) as request:
                    with urlopen(base+'/api/benzinga-korean?url=http://127.0.0.1/private') as response:
                        self.assertIn('items',json.load(response))
                    server.korean_index.future.result(timeout=1)
                    request.assert_called_once_with()
                with self.assertRaises(HTTPError):
                    urlopen(base+'/data/.relay-cache/benzinga-korean-links.json')
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)


if __name__ == '__main__':unittest.main()
