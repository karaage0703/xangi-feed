import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xangi_feed.catalog import BUNDLED, export
from xangi_feed.feeds import parse_feed
from xangi_feed.store import Store
from xangi_feed.youtube import feed_url, resolve, fetch_entries, parse_channel_page

CHANNEL = 'UC' + 'a' * 22
VIDEO = 'b' * 11
ATOM = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/"><entry>
<title>Video title</title><link href="https://www.youtube.com/watch?v={VIDEO}"/>
<published>2026-11-04T01:00:00Z</published><media:group><media:description>English description</media:description></media:group>
</entry></feed>'''.encode()


class ChannelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'feed.db', seed=False)
        self.source = self.store.source(self.store.add_source('Test', 'https://example.org/feed', 'blog'))
        self.store.ingest(self.source, [{'title': 'Test', 'url': 'https://example.org/post', 'summary': 'Text',
                                        'published': 1, 'updated': None, 'topics': [], 'audio_url': None}])
        self.item_id = self.store.list_items()['items'][0]['id']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


    def test_youtube_resolver_and_safe_channel_page_discovery(self):
        self.assertEqual(resolve(CHANNEL), feed_url(CHANNEL))
        self.assertEqual(resolve('https://youtube.com/channel/' + CHANNEL), feed_url(CHANNEL))
        page = f'<link rel="alternate" href="https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}">'.encode()
        self.assertEqual(resolve('@example', lambda _: page), feed_url(CHANNEL))
        self.assertEqual(resolve('https://www.youtube.com/@example/videos', lambda _: page), feed_url(CHANNEL))
        for bad in ['https://youtube.com.evil.example/channel/' + CHANNEL, 'http://127.0.0.1/feed', 'https://youtube.com/watch?v=' + VIDEO]:
            self.assertRaises(ValueError, resolve, bad)

    def test_youtube_media_description_and_page_fallback(self):
        self.assertEqual(parse_feed(ATOM, feed_url(CHANNEL))[0]['summary'], 'English description')
        payload = {'videoRenderer': {'videoId': VIDEO, 'title': {'runs': [{'text': 'Public title'}]}}}
        html = ('<script>var ytInitialData = ' + json.dumps(payload) + ';</script>').encode()
        calls = []
        def fetch(url):
            calls.append(url)
            if 'videos.xml' in url:
                raise ValueError('HTTP 404')
            return html
        items = fetch_entries(feed_url(CHANNEL), fetch)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(items[0]['published'])
        self.assertEqual(items[0]['origin'], 'page')
        self.assertEqual(items[0]['url'], 'https://www.youtube.com/watch?v=' + VIDEO)
        self.assertRaises(ValueError, parse_channel_page, b'<html>changed</html>')

    def test_video_kind_uses_stable_feed_and_existing_feedback(self):
        source_id = self.store.add_source('YouTube', CHANNEL, 'video')
        source = self.store.source(source_id)
        self.assertEqual(source['url'], feed_url(CHANNEL))
        self.store.ingest(source, parse_feed(ATOM, source['url']))
        video = self.store.list_items(kind='video')['items'][0]
        self.store.react(video['id'], {'rating': 1, 'read': True})
        self.assertEqual(self.store.list_items(tab='read', kind='video')['total'], 1)

    def test_page_fallback_only_reads_selected_channel_tab(self):
        def video(video_id):
            return {'videoRenderer': {'videoId': video_id, 'title': {'simpleText': video_id}}}
        data = {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [
            {'tabRenderer': {'content': video('a' * 11)}},
            {'tabRenderer': {'selected': True, 'content': video(VIDEO)}}]}}}
        html = ('<script>var ytInitialData = ' + json.dumps(data) + ';</script>').encode()
        self.assertEqual([e['url'] for e in parse_channel_page(html)], ['https://www.youtube.com/watch?v=' + VIDEO])

    def test_common_catalog_exports_have_same_urls_and_podcast_metadata(self):
        feed = export('feed')
        patrol = export('patrol')['sites']
        podcasts = export('podcast')
        tech = export('technews')
        self.assertTrue(feed)
        self.assertEqual(len(podcasts), sum('podcast' in r['consumers'] for r in json.loads(BUNDLED.read_text())['sources']))
        self.assertTrue(all(p['url'] in {r['url'] for r in feed} for p in patrol))
        self.assertTrue(all(t['url'] in {r['url'] for r in feed} for t in tech))
        self.assertTrue(all(p['id'] for p in podcasts))

    def test_catalog_change_adds_sources_and_stops_removed_managed_sources(self):
        path = self.root / 'catalog.json'
        data = json.loads(BUNDLED.read_text())
        path.write_text(json.dumps(data))
        managed = Store(self.root / 'managed.db', catalog_path=path)
        try:
            first = data['sources'][0]
            existing = next(s for s in managed.sources() if s['url'] == first['url'])
            managed.enable_source(existing['id'], False)
            data['sources'].append({'id': 'new', 'name': 'New', 'url': 'https://example.org/new-feed', 'kind': 'blog', 'adapter': 'rss', 'consumers': ['feed', 'patrol']})
            path.write_text(json.dumps(data)); managed.sync_catalog()
            self.assertFalse(managed.source(existing['id'])['enabled'])
            self.assertTrue(any(s['name'] == 'New' for s in managed.sources()))
            data['sources'] = [r for r in data['sources'] if r['id'] != 'new']
            path.write_text(json.dumps(data)); managed.sync_catalog()
            self.assertFalse(next(s for s in managed.sources() if s['name'] == 'New')['enabled'])
        finally:
            managed.close()

    def test_skill_wrapper_uses_shared_list_without_running_real_skill_state(self):
        script = self.root / 'fake-podcast.py'
        script.write_text('import json\nPODCASTS=[]\ndef main(): print(json.dumps({"count":len(PODCASTS)}))\n')
        wrapper = Path(__file__).parent.parent / 'scripts/source-catalog.py'
        result = subprocess.check_output([sys.executable, str(wrapper), '--consumer', 'podcast', '--run-script', str(script)], text=True)
        self.assertEqual(json.loads(result)['count'], len(export('podcast')))
