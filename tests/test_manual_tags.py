import tempfile
import unittest
from pathlib import Path
from xangi_feed.store import Store


class ManualTagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'feed.db'
        self.store = Store(self.path, seed=False)
        self.a = self.store.add_article({'url': 'https://example.org/a'})[0]['id']
        self.b = self.store.add_article({'url': 'https://example.org/b'})[0]['id']

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_add_reuse_remove_and_restart(self):
        self.store.change_tag(self.a, 'add', ' 工作で試す ')
        self.store.change_tag(self.a, 'add', '工作で試す')
        self.store.change_tag(self.b, 'add', '工作で試す')
        self.assertEqual(self.store.tag_options(), [{'name': '工作で試す', 'count': 2}])
        self.store.change_tag(self.a, 'remove', '工作で試す')
        self.assertEqual(self.store.item(self.a)['tags'], [])
        self.assertEqual(self.store.item(self.b)['tags'], ['工作で試す'])
        self.store.close(); self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.item(self.b)['tags'], ['工作で試す'])

    def test_delete_tag_preserves_articles_notes_rating_and_analysis(self):
        self.store.save_note(self.a, '残すメモ')
        self.store.react(self.a, {'rating': 1, 'read': True})
        self.store.save_analysis(self.a, self.store.content_hash(self.store.item(self.a)), 'done', {'summary_ja': '保持する要約'})
        before_profile = self.store.profile()
        for item in [self.a, self.b]: self.store.change_tag(item, 'add', '削除するタグ')
        self.store.delete_tag('削除するタグ')
        self.assertEqual(self.store.tag_options(), [])
        self.assertEqual(self.store.list_items(tab='all')['total'], 2)
        a = self.store.item(self.a)
        self.assertEqual(a['note'], '残すメモ')
        self.assertEqual(a['rating'], 1)
        self.assertTrue(a['read'])
        self.assertEqual(a['analysis']['status'], 'done')
        self.assertEqual(self.store.profile(), before_profile)
        self.assertEqual(self.store.item(self.b)['tags'], [])

    def test_filter_or_and_search_before_paging(self):
        self.store.change_tag(self.a, 'add', '工作')
        self.store.change_tag(self.b, 'add', '勉強会')
        result = self.store.list_items(tab='all', tags=['工作', '勉強会'], limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(self.store.list_items(tab='all', tags=['工作'])['items'][0]['id'], self.a)
        self.assertEqual(self.store.list_items(tab='all', tags=['工作'], topics=['科学'])['total'], 0)
        self.assertEqual(self.store.list_items(tab='all', q='勉強会')['items'][0]['id'], self.b)

    def test_validation_and_limit(self):
        for name in ['', ' ', 'a' * 41, 'a\nb', None]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.change_tag(self.a, 'add', name)
        for i in range(20): self.store.change_tag(self.a, 'add', str(i))
        with self.assertRaises(ValueError): self.store.change_tag(self.a, 'add', 'overflow')
        self.assertNotIn('overflow', [tag['name'] for tag in self.store.tag_options()])
        with self.assertRaises(KeyError): self.store.change_tag('missing', 'add', 'unknown')
        self.assertNotIn('unknown', [tag['name'] for tag in self.store.tag_options()])


    def test_http_confirmation_and_auth(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        from xangi_feed.server import Server
        server = Server(self.store, 'fixture-' * 8)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def post(path, data, auth=True):
            headers = {'Content-Type': 'application/json'}
            if auth: headers['Authorization'] = 'Bearer ' + 'fixture-' * 8
            return urllib.request.urlopen(urllib.request.Request(origin + path, data=json.dumps(data).encode(), headers=headers))
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                post(f'/api/items/{self.a}/tags', {'add': '工作'}, False)
            self.assertEqual(error.exception.code, 401)
            with post(f'/api/items/{self.a}/tags', {'add': '工作'}) as response:
                self.assertEqual(json.load(response)['tags'], ['工作'])
            for data in [{'name': '工作'}, {'name': '工作', 'confirm': 1}]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/tags/delete', data)
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(self.store.item(self.a)['tags'], ['工作'])
            with post('/api/tags/delete', {'name': '工作', 'confirm': True}) as response:
                self.assertTrue(json.load(response)['deleted'])
            self.assertEqual(self.store.item(self.a)['tags'], [])
            self.assertEqual(self.store.list_items(tab='all')['total'], 2)
        finally:
            server.shutdown(); server.server_close(); thread.join(); server.collector.close(); server.enricher.close()

    def test_existing_database_gains_tag_tables_without_losing_data(self):
        import sqlite3
        self.store.save_note(self.a, '移行前のメモ')
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE item_tags')
            db.execute('DROP TABLE manual_tags')
        self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.item(self.a)['note'], '移行前のメモ')
        self.assertEqual(self.store.list_items(tab='all')['total'], 2)
        self.store.change_tag(self.a, 'add', '移行後のタグ')
        self.assertEqual(self.store.item(self.a)['tags'], ['移行後のタグ'])
