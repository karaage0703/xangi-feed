import io
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from xangi_feed.service import Discussions, HostClient
from xangi_feed.store import Store


class SortDiscussionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'news.db'
        self.store = Store(self.path, seed=False)
        self.source = self.store.source(self.store.add_source('Test', 'https://example.org/feed', 'tech'))
        self.entries = [self.entry('a', 100, 300), self.entry('b', 200, 250), self.entry('c', 250)]
        self.store.ingest(self.source, self.entries)
        self.ids = {i['title']: i['id'] for i in self.store.list_items()['items']}
        self.host = Mock(configured=True, agent_id='')
        self.host.agents.return_value = [{'id': 'agent-a', 'available': True}, {'id': 'agent-b', 'available': True}, {'id': 'disabled', 'available': False}]
        self.host.create.return_value = 'session-1'
        self.host.session.return_value = {'lifecycle': 'open', 'isActive': False}
        self.discussions = Discussions(self.store, self.host)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def entry(self, name, published, updated=None):
        return {'title': name, 'url': 'https://example.org/' + name, 'summary': 'Public feed text.',
                'published': published, 'updated': updated, 'topics': ['ロボット'], 'audio_url': None}

    def test_updated_default_and_publication_sort_are_distinct(self):
        self.assertEqual(self.store.list_items()['items'][0]['title'], 'a')
        self.assertEqual(self.store.list_items(sort='published')['items'][0]['title'], 'c')
        # Epoch 0 is a real date, not an unknown date.
        self.store.ingest(self.source, [self.entry('epoch', 0)])
        self.assertEqual(self.store.list_items(sort='published')['items'][-1]['title'], 'epoch')
        self.store.ingest(self.source, [self.entry('undated', None)])
        self.assertEqual(self.store.list_items()['items'][0]['title'], 'undated')

    def test_preference_sort_uses_good_bad(self):
        self.store.react(self.ids['c'], {'rating': 1})
        self.assertEqual(self.store.list_items(sort='preference')['items'][0]['title'], 'c')
        with self.assertRaises(ValueError): self.store.list_items(sort='importance')

    def test_revised_rss_date_and_known_update_are_preserved(self):
        self.store.ingest(self.source, [self.entry('b', 50)])
        self.assertEqual(self.store.item(self.ids['b'])['published'], 200)
        self.assertEqual(self.store.item(self.ids['b'])['updated'], 250)
        revised = self.entry('c', 500)
        self.store.ingest(self.source, [revised])
        self.assertEqual(self.store.item(self.ids['c'])['published'], 250)
        self.assertEqual(self.store.item(self.ids['c'])['updated'], 500)
        self.assertEqual(self.store.list_items()['items'][0]['title'], 'c')
        self.store.ingest(self.source, [self.entry('c', 250)])
        self.assertEqual(self.store.item(self.ids['c'])['updated'], 500)

    def test_date_window_uses_update_date(self):
        self.store.ingest(self.source, [self.entry('a', 100, time.time())])
        self.store.save_settings({'recommendation_days': 1})
        self.assertEqual([i['title'] for i in self.store.list_items()['items']], ['a'])
        self.assertEqual(self.store.list_items(tab='all')['total'], 3)

    def test_invalid_sort(self):
        self.assertRaises(ValueError, self.store.list_items, sort='arbitrary')

    def test_prompt_agent_and_same_request_are_sent_once(self):
        request_id = str(uuid.uuid4())
        result = self.discussions.open(self.ids['a'], prompt='自分で試す手順を教えて', agent_id='agent-a', request_id=request_id)
        repeated = self.discussions.open(self.ids['a'], prompt='自分で試す手順を教えて', agent_id='agent-a', request_id=request_id)
        self.assertEqual(result, repeated)
        self.host.create.assert_called_once_with(agent_id='agent-a')
        self.host.send.assert_called_once()
        self.assertIn('自分で試す手順を教えて', self.host.send.call_args.args[1])
        self.assertIn('https://example.org/a', self.host.send.call_args.args[1])
        self.assertEqual(self.store.item(self.ids['a'])['discussion_agent_id'], 'agent-a')
        self.assertRaises(ValueError, self.discussions.open, self.ids['a'], prompt='違う内容', request_id=request_id)

    def test_followup_existing_and_empty_resume(self):
        self.discussions.open(self.ids['a'])
        request_id = str(uuid.uuid4())
        self.discussions.open(self.ids['a'], prompt='追加の質問です', request_id=request_id)
        self.discussions.open(self.ids['a'], prompt='追加の質問です', request_id=request_id)
        self.assertEqual(self.host.send.call_count, 2)
        self.assertIn('追加の質問です', self.host.send.call_args.args[1])
        self.assertNotIn('Public feed text.', self.host.send.call_args.args[1])
        self.discussions.open(self.ids['a'])
        self.assertEqual(self.host.send.call_count, 2)

    def test_new_agent_requires_new_conversation(self):
        self.host.create.side_effect = ['session-1', 'session-2']
        self.discussions.open(self.ids['a'], agent_id='agent-a', request_id=str(uuid.uuid4()))
        self.assertRaises(ValueError, self.discussions.open, self.ids['a'], agent_id='agent-b', request_id=str(uuid.uuid4()))
        result = self.discussions.open(self.ids['a'], agent_id='agent-b', new_conversation=True, request_id=str(uuid.uuid4()))
        self.assertEqual(result['session_id'], 'session-2')
        self.assertEqual(self.host.create.call_count, 2)

    def test_busy_or_closed_followup_is_not_sent(self):
        self.discussions.open(self.ids['a'])
        for state in [{'lifecycle': 'open', 'isActive': True}, {'lifecycle': 'closed'}]:
            self.host.session.return_value = state
            self.assertRaises(ValueError, self.discussions.open, self.ids['a'], prompt='追記', request_id=str(uuid.uuid4()))
        self.assertEqual(self.host.send.call_count, 1)

    def test_timeout_after_send_does_not_repeat_even_after_restart(self):
        self.host.send.side_effect = TimeoutError()
        request_id = str(uuid.uuid4())
        result = self.discussions.open(self.ids['a'], prompt='質問', request_id=request_id)
        self.assertEqual(result['state'], 'uncertain')
        self.store.close(); self.store = Store(self.path, seed=False)
        repeated = Discussions(self.store, self.host).open(self.ids['a'], prompt='質問', request_id=request_id)
        self.assertEqual(result, repeated)
        self.host.send.assert_called_once()

    def test_creation_timeout_does_not_repeat(self):
        self.host.create.side_effect = TimeoutError()
        request_id = str(uuid.uuid4())
        self.assertRaises(TimeoutError, self.discussions.open, self.ids['a'], prompt='質問', request_id=request_id)
        self.assertRaises(ValueError, self.discussions.open, self.ids['a'], prompt='質問', request_id=request_id)
        self.host.create.assert_called_once()

    def test_pending_journal_becomes_uncertain_on_restart(self):
        request_id = str(uuid.uuid4())
        self.store.begin_discussion_request(request_id, self.ids['a'], 'fingerprint')
        self.store.close(); self.store = Store(self.path, seed=False)
        self.assertRaises(ValueError, self.store.discussion_request, request_id, 'fingerprint')

    def test_invalid_options_are_not_sent(self):
        for data in [{'prompt': 1}, {'prompt': 'x' * 4001}, {'prompt': '質問'}, {'agent_id': 'disabled', 'request_id': str(uuid.uuid4())}, {'new_conversation': 1}, {'request_id': 'not-uuid'}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.discussions.open(self.ids['a'], **data)
        self.host.send.assert_not_called()

    def test_host_create_explicitly_uses_no_project(self):
        host = HostClient('http://127.0.0.1:12345', 'fixture-token')
        with patch.object(host, 'request', return_value=io.BytesIO(json.dumps({'sessionId': 's1'}).encode())) as request:
            self.assertEqual(host.create(agent_id='agent-a'), 's1')
        request.assert_called_once_with('/api/sessions', {'projectId': None, 'agentId': 'agent-a'})


if __name__ == '__main__':
    unittest.main()
