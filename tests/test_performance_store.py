import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xangi_feed import store as store_module
from xangi_feed.store import Store


class StorePaginationPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.source = self.store.source(self.store.add_source('工作ブログ', 'https://example.org/rss', 'blog'))
        self.store.ingest(self.source, [
            {'url': f'https://example.org/{i}', 'title': f'Article {i}',
             'summary': '公開要約', 'published': i + 1,
             'topics': ['ロボット'] if i % 2 == 0 else ['科学']}
            for i in range(120)
        ])
        self.ids = {}
        with self.store.transaction() as db:
            for row in db.execute('SELECT * FROM items').fetchall():
                item = dict(row)
                i = int(item['title'].split()[-1])
                self.ids[i] = item['id']
                db.execute('INSERT INTO analyses VALUES (?,?,?,?,?,?)',
                           (item['id'], self.store.content_hash(item), 'done',
                            json.dumps({'summary_ja': 'only-analysis' if i % 3 == 0 else '解説'}), None, 1000))
                for revision in range(2):
                    state = {'request_id': f'{i}-{revision}', 'item_id': item['id'],
                             'status': 'done', 'created': 1000 + revision, 'revision': revision}
                    db.execute('INSERT INTO save_requests VALUES (?,?,?,?)',
                               (state['request_id'], item['id'], json.dumps(state), state['created']))
            db.execute("UPDATE analyses SET input_hash='stale' WHERE item_id=?", (self.ids[6],))

    def assert_matches_detail(self, item):
        self.assertEqual({k: v for k, v in item.items() if k not in {'score', 'recommendation'}},
                         self.store.item(item['id']))

    def test_no_search_hash_and_label_work_is_bounded_by_visible_page(self):
        with patch.object(self.store, 'content_hash', wraps=self.store.content_hash) as content_hash, \
                patch.object(store_module, 'publication_label', wraps=store_module.publication_label) as label:
            result = self.store.list_items(tab='all', offset=7, limit=3)
        self.assertEqual(result['total'], 120)
        self.assertEqual([item['id'] for item in result['items']], [self.ids[i] for i in [112, 111, 110]])
        self.assertEqual(content_hash.call_count, 3)
        self.assertEqual(label.call_count, 3)
        for item in result['items']:
            self.assert_matches_detail(item)
            self.assertEqual(item['save_request']['revision'], 1)

    def test_analysis_search_includes_all_candidates_and_excludes_stale_results(self):
        first = self.store.list_items(tab='all', q='ONLY-ANALYSIS', offset=2, limit=3)
        second = self.store.list_items(tab='all', q='ONLY-ANALYSIS', offset=5, limit=3)
        expected = [self.ids[i] for i in reversed(range(120)) if i % 3 == 0 and i != 6]
        self.assertEqual(first['total'], len(expected))
        self.assertEqual(second['total'], len(expected))
        self.assertEqual([item['id'] for item in first['items'] + second['items']], expected[2:8])
        for item in first['items'] + second['items']:
            self.assert_matches_detail(item)

    def test_stale_and_missing_analysis_remain_none_without_search(self):
        with self.store.transaction() as db:
            db.execute('DELETE FROM analyses WHERE item_id=?', (self.ids[7],))
        result = self.store.list_items(tab='all', q='Article ', offset=112, limit=2)
        self.assertEqual({item['id'] for item in result['items']}, {self.ids[6], self.ids[7]})
        for item in result['items']:
            self.assertIsNone(item['analysis'])
            self.assert_matches_detail(item)
        result = self.store.list_items(tab='all', offset=112, limit=2)
        for item in result['items']:
            self.assertIsNone(item['analysis'])
            self.assert_matches_detail(item)

    def test_mutations_are_visible_on_next_page_request(self):
        ident = self.ids[119]
        before = self.store.list_items(tab='all', limit=1)['items'][0]
        self.assertIsNotNone(before['analysis'])
        state = {**before['save_request'], 'status': 'error', 'error': '再試行待ち'}
        self.store.record_save_request(state)
        self.store.react(ident, {'rating': 1, 'saved': True})
        with self.store.transaction() as db:
            db.execute("UPDATE items SET summary='更新した要約' WHERE id=?", (ident,))
        after = self.store.list_items(tab='all', limit=1)['items'][0]
        self.assertEqual(after['save_request'], state)
        self.assertIsNone(after['analysis'])
        self.assertEqual(after['rating'], 1)
        self.assertEqual(after['saved'], 1)
        self.assert_matches_detail(after)

    def test_filters_and_search_keep_count_before_pagination(self):
        for i in [0, 6, 12, 18]:
            self.store.change_tag(self.ids[i], 'add', '工作')
        result = self.store.list_items(tab='all', tags=['工作'], topics=['ロボット'],
                                       kinds=['blog'], source_id=str(self.source['id']),
                                       q='only-analysis', offset=1, limit=1)
        self.assertEqual(result['total'], 3)
        self.assertEqual([item['id'] for item in result['items']], [self.ids[12]])
        self.assert_matches_detail(result['items'][0])

    def test_empty_page_does_not_hash_or_label_nonvisible_rows(self):
        with patch.object(self.store, 'content_hash', wraps=self.store.content_hash) as content_hash, \
                patch.object(store_module, 'publication_label', wraps=store_module.publication_label) as label:
            result = self.store.list_items(tab='all', offset=200, limit=50)
        self.assertEqual(result, {'total': 120, 'items': []})
        self.assertEqual(content_hash.call_count, 0)
        self.assertEqual(label.call_count, 0)


if __name__ == '__main__':
    unittest.main()
