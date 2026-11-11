import sqlite3
import tempfile
import unittest
import json
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

from xangi_feed.store import Store
from xangi_feed.server import Server


class SourceDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'feed.db'
        self.store = Store(self.path, seed=False)
        self.source = self.store.source(self.store.add_source('ブログ', 'https://example.org/rss', 'blog'))
        self.entry = {'url': 'https://example.org/post', 'title': '記事', 'summary': '抜粋', 'published': 123, 'topics': []}
        self.store.ingest(self.source, [self.entry])
        self.item_id = self.store.list_items(tab='all')['items'][0]['id']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_delete_preserves_article_and_user_data(self):
        self.store.save_note(self.item_id, '残したいメモ')
        self.store.react(self.item_id, {'rating': 1, 'saved': True, 'read': True})
        self.store.delete_source(self.source['id'])
        self.assertEqual(self.store.sources(), [])
        with self.assertRaises(KeyError):
            self.store.enable_source(self.source['id'], True)
        item = self.store.item(self.item_id)
        self.assertEqual(item['note'], '残したいメモ')
        self.assertEqual(item['rating'], 1)
        self.assertTrue(item['read'])
        self.assertTrue(item['saved'])
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)

    def test_readd_restores_registration_without_duplicate_article(self):
        self.store.delete_source(self.source['id'])
        restored = self.store.add_source('登録し直したブログ', self.source['url'], 'news')
        self.assertEqual(restored, self.source['id'])
        self.assertEqual(len(self.store.sources()), 1)
        self.assertTrue(self.store.source(restored)['enabled'])
        self.assertEqual(self.store.source(restored)['name'], '登録し直したブログ')
        self.assertEqual(self.store.ingest(self.store.source(restored), [self.entry]), 0)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_source('重複', self.source['url'], 'rss')

    def test_catalog_and_restart_do_not_resurrect_deleted_source(self):
        definitions = [{'name': '標準ブログ', 'url': self.source['url'], 'kind': 'blog', 'adapter': 'rss'}]
        with patch('xangi_feed.store.catalog_sources', return_value=definitions):
            self.store.sync_catalog()
            self.store.delete_source(self.source['id'])
            definitions[0]['name'] = 'カタログ更新'
            self.store.sync_catalog()
            self.assertEqual(self.store.sources(), [])
            self.store.close()
            self.store = Store(self.path, seed=True)
            self.assertEqual(self.store.sources(), [])
            self.assertEqual(self.store.list_items(tab='all')['total'], 1)

    def test_inflight_result_after_deletion_is_discarded(self):
        self.store.delete_source(self.source['id'])
        self.assertEqual(self.store.ingest(self.source, [{**self.entry, 'url': 'https://example.org/new'}]), 0)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)


    def test_unknown_source_is_not_success(self):
        with self.assertRaises(KeyError):
            self.store.delete_source(999)

    def test_http_delete_confirmation_auth_and_readd(self):
        server = Server(self.store, 'a' * 32)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'

        def post(path, data, auth=True):
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = 'Bearer ' + 'a' * 32
            request = urllib.request.Request(origin + path, data=json.dumps(data).encode(), headers=headers)
            return urllib.request.urlopen(request)

        endpoint = f"/api/sources/{self.source['id']}/delete"
        try:
            for data, auth, code in [({}, True, 400), ({'confirm': 1}, True, 400), ({'confirm': True}, False, 401)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(endpoint, data, auth)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(len(self.store.sources()), 1)
            with post(endpoint, {'confirm': True}) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(json.load(response)['deleted'])
            self.assertEqual(self.store.sources(), [])
            self.assertEqual(self.store.item(self.item_id)['title'], '記事')
            with post('/api/sources', {'name': '再追加', 'url': self.source['url'], 'kind': 'rss'}) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.load(response)['id'], self.source['id'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            server.collector.close()
            server.enricher.close()
