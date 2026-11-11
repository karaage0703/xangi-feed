import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from xangi_feed.discovery_prompt import compile_prompt, parse_plan, japanese, PromptError
from xangi_feed.store import Store

POSITIVE = [{'name':'科学','weight':3}]


class SearchPromptTests(unittest.TestCase):
    def test_japanese_instruction_works_without_ai_and_preserves_meaning(self):
        host=Mock(configured=False)
        plan=compile_prompt('日本語の記事だけ探してください。',POSITIVE,host,'')
        self.assertEqual(plan['language'],'ja')
        self.assertEqual(plan['queries'],['科学'])
        host.plan_search.assert_not_called()

    def test_japanese_only_and_days_do_not_leave_a_partial_query(self):
        host=Mock(configured=False)
        for text in ['日本語のみ。直近4日','日本語の記事のみ。直近4日','日本語だけ。直近4日']:
            with self.subTest(text=text):
                plan=compile_prompt(text,POSITIVE,host,'')
                self.assertEqual(plan['queries'],['科学'])
                self.assertEqual(plan['max_age_days'],4)
                self.assertEqual(plan['language'],'ja')
                self.assertNotIn('notice',plan)

    def test_free_text_is_interpreted_by_ai_and_never_executed(self):
        host=Mock(configured=True)
        host.plan_search.return_value='{"queries":["ロボット 実験"],"language":"ja","exclude_terms":["広告"],"max_age_days":7}'
        plan=compile_prompt('日本語でロボットの実験、直近7日。広告は除く。',POSITIVE,host,'selected')
        self.assertEqual(plan['exclude_terms'],['広告'])
        self.assertEqual(plan['max_age_days'],7)
        host.plan_search.assert_called_once()
        self.assertEqual(host.plan_search.call_args.args[1],'selected')
        self.assertEqual(host.plan_search.call_args.kwargs['timeout'],25)

    def test_timeout_falls_back_explicitly_without_dropping_text_or_language(self):
        host=Mock(configured=True)
        host.plan_search.side_effect=TimeoutError()
        plan=compile_prompt('日本語だけ。音声認識の実装記事を探して',POSITIVE,host,'selected')
        self.assertEqual(plan['language'],'ja')
        self.assertIn('音声認識',plan['queries'][0])
        self.assertTrue(plan['notice'])

    def test_injection_invalid_types_and_unsupported_conditions(self):
        for value in ['{}','[]','{"queries":["x"],"language":"ja","max_age_days":true}', '{"queries":["x"],"language":"ja","max_age_days":7,"command":"exec"}']:
            with self.assertRaises((ValueError,TypeError)):parse_plan(value)
        with self.assertRaises(PromptError):parse_plan('{"unsupported":"cannot"}')

    def test_japanese_text_not_domain_determines_language(self):
        self.assertTrue(japanese('ロボットの新しい実験手順を公開しました。'))
        self.assertFalse(japanese('New robot released in Japan with AI features.'))
        self.assertFalse(japanese('機器人研究最新結果公開'))

    def test_prompt_setting_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db',seed=False)
            try:
                text='日本語の記事だけ。音声認識の実装例を探す。'
                self.assertEqual(store.save_settings({'discovery_prompt':text})['discovery_prompt'],text)
                for invalid in [None,False,[],2,'x'*2001]:
                    with self.subTest(value=str(invalid)[:20]),self.assertRaises(ValueError):store.save_settings({'discovery_prompt':invalid})
            finally:store.close()

    def test_local_planner_uses_selected_model_without_host_credentials(self):
        import json
        import os
        from unittest.mock import patch
        from xangi_feed.service import HostClient
        host=HostClient('http://127.0.0.1:18888','host-secret')
        host.agents=Mock(return_value=[{'id':'chosen','backend':'local-llm','model':'selected-model'}])
        response=Mock()
        response.read.return_value=json.dumps({'choices':[{'message':{'content':'{}'}}]}).encode()
        host.opener=MagicMock()
        host.opener.open.return_value.__enter__.return_value=response
        with patch.dict(os.environ,{'LOCAL_LLM_BASE_URL':'http://127.0.0.1:8080/v1','LOCAL_LLM_API_KEY':'model-secret'}):
            self.assertEqual(host.plan_search('instructions','chosen'),'{}')
        request=host.opener.open.call_args.args[0]
        payload=json.loads(request.data)
        self.assertEqual(payload['model'],'selected-model')
        self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(request.headers['Authorization'],'Bearer model-secret')
        self.assertNotIn('host-secret',repr(request.headers))
