"""UI formatting regression checks without an extra browser dependency."""
import os
import subprocess
import unittest
from pathlib import Path


class DateFormattingTests(unittest.TestCase):
    def test_reused_formatter_preserves_local_dates_and_unknown_values(self):
        app = (Path(__file__).resolve().parent.parent / 'public/app.js').read_text()
        date_code = app[app.index('const dateFormatter ='):app.index('\n\nfunction el')]
        assertions = """
          const assert = require('node:assert/strict');
          const options = {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'};
          for (const value of [0, -1, 1.25, 1704067200, 1710053999, 1710054000, 1730613599, 1730613600, 1794319200, NaN, Infinity]) {
            assert.equal(date(value), new Date(value * 1000).toLocaleString('ja-JP', options), String(value));
          }
          assert.equal(date(null), '公開日不明');
          assert.equal(date(undefined), '公開日不明');
        """
        for timezone in ['UTC', 'Asia/Tokyo', 'America/New_York']:
            with self.subTest(timezone=timezone):
                result = subprocess.run(['node', '-e', date_code + assertions],
                                        env={**os.environ, 'TZ': timezone},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
