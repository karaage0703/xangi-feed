import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from xangi_feed.feeds import MAX_BYTES, fetch_feed, parse_feed, plain, public_address, web_url
from xangi_feed.server import Server
from xangi_feed.service import Collector, Discussions, HostClient
from xangi_feed.store import Store, canonical

RSS = b'''<rss version="2.0"><channel><title>Test</title><item>
<title>Local LLM robotics</title><link>https://example.org/post?utm_source=rss</link>
<description><![CDATA[<p>GPU robot research</p><script>bad()</script>]]></description>
<pubDate>Tue, 03 Nov 2026 09:00:00 GMT</pubDate>
<enclosure url="https://example.org/episode.mp3" type="audio/mpeg"/>
</item></channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>AI Agent paper</title><id>http://arxiv.org/abs/2611.00001v2</id>
<link href="https://arxiv.org/abs/2611.00001v2" rel="alternate"/>
<published>2026-11-02T09:00:00Z</published><updated>2026-11-03T09:00:00Z</updated>
<summary>Agent study with limits.</summary></entry></feed>'''


class FeedTests(unittest.TestCase):
    def test_rss_html_audio_topics(self):
        entry = parse_feed(RSS, 'https://example.org/rss')[0]
        self.assertEqual(entry['summary'], 'GPU robot research')
        self.assertIn('ローカルAI', entry['topics'])
        self.assertTrue(entry['audio_url'].endswith('.mp3'))
        self.assertIsNotNone(entry['published'])

    def test_atom(self):
        item = parse_feed(ATOM, 'https://arxiv.org')[0]
        self.assertEqual(item['title'], 'AI Agent paper')
        self.assertGreater(item['updated'], item['published'])

    def test_bad_dates_preserve_article(self):
        entry = parse_feed(RSS.replace(b'Tue, 03 Nov 2026 09:00:00 GMT', b'no date'), 'https://example.org')[0]
        self.assertIsNone(entry['published'])

    def test_reject_nonfeed_empty_dtd_oversize(self):
        for data in [b'<html/>', b'<rss/>', b'<!DOCTYPE rss [<!ENTITY x "hello">]><rss/>', b'x' * (MAX_BYTES + 1)]:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                parse_feed(data, 'https://example.org')

    def test_unsafe_links_are_not_items(self):
        self.assertRaises(ValueError, parse_feed, RSS.replace(b'https://example.org/post?utm_source=rss', b'javascript:alert(1)'), 'https://example.org')

    def test_relative_atom_url(self):
        item = parse_feed(ATOM.replace(b'https://arxiv.org/abs/2611.00001v2', b'/abs/2611.00001v2'), 'https://arxiv.org/feed')[0]
        self.assertEqual(item['url'], 'https://arxiv.org/abs/2611.00001v2')

    def test_url_validation(self):
        for url in ['file:///etc/passwd', 'https://u:p@example.org/rss', 'https://example.org:1234/rss', 'https://example.org/a\nb']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                web_url(url)

    def test_ssrf_addresses(self):
        for ip in ['127.0.0.1', '10.0.0.2', '169.254.169.254', '::1', '::ffff:127.0.0.1']:
            with patch('socket.getaddrinfo', return_value=[(None, None, None, None, (ip, 443))]), self.assertRaises(ValueError):
                public_address('example.org', 443)

    def test_ssrf_redirect_rechecked(self):
        response = unittest.mock.Mock(status=302)
        response.getheader.return_value = 'http://127.0.0.1/secret'
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch('xangi_feed.feeds.public_address', side_effect=['93.184.216.34', ValueError('blocked')]) as address, patch('xangi_feed.feeds.socket.create_connection'), patch('xangi_feed.feeds.http.client.HTTPConnection', return_value=connection):
            self.assertRaises(ValueError, fetch_feed, 'http://example.org/feed')
            self.assertEqual(address.call_count, 2)

    def test_plaintext(self):
        self.assertEqual(plain('<p>A &amp; B</p><style>x</style>'), 'A & B')

    def test_html_doctype_inside_cdata_is_only_text(self):
        entries = parse_feed(RSS.replace(b'<p>GPU robot research</p>', b'<!DOCTYPE html><p>GPU robot research</p>'), 'https://example.org')
        self.assertEqual(entries[0]['summary'], 'GPU robot research')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'feed.sqlite3'
        self.store = Store(self.path, seed=False)
        source_id = self.store.add_source('Test', 'https://example.org/rss', 'rss')
        self.source = self.store.source(source_id)
        self.entries = parse_feed(RSS, self.source['url'])
        self.store.ingest(self.source, self.entries)
        self.item_id = self.store.list_items()['items'][0]['id']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_repeated_fetch_deduplicates(self):
        self.assertEqual(self.store.ingest(self.source, self.entries), 0)
        other_id = self.store.add_source('Other', 'https://other.org/rss', 'rss')
        self.store.ingest(self.store.source(other_id), self.entries)
        item = self.store.item(self.item_id)
        self.assertEqual(len(item['sources']), 2)
        self.assertEqual(self.store.list_items()['total'], 1)

    def test_arxiv_revisions_keep_rating_conversation(self):
        source_id = self.store.add_source('arXiv', 'https://rss.arxiv.org/rss/cs.RO', 'paper')
        source = self.store.source(source_id)
        self.store.ingest(source, parse_feed(ATOM, source['url']))
        item = self.store.list_items(kind='paper')['items'][0]
        self.store.react(item['id'], {'rating': 1, 'saved': True})
        self.store.discussion(item['id'], 'sent', 'session-1')
        new = parse_feed(ATOM.replace(b'v2', b'v3').replace(b'AI Agent paper', b'Updated paper'), source['url'])
        self.assertEqual(self.store.ingest(source, new), 0)
        updated = self.store.item(item['id'])
        self.assertEqual((updated['rating'], updated['saved'], updated['session_id']), (1, 1, 'session-1'))
        self.assertEqual(updated['title'], 'Updated paper')
        self.assertEqual(canonical('https://arxiv.org/pdf/2611.00001v2.pdf'), 'https://arxiv.org/abs/2611.00001')

    def test_bad_reason_does_not_poison_preferences(self):
        self.store.react(self.item_id, {'rating': -1, 'reason': 'summary'})
        self.assertEqual(self.store.profile()['bad'], 1)
        self.assertEqual(self.store.profile()['topics'], [])
        self.store.react(self.item_id, {'reason': 'interest'})
        self.assertTrue(all(t['weight'] == -1 for t in self.store.profile()['topics']))
        self.store.react(self.item_id, {'rating': 1})
        self.assertEqual(self.store.item(self.item_id)['reason'], '')
        self.assertTrue(all(t['weight'] == 1 for t in self.store.profile()['topics']))

    def test_persistence_and_reset(self):
        self.store.react(self.item_id, {'rating': 1, 'saved': True, 'read': True})
        self.store.discussion(self.item_id, 'sent', 's1')
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.item(self.item_id)['rating'], 1)
        self.store.reset_profile()
        item = self.store.item(self.item_id)
        self.assertEqual((item['rating'], item['saved'], item['session_id']), (0, 1, 's1'))

    def test_ranking_favors_rated_topic_and_new_view_keeps_all(self):
        for title, url in [('Local GPU workshop', 'https://example.org/2'), ('Cooking dinner', 'https://example.org/3')]:
            entry = parse_feed(RSS.replace(b'Local LLM robotics', title.encode()).replace(b'https://example.org/post?utm_source=rss', url.encode()).replace(b'GPU robot research', title.encode()), self.source['url'])
            self.store.ingest(self.source, entry)
        self.store.react(self.item_id, {'rating': 1})
        items = self.store.list_items(sort='recommended')['items']
        self.assertLess(next(i for i, x in enumerate(items) if x['title'] == 'Local GPU workshop'), next(i for i, x in enumerate(items) if x['title'] == 'Cooking dinner'))
        self.assertEqual(self.store.list_items(tab='all')['total'], 3)

    def test_views_pagination_filters(self):
        self.store.react(self.item_id, {'saved': True})
        self.assertEqual(self.store.list_items(tab='saved')['total'], 1)
        self.assertEqual(self.store.list_items(tab='discussed')['total'], 0)
        self.assertEqual(self.store.list_items(q='llm')['total'], 1)
        self.assertEqual(self.store.list_items(kind='paper')['total'], 0)
        self.assertEqual(self.store.list_items(offset=1)['items'], [])

    def test_invalid_rating_and_source(self):
        for data in [{'rating': True}, {'rating': 4}, {'saved': 1}, {'reason': 'oops'}, {}]:
            self.assertRaises(ValueError, self.store.react, self.item_id, data)
        self.assertRaises(ValueError, self.store.add_source, '', 'https://example.org', 'rss')

    def test_seed_once(self):
        other_path = Path(self.tmp.name) / 'seed.db'
        store = Store(other_path)
        source_id = store.sources()[0]['id']
        store.enable_source(source_id, False)
        store.close()
        store = Store(other_path)
        self.assertEqual(store.source(source_id)['enabled'], 0)
        store.close()

    def test_collector_partial_failure_and_recovery(self):
        other = self.store.add_source('Fail', 'https://other.org/rss', 'rss')
        def fetch(url):
            if 'other.org' in url:
                raise ValueError('HTTP 503')
            return RSS
        collector = Collector(self.store, fetch)
        collector.trigger(); collector.worker.join()
        self.assertEqual(self.store.source(other)['error'], 'HTTP 503')
        self.assertEqual(self.store.list_items()['total'], 1)
        collector.fetcher = lambda _: RSS
        collector.trigger(); collector.worker.join()
        self.assertIsNone(self.store.source(other)['error'])
        collector.close()

    def test_collector_single_flight(self):
        gate = threading.Event()
        collector = Collector(self.store, lambda _: (gate.wait(2), RSS)[1])
        self.assertTrue(collector.trigger())
        self.assertFalse(collector.trigger())
        gate.set(); collector.close()

    def test_discussion_create_once_and_resume_without_resend(self):
        host = unittest.mock.Mock(configured=True)
        host.create.return_value = 's1'
        host.session.return_value = {'lifecycle': 'open'}
        discussions = Discussions(self.store, host)
        self.assertEqual(discussions.open(self.item_id)['session_id'], 's1')
        self.assertEqual(discussions.open(self.item_id)['session_id'], 's1')
        host.create.assert_called_once(); host.send.assert_called_once()
        self.assertIn('https://example.org/post', host.send.call_args.args[1])
        self.assertIn('未確認', host.send.call_args.args[1])

    def test_discussion_send_timeout_and_restart_do_not_resend(self):
        host = unittest.mock.Mock(configured=True)
        host.create.return_value = 's1'; host.send.side_effect = TimeoutError()
        host.session.return_value = {'lifecycle': 'open'}
        discussions = Discussions(self.store, host)
        self.assertEqual(discussions.open(self.item_id)['state'], 'uncertain')
        discussions.open(self.item_id)
        host.send.assert_called_once()

    def test_discussion_create_timeout_no_automatic_retry(self):
        host = unittest.mock.Mock(configured=True)
        host.create.side_effect = TimeoutError()
        discussions = Discussions(self.store, host)
        self.assertRaises(TimeoutError, discussions.open, self.item_id)
        self.assertRaises(ValueError, discussions.open, self.item_id)
        host.create.assert_called_once()

    def test_discussion_recovery_after_process_death(self):
        self.store.discussion(self.item_id, 'sending', 's1')
        self.store.close(); self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.item(self.item_id)['discussion_state'], 'uncertain')


class HTTPTests(unittest.TestCase):
    setUp = StoreTests.setUp
    tearDown = StoreTests.tearDown
    def test_http_auth_validation_and_mutation(self):
        server = Server(self.store, 'a' * 32)
        server.articles.fetcher = lambda url: b'<html><title>Fixture</title><article>Fixture article body.</article></html>'
        worker = threading.Thread(target=server.serve_forever); worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def request(path, data=None, auth=True):
            headers = {'Authorization': 'Bearer ' + 'a' * 32} if auth else {}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(origin + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            return urllib.request.urlopen(req)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items', auth=False)
            self.assertEqual(error.exception.code, 401)
            with request('/api/items') as response:
                self.assertEqual(json.load(response)['total'], 1)
            with request('/api/items', {'url': 'https://example.org/individual', 'title': '個別記事'}) as response:
                self.assertEqual(response.status, 201)
                manual_id = json.load(response)['item']['id']
            with request('/api/items', {'url': 'https://example.org/individual#part'}) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)['item']['id'], manual_id)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items', {'url': 'https://example.org/unauthorized'}, auth=False)
            self.assertEqual(error.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items', {'url': 'file:///tmp/article'})
            self.assertEqual(error.exception.code, 400)

            with request('/api/recommendations') as response:
                settings = json.load(response)
                self.assertIn('recommendation_min_preference', settings)
                self.assertNotIn('agent_id', settings)
                self.assertNotIn('notion_parent_id', settings)
            with request('/api/filter-options') as response:
                self.assertIn('ロボット', json.load(response)['topics'])
            with request('/api/items?tab=all&topic=%E3%83%AD%E3%83%9C%E3%83%83%E3%83%88') as response:
                self.assertEqual(json.load(response)['total'], 1)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items?topic=unknown')
            self.assertEqual(error.exception.code, 400)
            with request('/api/source-adapters') as response:
                self.assertEqual(json.load(response)['adapters'][0]['id'], 'rss')
            with request('/api/items/' + self.item_id + '/note', {'note': '振り返りたい感想'}) as response:
                self.assertEqual(json.load(response)['note'], '振り返りたい感想')
            with request('/api/items?tab=notes') as response:
                self.assertEqual(json.load(response)['total'], 1)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items/' + self.item_id + '/note', {'note': 'unauthorized'}, auth=False)
            self.assertEqual(error.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items/' + self.item_id + '/notion', {})
            self.assertEqual(error.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/items/' + self.item_id + '/note', {'note': []})
            self.assertEqual(error.exception.code, 400)
            with request('/api/settings') as response:
                settings = json.load(response)
                self.assertEqual(settings['save_mode'], 'skill')
                self.assertNotIn('notion_token', settings)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/sources', {'name': '任意スクリプト', 'url': 'https://example.org/', 'adapter': '../custom'})
            self.assertEqual(error.exception.code, 400)
            with request('/api/items/' + self.item_id + '/react', {'rating': 1}) as response:
                self.assertEqual(json.load(response)['rating'], 1)
            with request('/api/items/' + self.item_id + '/react', {'read': True}) as response:
                self.assertTrue(json.load(response)['read'])
            with request('/api/items?tab=read') as response:
                self.assertEqual(json.load(response)['items'][0]['id'], self.item_id)
            with request('/api/items/' + self.item_id + '/react', {'read': False}) as response:
                self.assertFalse(json.load(response)['read'])
            with request('/api/items?tab=read') as response:
                self.assertEqual(json.load(response)['total'], 0)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/profile/reset', {})
            self.assertEqual(error.exception.code, 400)
            with request('/') as response:
                self.assertIn(b'xangi-feed', response.read())
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            with request('/entry') as response:
                self.assertIn(b'http-equiv="refresh" content="0;url=./"', response.read())
        finally:
            server.shutdown(); server.server_close(); worker.join(); server.collector.close()


class HostTests(unittest.TestCase):
    def test_host_redirect_disabled_and_url_validated(self):
        for url in ['file:///tmp/foo', 'https://a:b@example.org', 'https://example.org/path']:
            self.assertRaises(ValueError, HostClient, url, 'token')
        client = HostClient('http://127.0.0.1:12345', 'token')
        self.assertTrue(client.configured)

    def test_actual_entrypoint_ready_and_parent_exit(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            import os
            env = {**os.environ, 'XANGI_EXTENSION_AUTH_TOKEN': 'a' * 32}
            # All sources disabled in this isolated lifecycle check: no external fetch.
            store = Store(Path(tmp) / '.xangi-feed/feed.sqlite3')
            for source in store.sources():
                store.enable_source(source['id'], False)
            store.close()
            process = subprocess.Popen([str(root / 'scripts/xangi-extension'), 'serve', '--workspace', tmp], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                event = json.loads(process.stdout.readline())
                self.assertEqual((event['schemaVersion'], event['id']), (2, 'xangi-feed'))
                request = urllib.request.Request(event['baseUrl'] + '/api/health', headers={'Authorization': 'Bearer ' + 'a' * 32})
                with urllib.request.urlopen(request) as response:
                    self.assertTrue(json.load(response)['ready'])
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
                process.stdout.close(); process.stderr.close()


if __name__ == '__main__':
    unittest.main()
