import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from xangi_feed.comment_references import present_comment
from xangi_feed.enrichment import Enricher
from xangi_feed.store import Store

SOURCES = [{'id': 'S2', 'path': 'projects/conversation-example.jsonl', 'excerpt': '勉強会の題材の提案'},
           {'id': 'S3', 'path': 'notes/scheduler-example.jsonl', 'excerpt': '自動処理によるアイデアの提案'}]


class CommentReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db', seed=False)
        self.addCleanup(self.store.close)
        self.item = self.store.add_article({'url': 'https://example.org/article', 'title': '勉強会の実演手順'})[0]
        self.store.save_settings({'agent_id': 'fixture'})

    def result(self, text):
        return {'id': self.item['id'], 'title_ja': '勉強会の実演手順', 'summary_ja': '実演の進め方を紹介します。',
                'comment': text, 'comment_version': 1, 'evidence_ids': ['S2', 'S3'],
                'comment_context': {'sources': SOURCES, 'status': 'ready'}}

    def test_citation_and_filename_prose_is_rewritten_not_renamed(self):
        for text in ['関連記録S2・S3には企画への期待が示されています。',
                     '「conversation-example.jsonl」・「scheduler-example.jsonl」に期待が示されています。',
                     '[S2, S3]を参考にできます。', 'S2とS3を参考にできます。']:
            with self.subTest(text=text):
                original = self.result(text)
                displayed = present_comment(original)
                self.assertEqual(displayed['comment'], '')
                self.assertTrue(displayed['comment_needs_rewrite'])
                self.assertEqual(displayed['summary_ja'], original['summary_ja'])
                self.assertEqual(original['comment'], text)
                self.assertEqual(displayed['comment_context'], original['comment_context'])

    def test_useful_comment_and_technical_names_are_kept(self):
        for text in ['勉強会の題材を探すなら、記事の手順を短い実演にして紹介できそうです。',
                     'Amazon S3を使えます。', 'AWS S3を使えます。', 'S3互換ストレージです。',
                     'https://example.org/S2 を使います。', 'S2.md の記録です。', 'S2Dモデルです。']:
            with self.subTest(text=text):
                original = self.result(text)
                self.assertEqual(present_comment(original), original)

    def test_unknown_explicit_reference_is_not_invented(self):
        displayed = present_comment(self.result('関連記録S99を参考にします。'))
        self.assertTrue(displayed['comment_needs_rewrite'])
        self.assertEqual(displayed['comment'], '')

    def test_store_readers_and_pending_cache_preserve_original(self):
        original = self.result('関連記録S2・S3に期待が示されています。')
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'done', original)
        detail = self.store.item(self.item['id'])['analysis']['result']
        listed = self.store.list_items(tab='all')['items'][0]['analysis']['result']
        self.assertTrue(detail['comment_needs_rewrite'])
        self.assertEqual(detail, listed)
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'pending')
        raw = json.loads(self.store.db.execute('SELECT result FROM analyses').fetchone()[0])
        self.assertEqual(raw['comment'], original['comment'])

    def test_only_bad_visible_comment_is_queued_for_rewrite(self):
        host = Mock(configured=True)
        worker = Enricher(self.store, host)
        self.addCleanup(worker.close)
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'done', self.result('関連記録S2を参考にします。'))
        self.assertEqual(worker.enqueue([self.item]), 1)
        self.assertEqual(worker.enqueue([self.item]), 0)
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'done', self.result('記事の手順を実演するときに使えます。'))
        self.assertEqual(worker.enqueue([self.item]), 0)

    def worker(self, comment):
        host = Mock(configured=True)
        host.request.side_effect = lambda *a, **kw: io.BytesIO(json.dumps({'results': [
            {'file_path': 'notes/conversation-example.jsonl', 'content': 'AIの自動提案: 勉強会の題材を考える'}]}).encode())
        host.run.return_value = json.dumps({'items': [{**self.result(comment), 'evidence_ids': ['S1']}]})
        worker = Enricher(self.store, host)
        self.addCleanup(worker.close)
        return worker, host

    def test_model_gets_content_without_path_and_returns_useful_comment(self):
        comment = '勉強会の題材を探すなら、記事の手順を短い実演にして紹介できそうです。'
        worker, host = self.worker(comment)
        worker.process([(self.item['id'], self.store.content_hash(self.item))])
        cached = self.store.item(self.item['id'])['analysis']
        self.assertEqual(cached['status'], 'done')
        self.assertEqual(cached['result']['comment'], comment)
        prompt = host.run.call_args.args[0]
        self.assertIn('AIの自動提案', prompt)
        self.assertIn('発言者や出所は未確認', prompt)
        self.assertNotIn('conversation-example.jsonl', prompt)

    def test_new_bad_reply_is_rejected_with_summary_cache_intact(self):
        original = self.result('記事の手順を実演するときに使えます。')
        self.store.save_analysis(self.item['id'], self.store.content_hash(self.item), 'done', original)
        worker, host = self.worker('関連記録S1には期待が示されています。')
        worker.process([(self.item['id'], self.store.content_hash(self.item))])
        cached = self.store.item(self.item['id'])['analysis']
        self.assertEqual(cached['status'], 'error')
        self.assertEqual(cached['result']['comment'], original['comment'])
        self.assertEqual(cached['result']['summary_ja'], original['summary_ja'])

    def test_no_comment_keeps_none(self):
        self.assertIsNone(present_comment(None))
