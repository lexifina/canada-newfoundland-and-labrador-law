"""Reads one statute page (current or point-in-time) of the House of Assembly site into blocks: (class, text) in page
order. Every <p>, heading and list item is one block; a table row is one block, its cells joined by ' | '. Text that
sits outside any block (rare, in Word's exported HTML) is a block of its own with class '' ."""
from __future__ import annotations

import re

from lxml import html as lhtml

BLOCK_TAGS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'div', 'table', 'ul', 'ol', 'body', 'blockquote',
              'center', 'dl', 'dt', 'dd', 'td', 'th', 'tbody', 'thead', 'pre', 'form', 'html'}
LEAF = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'dt', 'dd', 'pre'}
SKIP = {'script', 'style', 'head', 'title', 'meta', 'link', 'xml'}
SPACE = re.compile(r'[\s    ​]+')


C1 = re.compile('[\x80-\x9f]')


def _c1(m) -> str:
    """A character reference to 128-159 (Word writes &#146; for a right quote) shows as its windows-1252 character, as
    browsers show it."""
    try:
        return bytes([ord(m.group(0))]).decode('cp1252')
    except UnicodeDecodeError:
        return ''


def clean(s: str) -> str:
    return SPACE.sub(' ', C1.sub(_c1, s).replace('­', '')).strip()


def _text(e) -> str:
    """The text of an element as shown: <br> and nested blocks as spaces."""
    parts = []

    def walk(x):
        if not isinstance(x.tag, str):
            return
        tag = x.tag.lower()
        if tag in SKIP:
            return
        if tag == 'br' or tag in BLOCK_TAGS:
            parts.append(' ')
        parts.append(x.text or '')
        for c in x:
            walk(c)
            parts.append(c.tail or '')
        if tag in BLOCK_TAGS:
            parts.append(' ')
    parts.append(e.text or '')
    for c in e:
        walk(c)
        parts.append(c.tail or '')
    return clean(''.join(parts))


def _has_block(x) -> bool:
    return any(isinstance(d.tag, str) and d.tag.lower() in BLOCK_TAGS for d in x.iterdescendants())


def blocks(raw: bytes) -> list[tuple[str, str]]:
    """Streams the page: every block element starts a new block (its class kept for <p>, headings and list items);
    the text after a nested block closes, still inside the outer one, is a block with class ''. Word's exported HTML
    leaves <p> unclosed inside <font>, so the parser nests blocks; streaming keeps them apart all the same."""
    doc = lhtml.fromstring(raw.decode('cp1252', errors='replace'))
    out: list[tuple[str, str]] = []
    cur = ['', []]

    def flush(new_class=''):
        t = clean(''.join(cur[1]))
        if t:
            out.append((cur[0], t))
        cur[0], cur[1] = new_class, []

    def walk(x):
        if not isinstance(x.tag, str):
            return
        tag = x.tag.lower()
        if tag in SKIP:
            return
        if tag == 'tr' and not _has_block_but_cells(x):
            flush()
            cells = [_text(td) for td in x if isinstance(td.tag, str) and td.tag.lower() in ('td', 'th')]
            row = ' | '.join(c for c in cells if c)
            if row:
                out.append(('tr', row))
            return
        if tag == 'br':
            cur[1].append(' ')
        elif tag in BLOCK_TAGS:
            flush((x.get('class') or '').strip() if tag in LEAF else '')
        cur[1].append(x.text or '')
        for c in x:
            walk(c)
            cur[1].append(c.tail or '')
        if tag in BLOCK_TAGS:
            flush()

    def _has_block_but_cells(x) -> bool:
        return any(isinstance(d.tag, str) and d.tag.lower() in BLOCK_TAGS - {'td', 'th'} for d in x.iterdescendants())

    body = doc.find('.//body')
    walk(body if body is not None else doc)
    flush()
    return out
