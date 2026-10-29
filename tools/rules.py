"""The Rules of the Supreme Court, 1986 (SNL1986 c42 Schedule D). The site prints them as a table of contents
(regulations/rc86rules.htm: the citation, the title, the Amended list and an Analysis) linking one page per rule
(regulations/RulesSC/Rc86ru01.htm ... Rc86ru58.htm, and Rc86PartIV.htm for Part IV, the family rules). The builder
joins the linked pages, in the order the table of contents lists them, into the one current version of one regulation:

- front.md: the head of the table of contents (citation, title, "RULES"), without the site banner and the Amended list;
- one file per rule page for the text above its Analysis (the rule's heading, e.g. rule-01.md "RULE 1 CITATION ..."),
  named by the page's file name (Rc86ru04a.htm -> rule-04a, Rc86PartIV.htm -> part-iv);
- one file per section, keyed by its number ('1.01' -> s1.01.md, '4A.01' -> s4a.01.md, 'F37A.02' -> sf37a.02.md).

The table of contents also links the forms (RulesSC/RulesSCForms/IndexForms.htm, a list of PDF files): they are not
pages of text, and are not included.
"""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from typing import Callable

from blocks import blocks
from nlread import BANNER
from regread import REG_BANNER, read_reg

RULES_TOC = 'https://www.assembly.nl.ca/Legislation/sr/regulations/rc86rules.htm'
RULES_ID = 'rc86rules'
# a section number of the rules: '1.01', '4A.01', '18A.06', 'F1.01', 'F37A.02', 'F25A' (a rule of one section), '28'
# (a repealed rule: '28. [Rep. by 69/97 s2]'), '5 3.01' (the publisher's stray space in 53.01); then '.', a space,
# '(' or '['
RULE_SEC = re.compile(r'^(F?\d(?: ?\d)*[A-Z]{0,2}(?:\.\d+[A-Z]{0,2})?)\.?(?=\s|\(|$|\[)')


def sec_key(line: str) -> str | None:
    m = RULE_SEC.match(line)
    return 's' + m[1].replace(' ', '').lower() if m else None


def is_rules(url: str) -> bool:
    return url.lower() == RULES_TOC.lower()


def rule_links(raw: bytes) -> list[tuple[str, str]]:
    """(absolute URL, label) of every page of text under regulations/RulesSC/ that the table of contents links, in
    order, each once (not the forms index)."""
    page = raw.decode('cp1252', errors='replace')
    out, seen = [], set()
    for m in re.finditer(r'(?is)<a[^>]+href="([^"]*RulesSC/[^"]+\.htm)"[^>]*>(.*?)</a>', page):
        url = urllib.parse.urljoin(RULES_TOC, html.unescape(m[1]).strip())
        url = re.sub(r'(?i)^https?://(www\.)?assembly\.nl\.ca', 'https://www.assembly.nl.ca', url)
        if url.lower() in seen or '/rulesscforms/' in url.lower():
            continue
        seen.add(url.lower())
        label = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', m[2]))).strip()
        out.append((url, label))
    return out


def page_prefix(url: str) -> str:
    """'.../Rc86ru04a.htm' -> 'rule-04a'; '.../Rc86PartIV.htm' -> 'part-iv'."""
    name = url.rsplit('/', 1)[1][:-4].lower()
    m = re.fullmatch(r'rc86ru(\d+[a-z]?)', name)
    if m:
        return f'rule-{m[1]}'
    m = re.fullmatch(r'rc86part([ivx]+)', name)
    if m:
        return f'part-{m[1]}'
    raise ValueError(f'unexpected page of the rules: {url}')


def toc_front(raw: bytes) -> tuple[list[str], list[str]]:
    """(the lines of the head of the table of contents, the items of its Amended list). The head ends at the
    Analysis."""
    lines, amended, in_amended = [], [], False
    for c, t in blocks(raw):
        if t == 'Analysis' or c in ('Analysis', 'AnalysisPara'):
            break
        if t in REG_BANNER or BANNER.match(t) or c == 'Copyright':
            continue
        if c == 'Amended' or (in_amended and c == 'Amended'):
            if not re.match(r'^Amended( ?by)? *:?$', t):
                amended.append(t)
            in_amended = True
            continue
        in_amended = False
        lines.append(t)
    return lines, amended


def read_rules(raw: bytes, get: Callable[[str], bytes]) -> dict:
    """The joined regulation, in the shape of nlread.read_version's result (plus 'pages', 'sha256')."""
    front, amended = toc_front(raw)
    v = {'range': None, 'range_text': '', 'chapter': front[0] if front else '', 'title': '', 'amended': amended,
         'repealed': '', 'assent_note': '', 'unavailable': False, 'units': {}, 'order': [], 'unread': [],
         'reg_key': None}
    v['title'] = next((t for c, t in blocks(raw)[:40] if c == 'RegLongTitle'), '')
    v['units']['front'] = {'key': 'front', 'label': 'Front matter', 'text': '\n'.join(front) + '\n' if front else '',
                           'note': []}
    v['order'].append('front')
    h = hashlib.sha256(raw)
    pages = []
    for url, _label in rule_links(raw):
        body = get(url)
        h.update(body)
        pages.append(url)
        r = read_reg(body)
        if r['unavailable']:
            raise SystemExit(f'a page of the rules cannot be read: {url}')
        prefix = page_prefix(url)
        for k in r['order']:
            u = r['units'][k]
            if k == 'front':
                key, label = prefix, f'{prefix.replace("-", " ").capitalize()}: heading'
            else:
                note = set(u['note'])     # the history note ('1986 c42 Sch D rule 53.01') is not a number
                first = next((sec_key(x) for x in u['text'].split('\n') if x not in note and sec_key(x)), None)
                if first:
                    key, label = first, f'r. {first[1:].upper()}'
                else:
                    key, label = f'{prefix}-{k}', f'{prefix}: {u["label"]}'
            base, n = key, 1
            while key in v['units']:
                n += 1
                key = f'{base}-{n}'
            v['units'][key] = {'key': key, 'label': label if n == 1 else f'{label} ({n})', 'text': u['text'],
                               'note': u['note']}
            v['order'].append(key)
    v['pages'] = pages
    v['sha256'] = h.hexdigest()
    return v
