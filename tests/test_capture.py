import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock
from xangi_feed.server import Server, ENTRY_SCRIPT_HASH
from xangi_feed.store import Store


class CapturePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'.xangi-feed/feed.db',seed=False)
        self.host=Mock(configured=True)
        self.server=Server(self.store,'capture-test-token-'*4,host=self.host)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.store.close();self.tmp.cleanup()

    def get(self,path,auth=True):
        request=urllib.request.Request(self.origin+path,headers={'Authorization':'Bearer '+self.server.token} if auth else {})
        return urllib.request.urlopen(request)

    def test_capture_assets_require_existing_auth_and_keep_csp(self):
        for path,mime in [('/capture','text/html'),('/capture.js','text/javascript'),('/capture-core.js','text/javascript'),('/capture.css','text/css')]:
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as error:self.get(path,False)
                self.assertEqual(error.exception.code,401)
                with self.get(path) as response:
                    self.assertEqual(response.status,200)
                    self.assertTrue(response.headers['Content-Type'].startswith(mime))
                    self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])

    def test_opening_capture_never_creates_items_or_ai_requests(self):
        with self.get('/capture?save=true&confirm=true&url=https%3A%2F%2Fexample.org') as response:self.assertEqual(response.status,200)
        self.assertEqual(self.store.list_items('manual')['items'],[])
        self.host.send.assert_not_called()
        with self.assertRaises(urllib.error.HTTPError) as error:self.get('/capture/../../AGENTS.md')
        self.assertEqual(error.exception.code,404)

    def test_entry_allows_only_fixed_handoff_script_with_hash(self):
        with self.get('/entry') as response:
            policy=response.headers['Content-Security-Policy']
            self.assertIn("'sha256-"+ENTRY_SCRIPT_HASH+"'",policy)
            self.assertNotIn("'unsafe-inline'",policy)
            self.assertIn(b'location.hash',response.read())
        with self.get('/capture') as response:
            self.assertNotIn('sha256-',response.headers['Content-Security-Policy'])
