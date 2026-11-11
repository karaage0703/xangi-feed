"""Small, bounded HTML tree for reviewed site adapters; no scripts or CSS run."""
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from ..feeds import MAX_BYTES, timestamp, topics, web_url
from urllib.parse import urljoin

JST = timezone(timedelta(hours=9))


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def texts(self):
        for child in self.children:
            if isinstance(child, str):
                yield child
            else:
                yield from child.texts()

    def text(self):
        return ' '.join(' '.join(self.texts()).split())

    def find(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                    yield child
                yield from child.find(tag, cls)


class Tree(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack, self.nodes = [self.root], 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100000 or len(self.stack) > 256:
            raise ValueError('ページの構造が大きすぎます')
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not any(n.tag in {'script', 'style'} for n in self.stack):
            self.stack[-1].children.append(data)


def tree(data, encoding='utf-8'):
    if len(data) > MAX_BYTES:
        raise ValueError('ページが8MiBを超えています')
    return Tree(data.decode(encoding, errors='replace')).root


def page_date(text, now=None):
    text = text.strip()
    now = now or datetime.now(JST)
    try:
        m = re.fullmatch(r'(\d+)(分|時間)前', text)
        if m:
            return (now - timedelta(**{'minutes' if m[2] == '分' else 'hours': int(m[1])})).timestamp()
        if text == '昨日':
            return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        m = re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日', text)
        if m:
            year = int(m[1]) if m[1] else now.year + int(now.month == 12 and int(m[2]) == 1)
            return datetime(year, int(m[2]), int(m[3]), tzinfo=JST).timestamp()
        # HTML page dates without a timezone follow the site's Japanese locale.
        dt = datetime.strptime(text, '%Y/%m/%d') if '/' in text else datetime.fromisoformat(text)
        return dt.replace(tzinfo=dt.tzinfo or JST).timestamp()
    except (ValueError, OverflowError):
        return timestamp(text)


def entry(title, href, source_url, published=None, summary='', identity=None):
    url = web_url(urljoin(source_url, href))
    title = ' '.join(title.split())[:500]
    if not title:
        raise ValueError('記事タイトルが空です')
    return {'title': title, 'url': url, 'summary': summary[:6000], 'published': published,
            'updated': None, 'audio_url': None, 'topics': topics(title + ' ' + summary),
            **({'identity': identity} if identity else {})}
