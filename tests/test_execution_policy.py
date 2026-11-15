import io
import json
import unittest
from unittest.mock import Mock, patch

from xangi_feed.service import HostClient


class ExecutionPolicyTests(unittest.TestCase):
    def test_inherited_local_model_settings_do_not_bypass_host(self):
        host = HostClient('http://127.0.0.1:12345', 'test-token')
        host.agents = Mock()
        with patch.dict('os.environ', {'LOCAL_LLM_BASE_URL': 'http://127.0.0.1:9/v1', 'XANGI_FEED_DIRECT_LLM': ''}):
            self.assertIsNone(host.local_completion('prompt', 'chosen', 3, 50))
        host.agents.assert_not_called()

    def test_default_agent_is_resolved_by_host_without_persisting_a_foreign_agent(self):
        host = HostClient('http://127.0.0.1:12345', 'test-token')
        host.request = Mock(return_value=io.BytesIO(b'{"sessionId":"test-session"}'))
        self.assertEqual(host.create('', automation=True), 'test-session')
        self.assertEqual(host.request.call_args.args, ('/api/sessions', {'projectId': None, 'executionOrigin': 'automation'}))

    def test_default_display_exposes_only_backend_and_model(self):
        host = HostClient('http://127.0.0.1:12345', 'test-token')
        host.request = Mock(return_value=io.BytesIO(json.dumps({'backend': {'value': {'backend': 'codex', 'model': 'test-model', 'prompt': 'private'}}}).encode()))
        self.assertEqual(host.default_backend(), {'backend': 'codex', 'model': 'test-model'})
