import threading
import time
import unittest
from unittest.mock import Mock

from xangi_feed.web_discovery import discover, scope

NOW = 1794310000
SEARCH = b'<rss><channel><item><title>New lab</title><link>https://new.example/post</link></item><item><title>Known lab</title><link>https://known.example/post</link></item></channel></rss>'
PAGE = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'
FEED = b'<rss><channel><title>New Lab</title><item><title>Robot prototype</title><link>https://new.example/article</link><pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate></item></channel></rss>'


class PublicDiscoveryTests(unittest.TestCase):
    def fetch(self, url):
        if 'bing.com/search?' in url: return SEARCH
        if 'news.google.com/' in url: raise ValueError('upstream failed')
        if url == 'https://new.example/post': return PAGE
        if url == 'https://new.example/rss': return FEED
        raise AssertionError('unexpected fetch: ' + url)

    def test_actual_search_page_feed_path_matches_topic_and_checks_registration(self):
        fetcher = Mock(side_effect=self.fetch)
        items, diagnostics = discover([{'name': 'ロボット', 'weight': 2}], [], ['https://known.example/rss'], fetcher=fetcher, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['feed_url'], 'https://new.example/rss')
        self.assertEqual(items[0]['title'], 'Robot prototype')
        self.assertEqual(diagnostics['searches_failed'], 1)
        self.assertEqual(diagnostics['registered_skipped'], 1)
        self.assertFalse(any(c.args[0].startswith('https://known.example') for c in fetcher.call_args_list))
        self.assertEqual(items[0]['matched_topics'], ['ロボット'])

    def test_old_unrelated_and_registered_articles_are_never_offered(self):
        for now, positive, registered in [
            (NOW + 40*86400, 'ロボット', []), (NOW, '音声', []), (NOW, 'ロボット', ['https://new.example/rss'])
        ]:
            items, _ = discover([{'name':positive, 'weight':1}], [], registered, fetcher=self.fetch, now=now)
            self.assertEqual(items, [])

    def test_hosted_publications_are_distinct_and_alias_hosts_are_equal(self):
        self.assertNotEqual(scope('https://note.com/alice/rss'), scope('https://note.com/bob/rss'))
        self.assertEqual(scope('https://www.example.org/feed'), scope('https://example.org/'))

    def test_slow_search_does_not_block_overall_deadline(self):
        release = threading.Event()
        def slow(_): release.wait(2); return SEARCH
        start = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                discover([{'name':'ロボット','weight':1}], [], [], fetcher=slow, now=NOW, budget=0.02)
            self.assertLess(time.monotonic()-start, 0.5)
        finally: release.set()


    def test_japanese_and_exclusion_and_date_constraints_are_enforced(self):
        positive=[{'name':'ロボット','weight':2}]
        plan={'queries':['ロボット'], 'language':'ja', 'exclude_terms':[], 'max_age_days':7}
        items,_=discover(positive,[],[],fetcher=self.fetch,now=NOW,plan=plan)
        self.assertEqual(items,[])  # English article, even if search requested Japanese.
        japanese_feed=FEED.replace(b'Robot prototype','ロボットの新しい実験手順を公開しました'.encode())
        def fetch(url):
            return japanese_feed if url=='https://new.example/rss' else self.fetch(url)
        items,_=discover(positive,[],[],fetcher=fetch,now=NOW,plan=plan)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['language'],'ja')
        items,_=discover(positive,[],[],fetcher=fetch,now=NOW,plan={**plan,'exclude_terms':['実験']})
        self.assertEqual(items,[])
        items,_=discover(positive,[],[],fetcher=fetch,now=NOW+8*86400,plan=plan)
        self.assertEqual(items,[])

    def test_explicit_query_can_match_outside_fixed_topic_taxonomy(self):
        feed = FEED.replace(b'Robot prototype', b'Astronomy telescope guide')
        def fetch(url):
            return feed if url == 'https://new.example/rss' else self.fetch(url)
        plan = {'queries': ['astronomy telescope'], 'language': 'any', 'exclude_terms': [], 'max_age_days': 30}
        for positive in ([], [{'name': 'ロボット', 'weight': 2}]):
            with self.subTest(positive=positive):
                items, _ = discover(positive, [], [], fetcher=fetch, now=NOW, plan=plan)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]['matched_topics'], [])
                items, _ = discover(positive, [], [], fetcher=fetch, now=NOW, plan={**plan, 'queries': ['unrelated']})
                self.assertEqual(items, [])
