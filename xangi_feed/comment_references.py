"""Identify comments that need a content-based rewrite instead of label substitution."""
import re


GROUP = r'S[1-9]\d*(?:(?:\s*[・、,/]\s*|\s*(?:と|や|および|及び)\s*)S[1-9]\d*)*'
CITATION = re.compile(
    r'(?P<prefix>(?:関連する記録|関連記録|参照記録|参照元|記録|出典)\s*[:：]?\s*)'
    r'(?P<ids>' + GROUP + r')(?![A-Za-z0-9_])'
    r'|[\[【（(]\s*(?P<bracket>' + GROUP + r')\s*[\]】）)]'
    r'|(?<![A-Za-z0-9_./\\-])(?P<bare>' + GROUP + r')(?![A-Za-z0-9_.\\-])'
)


def needs_rewrite(result, sources=None):
    if not result or not isinstance(result.get('comment'), str):
        return False
    sources = sources if sources is not None else (result.get('comment_context') or {}).get('sources', [])
    text = result['comment']
    known = {source.get('id') for source in sources}
    for source in sources:
        path = source.get('path', '')
        if isinstance(path, str) and path:
            name = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
            if path in text or (re.search(r'\.[a-z0-9]{1,8}$', name, re.I) and name in text):
                return True
    for match in CITATION.finditer(text):
        if match.group('prefix') or match.group('bracket'):
            return True
        ids = re.findall(r'S[1-9]\d*', match.group('bare'))
        if re.search(r'(?:Amazon|AWS)\s*$', text[:match.start()], re.I) or re.match(r'\s*(?:互換|バケット|ストレージ|API)', text[match.end():]):
            continue
        if any(ref in known for ref in ids):
            return True
    return False


def present_comment(result):
    # Do not invent prose by editing a citation-dependent sentence. Preserve the
    # stored response and summary; the usual enrichment queue rewrites its comment.
    if needs_rewrite(result):
        return {**result, 'comment': '', 'comment_needs_rewrite': True}
    return result
