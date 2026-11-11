"""Validate Notion URLs reported by the saving skill."""
import re
import uuid
from urllib.parse import urlsplit


def page_id(value):
    if not isinstance(value, str):
        raise ValueError('NotionのページURLまたはIDを指定してください')
    raw = value.strip()
    if raw.startswith('https://'):
        parts = urlsplit(raw)
        if parts.hostname not in {'notion.so', 'www.notion.so', 'notion.site', 'www.notion.site', 'app.notion.com'}:
            raise ValueError('NotionのページURLまたはページIDを指定してください')
        raw = parts.path.rstrip('/').rsplit('/', 1)[-1]
        match = re.search(r'([0-9a-fA-F]{32})$', raw)
        raw = match[1] if match else raw
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        raise ValueError('NotionのページURLまたはIDが不正です') from None
