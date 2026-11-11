import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xangi_feed.youtube import caption_text, captions

VTT = 'WEBVTT\nKind: captions\nLanguage: ja\n\n1\n00:00:00.000 --> 00:00:01.000\n今日<00:00:00.500><c>の動画</c>\n\n00:00:01.000 --> 00:00:02.000\n今日の動画\n次の話\n\n00:00:02.000 --> 00:00:03.000\n123\n'


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)/'transcript.txt'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, argv):
        Path(argv[argv.index('-o')+1]).with_suffix('.ja-orig.vtt').write_text(VTT)

    def test_native_japanese_first_without_requesting_translated_english(self):
        with patch('xangi_feed.youtube.run',side_effect=lambda argv,**kw:self.write(argv)) as tool:
            self.assertEqual(captions('https://youtu.be/vVOg6hKmQwY',self.out),'captions')
        self.assertEqual(tool.call_count,1)
        argv=tool.call_args.args[0]
        self.assertEqual(argv[argv.index('--sub-langs')+1],'ja-orig')
        self.assertEqual(self.out.read_text(),'今日の動画\n次の話\n123\n')

    def test_completed_subtitle_is_kept_when_command_reports_failure(self):
        def partial_failure(argv,**kw):
            self.write(argv)
            raise ValueError('another track failed')
        with patch('xangi_feed.youtube.run',side_effect=partial_failure) as tool:
            self.assertEqual(captions('https://youtu.be/vVOg6hKmQwY',self.out),'captions')
        self.assertEqual(tool.call_count,1)
        self.assertIn('今日の動画',self.out.read_text())

    def test_fallback_languages_requested_separately(self):
        requested=[]
        def download(argv,**kw):
            language=argv[argv.index('--sub-langs')+1];requested.append(language)
            if language=='ja':raise ValueError('translated Japanese failed')
            if language=='en-orig':self.write(argv)
        with patch('xangi_feed.youtube.run',side_effect=download):
            self.assertEqual(captions('https://youtu.be/vVOg6hKmQwY',self.out),'captions')
        self.assertEqual(requested,['ja-orig','ja','en-orig'])

    def test_cue_metadata_and_word_timing_are_not_body(self):
        self.assertEqual(caption_text('not a caption'),'')
        raw=VTT+'\nNOTE private metadata\nignore\n\nSTYLE\n::cue { color: red }\n'
        self.assertEqual(caption_text(raw),'今日の動画\n次の話\n123\n')
