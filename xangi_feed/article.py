"""Retrieve a directly added public HTML article without executing page code."""
import re
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .adapters.html import Node, tree
from .feeds import fetch_feed, timestamp
from .youtube import captions, youtube_url
from .publication import clean, GENERIC_NAMES

BODY_LIMIT = 50000
SKIP = {'script', 'style', 'noscript', 'template', 'nav', 'header', 'footer', 'aside', 'form', 'svg', 'iframe', 'head', 'title'}
BLOCK = {'p', 'div', 'section', 'article', 'main', 'li', 'h1', 'h2', 'h3', 'h4', 'br', 'blockquote', 'pre'}


def text(node):
    if node.tag in SKIP or 'hidden' in node.attrs or node.attrs.get('aria-hidden') == 'true':
        return ''
    parts = [text(child) if isinstance(child, Node) else child for child in node.children]
    value = ''.join(parts)
    return '\n' + value + '\n' if node.tag in BLOCK else value


def parse_article(data, url):
    if not re.search(br'<(?:html|body|article|main|p|div|title|meta|!doctype)\b', data[:65536], re.I):
        raise ValueError('HTML記事ではありません')
    if (urlsplit(url).hostname or '').lower() in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
        return parse_youtube(data, url)
    charset = re.search(br'charset\s*=\s*["\']?([\w-]+)', data[:4096], re.I)
    root = tree(data, charset[1].decode('ascii') if charset else 'utf-8-sig')
    meta = {}
    for node in root.find('meta'):
        name = node.attrs.get('property', node.attrs.get('name', '')).lower()
        meta.setdefault(name, node.attrs.get('content', ''))
    title = meta.get('og:title') or meta.get('twitter:title') or next((n.text() for n in root.find('title')), '')
    title = ' '.join(title.split())[:1000]
    publication = clean(meta.get('og:site_name') or meta.get('application-name'))
    author = clean(meta.get('author') or meta.get('article:author'))
    # A platform name alone cannot identify a blog. Use an explicit title suffix
    # only when the page title starts with the separately provided article title.
    page_title = clean(next((n.text() for n in root.find('title')), ''), 1000)
    if not publication or publication.casefold() in GENERIC_NAMES:
        suffix = page_title[len(title):] if page_title.startswith(title) else ''
        match = re.fullmatch(r'\s*[-|｜–—]\s*(.+)', suffix)
        if match:
            publication = clean(match[1])
    if author.startswith(('http://', 'https://')):
        author = ''  # An author profile URL is not a person's display name.
    candidates = list(root.find('article')) or list(root.find('main')) or list(root.find('body')) or [root]
    body = max((text(n) for n in candidates), key=len)
    body = '\n'.join(' '.join(line.split()) for line in body.splitlines() if line.strip())[:BODY_LIMIT]
    return {'title': title, 'body': body, 'summary': (body or meta.get('description') or meta.get('og:description', ''))[:5000],
            'kind': 'blog', 'content_kind': 'article', 'publication_name': publication, 'author': author,
            'published': timestamp(meta.get('article:published_time')),
            'error': '' if body else 'ページのタイトルは取得できましたが、本文を抽出できませんでした。ログインやJavaScriptが必要なページは未対応です。'}


def parse_youtube(data, url):
    identity = youtube_url(url).split('v=')[1]
    page = data.decode('utf-8', errors='replace')
    for match in re.finditer(r'"videoDetails"\s*:\s*', page):
        try:
            details, _ = json.JSONDecoder().raw_decode(page[match.end():])
        except (ValueError, TypeError):
            continue
        if not isinstance(details, dict) or details.get('videoId') != identity:
            continue
        title = ' '.join(str(details.get('title', '')).split())[:1000]
        description = str(details.get('shortDescription', ''))[:BODY_LIMIT]
        if title:
            return {'title': title, 'body': description, 'summary': description[:5000], 'kind': 'video',
                    'content_kind': 'youtube_description', 'publication_name': clean(details.get('author')),
                    'author': clean(details.get('author')), 'error': ''}
    raise ValueError('YouTubeの動画情報を取得できませんでした')


class ArticleImporter:
    def __init__(self, store, fetcher=None):
        self.store, self.fetcher = store, fetcher
        self.lock = threading.Lock()
        self.workers = {}
        self.on_complete = lambda item: None

    def add(self, data):
        item, created = self.store.add_article(data)
        if item['content_origin'] == 'manual' and not item.get('content_text'):
            item = self.fetch(item['id'])
        return item, created

    def start(self, item_id):
        with self.lock:
            self.workers = {key: value for key, value in self.workers.items() if value.is_alive()}
            if item_id in self.workers:
                return self.store.item(item_id)
            if len(self.workers) >= 2:
                raise ValueError('本文を取得中です。完了を待って再取得してください')
            self.store.article_fetch_state(item_id, 'fetching')
            worker = threading.Thread(target=self._run, args=(item_id,), daemon=False)
            self.workers[item_id] = worker
            worker.start()
            return self.store.item(item_id)

    def _run(self, item_id):
        try:
            self.fetch(item_id)
        except Exception:
            self.store.save_article_content(item_id, {'error': '本文取得を完了できませんでした。本文を再取得してください。'})

    def close(self):
        with self.lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join()

    def fetch(self, item_id):
        item = self.store.item(item_id)
        if item['content_origin'] != 'manual':
            raise ValueError('直接追加した記事だけ本文を再取得できます')
        self.store.article_fetch_state(item_id, 'fetching')
        try:
            fields = parse_article((self.fetcher or fetch_feed)(item['url']), item['url'])
            if fields['kind'] == 'video':
                try:
                    with tempfile.TemporaryDirectory(dir=self.store.path.resolve().parent) as tmp:
                        output = Path(tmp) / 'captions.txt'
                        captured = captions(item['url'], output)
                        if captured:
                            fields.update(body=output.read_text(encoding='utf-8')[:BODY_LIMIT], content_kind='youtube_captions')
                            fields['summary'] = fields['body'][:5000]
                        else:
                            fields['error'] = '動画情報と説明欄を取得しました。公開字幕がないため字幕本文は未取得です。必要な取得や保存は保存AIと利用者のスキルで判断します。'
                except Exception:
                    fields['error'] = '動画情報と説明欄を取得しましたが、字幕の取得に失敗しました。本文を再取得するか保存AIへ相談してください。'
        except Exception:
            fields = {'error': '本文を取得できませんでした。公開HTMLページか、接続状態を確認してください。ログインやJavaScriptが必要なページは未対応です。'}
        if item.get('content_kind') in {'youtube_captions', 'youtube_transcript'} and fields.get('content_kind') == 'youtube_description':
            fields['body'] = item['content_text']
            fields['content_kind'] = item['content_kind']
        updated = self.store.save_article_content(item_id, fields)
        if updated['summary'] or updated.get('content_text'):
            self.on_complete(updated)
        return updated
