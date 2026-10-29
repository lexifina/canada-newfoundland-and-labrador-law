"""An independent reader of the statute and regulation pages, for the letters-and-digits check: it shares no code with the build
(standard library html.parser, not lxml, blocks.py or nlread.py).

It reads the page as lines (one per paragraph, heading, list item or table cell), drops what is not law text by its
own rules, and keeps only letters and digits, case-folded. No sections, no notes, no structure: if the build left out,
invented, doubled or moved any letter, the tiling fails.

Dropped, everywhere: the Analysis (table of contents, classes Analysis and AnalysisPara), "Back to Top" and "Top"
links, copyright lines. Dropped in the head of the page (before the chapter line or long title): the site banner and
links, the point-in-time range, "Repealed on ..." and "Important to Note ..." lines, and the act's Amended list.
"""
from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

_KEEP = re.compile(r'[^\W_]+')
BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'td', 'th', 'div', 'table', 'ul', 'ol', 'dt', 'dd',
         'blockquote', 'center', 'pre', 'body', 'br'}
SKIP = {'script', 'style', 'title', 'head'}
HEAD_END = {'LongTitle', 'AssentedTo', 'Analysis', 'Sidenote', 'Enacting', 'RSN'}
NAV = re.compile(r'^(this is (not )?an official version\.?|point in time|important information.*|table of public '
                 r'statutes|table of regulations|main site|how current is this (statute|regulation)\??|'
                 r'responsible department|'
                 r"st\. john.s, newfoundland and labrador, canada|\(includes details about the availability.*)$", re.I)
YEAR_RANGE = re.compile(r'^(PIT *- *)?(From|As at)\b|^(Royal [Aa]ssent [Dd]ate +|Gazette [Dd]ate +)?[A-Z][a-z]+\.? +'
                        r'\d{1,2} *[,.]? *\d{4} +(to +)?(Royal [Aa]ssent [Dd]ate +|Gazette [Dd]ate +)?[A-Z][a-z]+\.? +\d')
AMENDED = re.compile(r"^(Schedule )?Amended( ?by)? *(:|$)")    # 'Amendedby:' on some regulation pages
REP_BY = re.compile(r'^(Rep\. )?(by )?(RSNL|SNL)? ?\d{4} ?c')
CITE_LIST = re.compile(r'^(R&S\s*)?((RSNL|RSN|SNL|SN)\s*)?\d{4}(-\d\d)?\s*(c|No)|^\d{1,4}/\d\d', re.I)
REP_BRACKET = re.compile(r'^\[\s*Rep(ealed|\.)?\s+(on|as of)\b[^\]]*\]\s*')
CHAPTERLINE = re.compile(r'^(RSNL|RSN|SNL|SLN|SN) ?\d{4} +(CHAPTER|c)', re.I)


def letters(s: str) -> str:
    return ''.join(_KEEP.findall(s.casefold()))


class _Lines(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, str]] = []
        self.cls = ''
        self.buf: list[str] = []
        self.skip = 0

    def flush(self, cls=''):
        t = ' '.join(''.join(self.buf).split())
        if t:
            self.lines.append((self.cls, t))
        self.buf = []
        self.cls = cls

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag == 'br':
            self.buf.append(' ')      # a line break inside a paragraph is a space within it
        elif tag in BLOCK:
            self.flush(dict(attrs).get('class') or '' if tag in ('p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6') else '')

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK and tag != 'br':
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def page_lines(raw: bytes) -> list[tuple[bool, str, str]]:
    """Every line of the page as (kept, class, text)."""
    p = _Lines()
    p.feed(raw.decode('cp1252', errors='replace').replace('\xad', ''))
    p.close()
    p.flush()
    out = []
    head = True
    in_amended = False
    after_repealed = False
    front = True
    for cls, t in p.lines:
        cls = cls.strip()
        if head and (cls in HEAD_END or (t.startswith('CHAPTER ') and not CHAPTERLINE.match(t))):
            head = False
        keep = True
        if front and (cls in ('Analysis', 'Sidenote', 'RSN') or cls.startswith('Section')):
            front = False
        if front:
            # a regulation's repeal in brackets at the head, '[Rep. on Mar. 28/13 by 39/13]', sometimes on the line of
            # its number: metadata, not text
            m = REP_BRACKET.match(t)
            if m:
                out.append((False, cls, t[:m.end()]))
                t = t[m.end():]
                if not t:
                    continue
        lt = letters(t)
        if cls in ('Analysis', 'AnalysisPara', 'Copyright'):
            keep = False
        elif lt in ('backtotop', 'top') or (t.lower().startswith(('copyright', '©')) and 'printer' in lt):
            keep = False
        elif head:
            m = re.search(r'( Schedule)? Amended( by)? *:', t)
            if m and not AMENDED.match(t):     # a title and the Amended list printed as one line
                t = t[:m.start()]
                in_amended = True
            if NAV.match(t) or YEAR_RANGE.match(t) or t.startswith(('Repealed on', 'Important to Note')):
                keep = False
                after_repealed = t.startswith('Repealed on')
                out.append((keep, cls, t))
                continue
            if after_repealed and REP_BY.match(t):
                keep = False
            elif AMENDED.match(t):
                in_amended = True
                keep = False
            elif in_amended and CITE_LIST.match(t) and cls != 'RegTitle':   # a regulation's own citation line stays
                keep = False
        after_repealed = False
        out.append((keep, cls, t))
    return out


def law_letters(raw: bytes) -> str:
    """The letters and digits of the page's law text, in page order."""
    return ''.join(letters(t) for keep, _, t in page_lines(raw) if keep)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and dict(attrs).get('href'):
            self.hrefs.append(dict(attrs)['href'].strip())


def joined_pages(url: str, raw: bytes) -> list[str] | None:
    """The Rules of the Supreme Court, 1986 (regulations/rc86rules.htm) is a table of contents: the URLs of the pages
    of text it links (under RulesSC/, not the forms), in its order, each once. None for any other page."""
    if not url.lower().endswith('/regulations/rc86rules.htm'):
        return None
    p = _Links()
    p.feed(raw.decode('cp1252', errors='replace'))
    out, seen = [], set()
    for h in p.hrefs:
        if 'rulessc/' in h.lower() and h.lower().endswith('.htm') and 'rulesscforms' not in h.lower():
            u = urllib.parse.urljoin(url, h)
            u = re.sub(r'(?i)^https?://(www\.)?assembly\.nl\.ca', 'https://www.assembly.nl.ca', u)
            if u.lower() not in seen:
                seen.add(u.lower())
                out.append(u)
    return out


def source_letters(url: str, read) -> str:
    """The law letters of a version's source: its page; for the table of contents of the Rules, the head of the
    table of contents (up to its Analysis, which with the link to the forms is not text) and then each linked page."""
    raw = read(url)
    pages = joined_pages(url, raw)
    if pages is None:
        return law_letters(raw)
    head = []
    for keep, _, t in page_lines(raw):
        if letters(t) == 'analysis':
            break
        if keep:
            head.append(letters(t))
    return ''.join(head) + ''.join(law_letters(read(u)) for u in pages)


def tile(raw: bytes, files: list[str], src: str | None = None) -> tuple[bool, str]:
    """Do the files' letters, end to end in some order, equal the page's law letters exactly? (ok, detail)."""
    if src is None:
        src = law_letters(raw)
    streams = [s for s in (letters(f) for f in files) if s]
    total = sum(len(s) for s in streams)
    K = 16
    by_head: dict[str, list[int]] = {}
    for j in sorted(range(len(streams)), key=lambda j: -len(streams[j])):
        by_head.setdefault(streams[j][:K], []).append(j)
    sizes = sorted({len(h) for h in by_head}, reverse=True)
    used = [False] * len(streams)
    pos = 0
    for _ in range(len(streams)):
        hit = None
        for n in sizes:
            for j in by_head.get(src[pos:pos + n], ()):
                if not used[j] and src.startswith(streams[j], pos):
                    hit = j
                    break
            if hit is not None:
                break
        if hit is None:
            return False, (f'no file continues the page at letter {pos} of {len(src)} (files {total}): '
                           f'{src[pos:pos + 80]!r}')
        pos += len(streams[hit])
        used[hit] = True
    if pos != len(src):
        return False, f'the files end at letter {pos}, the page at {len(src)}: page goes on {src[pos:pos + 80]!r}'
    return True, f'{total}'
