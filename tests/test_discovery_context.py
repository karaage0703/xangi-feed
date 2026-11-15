import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from xangi_feed.discovery_context import personalize, connect_items
from xangi_feed.discovery import Discovery
from xangi_feed.store import Store


PLAN = {'queries': [], 'language': 'ja', 'exclude_terms': ['広告'], 'max_age_days': 7}
THEME = {'query': '音声対話 ロボット', 'reason': '音声で応答する試作の実装に使う記事を探します。', 'source_ids': ['R1']}


class PersonalDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.host = Mock(configured=True)
        self.host.request.return_value = io.BytesIO(json.dumps({'results': [
            {'file_path': 'notes/robot.md', 'content': '架空の音声対話ロボットを試作している。'},
            {'file_path': 'notes/unused.md', 'content': '過去の作業。'}]}).encode())
        self.host.plan_search.return_value = json.dumps({'themes': [THEME]})

    def test_record_changes_search_themes_and_preserves_explicit_constraints(self):
        plan, context = personalize({**PLAN, 'notice': '入力文の先頭を使います'}, '日本語のみ。直近7日。広告は除く。', [], self.host, 'chosen')
        self.assertEqual(plan, {**PLAN, 'queries': [THEME['query']]})
        self.assertEqual(context['status'], 'ready')
        self.assertEqual([s['id'] for s in context['sources']], ['R1'])
        prompt = self.host.plan_search.call_args.args[0]
        self.assertIn('音声対話ロボット', prompt)
        self.assertIn('ファイル読取', prompt)
        self.assertEqual(self.host.plan_search.call_args.args[1], 'chosen')
        params = parse_qs(urlsplit(self.host.request.call_args.args[0]).query)
        self.assertEqual(params['mode'], ['hybrid'])
        self.assertIn('プロジェクト', params['q'][0])

    def test_unavailable_empty_and_search_failure_preserve_original_plan(self):
        self.host.configured = False
        self.assertEqual(personalize(PLAN, '', [], self.host, 'chosen')[0], PLAN)
        self.host.request.assert_not_called()
        self.host.configured = True
        self.host.request.return_value = io.BytesIO(b'{"results":[]}')
        self.assertEqual(personalize(PLAN, '', [], self.host, 'chosen')[1]['status'], 'empty')
        self.host.request.side_effect = TimeoutError('secret upstream URL')
        plan, context = personalize(PLAN, '', [], self.host, 'chosen')
        self.assertEqual(plan, PLAN)
        self.assertNotIn('secret', repr(context))
        self.host.plan_search.assert_not_called()

    def test_bad_ai_output_unknown_citations_and_identifiers_fall_back(self):
        for theme in [None, {**THEME, 'source_ids': ['invented']}, {**THEME, 'source_ids': []},
                      {**THEME, 'query': 'notes/robot.md'}, {**THEME, 'query': 'alice@example.org'},
                      {**THEME, 'reason': 'x' * 181}, {**THEME, 'source_ids': [{}]}]:
            with self.subTest(theme=theme):
                self.setUp()
                self.host.plan_search.return_value = json.dumps({'themes': [theme]})
                plan, context = personalize(PLAN, '', [], self.host, 'chosen')
                self.assertEqual(plan, PLAN)
                self.assertEqual(context['status'], 'error')
                self.assertEqual(context['sources'], [])

    def test_ai_timeout_and_no_relevant_theme_fall_back(self):
        self.host.plan_search.side_effect = TimeoutError('secret')
        self.assertEqual(personalize(PLAN, '', [], self.host, 'chosen')[0], PLAN)
        self.setUp()
        self.host.plan_search.return_value = '{"themes":[]}'
        self.assertEqual(personalize(PLAN, '', [], self.host, 'chosen')[1]['status'], 'empty')

    def test_only_matching_candidate_receives_used_records(self):
        _, context = personalize(PLAN, '', [], self.host, 'chosen')
        items = [{'matched_query': THEME['query']}, {'matched_query': '別の検索'}, {}]
        connect_items(items, context)
        self.assertEqual(items[0]['personal_sources'][0]['path'], 'notes/robot.md')
        self.assertEqual(items[0]['personal_reason'], THEME['reason'])
        self.assertNotIn('personal_reason', items[1])
        self.assertNotIn('personal_sources', items[2])

    def test_discovery_without_good_or_prompt_uses_records_and_toggle_hides_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db', seed=False)
            self.addCleanup(store.close)
            store.save_settings({'agent_id': 'chosen'})
            searcher = Mock(return_value=([{'url': 'https://new.example/article', 'feed_url': 'https://new.example/rss',
                                           'matched_query': THEME['query']}], {}))
            service = Discovery(store, self.host, searcher=searcher)
            self.addCleanup(service.close)
            self.assertTrue(service.trigger())
            service.worker.join(3)
            self.assertFalse(service.worker.is_alive())
            self.assertEqual(service.status()['items'][0]['personal_reason'], THEME['reason'])
            # Only public search conditions reach the searcher, not raw records or paths.
            self.assertNotIn('notes/', repr(searcher.call_args))
            self.assertNotIn('架空の', repr(searcher.call_args))
            self.assertEqual(store.profile()['topics'], [])
            store.save_settings({'discovery_use_personal_context': False})
            self.assertEqual(service.status()['items'], [])
            self.assertIsNone(service.status()['context'])
            self.assertFalse(service.trigger(force=True))
            self.assertIn('Good', service.status()['error'])
            self.assertEqual(service.status()['context']['status'], 'disabled')

    def test_toggle_during_run_discards_old_result_and_reruns_without_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db', seed=False)
            self.addCleanup(store.close)
            store.save_settings({'agent_id': 'chosen', 'discovery_prompt': '日本語だけ'})
            item = store.add_article({'title': 'robot', 'url': 'https://known.example/robot'})[0]
            store.react(item['id'], {'rating': 1})
            entered, release = threading.Event(), threading.Event()
            def search(*args, **kwargs):
                entered.set()
                release.wait(3)
                return [], {}
            service = Discovery(store, self.host, searcher=search)
            self.addCleanup(service.close)
            self.assertTrue(service.trigger())
            self.assertTrue(entered.wait(3))
            store.save_settings({'discovery_use_personal_context': False})
            release.set()
            worker = service.worker
            worker.join(3)
            service.worker.join(3)
            self.assertFalse(service.worker.is_alive())
            self.assertEqual(service.status()['context']['status'], 'disabled')
            self.assertEqual(self.host.request.call_count, 1)

    def test_setting_only_accepts_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db', seed=False)
            self.addCleanup(store.close)
            for value in ['true', 1, None]:
                with self.assertRaises(ValueError):
                    store.save_settings({'discovery_use_personal_context': value})
