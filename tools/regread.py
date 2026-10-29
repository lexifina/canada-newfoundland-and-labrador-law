"""Reads one regulation page. The consolidated regulations are printed like the statutes (the same Word export: a
history note, class RSN, under every section, "Back to Top" links, an Analysis), so the statute reader (nlread.py)
reads them; this module only adds what differs in the head of a regulation's page:

- the citation is "NEWFOUNDLAND AND LABRADOR REGULATION 45/15" (or "NEWFOUNDLAND REGULATION 11/98", "CONSOLIDATED
  NEWFOUNDLAND AND LABRADOR REGULATION 1151/96"), printed in a RegTitle paragraph (or one without a class), not a
  chapter line; it names the regulation's folder, and is the regulation's own citation (not an amendment);
- the title is the RegLongTitle paragraph ("Access to Information ... Regulations under the ... Act (O.C. 2015-137)");
- the site banner of a current regulation adds "Table of Regulations" and "How current is this regulation?", which
  are links of the site, not text of the regulation: they are left out of front.md like the statute banner.
"""
from __future__ import annotations

import re

from blocks import blocks
from nlread import read_version

# 'LABARDOR' is the publisher's typo on one page (42/00)
REGCITE = re.compile(r'^(CONSOLIDATED +)?NEWFOUNDLAND(?: +AND +LAB[A-Z]*)? +REGULATIONS? +(\d+) *[/-] *(\d{2,4})\b', re.I)
REG_BANNER = {'Table of Regulations', 'How current is this regulation?', 'How current is this regulation'}
# 'Amendedby:': the label of the Amended list without its space (nlread's label rule needs one); not text of the law
AMENDED_NOSPACE = re.compile(r'^(Schedule )?Amendedby *:?$')
UNAVAILABLE = re.compile(r'^This particular piece of Subordinate Legislation is not available')


def reg_key(line: str) -> str | None:
    """'NEWFOUNDLAND AND LABRADOR REGULATION 45/15' -> 'NLR 45/15'; 'CONSOLIDATED NEWFOUNDLAND AND LABRADOR
    REGULATION 1151/96' -> 'CNLR 1151/96'; 'NEWFOUNDLAND REGULATION 11/98' -> 'Nfld. Reg. 11/98' (the forms build.py's
    laws_in gives the same citations in a history note)."""
    m = REGCITE.match(line.strip())
    if not m:
        return None
    num, yy = int(m.group(2)), m.group(3)[-2:]
    if m.group(1):
        return f'CNLR {num}/{yy}'
    y = int(yy)
    return f'{"NLR" if (2000 + y if y < 50 else 1900 + y) >= 2002 else "Nfld. Reg."} {num}/{yy}'


# A repeal printed at the head of the page, in brackets: '[Rep. on Mar. 28/13 by 39/13]', '[Repealed on January 13,
# 2011]', '[Rep. as of May 1/12 by 26/12] NEWFOUNDLAND AND LABRADOR REGULATION 57/03' (on the line of the number).
# Like a statute's "Repealed on ..." line it is kept as metadata, not as text of the regulation. ('[Rep. by SNL2014
# cC-11.01 s41]', with no day, stays text, as on the statutes' pages.)
REP_BRACKET = re.compile(r'^\[\s*(Rep(?:ealed|\.)?\s+(?:on|as of)\b[^\]]*)\]\s*(.*)$')
# A point-in-time range nlread could not read ('January 9, 2009 to Movember 3, 2022'): the site's dating, not text
RANGE_LIKE = re.compile(r'^(PIT *- *)?(From|As at)\b|^(Royal [Aa]ssent [Dd]ate +|Gazette [Dd]ate +)?[A-Z][a-z]+\.? +'
                        r'\d{1,2} *[,.]? *\d{4} +(to +)?(Royal [Aa]ssent [Dd]ate +|Gazette [Dd]ate +)?[A-Z][a-z]+\.? +\d')
# a page that is only a table of contents of other pages (the Rules of the Supreme Court, 1986: one page per rule)
SPLIT = re.compile(rb'(?i)href="[^"]*/regulations/RulesSC/')


def strip_rep(t: str) -> str:
    m = REP_BRACKET.match(t)
    return m.group(2) if m else t


def read_reg(raw: bytes) -> dict:
    v = read_version(raw)
    v['reg_key'] = None
    if v['unavailable']:
        return v
    bl = blocks(raw)
    head = bl[:60]
    if head and UNAVAILABLE.match(head[0][1]):
        v['unavailable'] = True
        return v
    if SPLIT.search(raw) and not any(c == 'RSN' for c, _ in bl):
        v['unavailable'] = True
        v['unavailable_reason'] = ('the page is a table of contents only: the text is on one page per rule, which '
                                   'were not fetched')
        return v
    f = v['units'].get('front')
    front = [x for x in f['text'].split('\n') if x] if f is not None else []
    # A citation printed as the regulation's title line after its Amended list ('SNL2007 cH-10.1 Schedule', the rules
    # made as a schedule of an act) looks like an item of the list to nlread: it is the regulation's own citation and
    # goes back into front.md, where the page prints it.
    j = 0
    for c, t in head:
        if j < len(front) and t == front[j]:
            j += 1
        elif c == 'RegTitle' and t in v['amended']:
            v['amended'].remove(t)
            front.insert(j, t)
            j += 1
    if f is not None and '\n'.join(front) + '\n' != f['text']:
        f['text'] = '\n'.join(front) + '\n'
    rep = next((REP_BRACKET.match(x) for x in front if REP_BRACKET.match(x)), None)
    if rep and not v['repealed']:
        v['repealed'] = rep.group(1).strip()
    cite_line = next((strip_rep(t) for c, t in head if c in ('RegTitle', '', 'MsoNormal', 'CenteredText')
                      and reg_key(strip_rep(t))), '')
    v['reg_key'] = reg_key(cite_line) if cite_line else None
    v['chapter'] = cite_line
    long_title = next((t for c, t in head if c == 'RegLongTitle'), '')
    if not v['title']:
        v['title'] = long_title
    if f is not None:
        copyright = {t for c, t in bl if c == 'Copyright'}   # in the body nlread skips this class; in front.md too
        first_text = next((i for i, x in enumerate(front) if x not in REG_BANNER and not RANGE_LIKE.match(x)), 0)
        kept = [strip_rep(x) for i, x in enumerate(front) if x not in REG_BANNER and not AMENDED_NOSPACE.match(x)
                and x not in copyright and not (i < first_text and RANGE_LIKE.match(x))]
        kept = [x for x in kept if x]
        if kept != front:
            f['text'] = '\n'.join(kept) + '\n' if kept else ''
    return v
