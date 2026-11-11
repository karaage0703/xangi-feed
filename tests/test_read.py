import tempfile
import unittest
from pathlib import Path

from xangi_feed.store import Store


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'news.db'
        self.store = Store(self.path, seed=False)
        self.source = self.store.source(self.store.add_source('Test', 'https://example.org/feed', 'blog'))
        self.store.ingest(self.source, [self.entry(i) for i in range(3)])
        self.ids = [i['id'] for i in self.store.list_items()['items']]

    def entry(self, i):
        return {'title': 'News ' + str(i), 'url': 'https://example.org/' + str(i), 'summary': 'Example',
                'published': 100 + i, 'updated': None, 'topics': ['開発'], 'audio_url': None}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_read_hidden_before_pagination_and_can_be_restored(self):
        item = self.ids[0]
        profile = self.store.profile()
        self.store.react(item, {'read': True})
        result = self.store.list_items(limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['id'], self.ids[1])
        self.assertEqual(self.store.list_items(limit=1, offset=1)['items'][0]['id'], self.ids[2])
        self.assertEqual(self.store.list_items(tab='all')['total'], 3)
        self.assertEqual(self.store.profile(), profile)
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.list_items()['total'], 2)
        self.store.react(item, {'read': False})
        self.assertEqual(self.store.list_items()['total'], 3)

    def test_stored_read_flag_is_used_directly_after_reopen(self):
        with self.store.transaction() as db:
            db.execute('UPDATE items SET read=1 WHERE id=?', (self.ids[0],))
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertTrue(self.store.item(self.ids[0])['read'])
        self.assertEqual(self.store.list_items(tab='read')['total'], 1)
        self.assertEqual(self.store.list_items()['total'], 2)
        self.store.react(self.ids[0], {'read': False})
        self.assertEqual(self.store.list_items(tab='read')['total'], 0)
        self.assertEqual(self.store.list_items()['total'], 3)

    def test_read_does_not_change_saved_discussion_comment_or_analysis_candidates(self):
        item = self.ids[0]
        self.store.react(item, {'saved': True, 'rating': 1})
        self.store.discussion(item, 'sent', 'session-1')
        original = self.store.item(item)
        self.store.save_analysis(item, self.store.content_hash(original), 'done', {'summary_ja': '保持する要約', 'comment': '保持'})
        self.store.react(item, {'read': True})
        self.assertEqual(self.store.list_items(tab='saved')['items'][0]['id'], item)
        self.assertEqual(self.store.list_items(tab='discussed')['items'][0]['id'], item)
        self.assertEqual(self.store.item(item)['analysis']['result']['comment'], '保持')
        self.assertEqual(self.store.list_items(apply_recommendation=False)['total'], 3)
        self.store.reset_profile()
        self.assertTrue(self.store.item(item)['read'])

    def test_read_list_is_paginated_and_independent_of_recommendation_conditions(self):
        self.store.react(self.ids[0], {'read': True, 'saved': True})
        self.store.react(self.ids[1], {'read': True})

        result = self.store.list_items(tab='read', limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['id'], self.ids[1])
        self.assertEqual(self.store.list_items(tab='read', limit=1, offset=1)['items'][0]['id'], self.ids[0])
        self.assertEqual(self.store.list_items(tab='read', q='News 2')['total'], 1)
        self.store.react(self.ids[0], {'read': False})
        self.assertEqual(self.store.list_items(tab='read')['total'], 1)
        self.assertEqual(self.store.list_items(tab='saved')['items'][0]['id'], self.ids[0])

        self.assertIn(self.ids[0], [i['id'] for i in self.store.list_items()['items']])


    def test_new_catalog_does_not_include_nullevi(self):
        self.store.close()
        self.store = Store(self.path)
        from xangi_feed.catalog import export
        self.assertEqual(len(self.store.sources()), len(export('feed')) + 1)
        self.assertFalse(any('news.nullevi.app' in s['url'] for s in self.store.sources()))
