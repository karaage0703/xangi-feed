import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from xangi_feed.server import Server
from xangi_feed.store import Store


class ReadOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'feed.db'
        self.store = Store(self.path, seed=False)
        self.items = [self.store.add_article({'url': f'https://example.org/{i}', 'title': f'Article {i}'})[0]['id'] for i in range(3)]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_manual_and_link_read_order_persist_and_paginate(self):
        a, b, legacy = self.items
        with self.store.transaction() as db:
            db.execute('UPDATE items SET read=1 WHERE id=?', (legacy,))
            db.execute('UPDATE items SET updated=9999999999 WHERE id=?', (a,))
        with patch('xangi_feed.store.time.time', return_value=10):
            self.store.react(a, {'read': True})
        with patch('xangi_feed.store.time.time', return_value=20):
            self.store.open_item(b)
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertEqual([item['id'] for item in self.store.list_items(tab='read')['items']], [b, a, legacy])
        self.assertEqual(self.store.list_items(tab='read', limit=1, offset=1)['items'][0]['id'], a)
        self.assertEqual(self.store.list_items(tab='read', q='Article 2')['items'][0]['id'], legacy)
        self.assertEqual(self.store.list_items(tab='read', sort='updated')['items'][0]['id'], a)
        self.assertEqual(self.store.list_items(tab='read', sort='read_at')['items'][0]['id'], b)

    def test_repeat_read_preserves_time_and_unread_then_read_moves_to_top(self):
        a, b, _ = self.items
        with patch('xangi_feed.store.time.time', return_value=10):
            with self.assertRaises(ValueError): self.store.react(a, {'read': True, 'read_at': -100})
            self.store.react(a, {'read': True})
        with patch('xangi_feed.store.time.time', return_value=20):
            self.store.open_item(b)
        with patch('xangi_feed.store.time.time', return_value=30):
            self.store.react(a, {'read': True, 'saved': True})
            self.store.open_item(a)
        self.assertEqual(self.store.item(a)['read_at'], 10)
        self.store.react(a, {'read': False})
        self.assertIsNone(self.store.item(a)['read_at'])
        self.store.save_settings({'mark_read_on_open': False})
        self.store.open_item(a)
        self.assertIsNone(self.store.item(a)['read_at'])
        with patch('xangi_feed.store.time.time', return_value=40):
            self.store.react(a, {'read': True})
        self.assertEqual(self.store.item(a)['read_at'], 40)
        self.assertEqual(self.store.list_items(tab='read')['items'][0]['id'], a)
        self.assertTrue(self.store.item(a)['saved'])


    def test_http_read_default_uses_read_time(self):
        a, b, _ = self.items
        with self.store.transaction() as db:
            db.execute('UPDATE items SET updated=9999999999 WHERE id=?', (a,))
        with patch('xangi_feed.store.time.time', return_value=10):
            self.store.react(a, {'read': True})
        with patch('xangi_feed.store.time.time', return_value=20):
            self.store.react(b, {'read': True})
        server = Server(self.store, 'fixture-' * 8)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/items?tab=read&limit=1'
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + 'fixture-' * 8})
            with urllib.request.urlopen(request) as response:
                payload = json.load(response)
            self.assertEqual(payload['items'][0]['id'], b)
            self.assertEqual(payload['items'][0]['read_at'], 20)
            self.assertEqual(payload['total'], 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            server.collector.close()
            server.enricher.close()


if __name__ == '__main__':
    unittest.main()
