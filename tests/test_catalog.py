import tempfile
import unittest
from pathlib import Path

from xangi_feed.feeds import web_url
from xangi_feed.store import Store


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'news.db'
        self.store = Store(self.path, seed=False)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = Store(self.path)

    def test_fresh_catalog_has_no_aggregate_or_duplicate_feeds(self):
        self.reopen()
        sources = self.store.sources()
        urls = [s['url'] for s in sources]
        from xangi_feed.catalog import export
        self.assertEqual(len(urls), len(export('feed')))
        self.assertEqual(len(set(map(web_url, urls))), len(urls))
        for definition in export('feed'):
            source = next(s for s in sources if s['url'] == web_url(definition['url']))
            self.assertEqual((source['name'], source['kind'], source['adapter'], source['enabled']),
                             (definition['name'], definition['kind'], definition['adapter'], 1))



    def test_catalog_sync_is_idempotent_and_disabled_sources_stay_disabled(self):
        self.reopen()
        source = self.store.sources()[0]
        self.store.enable_source(source['id'], False)
        count = len(self.store.sources())
        self.reopen()
        self.assertEqual(len(self.store.sources()), count)
        self.assertEqual(self.store.source(source['id'])['enabled'], 0)

    def test_catalog_file_is_unique_by_normalized_url(self):
        catalog = __import__('xangi_feed.catalog', fromlist=['export']).export('feed')
        urls = [web_url(s['url']) for s in catalog]
        self.assertEqual(len(set(urls)), len(urls))


if __name__ == '__main__':
    unittest.main()
