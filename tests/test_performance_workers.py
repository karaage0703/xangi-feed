"""Regressions for avoiding unused work in background workers."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xangi_feed.enrichment import Enricher
from xangi_feed.store import Store


class AutomaticEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'feed.db', seed=False)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        source = self.store.source(self.store.add_source('Test', 'https://example.org/rss', 'rss'))
        self.store.ingest(source, [{
            'title': 'Robot prototype', 'url': 'https://example.org/post',
            'summary': 'A robot experiment.', 'published': time.time(),
            'updated': None, 'audio_url': None, 'topics': ['ロボット'],
        }])
        self.item = self.store.list_items(tab='all')['items'][0]
        self.store.save_settings({'agent_id': 'test-agent'})
        self.host = Mock(configured=True)
        self.host.mail_agent.return_value = ''
        self.enricher = Enricher(self.store, self.host)

    def test_inactive_automatic_skips_candidates_without_touching_analysis(self):
        for mode in ['disabled', 'no-agent', 'no-host', 'zero-limit', 'stopped']:
            with self.subTest(mode=mode):
                self.store.save_settings({'enabled': mode != 'disabled',
                                          'agent_id': '' if mode == 'no-agent' else 'test-agent',
                                          'automatic_limit': 0 if mode == 'zero-limit' else 30})
                self.host.configured = mode != 'no-host'
                if mode == 'stopped':
                    self.enricher.stop.set()
                with patch.object(self.store, 'list_items', wraps=self.store.list_items) as listing:
                    self.enricher.automatic()
                listing.assert_not_called()
                self.assertEqual(self.enricher.status()['queued'], 0)
                self.assertIsNone(self.store.analysis(self.item['id']))
        self.host.run.assert_not_called()

    def test_reenable_resumes_automatic_analysis(self):
        self.store.save_settings({'enabled': False})
        self.enricher.automatic()
        self.store.save_settings({'enabled': True})
        self.enricher.automatic()
        self.assertIn(self.item['id'], self.enricher.pending)
        self.assertEqual(self.store.analysis(self.item['id'])['status'], 'pending')

    def test_agent_autoconfiguration_still_precedes_candidate_selection(self):
        self.store.save_settings({'agent_id': ''})
        self.host.mail_agent.return_value = 'mail-agent'
        self.enricher.automatic()
        self.assertEqual(self.store.settings()['agent_id'], 'mail-agent')
        self.assertIn(self.item['id'], self.enricher.pending)

    def test_zero_automatic_limit_still_allows_explicit_enqueue(self):
        self.store.save_settings({'automatic_limit': 0})
        self.enricher.automatic()
        self.assertEqual(self.enricher.enqueue([self.item]), 1)

    def test_disabled_automatic_keeps_existing_pending_work(self):
        self.enricher.enqueue([self.item])
        pending = list(self.enricher.pending.items())
        analysis = self.store.analysis(self.item['id'])
        self.store.save_settings({'enabled': False})
        self.enricher.automatic()
        self.assertEqual(list(self.enricher.pending.items()), pending)
        self.assertEqual(self.store.analysis(self.item['id']), analysis)


if __name__ == '__main__':
    unittest.main()
