import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from xangi_feed.server import Server
from xangi_feed.store import Store


class MuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'feed.db'
        self.store = Store(self.path, seed=False)
        self.source = self.store.source(self.store.add_source('Test feed', 'https://example.org/feed', 'blog'))
        self.entries = [{'url': f'https://example.org/{i}', 'title': f'Article {i}', 'summary': 'fixture', 'topics': ['開発'], 'published': 100 + i} for i in range(3)]
        self.store.ingest(self.source, self.entries)
        self.ids = [item['id'] for item in self.store.list_items()['items']]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_mute_filters_before_pagination_and_restores_unread(self):
        a, b, c = self.ids
        self.store.react(a, {'muted': True})
        result = self.store.list_items(limit=1)
        self.assertEqual((result['total'], result['items'][0]['id']), (2, b))
        self.assertEqual(self.store.list_items(limit=1, offset=1)['items'][0]['id'], c)
        self.assertEqual(self.store.list_items(tab='all')['total'], 2)
        self.assertEqual(self.store.list_items(tab='muted')['items'][0]['id'], a)
        self.assertEqual(self.store.list_items(tab='read')['total'], 0)
        self.store.react(a, {'read': False})
        self.assertEqual(self.store.list_items()['total'], 2)
        item = self.store.react(a, {'muted': False})
        self.assertFalse(item['read'])
        self.assertIsNone(item['read_at'])
        self.assertEqual(self.store.list_items()['total'], 3)

    def test_mute_preserves_user_data_learning_and_survives_ingest_reopen(self):
        a = self.ids[0]
        self.store.react(a, {'saved': True, 'rating': 1})
        self.store.save_note(a, 'Keep note')
        self.store.change_tag(a, 'add', 'Keep tag')
        self.store.discussion(a, 'sent', 'session-1')
        self.store.save_analysis(a, self.store.content_hash(self.store.item(a)), 'done', {'summary_ja': '保持する要約'})
        before = self.store.item(a)
        profile = self.store.profile()
        self.store.react(a, {'muted': True})
        self.store.ingest(self.source, self.entries)
        self.store.close()
        self.store = Store(self.path, seed=False)
        item = self.store.item(a)
        self.assertTrue(item['muted'])
        for key in ['read', 'read_at', 'saved', 'rating', 'reason', 'note', 'note_updated', 'tags', 'session_id', 'analysis']:
            self.assertEqual(item[key], before[key], key)
        self.assertEqual(self.store.profile(), profile)
        for tab in ['all', 'saved', 'notes', 'discussed']:
            self.assertNotIn(a, [item['id'] for item in self.store.list_items(tab=tab)['items']])
        self.store.react(a, {'read': True})
        self.assertNotIn(a, [item['id'] for item in self.store.list_items(tab='read')['items']])
        self.assertIn(a, [item['id'] for item in self.store.list_items(tab='muted')['items']])
        self.store.react(a, {'muted': False})
        self.assertTrue(self.store.item(a)['read'])
        self.assertIn(a, [item['id'] for item in self.store.list_items(tab='read')['items']])
        self.assertNotIn(a, [item['id'] for item in self.store.list_items()['items']])


    def test_all_normal_views_hide_muted_items_and_restore_the_original_state(self):
        a = self.ids[0]
        self.store.add_article({'url': self.store.item(a)['url']})
        self.store.react(a, {'read': True, 'saved': True, 'rating': 1})
        self.store.save_note(a, '見返すメモ')
        self.store.discussion(a, 'sent', 'conversation')
        before = self.store.item(a)
        for tab in ['all', 'read', 'saved', 'notes', 'discussed', 'manual']:
            self.assertIn(a, [item['id'] for item in self.store.list_items(tab=tab)['items']], tab)
        self.store.react(a, {'muted': True})
        for tab in ['recommended', 'all', 'read', 'saved', 'notes', 'discussed', 'manual']:
            self.assertNotIn(a, [item['id'] for item in self.store.list_items(tab=tab)['items']], tab)
        self.assertEqual(self.store.list_items(tab='muted', kind='blog', q='見返す', source_id=str(self.source['id']))['total'], 1)
        self.assertEqual(self.store.list_items(tab='muted', kind='video')['total'], 0)
        self.store.react(a, {'muted': False})
        after = self.store.item(a)
        for key in ['read', 'read_at', 'saved', 'rating', 'note', 'session_id', 'manually_added']:
            self.assertEqual(before[key], after[key], key)
        for tab in ['all', 'read', 'saved', 'notes', 'discussed', 'manual']:
            self.assertIn(a, [item['id'] for item in self.store.list_items(tab=tab)['items']], tab)
        self.assertEqual(self.store.list_items(tab='muted')['total'], 0)


    def test_http_mute_boolean_validation_auth_and_restore(self):
        a = self.ids[0]
        server = Server(self.store, 'fixture-' * 8)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def post(data, auth=True):
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = 'Bearer ' + 'fixture-' * 8
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/items/{a}/react', data=json.dumps(data).encode(), headers=headers)
            return urllib.request.urlopen(request)
        try:
            for data, auth, status in [({'muted': True}, False, 401), ({'muted': 1}, True, 400), ({'muted': 'true'}, True, 400), ({'muted': None}, True, 400)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(data, auth)
                self.assertEqual(error.exception.code, status)
                self.assertFalse(self.store.item(a)['muted'])
            for value in [True, True, False]:
                with post({'muted': value}) as response:
                    item = json.load(response)
                self.assertEqual(item['muted'], value)
                self.assertEqual(bool(item['muted']), value)
                self.assertFalse(item['read'])
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close()


if __name__ == '__main__':
    unittest.main()
