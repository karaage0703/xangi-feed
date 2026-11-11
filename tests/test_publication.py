import json
import tempfile
import unittest
from pathlib import Path

from xangi_feed.article import ArticleImporter, parse_article, parse_youtube
from xangi_feed.feeds import parse_feed
from xangi_feed.publication import label
from xangi_feed.store import Store


class PublicationParsingTests(unittest.TestCase):
    def test_hatena_rss_keeps_blog_name_and_creator(self):
        feed = '''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>整備士の工作日記</title>
          <item><title>工作記事</title><link>https://mechanic.hatenablog.com/entry/1</link>
          <dc:creator>整備士</dc:creator></item></channel></rss>'''.encode()
        entry = parse_feed(feed, 'https://mechanic.hatenablog.com/rss')[0]
        self.assertEqual(entry['publication_name'], '整備士の工作日記')
        self.assertEqual(entry['author'], '整備士')

    def test_atom_channel_and_multiple_paper_authors(self):
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><title>ロボット研究</title>
          <author><name>チャンネル管理者</name><email>hidden@example.org</email></author>
          <entry><title>研究</title><link href="https://arxiv.org/abs/2611.00001"/>
          <author><name>研究者A</name></author><author><name>研究者B</name></author></entry>
          <entry><title>動画</title><link href="https://youtube.com/watch?v=abcdefghijk"/></entry></feed>'''.encode()
        paper, video = parse_feed(feed, 'https://example.org/feed')
        self.assertEqual(paper['author'], '研究者A / 研究者B')
        self.assertEqual(video['author'], 'チャンネル管理者')
        self.assertNotIn('@', video['author'])

    def test_podcast_channel_title_and_author_are_inherited(self):
        feed = '''<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
          <title>工作ラジオ</title><itunes:author>整備士</itunes:author><item><title>第一回</title>
          <link>https://example.org/episode/1</link></item></channel></rss>'''.encode()
        entry = parse_feed(feed, 'https://example.org/feed')[0]
        self.assertEqual((entry['publication_name'], entry['author']), ('工作ラジオ', '整備士'))

    def test_rss_explicit_source_overrides_aggregator_title(self):
        feed = b'<rss><channel><title>Aggregator</title><item><title>Article</title><link>https://example.org/1</link><source>Original Blog</source><author>mail@example.org (Author)</author></item></channel></rss>'
        entry = parse_feed(feed, 'https://example.org/feed')[0]
        self.assertEqual((entry['publication_name'], entry['author']), ('Original Blog', 'Author'))

    def test_html_site_name_and_author_and_generic_hatena_suffix(self):
        page = '''<html><head><title>記事名 - 整備士の工作日記</title><meta property="og:title" content="記事名">
          <meta property="og:site_name" content="はてなブログ"><meta name="author" content="整備士"></head>
          <body><article>記事本文</article></body></html>'''.encode()
        result = parse_article(page, 'https://mechanic.hatenablog.com/entry/1')
        self.assertEqual((result['publication_name'], result['author']), ('整備士の工作日記', '整備士'))
        result = parse_article(b'<html><head><meta name="author" content="https://example.org/profile"></head><body>Text</body></html>', 'https://example.org/article')
        self.assertEqual(result['author'], '')

    def test_youtube_description_keeps_channel_name(self):
        data = json.dumps({'videoDetails': {'videoId': 'abcdefghijk', 'title': 'Video', 'author': '工作チャンネル'}}).encode()
        result = parse_youtube(data, 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(result['publication_name'], '工作チャンネル')

    def test_missing_identity_uses_owner_address_not_platform_alone(self):
        for url, expected in [('https://mechanic.hatenablog.com/entry/1', 'mechanic.hatenablog.com'),
                              ('https://note.com/mechanic/n/123', 'note.com/mechanic'),
                              ('https://qiita.com/mechanic/items/123', 'qiita.com/mechanic'),
                              ('https://zenn.dev/mechanic/articles/123', 'zenn.dev/mechanic')]:
            with self.subTest(url=url):
                self.assertEqual(label({'url': url, 'sources': [{'name': 'はてなブログ'}]}), expected)


class PublicationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'feed.db'
        self.store = Store(self.path, seed=False)
        self.source = self.store.source(self.store.add_source('はてなブログ', 'https://example.org/feed', 'blog'))
        self.entries = [{'title': f'記事{i}', 'url': f'https://mechanic.hatenablog.com/entry/{i}',
                         'summary': '本文', 'published': None, 'topics': ['ロボット']} for i in range(3)]
        self.store.ingest(self.source, self.entries)
        self.ids = [item['id'] for item in self.store.list_items(tab='all')['items']]

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_scores_distinguish_unknown_zero_positive_and_negative_in_detail_and_list(self):
        a, b, c = self.ids
        self.assertFalse(self.store.item(c)['preference_known'])
        self.store.react(a, {'rating': 1})
        self.assertEqual(self.store.item(c)['preference_score'], 1)
        self.store.react(b, {'rating': -1, 'reason': 'interest'})
        item = self.store.item(c)
        self.assertTrue(item['preference_known']); self.assertEqual(item['preference_score'], 0)
        self.store.react(a, {'rating': 0})
        self.assertEqual(self.store.item(c)['preference_score'], -1)
        listed = next(i for i in self.store.list_items(tab='all')['items'] if i['id'] == c)
        self.assertEqual((listed['preference_known'], listed['preference_score']), (True, -1))
        self.store.react(b, {'reason': 'duplicate'})
        self.assertFalse(self.store.item(c)['preference_known'])

    def test_metadata_is_backfilled_by_refresh_and_not_erased_when_missing(self):
        entry = {**self.entries[0], 'publication_name': '整備士の工作日記', 'author': '整備士'}
        self.store.ingest(self.source, [entry])
        item = next(i for i in self.store.list_items(tab='all')['items'] if i['url'] == entry['url'])
        self.assertIn('整備士の工作日記', item['publisher_label'])
        self.assertIn('著者: 整備士', item['publisher_label'])
        self.store.react(item['id'], {'rating': 1, 'saved': True})
        self.store.ingest(self.source, [self.entries[0]])
        reread = self.store.item(item['id'])
        self.assertEqual((reread['author'], reread['rating'], reread['saved']), ('整備士', 1, 1))


    def test_manual_page_identity_is_saved_and_survives_failed_refetch(self):
        page = '<html><head><title>記事</title><meta property="og:site_name" content="工作ブログ"><meta name="author" content="整備士"></head><body>本文</body></html>'.encode()
        importer = ArticleImporter(self.store, fetcher=lambda _: page)
        item, _ = importer.add({'url': 'https://example.org/article'})
        self.assertEqual((item['publication_name'], item['author']), ('工作ブログ', '整備士'))
        self.store.save_article_content(item['id'], {'error': '取得失敗'})
        self.assertEqual(self.store.item(item['id'])['author'], '整備士')


if __name__ == '__main__':
    unittest.main()
