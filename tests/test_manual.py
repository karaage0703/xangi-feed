import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from xangi_feed.store import Store


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_manual_persists_and_appears_without_subscription(self):
        item, created = self.store.add_article({'url': 'https://example.org/post', 'title': 'AI工作', 'summary': 'ロボットの実験'})
        self.assertTrue(created)
        self.assertEqual(item['content_origin'], 'manual')
        self.assertEqual(item['sources'], [])
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.store.list_items()['items'][0]['id'], item['id'])
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.assertEqual(self.store.item(item['id'])['summary'], 'ロボットの実験')

    def test_duplicate_preserves_notes_rating_read_and_content(self):
        item, _ = self.store.add_article({'url': 'https://example.org/post?utm_source=manual'})
        self.store.save_note(item['id'], '大切なメモ')
        self.store.react(item['id'], {'rating': 1, 'read': True})
        again, created = self.store.add_article({'url': 'https://example.org/post#section', 'title': '上書きしない'})
        self.assertFalse(created)
        self.assertEqual(again['id'], item['id'])
        self.assertEqual(again['title'], item['title'])
        self.assertEqual(again['note'], '大切なメモ')
        self.assertEqual(again['rating'], 1)
        self.assertTrue(again['read'])

    def test_rss_duplicate_uses_existing_article(self):
        source = self.store.source(self.store.add_source('RSS', 'https://example.org/feed', 'rss'))
        self.store.ingest(source, [{'url': 'https://example.org/post', 'title': 'RSS記事', 'summary': '本文', 'published': None, 'topics': []}])
        item, created = self.store.add_article({'url': 'https://example.org/post'})
        self.assertFalse(created)
        self.assertEqual(item['title'], 'RSS記事')
        self.assertEqual(len(item['sources']), 1)
        self.assertTrue(item['manually_added'])
        self.assertEqual(self.store.list_items(tab='manual')['total'], 1)

    def test_manual_category_retains_blog_video_and_read_articles(self):
        blog, _ = self.store.add_article({'url': 'https://example.org/blog', 'title': '追加したブログ'})
        self.store.save_article_content(blog['id'], {'kind': 'blog', 'body': '本文'})
        video, _ = self.store.add_article({'url': 'https://youtu.be/fixture', 'title': '追加した映像'})
        self.store.save_article_content(video['id'], {'kind': 'video', 'content_kind': 'youtube_captions', 'body': '字幕'})
        self.store.react(blog['id'], {'read': True, 'rating': -1})
        self.store.save_note(blog['id'], '個別追加の検索用メモ')
        self.store.save_settings({ 'recommendation_hide_bad': True})
        self.assertEqual(self.store.list_items(tab='manual')['total'], 2)
        self.assertEqual(self.store.list_items(tab='manual', kinds=['video'])['items'][0]['id'], video['id'])
        self.assertEqual(self.store.list_items(tab='manual', q='検索用メモ')['items'][0]['id'], blog['id'])
        first = self.store.list_items(tab='manual', limit=1)
        second = self.store.list_items(tab='manual', limit=1, offset=1)
        self.assertEqual(first['total'], 2)
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])

    def test_manual_membership_survives_rss_reingest_without_marking_other_rss(self):
        item, _ = self.store.add_article({'url': 'https://example.org/added', 'title': '個別追加'})
        self.store.save_note(item['id'], '保持するメモ')
        source = self.store.source(self.store.add_source('配信元', 'https://example.org/feed', 'blog'))
        entries = [{'url': url, 'title': 'RSS記事', 'summary': '抜粋', 'published': None, 'topics': []}
                   for url in ['https://example.org/added', 'https://example.org/rss-only']]
        self.store.ingest(source, entries)
        self.assertEqual(self.store.item(item['id'])['content_origin'], 'rss')
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        result = self.store.list_items(tab='manual')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], item['id'])
        self.assertEqual(result['items'][0]['note'], '保持するメモ')
        self.assertEqual(self.store.list_items(tab='all')['total'], 2)


    def test_added_order_ignores_publication_rss_refresh_and_duplicate(self):
        source = self.store.source(self.store.add_source('RSS', 'https://example.org/feed', 'rss'))
        entry = {'url': 'https://example.org/rss-old', 'title': '古いRSS記事', 'summary': '', 'published': 10, 'topics': []}
        self.store.ingest(source, [entry])
        with patch('xangi_feed.store.time.time', return_value=100):
            first, _ = self.store.add_article({'url': 'https://example.org/first'})
        with patch('xangi_feed.store.time.time', return_value=200):
            rss, _ = self.store.add_article({'url': entry['url']})
        # Opposite publication/update order must not override manual addition time.
        self.store.db.execute('UPDATE items SET published=999,updated=999 WHERE id=?', (first['id'],))
        self.store.db.commit()
        self.store.ingest(source, [{**entry, 'updated': 1}])
        with patch('xangi_feed.store.time.time', return_value=300):
            duplicate, _ = self.store.add_article({'url': entry['url']})
        self.assertEqual(duplicate['manual_added_at'], 200)
        self.assertEqual(self.store.list_items(tab='manual', limit=1)['items'][0]['id'], rss['id'])
        self.assertEqual(self.store.list_items(tab='manual', limit=1, offset=1)['items'][0]['id'], first['id'])
        self.assertEqual(self.store.list_items(tab='manual', sort='updated')['items'][0]['id'], first['id'])
        self.assertEqual(self.store.list_items(tab='all')['items'][0]['id'], first['id'])
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.assertEqual(self.store.item(rss['id'])['manual_added_at'], 200)

    def test_added_order_uses_first_seen_when_addition_unknown(self):
        first, _ = self.store.add_article({'url': 'https://example.org/legacy'})
        self.store.db.execute('UPDATE items SET fetched=50 WHERE id=?', (first['id'],))
        self.store.db.execute('UPDATE items SET manual_added_at=NULL')
        self.store.db.commit()
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.assertIsNone(self.store.item(first['id'])['manual_added_at'])
        with patch('xangi_feed.store.time.time', return_value=100):
            latest, _ = self.store.add_article({'url': 'https://example.org/latest'})
        rows = self.store.list_items(tab='manual', sort='manual_added_at')['items']
        self.assertEqual([i['id'] for i in rows], [latest['id'], first['id']])
        self.assertEqual(self.store.item(first['id'])['fetched'], 50)

    def test_invalid_inputs_leave_no_items(self):
        for data in [{'url': 'javascript:alert(1)'}, {'url': None}, {'url': 'https://u:p@example.org/'},
                     {'url': 'https://example.org/', 'title': 1}, {'url': 'https://example.org/', 'summary': 'x' * 5001},
                     {'url': 'https://example.org/', 'unknown': True}]:
            with self.subTest(data=str(data)[:60]), self.assertRaises(ValueError):
                self.store.add_article(data)
        self.assertEqual(self.store.list_items(tab='all')['total'], 0)
