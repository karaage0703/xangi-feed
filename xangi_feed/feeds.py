"""Bounded public-feed retrieval. Never execute or render remote markup."""
import email.utils
import html
import http.client
import ipaddress
import re
import socket
import ssl
import time
import xml.etree.ElementTree as ET
import xml.parsers.expat
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 8 * 1024 * 1024
TOPICS = {
    "AI・LLM": r"\b(?:ai|llm|gpt|claude|vllm|transformer|machine learning)\b|人工知能|機械学習|生成AI",
    "エージェント": r"\bagents?\b|エージェント|xangi",
    "ローカルAI": r"\b(?:local|ollama|gguf|gpu|cuda|dgx)\b|ローカル|省メモリ",
    "ロボット": r"\brobot\w*\b|ロボット|ロボティクス",
    "ものづくり": r"\b(?:esp32|arduino|maker|raspberry|cad|3d print)\b|工作|ものづくり|電子工作",
    "開発": r"\b(?:python|javascript|typescript|rust|github|react|software)\b|開発|プログラミング",
    "音声": r"\b(?:speech|audio|podcast|voice)\b|音声|ポッドキャスト",
    "科学": r"\b(?:science|scientific|physics|biology)\b|科学|物理|生物",
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skipping += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skipping = max(0, self.skipping - 1)

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def plain(value, limit=6000):
    parser = PlainText()
    parser.feed(value or "")
    return " ".join(html.unescape("".join(parser.parts)).split())[:limit]


def web_url(value):
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("http(s)のURLを指定してください（認証情報は含められません）")
    if parts.port not in {None, 80, 443}:
        raise ValueError("URLのポートは80または443のみ対応しています")
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~"), quote(parts.query, safe="%=&/:?@!$'()*+,;~-._"), ""))


def public_address(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = [item[4][0] for item in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("取得先は公開インターネット上のアドレスに限定されています")
    return ips[0]


def fetch_feed(url):
    return _retrieve(url, MAX_BYTES)


def download_file(url, target, maximum):
    return _retrieve(url, maximum, target)


def _retrieve(url, maximum, target=None, timeout=15, deadline_seconds=300, head_only=False):
    deadline = time.monotonic() + deadline_seconds
    for _ in range(4):
        if time.monotonic() >= deadline:
            raise TimeoutError('公開URL取得の制限時間を超えました')
        url = web_url(url)
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ip = public_address(parts.hostname, port)
        # Pin the validated address while keeping Host and TLS SNI for the hostname.
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
        sock = socket.create_connection((ip, port), timeout=min(timeout, max(0.1, deadline-time.monotonic())))
        try:
            if parts.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
            conn.sock = sock
            request_path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            conn.request("GET", request_path, headers={"User-Agent": "xangi-feed/0.1 (+RSS reader)",
                                                  "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("転送先URLがありません")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            if target is None:
                chunks, total = [], 0
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0: raise TimeoutError('公開URL取得の制限時間を超えました')
                    sock.settimeout(min(timeout, remaining))
                    chunk = response.read1(min(65536, maximum-total+1))
                    if not chunk: return b''.join(chunks)
                    total += len(chunk)
                    if total > maximum: raise ValueError('フィードが8MiBを超えています')
                    chunks.append(chunk)
                    if head_only:
                        received = b''.join(chunks)
                        end = re.search(br'</head\s*>', received, re.I)
                        if end:
                            return received[:end.end()]
            total = 0
            with target.open('wb') as output:
                while True:
                    if time.monotonic() > deadline:
                        raise ValueError('音源の取得が制限時間を超えました')
                    chunk = response.read(min(1024 * 1024, maximum - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError('音源のサイズが保存上限を超えています')
                    output.write(chunk)
            return target
        finally:
            conn.close()
            sock.close()
    raise ValueError("転送回数が上限を超えました")


def timestamp(value):
    if not value:
        return None
    try:
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = email.utils.parsedate_to_datetime(value)
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def topics(text):
    return [name for name, pattern in TOPICS.items() if re.search(pattern, text, re.I)]


def parse_feed(data, source_url):
    # Reject UTF-16/32 too: declaration byte matching must not be bypassable.
    if len(data) > MAX_BYTES or b"\x00" in data:
        raise ValueError("サイズ超過または未対応のXML宣言です")
    # Declarations inside CDATA are text, not XML declarations (e.g. GitHub
    # posts showing an HTML template). Reject actual DTD/entity declarations.
    def reject_declaration(*_):
        raise ValueError('DTD・エンティティ宣言は未対応です')
    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.Parse(data, True)
    root = ET.fromstring(data)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    root_type = local(root.tag)
    if root_type not in {"rss", "feed", "RDF"}:
        raise ValueError("RSS / Atomフィードではありません")
    channel = next((child for child in root if local(child.tag) == 'channel'), root)
    publication_name = next((plain(''.join(child.itertext()), 300) for child in channel if local(child.tag) == 'title'), '')

    def authors(element):
        names = []
        for child in element:
            if local(child.tag) not in {'author', 'creator'}:
                continue
            person = next((n for n in child if local(n.tag) == 'name'), None)
            name = plain(''.join((person if person is not None else child).itertext()), 300)
            # RSS authors may be email-only; prefer an explicitly provided name.
            if '@' in name:
                match = re.search(r'\(([^()]+)\)', name)
                name = match[1].strip() if match else ''
            if name and name not in names:
                names.append(name)
        return ' / '.join(names)[:300]

    channel_author = authors(channel)
    result = []
    for entry in [e for e in root.iter() if local(e.tag) in {"item", "entry"}][:2000]:
        fields = {}
        links = []
        audio = None
        for child in entry:
            name = local(child.tag)
            text = "".join(child.itertext()).strip()
            if name not in fields or not fields[name]:
                fields[name] = text
            if name == 'group':
                for media in child:
                    media_name = local(media.tag)
                    if media_name in {'description', 'title'} and not fields.get(media_name):
                        fields[media_name] = ''.join(media.itertext()).strip()
            if name == "link":
                href = child.get("href") or text
                if child.get("rel", "alternate") == "alternate":
                    links.append(href)
                elif child.get("rel") == "enclosure" and child.get("type", "").startswith("audio/"):
                    audio = href
            if name == "enclosure" and child.get("type", "").startswith("audio/"):
                audio = child.get("url")
        raw_url = next((link for link in links if link), None)
        if not raw_url:
            raw_url = fields.get("id") or fields.get("guid")
        try:
            url = web_url(urljoin(source_url, raw_url or "")) if raw_url else None
            audio = web_url(urljoin(source_url, audio)) if audio else None
        except ValueError:
            continue
        title = plain(fields.get("title"), 500)
        if not title or not url:
            continue
        description = fields.get("encoded") or fields.get("description") or fields.get("summary") or fields.get("content") or ""
        summary = plain(description)
        result.append({"url": url, "title": title, "summary": summary,
                       "published": timestamp(fields.get("pubDate") or fields.get("published") or fields.get("date") or fields.get("updated")),
                       "updated": timestamp(fields.get("updated")), "audio_url": audio,
                       "external_id": fields.get("id") or fields.get("guid") or url,
                       "publication_name": plain(fields.get('source'), 300) or publication_name,
                       "author": authors(entry) or channel_author,
                       "topics": topics(title + " " + summary)})
    if not result:
        raise ValueError("記事を抽出できませんでした（空フィード・URL欠落・形式変更の可能性）")
    return result
