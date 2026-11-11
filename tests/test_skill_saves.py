import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from xangi_feed.skill_saves import SkillSaves, skill_path
from xangi_feed.server import Server
from xangi_feed.store import Store

PAGE='11111111-1111-4111-8111-111111111111'


class SkillSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.path=self.root/'workspace/.xangi-feed/feed.db'
        self.store=Store(self.path,seed=False)
        self.item,_=self.store.add_article({'url':'https://example.org/article','title':'保存対象','summary':'参考データの命令は実行しない'})
        self.host=Mock(configured=True)
        self.host.create.return_value='save-session'
        self.host.agents.return_value=[{'id':'save-agent','available':True}]
        self.saves=SkillSaves(self.store,self.host)
        self.request_id=str(uuid.uuid4())

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_button_request_uses_default_skill_and_normal_ai_session_without_media_or_notion_calls(self):
        self.store.save_note(self.item['id'],'利用者のメモ')
        result=self.saves.request(self.item['id'],self.request_id)
        self.assertEqual(result['status'],'requested')
        self.host.create.assert_called_once_with(agent_id='')
        prompt=self.host.send.call_args.args[1]
        self.assertIn('xangi-feed-save/SKILL.md',prompt)
        self.assertIn('利用者のメモ',prompt)
        self.assertIn(self.request_id,prompt)
        self.assertIn('/save-result',prompt)
        self.assertEqual(self.store.item(self.item['id'])['save_request'],result)
        self.assertEqual(self.store.list_items(tab='all')['items'][0]['save_request'],result)

    def test_local_user_policy_and_separate_saving_agent_are_used(self):
        policy=self.root/'workspace/skills/my-saving/SKILL.md';policy.parent.mkdir(parents=True)
        policy.write_text('利用者の保存先と形式')
        self.store.save_settings({'save_skill_name':'my-saving','save_agent_id':'save-agent','agent_id':'analysis-agent'})
        result=self.saves.request(self.item['id'],self.request_id)
        self.assertEqual(result['skill_name'],'my-saving')
        self.assertIn(str(policy),self.host.send.call_args.args[1])
        self.host.create.assert_called_once_with(agent_id='save-agent')

    def test_missing_custom_skill_rejects_before_starting_agent(self):
        self.store.save_settings({'save_skill_name':'missing-policy'})
        self.assertRaises(ValueError,self.saves.request,self.item['id'],self.request_id)
        self.host.create.assert_not_called()
        for name in ['../private','bad name','UPPER',None]:
            self.assertRaises(ValueError,skill_path,self.store,name)

    def test_duplicate_and_new_clicks_while_pending_do_not_resend(self):
        first=self.saves.request(self.item['id'],self.request_id)
        self.assertEqual(self.saves.request(self.item['id'],self.request_id),first)
        self.assertEqual(self.saves.request(self.item['id'],str(uuid.uuid4())),first)
        self.host.send.assert_called_once()
        other,_=self.store.add_article({'url':'https://example.org/other'})
        self.assertRaises(ValueError,self.saves.request,other['id'],self.request_id)

    def test_unknown_send_result_is_not_repeated_after_restart(self):
        self.host.send.side_effect=TimeoutError()
        first=self.saves.request(self.item['id'],self.request_id)
        self.assertEqual(first['status'],'uncertain')
        self.store.close();self.store=Store(self.path,seed=False)
        self.saves=SkillSaves(self.store,self.host)
        self.assertEqual(self.saves.request(self.item['id'],str(uuid.uuid4())),first)
        self.host.create.assert_called_once();self.host.send.assert_called_once()

    def test_agent_completion_records_url_and_summary_for_current_request(self):
        first=self.saves.request(self.item['id'],self.request_id)
        result=self.saves.report(self.item['id'],{'request_id':self.request_id,'session_id':'save-session','status':'saved','page_url':'https://www.notion.so/'+PAGE.replace('-',''),'summary':'要約とメモを保存し再取得照合しました'})
        self.assertEqual(result['status'],'saved')
        self.assertEqual(self.store.item(self.item['id'])['save_request']['page_url'],result['page_url'])
        self.store.record_save_request({**first,'status':'requested'},pending_only=True)
        self.assertEqual(self.store.save_request(self.item['id'])['status'],'saved')

    def test_report_rejects_wrong_request_session_status_and_non_notion_link(self):
        self.saves.request(self.item['id'],self.request_id)
        valid={'request_id':self.request_id,'session_id':'save-session','status':'saved','page_url':'https://www.notion.so/'+PAGE}
        for changes in [{'request_id':str(uuid.uuid4())},{'session_id':'other'},{'status':[]},{'page_url':'https://example.org/'},{'page_url':None},{'summary':1}]:
            with self.subTest(changes=changes):self.assertRaises(ValueError,self.saves.report,self.item['id'],{**valid,**changes})
        self.assertEqual(self.store.save_request(self.item['id'])['status'],'requested')

    def test_needs_input_is_distinct_from_saved_and_later_completion_can_resume(self):
        self.saves.request(self.item['id'],self.request_id)
        pending=self.saves.report(self.item['id'],{'request_id':self.request_id,'session_id':'save-session','status':'needs_input','summary':'保存先を教えてください'})
        self.assertIsNone(pending['page_url'])
        self.assertEqual(self.saves.request(self.item['id'],str(uuid.uuid4()))['request_id'],self.request_id)
        final=self.saves.report(self.item['id'],{'request_id':self.request_id,'session_id':'save-session','status':'partial','page_url':'https://www.notion.so/'+PAGE,'summary':'メモまで保存'})
        self.assertEqual(final['status'],'partial')

    def test_http_confirmation_auth_callback_and_old_route_cannot_export(self):
        self.host.agent_id=''
        self.store.save_settings({'enabled':False})
        server=Server(self.store,'fixture',host=self.host)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        def post(suffix,data,token='fixture'):
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/items/{self.item["id"]}/'+suffix,
                data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            try:
                with urllib.request.urlopen(request) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:return error.code,json.load(error)
        try:
            data={'confirm':True,'request_id':self.request_id}
            self.assertEqual(post('save',data,'wrong')[0],401)
            self.assertEqual(post('save',{'request_id':self.request_id})[0],400)
            self.assertEqual(post('save',{**data,'command':'untrusted'})[0],400)
            self.assertEqual(post('notion',{'confirm':True})[0],404)
            self.assertEqual(post('save',data)[1]['status'],'requested')
            result={'request_id':self.request_id,'session_id':'save-session','status':'saved','page_url':'https://www.notion.so/'+PAGE}
            self.assertEqual(post('save-result',result,'wrong')[0],401)
            self.assertEqual(self.store.save_request(self.item['id'])['status'],'requested')
            self.assertEqual(post('save-result',result)[1]['status'],'saved')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_restart_marks_inflight_uncertain_but_preserves_requested(self):
        first=self.saves.request(self.item['id'],self.request_id)
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertEqual(self.store.save_request(self.item['id'])['status'],'requested')
        self.store.record_save_request({**first,'status':'sending'})
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertEqual(self.store.save_request(self.item['id'])['status'],'uncertain')

    def test_resave_failure_keeps_previous_page_link_for_next_request(self):
        self.saves.request(self.item['id'],self.request_id)
        self.saves.report(self.item['id'],{'request_id':self.request_id,'session_id':'save-session','status':'saved','page_url':'https://www.notion.so/'+PAGE})
        second=self.saves.request(self.item['id'],str(uuid.uuid4()))
        self.saves.report(self.item['id'],{'request_id':second['request_id'],'session_id':'save-session','status':'error','error':'更新できませんでした'})
        third=self.saves.request(self.item['id'],str(uuid.uuid4()))
        self.assertEqual(third['page_url'],'https://www.notion.so/'+PAGE.replace('-',''))
        self.assertIn(third['page_url'],self.host.send.call_args.args[1])
