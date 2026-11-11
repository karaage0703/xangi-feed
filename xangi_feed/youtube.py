"""Resolve public channel feeds without API keys or executing page scripts."""
import re
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from .feeds import fetch_feed, web_url, parse_feed, topics, MAX_BYTES, plain

CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}')
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com'}


def feed_url(channel_id):
    if not CHANNEL.fullmatch(channel_id):
        raise ValueError('YouTubeのチャンネルIDを指定してください')
    return 'https://www.youtube.com/feeds/videos.xml?channel_id=' + channel_id


class ChannelPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('itemprop') == 'channelId':
            self.ids.add(attrs.get('content', ''))
        if tag == 'link' and attrs.get('rel') == 'alternate':
            parts = urlsplit(attrs.get('href', ''))
            if parts.hostname in HOSTS and parts.path in {'/feeds/videos.xml', '/xml/feeds/videos.xml'}:
                self.ids.update(parse_qs(parts.query).get('channel_id', []))


def resolve(value, fetcher=None):
    value = value.strip()
    if CHANNEL.fullmatch(value):
        return feed_url(value)
    if value.startswith('@'):
        value = 'https://www.youtube.com/' + value
    parts = urlsplit(web_url(value))
    if parts.hostname not in HOSTS:
        raise ValueError('YouTubeのチャンネルURL・@ハンドル・チャンネルIDを指定してください')
    if parts.path in {'/feeds/videos.xml', '/xml/feeds/videos.xml'}:
        ids = parse_qs(parts.query).get('channel_id', [])
        if len(ids) != 1:
            raise ValueError('チャンネルIDが1件必要です')
        return feed_url(ids[0])
    path = parts.path.rstrip('/')
    path = re.sub(r'/(?:videos|shorts|streams|featured)$', '', path)
    if path.startswith('/channel/'):
        return feed_url(path.split('/')[2])
    if not (re.fullmatch(r'/@[^/]+', path) or re.fullmatch(r'/(?:c|user)/[^/]+', path)):
        raise ValueError('動画URLではなくチャンネルのURLを指定してください')
    data = (fetcher or fetch_feed)('https://www.youtube.com' + path)
    text = data.decode('utf-8', errors='replace')
    page = ChannelPage()
    page.feed(text)
    ids = {i for i in page.ids if CHANNEL.fullmatch(i)}
    if not ids:
        ids = set(re.findall(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"', text))
    if len(ids) != 1:
        raise ValueError('チャンネルIDを確認できませんでした。/channel/形式のURLかIDを指定してください')
    return feed_url(ids.pop())


def parse_channel_page(data):
    if len(data) > MAX_BYTES:
        raise ValueError('YouTubeのページが大きすぎます')
    text = data.decode('utf-8', errors='replace')
    match = re.search(r'(?:var\s+ytInitialData|window\["ytInitialData"\]|ytInitialData)\s*=\s*', text)
    if not match:
        raise ValueError('YouTubeの動画一覧を抽出できませんでした（ページ構造変更の可能性）')
    initial, _ = json.JSONDecoder().raw_decode(text[match.end():])
    contents = initial.get('contents', {}) if isinstance(initial, dict) else {}
    renderer = contents.get('twoColumnBrowseResultsRenderer') or contents.get('singleColumnBrowseResultsRenderer') or {}
    if renderer.get('tabs'):
        selected = [t['tabRenderer'] for t in renderer['tabs'] if isinstance(t, dict) and t.get('tabRenderer', {}).get('selected')]
        if len(selected) != 1 or not selected[0].get('content'):
            raise ValueError('YouTubeの選択中チャンネル一覧を確認できませんでした')
        initial = selected[0]['content']
    result, seen = [], set()
    stack = [(initial, 0)]
    while stack and len(result) < 30:
        node, depth = stack.pop()
        if depth > 100:
            continue
        if isinstance(node, list):
            stack.extend((n, depth + 1) for n in reversed(node))
        elif isinstance(node, dict):
            video = node.get('videoRenderer')
            lockup = node.get('lockupViewModel')
            video_id, title, summary = '', '', ''
            if isinstance(video, dict):
                video_id = video.get('videoId', '')
                title_data = video.get('title', {})
                title = title_data.get('simpleText') or ''.join(r.get('text', '') for r in title_data.get('runs', []))
                summary = ''.join(r.get('text', '') for r in video.get('descriptionSnippet', {}).get('runs', []))
            elif isinstance(lockup, dict):
                video_id = lockup.get('contentId', '')
                title = lockup.get('metadata', {}).get('lockupMetadataViewModel', {}).get('title', {}).get('content', '')
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) and title and video_id not in seen:
                seen.add(video_id)
                result.append({'title': title[:500], 'url': 'https://www.youtube.com/watch?v=' + video_id,
                               'summary': summary[:6000], 'published': None, 'updated': None, 'audio_url': None,
                               'topics': topics(title + ' ' + summary), 'origin': 'page'})
            stack.extend((n, depth + 1) for n in reversed(list(node.values())))
    if not result:
        raise ValueError('YouTubeから動画を取得できませんでした（空チャンネル・ページ構造変更の可能性）')
    return result


def fetch_entries(url, fetcher=fetch_feed):
    normalized = resolve(url)
    try:
        return parse_feed(fetcher(normalized), normalized)
    except (ValueError, OSError):
        channel_id = parse_qs(urlsplit(normalized).query)['channel_id'][0]
        page = 'https://www.youtube.com/channel/' + channel_id + '/videos?hl=ja'
        return parse_channel_page(fetcher(page))


MAX_FILE = 512 * 1024 * 1024

def run(argv, timeout=600):
    """Run only server-selected programs, without a shell or private error output."""
    tool = Path(argv[0]).name
    try:
        # Keep arbitrary tool output (URLs, paths, headers) out of the UI.
        # Spool stderr rather than accumulating download progress in memory.
        with tempfile.TemporaryFile() as log:
            result = subprocess.run(argv, timeout=timeout, stdout=subprocess.DEVNULL, stderr=log)
            if result.returncode:
                log.seek(0, os.SEEK_END)
                length = log.tell()
                log.seek(max(0, length - 8192))
                diagnostic = log.read().decode('utf-8', errors='replace')
                status = re.search(r'HTTP(?: Error)?[ :]+(\d{3})', diagnostic)
                if status:
                    raise ValueError(f'{tool}による取得がHTTP {status[1]}で拒否されました')
                raise ValueError(f'{tool}が失敗しました（終了コード{result.returncode}）。取得可否とツール設定を確認してください')
    except FileNotFoundError:
        raise ValueError(f'保存に必要なツールがありません: {tool}') from None
    except subprocess.TimeoutExpired:
        raise ValueError(f'{tool}が制限時間を超えました。設定または対象サイズを確認してください') from None
    except OSError:
        raise ValueError(f'{tool}を実行できませんでした。サーバー側のツール設定を確認してください') from None

def valid_file(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE:
        raise ValueError('保存ファイルが空、または上限512MiBを超えています')
    return path

def youtube_url(url):
    parts = urlsplit(web_url(url))
    query = parse_qs(parts.query)
    if parts.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com'} and parts.path == '/watch':
        video = query.get('v', [''])[0]
    elif parts.hostname == 'youtu.be':
        video = parts.path[1:]
    elif parts.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com'} and parts.path.startswith('/shorts/'):
        video = parts.path.split('/')[2]
    else:
        video = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
        raise ValueError('YouTubeの動画URLを指定してください')
    return 'https://www.youtube.com/watch?v=' + video

def caption_text(raw):
    if not raw.startswith('WEBVTT'):
        return ''
    lines = []
    for cue in re.split(r'\r?\n\s*\r?\n', raw):
        parts = cue.splitlines()
        if not parts or parts[0].startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
            continue
        timing = next((index for index, line in enumerate(parts) if '-->' in line), None)
        if timing is None:
            continue
        for line in parts[timing + 1:]:
            # Inline word timing is WebVTT markup, not spoken content. The HTML
            # parser alone treats these numeric tags as literal text.
            line = plain(re.sub(r'<(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}>', '', line))
            if line and (not lines or line != lines[-1]):
                lines.append(line)
    return '\n'.join(lines) + ('\n' if lines else '')

def captions(url, output):
    url = youtube_url(url)
    command = os.environ.get('XANGI_FEED_YT_DLP', 'yt-dlp')
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        tmp = Path(tmp)
        argv = [command, '--ignore-config', '--no-playlist', '--no-cache-dir', '--socket-timeout', '15', '--retries', '1', '--max-filesize', str(MAX_FILE)]
        if shutil.which('node'):
            argv.extend(['--js-runtimes', 'node'])
        # A failure for a translated track must not discard a usable native
        # transcript. Request one language at a time, native Japanese first.
        caption_error = None
        for language in ('ja-orig', 'ja', 'en-orig', 'en'):
            try:
                run([*argv, '--skip-download', '--write-subs', '--write-auto-subs', '--sub-langs', language,
                     '--sub-format', 'vtt', '-o', str(tmp / 'sub'), '--', url], timeout=90)
            except ValueError as error:
                caption_error = error
            for subtitle in sorted(tmp.glob('*.vtt')):
                try:
                    raw = valid_file(subtitle).read_text(encoding='utf-8-sig')
                    transcript_text = caption_text(raw)
                    if not transcript_text:
                        continue
                except (ValueError, OSError, UnicodeError):
                    continue
                output.write_text(transcript_text, encoding='utf-8')
                return 'captions'
        if caption_error:
            raise caption_error
        return False
