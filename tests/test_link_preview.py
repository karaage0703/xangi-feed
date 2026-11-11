import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from xangi_feed.link_preview import LinkPreviews, metadata, fetch_public, IMAGE_LIMIT, HTML_LIMIT
from xangi_feed.store import Store
from xangi_feed.server import Server

HTML = b'''<html><head><meta property="og:title" content="Preview title"><meta property="og:site_name" content="Example"><meta property="og:description" content="Description"><meta property="og:image" content="/card.png"><meta property="og:image" content="/second.png"></head></html>'''
PNG = b'\x89PNG\r\n\x1a\nfixture'


class LinkPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'feed.db', seed=False)
        self.item, _ = self.store.add_article({'url':'https://example.org/article', 'title':'Original title'})
        self.fetcher = Mock(side_effect=lambda url, maximum: PNG if url.endswith('.png') else HTML)
        self.previews = LinkPreviews(self.store, self.fetcher)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.addCleanup(self.previews.close)

    def wait(self):
        deadline=time.monotonic()+3
        while self.previews.pending and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(self.previews.pending)

    def test_og_metadata_and_fallbacks_are_text_and_valid_urls(self):
        result=metadata(HTML,'https://example.org/a')
        self.assertEqual(result['image_url'],'https://example.org/card.png')
        self.assertEqual(result['site_name'],'Example')
        result=metadata(b'<title>Fallback</title><meta name="twitter:image" content="javascript:alert(1)">','https://example.org/a')
        self.assertEqual(result['title'],'Fallback')
        self.assertEqual(result['image_url'],'')
        self.assertEqual(result['site_name'],'example.org')

    def test_default_on_background_cache_persistence_and_no_read_effect(self):
        self.assertTrue(self.store.settings()['show_link_previews'])
        self.assertEqual(self.previews.request(self.item['id'])['status'],'pending'); self.wait()
        cached=self.previews.request(self.item['id'])
        self.assertTrue(cached['has_image']);self.assertEqual(cached['title'],'Preview title')
        self.assertEqual(self.previews.image(self.item['id']),(PNG,'image/png'))
        self.assertEqual(self.fetcher.call_count,2)
        self.assertFalse(self.store.item(self.item['id'])['read'])
        self.assertEqual(self.store.item(self.item['id'])['rating'],0)
        reopened=LinkPreviews(self.store,Mock())
        try:
            self.assertEqual(reopened.request(self.item['id'])['title'],'Preview title')
            reopened.fetcher.assert_not_called()
        finally:reopened.close()

    def test_off_persists_and_prevents_metadata_and_image_retrieval(self):
        for value in [1,'true',None]:
            with self.assertRaises(ValueError):self.store.save_settings({'show_link_previews':value})
        self.store.save_settings({'show_link_previews':False})
        self.assertEqual(self.previews.request(self.item['id']),{'status':'disabled'})
        self.fetcher.assert_not_called()
        with self.assertRaises(KeyError):self.previews.image(self.item['id'])
        reopened=Store(self.store.path,seed=False)
        try:self.assertFalse(reopened.settings()['show_link_previews'])
        finally:reopened.close()

    def test_failed_image_keeps_card_and_failed_page_is_negatively_cached(self):
        self.fetcher.side_effect=lambda url, maximum: b'<svg onload="bad()"/>' if url.endswith('.png') else HTML
        self.previews.request(self.item['id']);self.wait()
        self.assertFalse(self.previews.request(self.item['id'])['has_image'])
        self.assertEqual(self.previews.request(self.item['id'])['status'],'done')
        with self.store.transaction() as db:db.execute('DELETE FROM link_previews')
        self.fetcher.side_effect=TimeoutError('private-token')
        self.previews.request(self.item['id']);self.wait()
        result=self.previews.request(self.item['id'])
        self.assertEqual(result['status'],'error');self.assertNotIn('private-token',json.dumps(result))
        count=self.fetcher.call_count;self.previews.request(self.item['id']);self.assertEqual(self.fetcher.call_count,count)

    def test_queue_deduplicates_and_uses_bounded_public_fetch(self):
        release=threading.Event(); started=threading.Event()
        self.addCleanup(release.set)
        def fetch(url,maximum):started.set();release.wait(2);return HTML
        self.fetcher.side_effect=fetch
        self.previews.request(self.item['id']);self.assertTrue(started.wait(2))
        for _ in range(5):self.previews.request(self.item['id'])
        self.assertEqual(self.fetcher.call_count,1)
        release.set();self.wait()
        with patch('xangi_feed.feeds.public_address',side_effect=ValueError('private network')) as check:
            with self.assertRaises(ValueError):fetch_public('http://127.0.0.1/image',IMAGE_LIMIT)
            check.assert_called_once()

    def test_authenticated_api_and_same_origin_image(self):
        server=Server(self.store,'fixture-token')
        server.previews.fetcher=self.fetcher
        worker=threading.Thread(target=server.serve_forever);worker.start()
        root=f'http://127.0.0.1:{server.server_port}'
        def get(path):return urlopen(Request(root+path,headers={'Authorization':'Bearer fixture-token'}))
        try:
            with self.assertRaises(HTTPError) as error:urlopen(root+f'/api/items/{self.item["id"]}/preview')
            self.assertEqual(error.exception.code,401)
            with get(f'/api/items/{self.item["id"]}/preview') as response:self.assertEqual(json.load(response)['status'],'pending')
            deadline=time.monotonic()+3
            while server.previews.pending and time.monotonic()<deadline:time.sleep(.01)
            with get(f'/api/items/{self.item["id"]}/preview/image') as response:
                self.assertEqual(response.headers['Content-Type'],'image/png');self.assertEqual(response.read(),PNG)
                self.assertIn("img-src 'self'",response.headers['Content-Security-Policy'])
            self.store.save_settings({'show_link_previews':False})
            with get(f'/api/items/{self.item["id"]}/preview') as response:self.assertEqual(json.load(response)['status'],'disabled')
            with self.assertRaises(HTTPError) as error:get(f'/api/items/{self.item["id"]}/preview/image')
            self.assertEqual(error.exception.code,404)
        finally:server.shutdown();server.server_close();worker.join()

    def test_switching_off_during_fetch_stops_image_stage(self):
        started=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def fetch(url, maximum):
            started.set();release.wait(2);return HTML
        self.fetcher.side_effect=fetch
        self.previews.request(self.item['id']);self.assertTrue(started.wait(2))
        self.store.save_settings({'show_link_previews':False});release.set();self.wait()
        self.assertEqual(self.fetcher.call_count,1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM link_previews').fetchone()[0],0)

    def test_batch_cached_metadata_is_fresh_and_does_not_read_image_blobs(self):
        self.previews.request(self.item['id']);self.wait()
        queries=[];self.store.db.set_trace_callback(queries.append)
        try:
            cached=self.previews.cached_for([self.item])
        finally:self.store.db.set_trace_callback(None)
        self.assertTrue(cached[self.item['id']]['has_image'])
        self.assertEqual(sum('FROM link_previews' in q for q in queries),1)
        self.assertFalse(any('SELECT image' in q for q in queries))
        with patch.object(self.store,'item',side_effect=AssertionError('full article not needed')):
            self.assertEqual(self.previews.request(self.item['id'])['status'],'done')
            self.assertEqual(self.previews.image(self.item['id'])[0],PNG)
        with self.store.transaction() as db:db.execute('UPDATE link_previews SET updated=0')
        self.assertEqual(self.previews.cached_for([self.item]),{})
        self.store.save_settings({'show_link_previews':False})
        self.assertEqual(self.previews.cached_for([self.item]),{})

    def test_cached_metadata_is_in_list_and_detail_without_starting_fetch(self):
        server=Server(self.store,'fixture-token')
        server.previews.fetcher=self.fetcher
        server.previews.request(self.item['id'])
        deadline=time.monotonic()+3
        while server.previews.pending and time.monotonic()<deadline:time.sleep(.01)
        worker=threading.Thread(target=server.serve_forever);worker.start()
        root=f'http://127.0.0.1:{server.server_port}'
        try:
            for route in ['/api/items',f'/api/items/{self.item["id"]}']:
                with urlopen(Request(root+route,headers={'Authorization':'Bearer fixture-token'})) as response:data=json.load(response)
                item=data['items'][0] if 'items' in data else data
                self.assertEqual(item['link_preview']['status'],'done')
                self.assertTrue(item['link_preview']['has_image'])
            self.assertEqual(self.fetcher.call_count,2)
        finally:server.shutdown();server.server_close();worker.join()

    def test_html_fetch_finishes_at_split_head_closing_tag_but_feed_fetch_does_not(self):
        from xangi_feed.feeds import _retrieve
        for head_only in [True,False]:
            response=Mock(status=200)
            response.read1.side_effect=[b'<html><head><title>Title</title></HE',b'AD><body>large',b' body</body></html>',b'']
            connection=Mock();connection.getresponse.return_value=response
            with patch('xangi_feed.feeds.public_address',return_value='8.8.8.8'), patch('xangi_feed.feeds.socket.create_connection'), patch('xangi_feed.feeds.http.client.HTTPConnection',return_value=connection):
                value=_retrieve('http://example.org',HTML_LIMIT,head_only=head_only)
            self.assertEqual(response.read1.call_count,2 if head_only else 4)
            self.assertEqual(value.endswith(b'</HEAD>'),head_only)
