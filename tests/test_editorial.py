import io
import json
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import Mock, patch

from xangi_feed.enrichment import Enricher, parse_results
from xangi_feed.service import HostClient
from xangi_feed.store import Store


def result(item_id):
    return {'id': item_id, 'title_ja': '日本語の研究タイトル', 'summary_ja': '研究の要点です。',
            'comment': 'ロボットの音声入力を試す際の条件を確認できます。', 'evidence_ids': []}


def entry(i, kind='tech'):
    return {'title': f'GPU robot {i}', 'url': f'https://example.org/{kind}/{i}', 'summary': 'Agent study with limitations.',
            'published': 1793700000 + i, 'updated': None, 'audio_url': None, 'topics': ['ロボット']}


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'news.db', seed=False)
        sid = self.store.add_source('Tech', 'https://example.org/feed', 'tech')
        self.store.ingest(self.store.source(sid), [entry(1)])
        self.item = self.store.list_items()['items'][0]
        self.store.save_settings({'agent_id': 'fixture-agent'})
        self.host = Mock(configured=True)
        self.host.request.side_effect = lambda *args, **kwargs: io.BytesIO(b'{"results":[]}')
        self.host.run.return_value = json.dumps({'items': [result(self.item['id'])]}, ensure_ascii=False)
        self.enricher = Enricher(self.store, self.host)

    def tearDown(self):
        self.enricher.close()
        self.store.close()
        self.tmp.cleanup()

    def test_cached_analysis_is_japanese_summary_without_judgement(self):
        self.enricher.enqueue([self.item])
        batch = list(self.enricher.pending.items()); self.enricher.pending.clear()
        self.enricher.process(batch)
        self.assertEqual(self.store.item(self.item['id'])['analysis']['status'], 'done')
        cached = self.store.list_items(tab='all')['items'][0]['analysis']['result']
        self.assertEqual(set(cached), {'title_ja', 'summary_ja', 'comment', 'evidence_ids', 'comment_version', 'comment_context'})
        self.assertEqual(cached['comment_version'], 1)
        self.assertEqual(self.enricher.enqueue([self.item]), 0)
        self.assertEqual(self.enricher.enqueue([self.item], retry=True), 1)
        self.assertIn('ツール実行', self.host.run.call_args.args[0])

    def test_invalid_english_response_is_failure_not_fake_translation(self):
        item = result(self.item['id']); item['summary_ja'] = 'A generic summary'
        self.host.run.return_value = json.dumps([item])
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        analysis = self.store.item(self.item['id'])['analysis']
        self.assertEqual(analysis['status'], 'error')
        self.assertIsNone(analysis['result'])
        self.assertEqual(self.enricher.enqueue([self.item]), 0)

    def test_disabled_means_no_new_ai_jobs(self):
        self.store.save_settings({'enabled': False})
        self.assertEqual(self.enricher.enqueue([self.item], retry=True), 0)
        self.host.run.assert_not_called()


    def test_content_change_during_generation_is_not_committed_as_current(self):
        def run(*_):
            self.store.ingest(self.store.source(self.item['sources'][0]['id']), [{**entry(1), 'summary': '記事本文が更新された'}])
            return json.dumps([result(self.item['id'])], ensure_ascii=False)
        self.host.run.side_effect = run
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        self.assertNotEqual(self.store.analysis(self.item['id'])['status'], 'done')

    def test_balance_categories_before_paging(self):
        for kind in ['paper', 'blog', 'podcast']:
            sid = self.store.add_source(kind, f'https://example.org/{kind}/feed', kind)
            self.store.ingest(self.store.source(sid), [entry(i, kind) for i in range(100 if kind == 'paper' else 3)])
        first = self.store.list_items(limit=4, sort='recommended')['items']
        self.assertEqual({i['kind'] for i in first}, {'tech', 'paper', 'blog', 'podcast'})
        self.assertTrue(all(i['kind'] == 'paper' for i in self.store.list_items(tab='all', limit=10)['items']))

    def test_settings_validation(self):
        for data in [{'automatic_limit': True}, {'automatic_limit': 101}, {'enabled': 1}, {'token': 'bad'}, {'preferences': 1}, {'timeout_seconds': True}, {'timeout_seconds': 0}, {'timeout_seconds': 1801}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.save_settings(data)

    def test_all_includes_unrated_unanalysed_and_is_chronological(self):
        source = self.store.source(self.store.add_source('Papers', 'https://example.org/papers', 'paper'))
        self.store.ingest(source, [entry(i, 'paper') for i in range(60)])
        first = self.store.list_items(tab='all', limit=50)
        last = self.store.list_items(tab='all', limit=50, offset=50)
        self.assertEqual((first['total'], len(first['items']), len(last['items'])), (61, 50, 11))
        self.assertTrue(all(i['rating'] == 0 and i['analysis'] is None for i in first['items']))
        self.assertEqual(first['items'][0]['published'], entry(59)['published'])

    def test_agent_and_timeout_persist_and_are_passed_to_next_generation(self):
        self.store.save_settings({'agent_id': 'fixture-second', 'timeout_seconds': 45})
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        self.assertEqual(self.host.run.call_args.args[1], 'fixture-second')
        self.assertEqual(self.host.run.call_args.kwargs['timeout'], 45)
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'news.db', seed=False)
        self.assertEqual(self.store.settings()['agent_id'], 'fixture-second')
        self.assertEqual(self.store.settings()['timeout_seconds'], 45)

    def test_recommendation_filters_leave_all_untouched(self):

        self.assertEqual(self.store.list_items()['total'], 1)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])

        self.assertEqual(self.store.list_items()['total'], 1)
        self.store.react(self.item['id'], {'rating': -1})
        self.store.save_settings({'recommendation_hide_bad': True})
        self.assertEqual(self.store.list_items()['total'], 0)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)

    def test_date_window_unknown_date_and_unanalysed_toggle(self):
        src = self.store.source(self.item['sources'][0]['id'])
        current = entry(2); current.update(published=None, url='https://example.org/today')
        old = entry(3); old.update(published=time.time()-10*86400, url='https://example.org/old')
        self.store.ingest(src, [current, old])
        self.store.save_settings({'recommendation_days': 1})
        found = self.store.list_items()['items']
        self.assertTrue(any(i['url'].endswith('/today') for i in found))
        self.assertFalse(any(i['url'].endswith('/old') for i in found))
        self.assertEqual(self.store.list_items(tab='all')['total'], 3)


    def test_recommendation_change_keeps_cached_comment_and_ai_settings(self):
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        self.store.save_settings({'recommendation_days': 7, 'recommendation_priority': 'fresh', 'recommendation_mix_types': False})
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'news.db', seed=False)
        self.assertEqual(self.store.settings()['recommendation_days'], 7)
        self.assertEqual(self.store.settings()['agent_id'], 'fixture-agent')
        self.assertEqual(self.store.item(self.item['id'])['analysis']['status'], 'done')
        self.assertEqual(self.host.run.call_count, 1)

    def test_invalid_recommendation_settings(self):
        for data in [{'recommendation_days': -1}, {'recommendation_days': True}, {'recommendation_days': 366},
                     {'recommendation_min_importance': 1}, {'recommendation_include_unanalysed': True},
                     {'recommendation_hide_bad': 'yes'}, {'recommendation_mix_types': 1},
                     {'recommendation_priority': 'invalid'}, {'recommendation_priority': []}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.save_settings(data)

    def test_priority_changes_ranking_and_mix_can_be_disabled(self):
        src = self.store.source(self.item['sources'][0]['id'])
        old, new = entry(5), entry(6)
        old.update(title='candidate old', published=time.time()-48*3600)
        new.update(title='candidate new', published=time.time(), topics=[])
        self.store.ingest(src, [old, new])
        self.store.react(self.item['id'], {'rating': 1})
        self.store.save_settings({'recommendation_priority': 'fresh', 'recommendation_mix_types': False})
        self.assertEqual(self.store.list_items(q='candidate', sort='recommended')['items'][0]['title'], 'candidate new')
        self.store.save_settings({'recommendation_priority': 'interest'})
        self.assertEqual(self.store.list_items(q='candidate', sort='recommended')['items'][0]['title'], 'candidate old')
        blog = self.store.source(self.store.add_source('Blog', 'https://example.org/blog', 'blog'))
        older = entry(7, 'blog');older['published'] = time.time()-365*86400
        self.store.ingest(blog, [older])
        self.assertEqual([i['kind'] for i in self.store.list_items(limit=2, sort='recommended')['items']], ['tech', 'tech'])
        self.store.save_settings({'recommendation_mix_types': True})
        self.assertEqual([i['kind'] for i in self.store.list_items(limit=2, sort='recommended')['items']], ['tech', 'blog'])

    def test_strict_display_does_not_starve_automatic_analysis(self):
        src = self.store.source(self.item['sources'][0]['id'])
        item = entry(9);item['published'] = time.time()
        self.store.ingest(src, [item])

        self.assertGreater(self.store.list_items()['total'], 0)
        self.enricher.automatic()
        self.assertGreater(self.enricher.status()['queued'], 0)

    def test_ai_output_validates_ids_and_summary_fields(self):
        item_id = self.item['id']
        for values in [[], [result('unknown')], [result(item_id), result(item_id)], [{**result(item_id), 'title_ja': ''}], [{**result(item_id), 'summary_ja': ''}]]:
            self.assertRaises(ValueError, parse_results, json.dumps(values), {item_id})
        self.assertIn(item_id, parse_results('```json\n' + json.dumps([result(item_id)]) + '\n```', {item_id}))

    def test_summary_prompt_does_not_send_preferences_or_request_judgement(self):
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        prompt = self.host.run.call_args.args[0]
        self.assertNotIn('private-preference-fixture', prompt)
        self.assertNotIn('Good/Badの話題傾向', prompt)
        self.assertNotIn('"importance"', prompt)
        self.assertNotIn('"reason"', prompt)

    def test_search_records_feed_comment_and_only_used_sources_are_saved(self):
        sources = [{'file_path': 'projects/voice.md', 'content': 'ロボットの音声入力を試作中です。'},
                   {'file_path': 'projects/other.md', 'content': '別の作業です。'}]
        self.host.request.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({'results': sources}).encode())
        self.host.run.return_value = json.dumps([{**result(self.item['id']), 'evidence_ids': ['S1'],
                                                 'comment_context': {'sources': [{'path': 'made-up.md'}]}}])
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        cached = self.store.item(self.item['id'])['analysis']['result']
        self.assertEqual(cached['comment_context']['status'], 'ready')
        self.assertEqual(cached['comment_context']['sources'], [{'id': 'S1', 'path': 'projects/voice.md', 'excerpt': sources[0]['content']}])
        self.assertIn('ロボットの音声入力を試作中です。', self.host.run.call_args.args[0])
        self.assertEqual(self.store.profile()['good'], 0)
        self.assertEqual(self.store.item(self.item['id'])['preference_score'], 0)

    def test_search_failure_does_not_block_article_comment_or_fake_evidence(self):
        self.host.request.side_effect = TimeoutError('secret-upstream-detail')
        self.enricher.process([(self.item['id'], self.store.content_hash(self.item))])
        cached = self.store.item(self.item['id'])['analysis']['result']
        self.assertEqual(cached['comment_context']['status'], 'error')
        self.assertEqual(cached['comment_context']['sources'], [])
        self.assertTrue(cached['comment'])
        self.assertNotIn('secret-upstream-detail', self.host.run.call_args.args[0])

    def test_unknown_cross_article_or_duplicate_citations_are_rejected(self):
        item_id = self.item['id']
        for refs in [['S9'], ['S1', 'S1'], ['S1', {}], 'S1']:
            value = {**result(item_id), 'evidence_ids': refs}
            self.assertRaises(ValueError, parse_results, json.dumps([value]), {item_id}, {item_id: ['S1']})
        value = {**result(item_id), 'comment': ''}
        self.assertRaises(ValueError, parse_results, json.dumps([value]), {item_id})
        cross_article = [{**result(item_id), 'evidence_ids': ['S2']}, result('other-article')]
        self.assertRaises(ValueError, parse_results, json.dumps(cross_article), {item_id, 'other-article'},
                          {item_id: ['S1'], 'other-article': ['S2']})

    def test_adding_comments_preserves_old_summary_while_pending_and_on_failure(self):
        item_id = self.item['id']; input_hash = self.store.content_hash(self.item)
        old = {'title_ja': '既存の日本語タイトル', 'summary_ja': '既存の要約を保持する。'}
        self.store.save_analysis(item_id, input_hash, 'done', old)
        self.assertEqual(self.enricher.enqueue([self.item]), 1)
        analysis = self.store.item(item_id)['analysis']
        self.assertEqual(analysis['status'], 'pending')
        self.assertEqual(analysis['result'], old)
        self.host.run.return_value = 'invalid json'
        self.enricher.process([(item_id, input_hash)])
        analysis = self.store.item(item_id)['analysis']
        self.assertEqual(analysis['status'], 'error')
        self.assertEqual(analysis['result'], old)

    def test_old_summary_is_not_reused_for_changed_article(self):
        item_id = self.item['id']
        self.store.save_analysis(item_id, self.store.content_hash(self.item), 'done', result(item_id))
        self.store.save_analysis(item_id, 'different-content-hash', 'pending')
        self.assertIsNone(self.store.analysis(item_id)['result'])


    def test_duplicate_bad_does_not_penalize_interest(self):
        src = self.store.source(self.item['sources'][0]['id'])
        self.store.ingest(src, [{**entry(2), 'published': self.item['published']}])
        self.store.react(self.item['id'], {'rating': -1, 'reason': 'duplicate'})
        rows = self.store.list_items(sort='preference')['items']
        self.assertEqual([row['preference_score'] for row in rows], [0, 0])
        self.assertEqual(rows[0]['score'], rows[1]['score'])


    def test_existing_install_gets_catalog_without_resetting_reactions(self):
        self.store.react(self.item['id'], {'rating': 1, 'saved': True})
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'news.db')
        sources = self.store.sources()
        from xangi_feed.catalog import export
        from xangi_feed.feeds import web_url
        for definition in export('feed'):
            self.assertTrue(any(s['url'] == web_url(definition['url']) and s['kind'] == definition['kind'] for s in sources))
        self.assertEqual((self.store.item(self.item['id'])['rating'], self.store.item(self.item['id'])['saved']), (1, 1))
        self.store.enable_source(sources[-1]['id'], False)
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'news.db')
        self.assertEqual(self.store.source(sources[-1]['id'])['enabled'], 0)


    def test_unsent_pending_is_restored_but_processing_is_not_resent(self):
        self.enricher.enqueue([self.item])
        self.enricher.pending.clear()
        self.enricher.restore_pending()
        self.assertIn(self.item['id'], self.enricher.pending)
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'processing')
        self.store.close(); self.store = Store(Path(self.tmp.name) / 'news.db', seed=False)
        enricher = Enricher(self.store, self.host)
        enricher.restore_pending()
        self.assertEqual(enricher.status()['queued'], 0)
        self.assertEqual(self.store.analysis(self.item['id'])['status'], 'error')

    def test_failed_analysis_retries_after_cooldown_and_on_restart(self):
        key = self.store.content_hash(self.item)
        self.store.save_analysis(self.item['id'], key, 'error', error='failed')
        self.assertEqual(self.enricher.enqueue([self.item]), 0)
        with self.store.transaction() as db:
            db.execute('UPDATE analyses SET updated=0')
        self.enricher.restore_pending()
        self.assertEqual(self.enricher.status()['queued'], 1)
        self.enricher.process([(self.item['id'], key)])
        self.assertEqual(self.store.analysis(self.item['id'])['status'], 'done')

    def test_worker_sends_one_article_so_other_articles_survive_failure(self):
        other, _ = self.store.add_article({'url':'https://example.org/second', 'title':'second'})
        calls = []
        def run(prompt, *args, **kwargs):
            values = json.loads(prompt.split('記事データ: ')[1])
            calls.append([v['id'] for v in values])
            self.assertEqual(len(values), 1)
            if values[0]['id'] == self.item['id']:
                return 'invalid'
            self.enricher.stop.set()
            return json.dumps([result(other['id'])])
        self.host.run.side_effect = run
        self.enricher.enqueue([self.item, other])
        self.enricher.start()
        self.enricher.worker.join(5)
        self.assertFalse(self.enricher.worker.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.analysis(self.item['id'])['status'], 'error')
        self.assertEqual(self.store.analysis(other['id'])['status'], 'done')


class SSETests(unittest.TestCase):
    def test_done_response_and_session_close(self):
        host = HostClient('http://127.0.0.1:12345', 'fixture-token')
        host.create = Mock(return_value='fixture-session')
        calls = []
        def request(path, *args, **kwargs):
            calls.append(path)
            return io.BytesIO(b'event: done\ndata: {"response":"Japanese output"}\n\n')
        host.request = request
        self.assertEqual(host.run('prompt', 'agent-1'), 'Japanese output')
        self.assertTrue(calls[-1].endswith('/close'))
        host.create.assert_called_once_with('agent-1', automation=True)

    def test_close_failure_does_not_discard_completed_response(self):
        host = HostClient('http://127.0.0.1:12345', 'fixture-token')
        host.create = Mock(return_value='fixture-session')
        def request(path, *args, **kwargs):
            if path.endswith('/close'): raise TimeoutError('cleanup')
            return io.BytesIO(b'event: done\ndata: {"response":"valid output"}\n\n')
        host.request = request
        self.assertEqual(host.run('prompt', 'agent-1'), 'valid output')

    def test_local_editorial_uses_selected_model_and_larger_output_budget(self):
        host = HostClient('http://127.0.0.1:12345', 'host-secret')
        host.agents = Mock(return_value=[{'id':'chosen','backend':'local-llm','model':'chosen-model'}])
        host.create = Mock()
        response = Mock()
        response.read.return_value = json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}]}).encode()
        host.opener = Mock()
        host.opener.open.return_value.__enter__ = Mock(return_value=response)
        host.opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.dict('os.environ', {'LOCAL_LLM_BASE_URL':'http://127.0.0.1:8080/v1','LOCAL_LLM_API_KEY':'model-secret'}):
            self.assertEqual(host.run('prompt', 'chosen', timeout=90), '{"items":[]}')
            payload = json.loads(host.opener.open.call_args.args[0].data)
            self.assertEqual((payload['model'], payload['max_tokens']), ('chosen-model',2048))
            self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
            self.assertEqual(host.opener.open.call_args.kwargs['timeout'], 90)
            self.assertNotIn('host-secret', repr(host.opener.open.call_args.args[0].headers))
            host.create.assert_not_called()
            response.read.return_value = json.dumps({'choices':[{'finish_reason':'length','message':{'content':'partial'}}]}).encode()
            with self.assertRaisesRegex(ValueError, '出力上限'):
                host.run('prompt','chosen')

    def test_error_or_truncated_stream_still_closes(self):
        for data in [b'event: error\ndata: {}\n\n', b'event: delta\ndata: {}\n\n']:
            host = HostClient('http://127.0.0.1:12345', 'fixture-token')
            host.create = Mock(return_value='s1'); calls = []
            def request(path, *args, **kwargs):
                calls.append(path); return io.BytesIO(data)
            host.request = request
            self.assertRaises(ValueError, host.run, 'prompt', 'agent-1')
            self.assertTrue(calls[-1].endswith('/close'))


if __name__ == '__main__':
    unittest.main()
