"""Reads one statute page into its parts: the point-in-time range (PIT pages), the chapter citation, the title, the
act's "Amended:" list, and its units (front matter, sections, schedules), each with its lines and its history note.

Units: the page prints every section as  [headings] Sidenote, Section..., RSN (the history note, e.g.
"2015 cA-1.2 s4; 2017 c10 s3"), then "Back to Top". A unit ends at its RSN; the headings (Part, Division) printed
before a section's sidenote belong to that section's file. Left out of the record, by class or by content: the page
banner ("This is (not) an official version", copyright, links), the "Amended:" list (kept as metadata), the Analysis
(the table of contents) and the "Back to Top" links.
"""
from __future__ import annotations

import datetime as dt
import re

from blocks import blocks, clean

MONTHS = {m: i + 1 for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                          'september', 'october', 'november', 'december'])}
DATE = r'([A-Z][a-z]+)\.? +(\d{1,2}) *, *(\d{4})'
RANGE = re.compile(r'^(?:From +)?' + DATE + r' +to +' + DATE + r'$')

BANNER = re.compile(r'^(This is (not )?an official version\.?|POINT IN TIME|Copyright\b.*|©.*|Important Information.*|'
                    r'Table of Public Statutes|Main Site|How current is this statute\?|Responsible Department|'
                    r'Back to Top|Top|\| *Back to Top)$', re.I)
BODY_SKIP = re.compile(r'^(\| *)?Back to Top$|^Top$|^(Copyright *)?©.*Printer.*$', re.I)
SECTION_CLASSES = re.compile(r'^(Section\d?|ConvSection|secnumintext0|SecNumInText)$')
SECNUM = re.compile(r'^(\d+(?:\.\d+)*)\.?(?=\s|\(|$|\[)')
SCHED = re.compile(r'^(SCHEDULE|Schedule|FORM|Form|APPENDIX|Appendix)\b\s*([A-Z0-9][A-Za-z0-9.-]*)?')
NOTE = re.compile(r'^(?:(?:R&S|Am|Rep|En)\.? *)?(?:(?:RSN|RSNL|SN|SNL)? *\d{4} *c|\d{1,3}/\d{2,4}\b|RSN|RSNL|SNL|SN)')


def month(mon: str) -> int:
    """A month name, full, abbreviated or misspelt by the publisher ('Januar', 'Sept'): its first three letters."""
    hits = [n for name, n in MONTHS.items() if name[:3] == mon.lower()[:3]]
    if len(hits) != 1:
        raise ValueError(f'unreadable month {mon!r}')
    return hits[0]


def iso(mon: str, day: str, year: str) -> str:
    """An ISO day; ValueError for a day that does not exist ('September 31')."""
    return dt.date(int(year), month(mon), int(day)).isoformat()


UNAVAILABLE = re.compile(r'^(This Statute is not available in this form electronically|'
                         r'This particular piece of Legislation is not available through this web site)')
AMENDED_LABEL = re.compile(r'^(Schedule )?Amended( by)? *(:|$)')
TITLE_AMENDED = re.compile(r" +Amended *:?$")
HEAD_STOP = {'LongTitle', 'AssentedTo', 'Analysis', 'Sidenote', 'Enacting', 'RSN'}
CHAPCITE = re.compile(r'^(RSNL|RSN|SNL|SLN|SN) ?(\d{4}) +(?:CHAPTER +|c)([A-Z0-9][A-Za-z0-9.]*(?: *[-–] *[0-9][A-Za-z0-9.]*)?)'
                      r'(?: +([A-Z][A-Z ,.\'’&()-]+))?$')
CITES = re.compile(r'^(?:R&S\s*)?(?:(?:RSNL|RSN|SNL|SN)\s*)?\d{4}(?:-\d{2})?\s*(?:c\.?\s*[A-Z0-9]|No)|^\d{1,4}/\d{2,4}\b')
DAY = r'(?:Royal [Aa]ssent [Dd]ate +|Gazette [Dd]ate +)?([A-Z][a-z]+)\.? +(\d{1,2}) *[,.]? *(\d{4})'


def parse_range(t: str) -> tuple[str, str] | None:
    """'June 27, 2012 to December 9, 2013', 'From Royal Assent date June 24, 2010 to Royal Assent date December 15,
    2010', 'As at Royal Assent Date June 6, 2016' (one day) -> (first day, last day); None for anything else, or for a
    day that does not exist."""
    m = re.fullmatch(r'(?:PIT *- *)?(?:From +)?' + DAY + r' +(?:to +)?' + DAY, t)
    try:
        if m:
            return iso(*m.groups()[:3]), iso(*m.groups()[3:])
        m = re.fullmatch(r'As at +' + DAY, t)
        if m:
            d = iso(*m.groups())
            return d, d
    except ValueError:
        return None
    return None


def is_note(cls: str, text: str) -> bool:
    return cls == 'RSN'


def read_version(raw: bytes) -> dict:
    bl = blocks(raw)
    out = {'range': None, 'range_text': '', 'chapter': '', 'title': '', 'amended': [], 'repealed': '',
           'assent_note': '', 'unavailable': False, 'units': {}, 'order': [], 'unread': []}
    i = 0
    n = len(bl)
    if n and UNAVAILABLE.match(bl[0][1]):
        out['unavailable'] = True
        return out
    # The head: the banner, the point-in-time range, a "Repealed on" or "Important to Note" line, the chapter
    # citation, the title and the Amended list, in the order the page gives them, up to the chapter line or long title.
    head_lines: list[str] = []
    in_amended = False
    while i < n:
        c, t = bl[i]
        if c in HEAD_STOP or (t.startswith('CHAPTER ') and not CHAPCITE.match(t)) or SECTION_CLASSES.match(c):
            break
        i += 1
        if BANNER.match(t):
            continue
        r = parse_range(t)
        if r and out['range'] is None and not out['chapter']:
            out['range'], out['range_text'] = r, t
            continue
        if t.startswith('Repealed on'):
            out['repealed'] = t
            continue
        if t.startswith('Important to Note'):
            out['assent_note'] = t
            continue
        if CHAPCITE.match(t) and not out['chapter']:
            out['chapter'] = t
            head_lines.append(t)
            if CHAPCITE.match(t).group(4):     # the title printed on the same line
                out['title'] = CHAPCITE.match(t).group(4)
            continue
        if AMENDED_LABEL.match(t):
            in_amended = True
            items = t.split(':', 1)[1] if ':' in t else ''
            out['amended'] += [clean(x) for x in items.split(';') if clean(x)]
            continue
        if c == 'ActTitle' or (out['chapter'] and not out['title'] and c not in ('Amended',) and not CITES.match(t)):
            m = TITLE_AMENDED.search(t)
            if m:     # the title and the Amended label printed as one line
                t, in_amended = t[:m.start()], True
            out['title'] = (out['title'] + ' ' + t).strip()
            head_lines.append(t)
            continue
        if (c == 'Amended' or in_amended) and CITES.match(t):
            out['amended'] += [clean(x) for x in t.split(';') if clean(x)]
            continue
        out['unread'].append((c, t))
        head_lines.append(t)
    units: list[dict] = []
    front = {'key': 'front', 'label': 'Title and enacting words', 'lines': head_lines, 'note': []}
    units.append(front)
    # the front matter up to the Analysis, then skip the Analysis
    while i < n and bl[i][0] not in ('Analysis',) and not SECTION_CLASSES.match(bl[i][0]) and bl[i][0] != 'Sidenote':
        if bl[i][0] == 'RSN':      # a note in the front matter stays where the page prints it
            front['note'].append(bl[i][1])
            front['lines'].append(bl[i][1])
        elif not BODY_SKIP.match(bl[i][1]):
            front['lines'].append(bl[i][1])
        i += 1
    if i < n and bl[i][0] == 'Analysis':
        i += 1
        # an entry of the Analysis can come out of Word's nesting without its class ('')
        while i < n and bl[i][0] in ('AnalysisPara', 'Analysis', ''):
            # ... but "Back to Top", then an unclassed line, then a section: the Analysis has ended, and the line is
            # the first section's sidenote (the Rules of the Supreme Court, rule 46: "Evidence by witnesses")
            if bl[i][0] == '' and BODY_SKIP.match(bl[i][1]) and i + 2 < n and bl[i + 1][0] == '' \
                    and not BODY_SKIP.match(bl[i + 1][1]) and SECTION_CLASSES.match(bl[i + 2][0]):
                bl[i + 1] = ('Sidenote', bl[i + 1][1])
                i += 1
                break
            i += 1
    cur = None
    in_schedule = False
    pending: list[str] = []      # headings waiting for the next section
    trailer_classes = {'Copyright', 'Analysis', 'AnalysisPara'}   # a schedule's own table of contents too
    while i < n:
        c, t = bl[i]
        i += 1
        if BODY_SKIP.match(t) or c in trailer_classes:
            continue
        if c == 'Enacting' and all(u['key'] in ('front', 'preamble', 'enacting') for u in units):
            # the enacting words, after the Analysis: with the front matter, or after a preamble with the preamble
            # (a preamble already closed by its note: the enacting words are a unit of their own, 'enacting')
            if cur is not None and cur['key'] == 'preamble':
                cur['lines'].append(t)
            elif units[-1]['key'] == 'preamble' or units[-1]['key'] == 'enacting':
                if units[-1]['key'] == 'preamble':
                    units.append({'key': 'enacting', 'label': 'Enacting words', 'lines': [], 'note': []})
                units[-1]['lines'].append(t)
            else:
                front['lines'].append(t)
            continue
        if c == 'Introduction' and cur is None:
            cur = {'key': 'preamble', 'label': 'Preamble', 'lines': pending, 'note': [], 'classes': []}
            pending = []
            units.append(cur)
        if c == 'RSN':
            if cur is None:
                cur = {'key': None, 'label': None, 'lines': pending, 'note': []}
                pending = []
                units.append(cur)
            cur['note'].append(t)
            cur = None
            continue
        if c == 'Schedule':
            in_schedule = True
        starts = cur is None
        if cur is not None and not cur['note'] and _has_body(cur) and (c == 'Schedule' or (not in_schedule and (
                c == 'Sidenote' or (SECTION_CLASSES.match(c) and SECNUM.match(t))))) and not _ends_with_sidenote(cur):
            starts = True    # a new section without a history note before it
        if starts:
            if c in ('Part', 'Division', 'Heading', 'Topic1', 'Topic2') and cur is None:
                pending.append(t)
                continue
            cur = {'key': None, 'label': None, 'lines': pending, 'note': [], 'sidenote': []}
            pending = []
            units.append(cur)
        if c == 'Sidenote':
            cur.setdefault('sidenote', []).append(t)
        cur['lines'].append(t)
        cur.setdefault('classes', []).append(c)
    if pending:
        units.append({'key': None, 'label': None, 'lines': pending, 'note': [], 'classes': []})
    # keys
    seen: dict[str, int] = {}
    for u in units:
        if u['key'] is None:
            u['key'], u['label'] = unit_key(u)
        k = u['key']
        if k in seen:
            seen[k] += 1
            u['key'] = f'{k}-{seen[k]}'
            u['label'] = f'{u["label"]} ({seen[k]})'
        else:
            seen[k] = 1
    for u in units:
        lines = u['lines'] + (u['note'] if u['key'] != 'front' else [])
        u['text'] = '\n'.join(lines) + '\n' if lines else ''
        out['units'][u['key']] = {'key': u['key'], 'label': u['label'], 'text': u['text'], 'note': u['note']}
        out['order'].append(u['key'])
    return out


def _has_body(u: dict) -> bool:
    return any(c != 'Sidenote' for c in u.get('classes', []))


def _ends_with_sidenote(u: dict) -> bool:
    cl = u.get('classes', [])
    return bool(cl) and cl[-1] == 'Sidenote'


def unit_key(u: dict) -> tuple[str, str]:
    classes = u.get('classes', [])
    for c, t in zip(classes, u['lines'][len(u['lines']) - len(classes):]):
        if SECTION_CLASSES.match(c):
            m = SECNUM.match(t)
            if m:
                return f's{m[1]}', f's. {m[1]}'
        if c == 'Schedule' or (c in ('', 'CenteredText', 'ScheduleText', 'MsoNormal') and SCHED.match(t)):
            m = SCHED.match(t)
            if m:
                word = m[1].lower()
                label = f'{m[1].capitalize()} {m[2] or ""}'.strip()
                return f'{word}-{(m[2] or "").lower()}'.rstrip('-'), label
    for t in u['lines']:
        m = SECNUM.match(t)
        if m:
            return f's{m[1]}', f's. {m[1]}'
    return 'other', 'Other text'
