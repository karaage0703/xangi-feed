import io
import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from xangi_feed.article import ArticleImporter, parse_article, BODY_LIMIT
from xangi_feed.feeds import fetch_feed, download_file
from xangi_feed.store import Store

PAGE = '<html><head><title>記事のタイトル</title><meta property="article:published_time" content="2026-11-07T01:00:00Z"></head><body><nav>不要なナビ</nav><main><article><h1>見出し</h1><p>ロボットの本文です。</p><script>秘密の命令</script><p hidden>非表示</p><p>次の段落です。</p></article></main><footer>不要なフッター</footer></body></html>'.encode()
VIDEO='F55l_MtWP-g'


class TransportTests(unittest.TestCase):
    def test_successful_feed_and_streamed_download_do_not_confuse_http_path_with_file(self):
        for target in [None, 'file']:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)/'audio.mp3'
                response=io.BytesIO(b'actual response bytes'); response.status=200
                connection=MagicMock();connection.getresponse.return_value=response
                with patch('xangi_feed.feeds.public_address',return_value='93.184.216.34'),patch('xangi_feed.feeds.socket.create_connection'),patch('xangi_feed.feeds.http.client.HTTPConnection',return_value=connection):
                    if target is None:
                        self.assertEqual(fetch_feed('http://example.org/feed?q=1'),b'actual response bytes')
                    else:
                        download_file('http://example.org/feed?q=1',output,100)
                        self.assertEqual(output.read_bytes(),b'actual response bytes')
                    self.assertEqual(connection.request.call_args.args[:2],('GET','/feed?q=1'))


class DirectFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'feed.db'
        self.store=Store(self.path,seed=False)
        self.importer=ArticleImporter(self.store,lambda url:PAGE)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_url_only_extracts_title_body_date_kind_and_survives_restart(self):
        item,created=self.importer.add({'url':'https://example.org/article'})
        self.assertTrue(created)
        self.assertEqual(item['title'],'記事のタイトル')
        self.assertEqual(item['kind'],'blog')
        self.assertEqual(item['content_text'],'見出し\nロボットの本文です。\n次の段落です。')
        self.assertIsNotNone(item['published'])
        self.assertFalse(item['fetch_error'])
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertEqual(self.store.item(item['id'])['content_text'],item['content_text'])

    def test_readd_old_url_only_backfills_and_preserves_user_fields(self):
        old,_=self.store.add_article({'url':'https://example.org/article'})
        self.store.save_note(old['id'],'大事なメモ')
        self.store.react(old['id'],{'rating':1,'saved':True,'read':True})
        self.store.change_tag(old['id'],'add','調べる')
        item,created=self.importer.add({'url':'https://example.org/article#position'})
        self.assertFalse(created)
        self.assertEqual(item['id'],old['id'])
        self.assertEqual(item['note'],'大事なメモ')
        self.assertEqual((item['rating'],item['saved'],item['read']),(1,1,1))
        self.assertEqual(item['tags'],['調べる'])
        self.assertTrue(item['content_text'])

    def test_user_title_and_excerpt_and_existing_rss_are_not_overwritten(self):
        item,_=self.importer.add({'url':'https://example.org/article','title':'自分のタイトル','summary':'入力した抜粋'})
        self.assertEqual((item['title'],item['summary']),('自分のタイトル','入力した抜粋'))
        source=self.store.source(self.store.add_source('RSS','https://example.org/feed','rss'))
        self.store.ingest(source,[{'title':'RSS記事','url':'https://example.org/rss-article','summary':'RSS抜粋','published':None,'topics':[]}])
        self.importer.fetcher=MagicMock(side_effect=AssertionError())
        item,created=self.importer.add({'url':'https://example.org/rss-article'})
        self.assertFalse(created);self.assertEqual(item['title'],'RSS記事')
        self.importer.fetcher.assert_not_called()
        self.assertRaises(ValueError,self.importer.fetch,item['id'])

    def test_failure_is_visible_and_retry_recovers_without_losing_old_body(self):
        self.importer.fetcher=MagicMock(side_effect=TimeoutError('private detail'))
        item,_=self.importer.add({'url':'https://example.org/article'})
        self.assertTrue(item['fetch_error'])
        self.assertNotIn('private detail',item['fetch_error'])
        self.assertEqual(item['content_text'],'')
        self.importer.fetcher=lambda url:PAGE
        item=self.importer.fetch(item['id'])
        body=item['content_text'];self.assertFalse(item['fetch_error'])
        self.importer.fetcher=MagicMock(side_effect=TimeoutError())
        item=self.importer.fetch(item['id'])
        self.assertEqual(item['content_text'],body)
        self.assertTrue(item['fetch_error'])

    def test_encoding_and_captured_body_limit(self):
        fields=parse_article('<meta charset="shift_jis"><title>日本語</title><article>本文</article>'.encode('shift_jis'),'https://example.org/')
        self.assertEqual((fields['title'],fields['body']),('日本語','本文'))
        self.importer.fetcher=lambda url:('<title>Long</title><article>'+('本文\n'*20000)+'</article>').encode()
        item,_=self.importer.add({'url':'https://example.org/long'})
        self.assertEqual(len(item['content_text']),BODY_LIMIT)

    def test_youtube_uses_video_details_and_captions_instead_of_footer(self):
        details={'videoId':VIDEO,'title':'動画タイトル','shortDescription':'動画説明'}
        page=('<title>fallback</title><script>var player={"videoDetails":'+json.dumps(details)+'};</script><body>YouTube footer</body>').encode()
        self.importer.fetcher=lambda url:page
        def captions(url,output,**kwargs):
            output.write_text('字幕の本文です。',encoding='utf-8')
            return True
        with patch('xangi_feed.article.captions',side_effect=captions):
            item,_=self.importer.add({'url':'https://youtu.be/'+VIDEO})
        self.assertEqual((item['title'],item['kind'],item['content_kind']),('動画タイトル','video','youtube_captions'))
        self.assertEqual(item['content_text'],'字幕の本文です。')
        with patch('xangi_feed.article.captions',return_value=False):
            item=self.importer.fetch(item['id'])
        self.assertEqual(item['content_kind'],'youtube_captions')
        self.assertEqual(item['content_text'],'字幕の本文です。')
        self.assertIn('字幕がない',item['fetch_error'])
        with patch('xangi_feed.article.captions') as capture:
            item=self.importer.fetch(item['id'])
            capture.assert_called_once()
            self.assertEqual(item['kind'],'video')

    def test_background_fetch_and_interrupted_restart(self):
        item,_=self.store.add_article({'url':'https://example.org/async'})
        started,finish=threading.Event(),threading.Event()
        def delayed(url): started.set();finish.wait(3);return PAGE
        self.importer.fetcher=delayed
        self.assertEqual(self.importer.start(item['id'])['fetch_state'],'fetching')
        self.assertTrue(started.wait(1))
        self.assertEqual(self.importer.start(item['id'])['fetch_state'],'fetching')
        finish.set();self.importer.close()
        self.assertEqual(self.store.item(item['id'])['fetch_state'],'done')
        self.store.article_fetch_state(item['id'],'fetching')
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertEqual(self.store.item(item['id'])['fetch_state'],'error')
        self.assertIn('再起動',self.store.item(item['id'])['fetch_error'])


    def test_internal_addresses_are_not_contacted(self):
        self.importer.fetcher=None
        with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('127.0.0.1',80))]),patch('socket.create_connection') as connection:
            item,_=self.importer.add({'url':'http://127.0.0.1/private'})
        self.assertTrue(item['fetch_error']);connection.assert_not_called()
