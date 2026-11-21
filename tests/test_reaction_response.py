"""One profile snapshot per mutation, including custom-filter matching."""
import tempfile
import unittest
from pathlib import Path
from xangi_feed.store import Store, RECOMMENDATION_DEFAULTS, VIEW_DEFAULTS


class ReactionResponseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db', seed=False); self.addCleanup(self.store.close)
        self.item, _ = self.store.add_article({'url': 'https://example.org/one', 'title': 'AI robotics'})
        self.view = self.store.save_custom_filter({'name': 'Any', 'recommendations': dict(RECOMMENDATION_DEFAULTS), 'conditions': {**VIEW_DEFAULTS, 'read': 'any', 'muted': 'include'}})

    def test_single_profile_snapshot_and_matching_score(self):
        for data in [{'rating': 1}, {'rating': -1}, {'reason': 'duplicate'}, {'read': True}, {'saved': True}, {'muted': True}, {'rating': 0}]:
            with self.subTest(data=data):
                queries = []; self.store.db.set_trace_callback(queries.append)
                with self.store.transaction():
                    item = self.store.react(self.item['id'], data)
                    result = self.store.list_items(custom_filter=self.view['id'], only_ids=[item['id']], preference_topics=item['preference_weights'])
                self.store.db.set_trace_callback(None)
                self.assertEqual(sum('SELECT rating,reason,topics FROM items' in q for q in queries), 1)
                expected = self.store.item(item['id'])
                self.assertEqual({k: v for k, v in item.items() if k != 'preference_weights'}, expected)
                self.assertEqual(result['items'][0]['preference_score'], expected['preference_score'])
                self.assertEqual(item['preference_weights'], self.store.profile()['topics'])

    def test_read_timestamp_and_unknown_item(self):
        first = self.store.react(self.item['id'], {'read': True})
        self.assertEqual(first['read_at'], self.store.react(self.item['id'], {'read': True})['read_at'])
        self.assertIsNone(self.store.react(self.item['id'], {'read': False})['read_at'])
        with self.assertRaises(KeyError): self.store.react('missing', {'read': True})


if __name__ == '__main__': unittest.main()
