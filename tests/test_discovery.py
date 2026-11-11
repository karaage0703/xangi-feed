import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from xangi_feed.discovery import Discovery
from xangi_feed.server import Server
from xangi_feed.store import Store

NOW = 1794310000
FEED = b'''<rss><channel><title>New Robot Lab</title><item><title>New robot prototype</title><link>https://new.example/robot</link><pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate></item></channel></rss>'''
ROW = {'feed_url': 'https://new.example/rss', 'url': 'https://new.example/robot', 'reason': 'ロボットの試作に役立つ手順があります。', 'matched_topics': ['ロボット'], 'kind': 'blog'}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'feed.db', seed=False)
        self.host = Mock(configured=True)
        self.host.run.return_value = json.dumps([ROW])
        self.fetcher = Mock(return_value=FEED)
        self.candidates = [{**ROW, 'id': 'candidate', 'title': 'New robot prototype', 'source_name': 'New Robot Lab', 'published': NOW-100, 'verified_at': NOW}]
        self.searcher = Mock(return_value=(self.candidates, {'feeds_checked': 1}))
        self.service = Discovery(self.store, self.host, self.fetcher, clock=lambda: NOW, searcher=self.searcher)
        self.store.save_settings({'agent_id': 'fixture', 'enabled': False})
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.addCleanup(self.service.close)

    def like(self):
        item, _ = self.store.add_article({'url': 'https://known.example/robot', 'title': 'robot'})
        self.store.react(item['id'], {'rating': 1})
        return item

    def complete(self):
        self.service.worker.join(5)
        self.assertFalse(self.service.worker.is_alive())

    def test_daily_schedule_survives_restart_and_requires_learned_preferences(self):
        self.assertFalse(self.service.trigger())
        self.searcher.assert_not_called()
        item = self.like()
        profile = self.store.profile()
        self.assertTrue(self.service.trigger())
        self.complete()
        status = self.service.status()
        self.assertEqual(len(status['items']), 1)
        self.assertEqual(status['next_run'], NOW + 86400)
        self.assertEqual(self.store.profile(), profile)
        self.assertEqual(self.store.sources(), [])
        self.assertNotIn(item['url'], repr(self.searcher.call_args.args[0]))
        self.host.run.assert_not_called()
        self.assertFalse(self.service.trigger())
        restarted = Discovery(self.store, self.host, self.fetcher, clock=lambda: NOW + 86399, searcher=self.searcher)
        self.assertEqual(len(restarted.status()['items']), 1)
        self.assertFalse(restarted.trigger())
        restarted.clock = lambda: NOW + 86400
        self.assertTrue(restarted.trigger()); restarted.worker.join(5); restarted.close()
        self.assertEqual(self.searcher.call_count, 2)

    def test_failed_search_retains_previous_results_and_rate_limits_failures(self):
        self.like(); self.assertTrue(self.service.trigger()); self.complete()
        previous = self.service.status()['items']
        self.searcher.side_effect = ValueError('secret upstream body')
        self.assertTrue(self.service.trigger(force=True)); self.complete()
        self.assertEqual(self.service.status()['items'], previous)
        self.assertNotIn('secret', self.service.status()['error'])
        self.assertFalse(self.service.trigger())
        self.searcher.side_effect = None
        self.searcher.return_value = ([], {})
        self.assertTrue(self.service.trigger(force=True)); self.complete()
        self.assertEqual(self.service.status()['items'], previous)

    def test_error_and_retry_survive_restart_and_recover(self):
        self.like(); self.searcher.side_effect = TimeoutError()
        self.assertTrue(self.service.trigger()); self.complete()
        state = self.service.status()
        self.assertEqual(state['outcome'], 'failed')
        self.assertEqual(state['next_run'], NOW+900)
        restarted = Discovery(self.store, self.host, self.fetcher, clock=lambda: NOW+899, searcher=self.searcher)
        self.assertEqual(restarted.status()['error'], state['error'])
        self.assertFalse(restarted.trigger())
        self.searcher.side_effect = None
        restarted.clock = lambda: NOW+900
        self.assertTrue(restarted.trigger()); restarted.worker.join(5)
        self.assertEqual(restarted.status()['outcome'], 'success')
        self.assertIsNone(restarted.status()['error'])
        restarted.close()

    def test_interrupted_runs_are_retried_without_ai(self):
        self.like(); self.store.save_settings({'agent_id': ''})
        self.store.save_discovery({'items': [], 'updated': None, 'last_attempt': NOW-60, 'outcome': 'running'})
        self.assertTrue(self.service.trigger()); self.complete()
        self.host.run.assert_not_called()
        self.store.save_discovery({**self.store.discovery_state(), 'outcome': 'running'})
        restarted = Discovery(self.store, self.host, self.fetcher, clock=lambda: NOW+1, searcher=self.searcher)
        self.assertTrue(restarted.trigger()); restarted.worker.join(5); restarted.close()

    def test_prompt_change_hides_stale_results_and_searches_without_waiting_day(self):
        self.like(); self.assertTrue(self.service.trigger()); self.complete()
        self.assertEqual(len(self.service.status()['items']),1)
        self.store.save_settings({'discovery_prompt':'日本語の記事だけ探してください。'})
        self.assertEqual(self.service.status()['items'],[])
        self.searcher.return_value=([{**self.candidates[0],'language':'ja'}],{})
        self.assertTrue(self.service.trigger()); self.complete()
        self.assertEqual(self.service.status()['plan']['language'],'ja')
        self.assertEqual(len(self.service.status()['items']),1)
        self.assertEqual(self.store.discovery_state()['result_prompt'],'日本語の記事だけ探してください。')

    def test_busy_refresh_coalesces_and_immediately_runs_latest_prompt(self):
        import time
        self.like(); entered,release=threading.Event(),threading.Event()
        count=0
        def search(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1: entered.set(); release.wait(3)
            return self.candidates,{}
        self.searcher.side_effect=search
        self.assertTrue(self.service.request_refresh());self.assertTrue(entered.wait(1))
        self.store.save_settings({'discovery_prompt':'日本語のみ'})
        self.assertFalse(self.service.request_refresh())
        self.store.save_settings({'discovery_prompt':'直近7日'})
        self.assertFalse(self.service.request_refresh())
        release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            state=self.service.status()
            if not state['running'] and state.get('result_prompt')=='直近7日':break
            time.sleep(.01)
        self.assertEqual(state['result_prompt'],'直近7日')
        self.assertEqual(count,2)
        self.assertEqual(state['plan']['max_age_days'],7)

    def test_pause_preserves_articles_without_source_registration(self):
        self.like(); self.assertTrue(self.service.trigger()); self.complete()
        self.store.save_settings({'discovery_enabled': False})
        self.assertFalse(self.service.trigger())
        self.assertEqual(len(self.service.status()['items']),1)
        self.assertEqual(self.store.sources(),[])
        self.assertIsNone(self.service.status()['next_run'])

    def test_busy_and_stop_prevent_duplicate_runs_and_late_writes(self):
        self.like(); entered, release = threading.Event(), threading.Event()
        def wait(*args, **kwargs):
            entered.set(); release.wait(5); return self.candidates, {}
        self.searcher.side_effect = wait
        self.assertTrue(self.service.trigger()); self.assertTrue(entered.wait(1))
        self.assertFalse(self.service.trigger(force=True))
        self.service.close(); release.set(); self.complete()
        self.assertEqual(self.store.discovery_state()['items'], [])
        self.assertFalse(self.service.trigger(force=True))

    def test_settings_validation_and_preference_threshold_before_pagination(self):
        liked = self.like()
        similar, _ = self.store.add_article({'url': 'https://known.example/next', 'title': 'robot next'})
        unknown, _ = self.store.add_article({'url': 'https://other.example/x', 'title': 'unrelated'})
        self.store.save_settings({'recommendation_min_preference': 1})
        data = self.store.list_items(limit=1, sort='updated')
        self.assertEqual(data['total'], 2)
        self.store.save_settings({'recommendation_min_preference': 2})
        self.assertEqual([i['id'] for i in self.store.list_items()['items']], [liked['id']])
        self.assertEqual(self.store.list_items('all')['total'], 3)
        self.store.save_settings({'recommendation_min_preference': 0})
        self.assertEqual(self.store.list_items()['total'], 2)  # unknown is not a scored zero
        self.store.save_settings({'recommendation_min_preference': None})
        self.assertEqual(self.store.list_items()['total'], 3)
        for bad in [True, 1.2, '1', 101, -101]:
            with self.assertRaises(ValueError): self.store.save_settings({'recommendation_min_preference': bad})
        for bad in [True, 0, 169, '24']:
            with self.assertRaises(ValueError): self.store.save_settings({'discovery_interval_hours': bad})
        with self.assertRaises(ValueError): self.store.save_settings({'discovery_enabled': 'true'})

    def test_api_auth_and_article_results(self):
        self.like(); self.service.trigger(); self.complete()
        server = Server(self.store, 'fixture-token')
        server.discovery = self.service
        worker = threading.Thread(target=server.serve_forever); worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(HTTPError) as error: urlopen(base + '/api/discovery')
            self.assertEqual(error.exception.code, 401)
            headers = {'Authorization': 'Bearer fixture-token', 'Content-Type': 'application/json'}
            with urlopen(Request(base + '/api/discovery', headers=headers)) as response:
                candidate = json.load(response)['items'][0]
            with self.assertRaises(HTTPError) as missing:
                urlopen(Request(base + '/api/discovery/subscribe', data=b'{}', headers=headers))
            self.assertEqual(missing.exception.code,404)
            self.assertEqual(candidate['url'],ROW['url'])
            self.assertEqual(self.store.sources(),[])
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_explicit_query_searches_without_good(self):
        self.store.save_settings({'agent_id': '', 'discovery_prompt': 'astronomy'})
        self.assertTrue(self.service.trigger(force=True))
        self.complete()
        self.assertEqual(self.service.status()['outcome'], 'success')
        self.assertEqual(self.searcher.call_args.kwargs['plan']['queries'], ['astronomy'])
