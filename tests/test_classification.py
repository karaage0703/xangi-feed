import tempfile
import unittest
from pathlib import Path
from xangi_feed.store import Store


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.source = self.store.source(self.store.add_source('工作ブログ', 'https://example.org/rss', 'blog'))
        for index, tags in enumerate([['ロボット', 'ものづくり'], ['ローカルAI'], ['科学'], []]):
            self.store.ingest(self.source, [{'url': f'https://example.org/{index}', 'title': str(index), 'summary': '抜粋', 'published': index + 1, 'topics': tags}])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_topics_filter_before_count_and_pagination(self):
        result = self.store.list_items(tab='all', topics=['ロボット', 'ローカルAI'], limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['items']), 1)
        other = self.store.list_items(tab='all', topics=['ロボット', 'ローカルAI'], limit=1, offset=1)
        self.assertNotEqual(result['items'][0]['id'], other['items'][0]['id'])

    def test_kind_source_query_and_topics_intersect(self):
        self.assertEqual(self.store.list_items(tab='all', kind='blog', source_id=str(self.source['id']), q='0', topics=['ロボット'])['total'], 1)
        self.assertEqual(self.store.list_items(tab='all', kind='video', topics=['ロボット'])['total'], 0)
        self.assertEqual(self.store.list_items(tab='all', q='1', topics=['ロボット'])['total'], 0)

    def test_no_tags_does_not_exclude_untagged(self):
        self.assertEqual(self.store.list_items(tab='all')['total'], 4)
        self.assertEqual(self.store.list_items(tab='all', topics=['ロボット'])['total'], 1)

    def test_invalid_tag_rejected(self):
        for topics in [['unknown'], 'ロボット', ['ロボット'] * 9]:
            with self.subTest(topics=topics), self.assertRaises(ValueError):
                self.store.list_items(topics=topics)

    def test_multiple_kinds_union_before_paging_and_intersection(self):
        video = self.store.source(self.store.add_source('映像', 'https://example.org/video-feed', 'blog'))
        with self.store.transaction() as db:
            db.execute("UPDATE sources SET kind='video' WHERE id=?", (video['id'],))
        video = self.store.source(video['id'])
        self.store.ingest(video, [{'url': 'https://example.org/video', 'title': '映像', 'summary': '抜粋', 'published': 10, 'topics': ['ロボット']}])
        self.assertEqual(self.store.list_items(tab='all', kinds=['blog', 'video'])['total'], 5)
        self.assertEqual(self.store.list_items(tab='all', kinds=['video'])['total'], 1)
        self.assertEqual(self.store.list_items(tab='all', kinds=['blog', 'video'], topics=['ロボット'])['total'], 2)
        self.assertEqual(self.store.list_items(tab='all', kinds=['blog', 'video'], source_id=str(self.source['id']))['total'], 4)
        page = self.store.list_items(tab='all', kinds=['blog', 'video'], limit=1)
        other = self.store.list_items(tab='all', kinds=['blog', 'video'], limit=1, offset=1)
        self.assertEqual(page['total'], 5)
        self.assertNotEqual(page['items'][0]['id'], other['items'][0]['id'])
