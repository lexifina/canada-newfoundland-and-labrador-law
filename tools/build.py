"""Builds a git repository of the consolidated statutes of Newfoundland and Labrador from the House of Assembly's site
(www.assembly.nl.ca), in which every amending law is one commit, dated the day the version it produced took effect.

    python -X utf8 build.py --today 2026-10-29 [--repo ../../../canada-newfoundland-and-labrador-law-build]
                            [--offline] [--verify-rebuild] [--analyse]

The House of Assembly publishes each consolidated statute as it stands now and, for most, in its past "point in time"
versions (pointintime/pittitleindex.htm), each with the days it applies from and to. Under every section the page
prints a history note naming the acts (and regulations) that enacted and amended it: "2015 cA-1.2 s4; 2017 c10 s3".
We take every version of every statute, compare consecutive versions section by section, and credit each change to the
law whose citation is new in that section's note. The site's dates are hand-made and sometimes contradict each other;
every choice made about a date is written into the commit (In-force-basis:, Date-note:). A change nothing explains is
never credited to a law: it gets its own commit by the publisher, labelled.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetchlib import CACHE, NOT_FOUND, cache_path  # noqa: E402
from nlread import CHAPCITE, iso, parse_range, read_version  # noqa: E402
from regread import read_reg  # noqa: E402
from manifest import parse_day  # noqa: E402

ROOT = HERE.parents[1]
DEFAULT_REPO = ROOT.parent / 'canada-newfoundland-and-labrador-law-build'
SITE = 'https://www.assembly.nl.ca/legislation/sr'
COMMITTER = ('Lexifina', 'canada-newfoundland-and-labrador-law@lexifina.com')
PUBLISHER = ('House of Assembly of Newfoundland and Labrador (consolidated statutes)', 'statutes@assembly-nl.invalid')
REG_PUBLISHER = ('House of Assembly of Newfoundland and Labrador (consolidated regulations)',
                 'regulations@assembly-nl.invalid')
RETRIEVED: dict[str, str] = {}
REG_ROOT = 'regulations'     # the regulations' top-level folder; the statutes' folders are at the root
WORKERS = 4


def is_reg_url(url: str) -> bool:
    low = url.lower()
    return '/sr/regulations/' in low or '/pitregulations/' in low


def publisher_of(b: dict) -> tuple[str, str]:
    return REG_PUBLISHER if b.get('domain') == 'reg' else PUBLISHER


def noun(b: dict) -> str:
    return 'regulation' if b.get('domain') == 'reg' else 'act'


# ---------------------------------------------------------------- citations

STAT = re.compile(r'(?<![A-Za-z0-9/.-])(?:(RSNL|RSN|SNL|SN|SLN)\s*)?(\d{4})(?:-(\d{2}))?\s*c\.?\s*'
                  r'([A-Z](?:\s*[-–]\s*|-?)\d+(?:\.\d+)*|\d+)(?![0-9])')
OLD = re.compile(r'(?<![A-Za-z0-9/.-])(\d{4})(?:-(\d{2}))?\s*No\.?\s*(\d+)\b')
REG = re.compile(r'(?<![A-Za-z0-9/.-])(?:(CNLR|NLR|NR)\s*)?(\d{1,4})/(\d{2})(?![0-9/])')
OC = re.compile(r'\bOC\s*(\d{2,4})-(\d+)\b')


def statute_key(prefix: str | None, year: str, yy2: str | None, chap: str) -> str:
    """'2015', 'A-1.2' -> 'SNL2015 cA-1.2'; a revision ('RSN1990 cA-2', 'RSNL1990 CHAPTER A-2') -> 'RSNL1990 cA-2'.
    The publisher writes SNL for every year, also before the province's name changed in 2001; so do we."""
    chap = re.sub(r'\s+', '', chap).replace('–', '-')
    m = re.fullmatch(r'([A-Z])-?(\d.*)', chap)
    if m:
        chap = f'{m.group(1)}-{m.group(2)}'
    y = year + (f'-{yy2}' if yy2 else '')
    if prefix and prefix.startswith('RS'):
        return f'{"RSNL" if int(year) >= 1990 else "RSN"}{y} c{chap}'
    return f'SNL{y} c{chap}'


def laws_in(text: str) -> list[str]:
    """Every law a history note (or an Amended list item) cites, in order, as keys: 'SNL2017 c10', 'RSNL1990 cA-2',
    'SNL1971 No14' (the numbered statutes before 1977), 'NLR 10/18' (a regulation), 'OC94-204' (an order in
    council)."""
    out = []
    spans = []
    for m in STAT.finditer(text):
        out.append((m.start(), statute_key(m.group(1), m.group(2), m.group(3), m.group(4))))
        spans.append(m.span())
    for m in OLD.finditer(text):
        out.append((m.start(), f'SNL{m.group(1)}{"-" + m.group(2) if m.group(2) else ""} No{int(m.group(3))}'))
    for m in REG.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        yy = int(m.group(3))
        prefix = m.group(1) or ('NLR' if (2000 + yy if yy < 50 else 1900 + yy) >= 2002 else 'Nfld. Reg.')
        out.append((m.start(), f'{prefix} {int(m.group(2))}/{m.group(3)}'))
    for m in OC.finditer(text):
        out.append((m.start(), f'OC{m.group(1)}-{m.group(2)}'))
    seen, keys = set(), []
    for _, k in sorted(out):
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def law_order(k: str) -> tuple:
    m = re.fullmatch(r'(RSNL|RSN|SNL)(\d{4})(?:-\d{2})? (?:c([A-Z]-)?(\d+)(.*)|No(\d+))', k)
    if m:
        return (0, int(m.group(2)), 0 if m.group(1) == 'SNL' else 1, int(m.group(4) or m.group(6)),
                m.group(3) or '', m.group(5) or '')
    m = re.fullmatch(r'(.*) (\d+)/(\d{2})', k)
    if m:
        yy = int(m.group(3))
        return (1, 2000 + yy if yy < 50 else 1900 + yy, 0, int(m.group(2)), m.group(1), '')
    m = re.fullmatch(r'OC(\d+)-(\d+)', k)
    if m:
        y = int(m.group(1))
        return (2, y if y > 999 else (2000 + y if y < 50 else 1900 + y), 0, int(m.group(2)), '', '')
    return (3, 0, 0, 0, k, '')


def law_kind(k: str) -> str:
    if k.startswith(('SNL', 'RSN')):
        return 'Act of the House of Assembly' if not k.startswith('RSN') else 'Revised Statutes'
    if k.startswith('OC'):
        return 'Order in Council'
    return 'Regulation'


def law_slug(k: str) -> str:
    """'SNL2017 c10' -> 'snl2017-c10'; 'NLR 10/18' -> 'nlr-10-18'; 'Nfld. Reg. 64/97' -> 'nfld-reg-64-97'."""
    return re.sub(r'[^a-z0-9]+', '-', k.lower()).strip('-')


def tag_of(k: str) -> str:
    return 'law/' + law_slug(k)


def folder_of(chapter: str) -> str | None:
    """'SNL2015 CHAPTER A-1.2' -> 'SNL2015-cA-1.2'; 'RSNL1990 CHAPTER U - 2' -> 'RSNL1990-cU-2'."""
    m = CHAPCITE.match(chapter or '')
    if not m:
        return None
    prefix = {'SLN': 'SNL', 'SN': 'SNL', 'RSN': 'RSNL'}.get(m.group(1), m.group(1))
    return statute_key(prefix, m.group(2), None, m.group(3)).replace(' ', '-')


def reg_folder(key: str) -> str:
    """'NLR 45/15' -> 'regulations/NLR-45-15'; 'CNLR 1151/96' -> 'regulations/CNLR-1151-96'; 'Nfld. Reg. 11/98' ->
    'regulations/NfldReg-11-98'."""
    return f'{REG_ROOT}/' + re.sub(r'[^A-Za-z0-9]+', '-', key.replace('Nfld. Reg.', 'NfldReg')).strip('-')


def reg_aliases(key: str) -> set[str]:
    """The forms in which a regulation's history notes may cite the regulation itself: 'CNLR 1151/96' also as
    '1151/96' (read as 'Nfld. Reg. 1151/96' when the note has no prefix)."""
    out = {key}
    m = re.fullmatch(r'(?:CNLR|NLR|Nfld\. Reg\.) (\d+)/(\d{2})', key)
    if m:
        out |= set(laws_in(f'{m.group(1)}/{m.group(2)}'))
    return out


# ---------------------------------------------------------------- reading every page (in a pool)

def render(text: str) -> str:
    """One block of the page per paragraph, a blank line between paragraphs (so Markdown shows them apart)."""
    lines = [x for x in text.split('\n') if x]
    return '\n\n'.join(lines) + '\n' if lines else ''


def read_page(url: str) -> tuple[str, dict]:
    raw = cache_path(url).read_bytes()
    if raw == NOT_FOUND:
        return url, {'missing': True}
    v = read_reg(raw) if is_reg_url(url) else read_version(raw)
    out = {k: v[k] for k in ('range', 'range_text', 'chapter', 'title', 'amended', 'repealed', 'assent_note',
                             'unavailable', 'order')}
    if is_reg_url(url):
        out['reg_key'] = v.get('reg_key')
        if v.get('unavailable_reason'):
            out['unavailable_reason'] = v['unavailable_reason']
    out['missing'] = False
    out['sha256'] = hashlib.sha256(raw).hexdigest()
    out['amended_laws'] = sorted({k for it in v['amended'] for k in laws_in(it)})
    out['units'] = {k: {'label': u['label'], 'text': render(u['text']),
                        'cites': laws_in('; '.join(u['note']))} for k, u in v['units'].items()}
    return url, out


def read_all(urls: list[str]) -> dict[str, dict]:
    with cf.ProcessPoolExecutor(WORKERS) as ex:
        return dict(ex.map(read_page, urls, chunksize=8))


# ---------------------------------------------------------------- dates

def next_day(d: str) -> str:
    return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()


def label_range(label: str) -> tuple[str, str] | None:
    return parse_range(re.sub(r'\s+', ' ', label).strip())


REPEALED = re.compile(r'^Repealed on ([A-Z][a-z]+)\.? +(\d{1,2}) *, *(\d{4})\b(.*)$')
# the other forms on regulation pages: 'Repealed on Aug. 31/12 by NLR 62/12', 'Rep. on Mar. 28/13 by 39/13',
# 'Repealed as of March 31, 2011'
REG_REPEALED = re.compile(r'^Rep(?:ealed|\.)?\s+(?:on|as of)\s+(.+?)(?:\s+by\s+(.+))?$')


def resolve_act(a: dict, pages: dict, retrieved: str, today: str) -> dict:
    """The act's versions in order, each with its first day, the basis for that day and every note about it; the
    versions left out, with the reason; and how the record of the act ends."""
    res = {'id': a['id'], 'index_title': a['title'], 'versions': [], 'excluded': [], 'end': None, 'left_out': None}
    if a.get('domain') == 'reg':
        res['domain'], res['noun'], res['retrieved'] = 'reg', 'regulation', retrieved
    pits = []
    for i, v in enumerate(a['pit']):
        p = pages[v['url']]
        if p['missing']:
            res['excluded'].append({'url': v['url'], 'label': v['label'], 'reason': 'the page is not on the site '
                                    '(the site answers "this page could not be found")'})
            continue
        if p['unavailable']:
            res['excluded'].append({'url': v['url'], 'label': v['label'], 'reason': p.get('unavailable_reason') or
                                    'the page says the text is not available electronically'})
            continue
        P = p['range']
        L = label_range(v['label'])
        F = v['file_to']
        notes = []

        def ok_end(d):
            return d and '1800' < d <= retrieved
        ends = [e for e in ((P or (None, None))[1], (L or (None, None))[1], F) if ok_end(e)]
        end = None
        for e in ends:
            if ends.count(e) >= 2:
                end = e
                break
        if end is None:
            end = ends[0] if ends else None
        if end is None:
            res['excluded'].append({'url': v['url'], 'label': v['label'], 'reason': 'no readable last day'})
            continue
        src = 'page'
        start = P[0] if P and P[0] <= end else None
        if start is None:
            start = L[0] if L and L[0] <= end else None
            src = 'index'
            if start and P:
                notes.append(f'the page gives {P[0]} to {P[1]}, which cannot be right (the first day is after the '
                             f'last); the index gives {L[0]} to {L[1]}, so the first day is taken from the index')
            elif start:
                notes.append(f'the page gives no dates; the first day is taken from the index ({v["label"]!r})')
        elif L and L[0] != P[0]:
            notes.append(f'the page gives {P[0]} as the first day, the index {L[0]} ({v["label"]!r}); the day is '
                         f'taken from the page')
        if start is None:
            res['excluded'].append({'url': v['url'], 'label': v['label'], 'reason': 'no readable first day on or '
                                    f'before its last day (page {P}, index {v["label"]!r})'})
            continue
        if p['assent_note']:
            notes.append('the publisher dates this version by Royal Assent and says it "does not take into '
                         'consideration the commencement date of the individual sections or subsections"')
        pits.append({'kind': 'pit', 'url': v['url'], 'label': v['label'], 'start': start, 'end': end, 'basis': src,
                     'notes': notes, 'page': p, 'index_pos': i})
    # order by first day; two versions with the same first day: keep the one whose page and index agree
    pits.sort(key=lambda x: (x['start'], x['index_pos']))
    kept = []
    for v in pits:
        if kept and kept[-1]['start'] == v['start']:
            prev = kept[-1]
            agree = lambda x: x['page']['range'] and label_range(x['label']) == tuple(x['page']['range'])  # noqa: E731
            loser = v if agree(prev) or not agree(v) else prev
            winner = prev if loser is v else v
            res['excluded'].append({'url': loser['url'], 'label': loser['label'],
                                    'reason': f'its first day ({loser["start"]}) is also the first day of the version '
                                              f'{winner["url"].rsplit("/", 1)[1]}, which is kept'})
            kept[-1] = winner
            continue
        kept.append(v)
    # Where the page and the index disagree on the first day, and the index's day is the day after the version before
    # ends while the page's is not, the index is right about the sequence: use it (labelled either way).
    changed = False
    for prev, v in zip(kept, kept[1:]):
        L = label_range(v['label'])
        P = v['page']['range']
        if v['basis'] == 'page' and L and P and L[0] != P[0] and L[0] == next_day(prev['end']) \
                and P[0] != next_day(prev['end']) and L[0] <= v['end']:
            v['start'], v['basis'] = L[0], 'index'
            v['notes'] = [n for n in v['notes'] if not n.startswith('the page gives')] + [
                f'the page gives {P[0]} as the first day, the index {L[0]} ({v["label"]!r}); the index\'s day is the '
                f'day after the version before ends ({prev["end"]}), so it is used']
            changed = True
    if changed:
        kept.sort(key=lambda x: (x['start'], x['index_pos']))
        if len({v['start'] for v in kept}) != len(kept):
            raise SystemExit(f'{a["id"]}: two versions with one first day after taking the index\'s days')
    for i, v in enumerate(kept):
        if v['index_pos'] != sorted(x['index_pos'] for x in kept)[i]:
            v['notes'].append('the index lists this version out of order; it is placed by its first day')
    for prev, v in zip(kept, kept[1:]):
        if next_day(prev['end']) < v['start']:
            v['notes'].append(f'gap: the site has no version for {next_day(prev["end"])} to '
                              f'{(dt.date.fromisoformat(v["start"]) - dt.timedelta(days=1)).isoformat()}; changes '
                              f'made in that time are dated by the first day of this version')
            v['gap'] = True
        elif prev['end'] >= v['start']:
            v['notes'].append(f'overlap: the version before is said to apply until {prev["end"]}, after this one '
                              f'begins')
    res['versions'] = kept
    cur_url = a['current']
    cur = pages.get(cur_url) if cur_url else None
    if cur_url and (cur is None or cur['missing'] or cur['unavailable']):
        why = 'not on the site' if cur is None or cur['missing'] else 'not available electronically, the page says'
        if cur and cur.get('unavailable_reason'):
            why = f'not read: {cur["unavailable_reason"]}'
        res['excluded'].append({'url': cur_url, 'label': 'current', 'reason': f'the current version is {why}'})
        if not kept:
            res['left_out'] = f'no version of the text is available (the current page is {why})'
        else:
            res['end_note'] = (f'the record ends with the last point-in-time version (to {kept[-1]["end"]}): the '
                               f'current version is {why}')
        return res
    if cur_url:
        idx = a.get('current_from_index')
        after = next_day(kept[-1]['end']) if kept else None
        notes = []
        if idx and (not kept or idx > kept[-1]['start']):
            start, basis = idx, 'index'
            if after and idx != after:
                if idx > after:
                    notes.append(f'gap: the last point-in-time version ends {kept[-1]["end"]}, and the index says the '
                                 f'current version applies from {idx}')
                else:
                    notes.append(f'overlap: the last point-in-time version is said to apply until {kept[-1]["end"]}, '
                                 f'after the current version begins ({idx}, the index)')
        elif after:
            start, basis = after, 'day after the previous version ends'
            if idx:
                notes.append(f'the index says the current version applies from {idx}, which is not after the first '
                             f'day of the version before ({kept[-1]["start"]}); the day after that version ends is '
                             f'used instead')
        else:
            start, basis = retrieved, 'retrieval day'
            notes.append('the site gives no first day for this version and has no earlier one: it is dated by the '
                         f'day the text was retrieved ({retrieved}), when it was in force; it may have applied long '
                         'before')
        if kept and start <= kept[-1]['start']:
            notes.append('the current version cannot be placed after the last point-in-time version; left out')
            res['excluded'].append({'url': cur_url, 'label': 'current', 'reason': notes[-1]})
            return res
        res['versions'].append({'kind': 'current', 'url': cur_url, 'label': 'current', 'start': start, 'end': None,
                                'basis': basis, 'notes': notes, 'page': cur, 'index_pos': len(a['pit'])})
        return res
    if not kept:
        res['left_out'] = 'no point-in-time version has readable dates, and there is no current version'
        return res
    # Not in the current consolidation: the act left it after its last version.
    last = kept[-1]
    rep = last['page']['repealed']
    m = REPEALED.match(rep) if rep else None
    day, law, notes = next_day(last['end']), None, []
    basis = 'day after the last version ends'
    if m:
        try:
            said = iso(m.group(1), m.group(2), m.group(3))
        except ValueError:
            said = None
        laws = laws_in(m.group(4))
        if said and said > last['start']:
            day, basis = said, 'the last version\'s page ("Repealed on ...")'
            if said != next_day(last['end']):
                notes.append(f'the last version is said to apply until {last["end"]}, and its page says the act was '
                             f'repealed on {said}')
        elif said:
            notes.append(f'the page says "Repealed on {said}", which is not after the version\'s first day; the day '
                         'after the version ends is used')
        if len(laws) == 1:
            law = laws[0]
        elif len(laws) > 1:
            notes.append(f'the page names more than one law ({", ".join(laws)}); credited to neither')
    elif res.get('domain') == 'reg' and rep and REG_REPEALED.match(rep):
        # a regulation's own page: 'Repealed on Aug. 31/12 by NLR 62/12', '[Rep. on Mar. 28/13 by 39/13]' (regread.py)
        m = REG_REPEALED.match(rep)
        said = parse_day(m.group(1))
        laws = laws_in(m.group(2) or '')
        if said and said > last['start']:
            day, basis = said, 'the last version\'s page ("Repealed on ...")'
            if said != next_day(last['end']):
                notes.append(f'the last version is said to apply until {last["end"]}, and its page says the '
                             f'regulation was repealed on {said} ("{rep}")')
        else:
            notes.append(f'the page says "{rep}", which gives no readable day after the version\'s first day; the day '
                         'after the version ends is used')
        if len(laws) == 1:
            law = laws[0]
        elif len(laws) > 1:
            notes.append(f'the page names more than one law ({", ".join(laws)}); credited to neither')
    elif a.get('repealed_index'):
        # a regulation: the index of past versions prints '[Rep. on Jul. 17/15]' before its versions (no law named)
        said = a.get('repealed_index_day')
        if said and said > last['start']:
            day, basis = said, 'the index ("[Rep. on <day>]")'
            if said != next_day(last['end']):
                notes.append(f'the last version is said to apply until {last["end"]}, and the index says the '
                             f'regulation was repealed on {said} ("[{a["repealed_index"]}]")')
        else:
            notes.append(f'the index says "[{a["repealed_index"]}]", which gives no readable day after the first day '
                         'of the last version; the day after the version ends is used')
    else:
        notes.append(f'the {res.get("noun", "act")} is not in the current consolidation and its last version ends '
                     f'{last["end"]}; the {"index" if res.get("noun") == "regulation" else "page"} does not say why')
    res['end'] = {'day': day, 'law': law, 'text': rep, 'basis': basis, 'notes': notes, 'url': last['url'],
                  'index_text': a.get('repealed_index') or ''}
    return res


# ---------------------------------------------------------------- changes and causes

_WORD = re.compile(r'[^\W_]+')


def letters(s: str) -> str:
    """The letters and digits of a text, case-folded: two texts with the same letters differ only in spacing,
    punctuation, quotes or case."""
    return ''.join(_WORD.findall(s.casefold()))


def unit_order(key: str) -> tuple:
    if key == 'front':
        return (0, 0, (), key)
    if key in ('preamble', 'enacting'):
        return (0, 1 if key == 'preamble' else 2, (), key)
    m = re.fullmatch(r's(\d+)((?:\.\d+)*)(?:-(\d+))?', key)
    if m:
        return (1 if not m.group(3) else 2, int(m.group(1)), tuple(int(x) for x in m.group(2).split('.')[1:]), key)
    return (3, 0, (), key)


def build_act(r: dict) -> dict:
    """The changes between consecutive versions of one act, each file's change credited to a law, or labelled."""
    vs = r['versions']
    if r.get('domain') == 'reg':
        keys = [v['page']['reg_key'] for v in vs if v['page'].get('reg_key')]
        own = {alt for k in keys for alt in reg_aliases(k)}
        r['folder'] = reg_folder(keys[-1]) if keys else None
    else:
        chapters = [v['page']['chapter'] for v in vs if folder_of(v['page']['chapter'])]
        own = {folder_of(c).replace('-c', ' c', 1) for c in chapters}
        r['folder'] = folder_of(chapters[-1]) if chapters else None
    r['title'] = next((v['page']['title'] for v in reversed(vs) if v['page']['title']), '') or r['index_title'] or ''
    if r.get('domain') == 'reg' and r['index_title']:
        # a regulation's page title is its long title ('... Regulations under the ... Act (O.C. 2015-137)'); the
        # index's is the short one
        r['title'] = r['index_title']
    r['own'] = sorted(own)
    changes = []
    seen: dict[str, set[str]] = {}
    seq = vs + ([{'kind': 'end', 'start': r['end']['day'], 'url': r['end']['url'], 'page': {'units': {},
                  'amended_laws': []}, 'notes': r['end']['notes'], 'basis': r['end']['basis']}] if r['end'] else [])
    for i, (prev, cur) in enumerate(zip(seq, seq[1:])):
        if i:
            for k, u in seq[i - 1]['page']['units'].items():
                seen.setdefault(k, set()).add(u['text'])
        pu, cu = prev['page']['units'], cur['page']['units']
        allkeys = sorted(set(pu) | set(cu), key=unit_order)
        changed = [k for k in allkeys if (pu.get(k) or {}).get('text') != (cu.get(k) or {}).get('text')]
        new: dict[str, set[str]] = {}
        for k in changed:
            pc = set(pu[k]['cites']) if k in pu else set()
            cc = set(cu[k]['cites']) if k in cu else set()
            new[k] = cc - pc
            if new[k] - own:
                new[k] -= own
        amended_new = set(cur['page']['amended_laws']) - set(prev['page']['amended_laws']) - own
        L = sorted({law for k in changed for law in new[k]} | amended_new, key=law_order)
        cause, why = {}, {}
        if cur['kind'] == 'end':
            for k in changed:
                if r['end']['law']:
                    cause[k], why[k] = r['end']['law'], 'repeal'
                else:
                    cause[k], why[k] = 'removed', 'not in the current consolidation'
        else:
            for k in changed:
                if k in pu and k in cu and letters(pu[k]['text']) == letters(cu[k]['text']):
                    cause[k], why[k] = 'editorial', 'letters and digits unchanged (spacing, punctuation or quotes only)'
                elif len(new[k]) == 1:
                    cause[k], why[k] = next(iter(new[k])), 'citation'
                elif len(new[k]) > 1:
                    cause[k], why[k] = 'candidates', 'the note gains citations of two or more laws'
                elif len(L) == 1:
                    cause[k], why[k] = L[0], 'date only'
                elif L:
                    cause[k], why[k] = 'candidates', 'no new citation in its note; two or more laws are new that day'
                else:
                    cause[k], why[k] = 'unexplained', 'no new citation in its note or anywhere in the version'
                c = cu.get(k)
                if c and c['text'] in seen.get(k, ()) and why[k] != 'citation':
                    why[k] += '; the text equals an earlier version of it'
        groups: dict[str, list[str]] = {}
        for k in changed:
            groups.setdefault(cause[k], []).append(k)
        cand = sorted({law for k in groups.get('candidates', []) for law in (new[k] or set(L))}, key=law_order)
        changes.append({'from': cur['start'], 'url': cur['url'], 'laws': L, 'groups': groups, 'why': why, 'new': new,
                        'candidates': cand, 'prev': prev, 'cur': cur})
    r['changes'] = changes
    return r


# ---------------------------------------------------------------- the git repository

def git(repo: Path, *args: str, env: dict | None = None, input: str | None = None) -> str:
    out = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, encoding='utf-8', env=env, input=input)
    if out.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {out.stderr}')
    return out.stdout


def nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def last_sunday(year: int, month: int) -> dt.date:
    d = (dt.date(year, month + 1, 1) if month < 12 else dt.date(year + 1, 1, 1)) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def stjohns_offset(day: dt.date) -> int:
    """Minutes from UTC at noon on a day in St. John's (written out, because this machine has no tz database).
    Newfoundland Standard Time is UTC-3:30; daylight time UTC-2:30: from the second Sunday in March to the first
    Sunday in November since 2007, from the first Sunday in April to the last Sunday in October from 1987 to 2006
    (in 1988 "double daylight time", UTC-1:30), and from the last Sunday in April to the last Sunday in October
    before. Clocks change in the night, so noon on the first day is daylight time and noon on the last standard."""
    y = day.year
    if y >= 2007:
        start, end = nth_sunday(y, 3, 2), nth_sunday(y, 11, 1)
    elif y >= 1987:
        start, end = nth_sunday(y, 4, 1), last_sunday(y, 10)
    else:
        start, end = last_sunday(y, 4), last_sunday(y, 10)
    if start <= day < end:
        return -90 if y == 1988 else -150
    return -210


def commit_date(day: str, seq: list[int]) -> str:
    """Noon in St. John's on the day, as '<unix seconds> <offset>'. Git cannot record a time before 1970, so a day
    before then would be dated 1 January 1970 (UTC), one second apart in order; the real day is in In-force."""
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        seq[0] += 1
        return f'{seq[0]} +0000'
    off = stjohns_offset(d)
    stamp = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone(dt.timedelta(minutes=off))).timestamp())
    return f'{stamp} -{-off // 60:02d}{-off % 60:02d}'


def author_of(law: str) -> tuple[str, str]:
    return law, f'{law_slug(law)}@assembly-nl.invalid'


def change_kind(p: dict | None, c: dict | None) -> str:
    if p is None:
        return 'Added'
    if c is None:
        return 'Removed from the consolidation'
    return 'Amended'


PUBLISHER_GROUPS = ('unexplained', 'candidates', 'removed', 'editorial')


def plan_commits(built: list[dict]) -> list[dict]:
    """Every commit, in the order the changes took effect. On a day: the first version of each act; then one commit per
    amending law (laws in order of citation), covering every act it changed that day; then changes with two candidate
    laws, changes nothing explains, versions with no change, and acts that left the consolidation unexplained.
    The statutes' commits of a day come first, then the regulations' (the same order within each): a law that changed
    statutes and regulations on one day has one commit for each, so the statutes' commits are the same with or
    without the regulations."""
    items: dict[tuple, dict] = {}
    for ai, b in enumerate(built):
        dom = 1 if b.get('domain') == 'reg' else 0
        day0 = b['versions'][0]['start']
        items[(day0, dom, 0, (ai,), '')] = {'kind': 'start', 'day': day0, 'parts': [(b, None)], 'domain': dom}
        for ch in b['changes']:
            laws = [x for x in ch['groups'] if x not in PUBLISHER_GROUPS]
            for law in laws:
                key = (ch['from'], dom, 1, law_order(law), law)
                items.setdefault(key, {'kind': 'law', 'day': ch['from'], 'law': law, 'parts': [],
                                       'domain': dom})['parts'].append((b, ch))
            for n, kind in ((2, 'candidates'), (3, 'unexplained'), (4, 'editorial'), (6, 'removed')):
                if kind in ch['groups']:
                    items[(ch['from'], dom, n, (ai,), '')] = {'kind': kind, 'day': ch['from'], 'parts': [(b, ch)],
                                                              'domain': dom}
            if not ch['groups']:
                items[(ch['from'], dom, 5, (ai,), '')] = {'kind': 'nochange', 'day': ch['from'], 'parts': [(b, ch)],
                                                          'domain': dom}
    return [items[k] for k in sorted(items)]


def short_list(names: list[str], limit: int = 6) -> str:
    return ', '.join(names) if len(names) <= limit else f'{", ".join(names[:limit])} and {len(names) - limit} more'


def subject_for(parts: list[tuple[dict, list[str]]], tail: str) -> str:
    def full():
        return '; '.join(f'{b["folder"]}: {", ".join(labels)}' for b, labels in parts)

    def counts():
        return '; '.join(f'{b["folder"]}: {len(labels)} provision{"s" if len(labels) != 1 else ""}' for b, labels in parts)
    s = f'{full()} ({tail})'
    if len(s) <= 72:
        return s
    s = f'{counts()} ({tail})'
    if len(s) <= 72:
        return s
    return f'{len(parts)} {noun(parts[0][0])}s, {sum(len(x) for _, x in parts)} provisions ({tail})'


def version_trailers(b: dict, v: dict) -> list[str]:
    """In-force-basis and Date-note trailers of one act's version (one line per value)."""
    out = [f'In-force-basis: {b["folder"]}: {v["basis"]}']
    out += [f'Date-note: {b["folder"]}: {n}' for n in v['notes']]
    return out


def change_message(item: dict) -> tuple[tuple[str, str], list[str], list[str], list[tuple[dict, str, dict | None]]]:
    kind = item['kind']
    day = item['day']
    writes, listing, affecting, subject_parts, evidence, consolidated, vtrailers = [], [], [], [], {}, [], []
    for b, ch in item['parts']:
        group = item.get('law') if kind == 'law' else kind
        keys = sorted(ch['groups'].get(group, []), key=unit_order) if kind != 'nochange' else []
        consolidated.append(ch['url'])
        vtrailers += version_trailers(b, ch['cur'])
        by_kind: dict[str, list[str]] = {}
        labels = []
        for k in keys:
            p, c = ch['prev']['page']['units'].get(k), ch['cur']['page']['units'].get(k)
            u = c or p
            labels.append(u['label'])
            by_kind.setdefault(change_kind(p, c), []).append(u['label'])
            affecting.append(f'{b["folder"]} {u["label"]}')
            writes.append((b, k, c))
            evidence.setdefault(ch['why'][k], []).append(f'{b["folder"]} {u["label"]}')
        if ch['cur']['kind'] == 'end':
            subject_parts.append((b, [f'whole {noun(b)}']))
        else:
            subject_parts.append((b, labels))
        for name in ('Added', 'Amended', 'Removed from the consolidation'):
            if name in by_kind:
                listing.append(f'{b["folder"]} ({b["title"]}), {name.lower()}: {short_list(by_kind[name], 40)}')
    trailers = []
    PUB = publisher_of(item['parts'][0][0])
    if kind == 'law':
        law = item['law']
        author = author_of(law)
        msg = [subject_for(subject_parts, law), '']
        msg += listing + ['']
        whys = {w.split(';')[0] for w in evidence}
        if 'citation' in whys:
            trailers.append('Evidence: citation of this law added to the history note of the provision in the version '
                            'from this day')
        if 'repeal' in whys:
            for b, ch in item['parts']:
                if ch['cur']['kind'] == 'end':
                    msg += [f'{b["folder"]} ({b["title"]}) leaves the consolidation: the page of its last version '
                            f'says "{b["end"]["text"]}".', '']
                    trailers.append(f'Evidence: repeal named on the page of the last version ({b["folder"]})')
        for w, provs in sorted(evidence.items()):
            if w.startswith('date only'):
                msg += ['These provisions changed on this day, but their history notes cite no new law. This law is',
                        'the only one whose citation is new in this version (in the notes or the Amended list), so the',
                        f'change is credited to it by date only: {short_list(provs, 20)}.', '']
                trailers += [f'Evidence: date only for {p}' for p in provs]
            if 'equals an earlier version' in w:
                msg += ['The new text equals an earlier version of the provision (a reprint of older text): '
                        f'{short_list(provs, 20)}.', '']
        trailers.append(f'Kind: {law_kind(law)}')
        trailers.append(f'In-force: {day}')
    elif kind == 'candidates':
        author = PUB
        names = [c for b, ch in item['parts'] for c in ch['candidates']]
        msg = [subject_for(subject_parts, 'two or more laws, credited to neither'), '',
               f'These provisions changed on this day, and the laws that may have changed them are {", ".join(names)}.',
               'The history notes do not say which law made which change, so the change is credited to none of them.',
               ''] + listing + ['']
        for w, provs in sorted(evidence.items()):
            trailers += [f'Evidence: {w} ({p})' for p in provs]
        trailers.append('Kind: two or more laws, credited to neither (publisher)')
        trailers.append(f'In-force: {day}')
        trailers += [f'Candidate: {n}' for n in names]
    elif kind == 'unexplained':
        author = PUB
        msg = [subject_for(subject_parts, 'no amending law cited'), '',
               'The consolidated text of these provisions changed on this day, but no history note and no Amended list',
               'in the version from this day cites a new law. The change is credited to no law: it is the publisher\'s.',
               ''] + listing + ['']
        for w, provs in sorted(evidence.items()):
            if 'equals an earlier version' in w:
                msg += ['The new text equals an earlier version of the provision (a reprint of older text): '
                        f'{short_list(provs, 20)}.', '']
        trailers.append('Kind: no amending law cited (publisher)')
        trailers.append(f'In-force: {day}')
    elif kind == 'editorial':
        author = PUB
        msg = [subject_for(subject_parts, 'spacing and punctuation only'), '',
               'The text of these provisions changed on this day only in spacing, punctuation, quotes or case: their',
               'letters and digits are the same as in the version before. This is the publisher\'s reformatting, not',
               'an amendment, and is credited to no law.', ''] + listing + ['']
        trailers.append('Kind: editorial (publisher)')
        trailers.append(f'In-force: {day}')
    elif kind == 'removed':
        author = PUB
        b = item['parts'][0][0]
        if b.get('domain') == 'reg':
            msg = [f'{b["folder"]}: leaves the consolidation (no repealing law named)', '',
                   f'{b["title"]} is not among the current consolidated regulations. '
                   + (f'The index of past versions marks it "[{b["end"]["index_text"]}]" and names no repealing law.'
                      if b['end']['index_text'] else
                      'Its last point-in-time version ends the day before this one, and the site does not say why.'),
                   'Its files are removed; the change is credited to no law.', '']
        else:
            msg = [f'{b["folder"]}: leaves the consolidation (no repealing law named)', '',
                   f'{b["title"]} is not among the current consolidated statutes, and its last point-in-time version ends',
                   f'the day before this one. ' + (f'The page of that version says "{b["end"]["text"]}", naming no single law.'
                                                    if b['end']['text'] else 'The site does not say why.'),
                   'Its files are removed; the change is credited to no law.', '']
        trailers.append('Kind: removed from the consolidation (publisher)')
        trailers.append(f'In-force: {day}')
    else:   # nochange
        author = PUB
        b = item['parts'][0][0]
        msg = [f'{b["folder"]}: new version, no change in the text', '',
               f'The site starts a new version of {b["title"]} on this day, but its text, including the history notes,',
               'is the same as the version before (the change may be in the Amended list only, or outside the text).',
               'This commit changes no file; it marks the day.', '']
        trailers.append('Kind: version without a change in the text (publisher)')
        trailers.append(f'In-force: {day}')
    trailers += vtrailers
    trailers += [f'Affecting: {a}' for a in affecting]
    trailers += [f'Consolidated: {u}' for u in consolidated]
    return author, msg, trailers, writes


def start_message(b: dict) -> tuple[tuple[str, str], list[str], list[str]]:
    v = b['versions'][0]
    day = v['start']
    what = 'first point-in-time version' if v['kind'] == 'pit' else 'current version (the only one)'
    msg = [f'{b["folder"]}: {b["title"]}, as consolidated on {day}', '',
           f'The text of the {what} the House of Assembly publishes for this {noun(b)},',
           f'in force from {day}. It is the {noun(b)} as {"made" if noun(b) == "regulation" else "enacted"} and amended up to then; the site has no earlier',
           'version, so the lines of this commit are credited to the publisher, not to a law. The history note of',
           'each section lists the laws that made it.', '']
    trailers = ['Kind: first published version (publisher)', f'In-force: {day}'] + version_trailers(b, v)
    trailers.append(f'Consolidated: {v["url"]}')
    return publisher_of(b), msg, trailers


def write_repo(repo: Path, built: list[dict], left: list[dict], today: str, retrieved: str) -> tuple[list[dict], dict]:
    """Writes the history with one `git fast-import`, then the documentation commit in the working tree. The folder is
    emptied first, so it must be a build folder: one with a remote (the checkout that pushes) is refused."""
    if (repo / '.git').exists():
        remotes = subprocess.run(['git', 'remote'], cwd=repo, capture_output=True, text=True).stdout.split()
        if remotes:
            raise SystemExit(f'{repo} has a remote ({", ".join(remotes)}): it is a checkout, not a build folder; '
                             'refusing to empty it')
    elif repo.exists() and any(repo.iterdir()):
        raise SystemExit(f'{repo} is not empty and not a git repository; refusing to write into it')

    def force(fn, path, _exc):
        os.chmod(path, 0o700)
        fn(path)
    if repo.exists():
        for p in repo.iterdir():
            shutil.rmtree(p, onexc=force) if p.is_dir() else p.unlink()
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'core.autocrlf', 'false')

    seq = [0]
    tags: dict[str, int] = {}
    law_days: dict[str, list[tuple[str, int]]] = {}
    log = []
    stream = io.BytesIO()
    last_main = None
    future_started = False

    def data(x: bytes):
        stream.write(b'data %d\n' % len(x) + x + b'\n')

    for mark, item in enumerate(plan_commits(built), 1):
        day = item['day']
        if item['kind'] == 'start':
            b = item['parts'][0][0]
            writes = [(b, k, u) for k, u in b['versions'][0]['page']['units'].items()]
            author, msg, trailers = start_message(b)
            folders = [b['folder']]
        else:
            author, msg, trailers, writes = change_message(item)
            folders = [b['folder'] for b, _ in item['parts']]
        when = commit_date(day, seq)
        text = '\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n'
        if '\r' in text:
            raise SystemExit('a carriage return in a commit message')
        branch = 'main'
        if day > today:
            branch = 'future'
        stream.write(f'commit refs/heads/{branch}\nmark :{mark}\n'
                     f'author {author[0]} <{author[1]}> {when}\n'
                     f'committer {COMMITTER[0]} <{COMMITTER[1]}> {when}\n'.encode('utf-8'))
        data(text.encode('utf-8'))
        if branch == 'future' and not future_started:
            future_started = True
            if last_main:
                stream.write(f'from :{last_main}\n'.encode('utf-8'))
        for b, k, u in writes:
            path = f'{b["folder"]}/{k}.md'
            if u is None:
                stream.write(f'D {path}\n'.encode('utf-8'))
            else:
                stream.write(f'M 100644 inline {path}\n'.encode('utf-8'))
                data(u['text'].encode('utf-8'))
        stream.write(b'\n')
        if branch == 'main':
            last_main = mark
        for f in folders:
            tags[f'{f}/{day}'] = mark
        if item['kind'] == 'law':
            law_days.setdefault((item['domain'], item['law']), []).append((day, mark))
        log.append({'day': day, 'kind': item['kind'], 'law': item.get('law'), 'author': author[0], 'branch': branch,
                    'units': len(writes), 'folders': folders, 'clamped': day < '1970-01-01',
                    'domain': 'reg' if item.get('domain') else 'statute'})
    # a law's tags: law/<slug> for what it did to the statutes, regulations/law/<slug> for what it did to the
    # regulations (so the statutes' tags are the same with or without the regulations)
    for (dom, law), days in law_days.items():
        name = (f'{REG_ROOT}/' if dom else '') + tag_of(law)
        if len(days) == 1:
            tags[name] = days[0][1]
        else:
            for day, mark in days:
                tags[f'{name}/{day}'] = mark
    for name in sorted(tags):
        stream.write(f'reset refs/tags/{name}\nfrom :{tags[name]}\n\n'.encode('utf-8'))
    out = subprocess.run(['git', 'fast-import', '--quiet', '--date-format=raw', '--done'], cwd=repo,
                         input=stream.getvalue() + b'done\n', capture_output=True)
    if out.returncode:
        raise SystemExit(f'git fast-import failed: {out.stderr.decode(errors="replace")}')
    git(repo, 'checkout', '-q', '-f', 'main')
    docs = HERE / 'repo'
    for p in sorted(docs.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            dest = repo / p.relative_to(docs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    for name in ('build.py', 'nlread.py', 'regread.py', 'blocks.py', 'fetchlib.py', 'manifest.py', 'reproduce.py', 'check_repo.py',
                 'check_letters.py', 'checks.py'):
        if (HERE / name).exists():
            (repo / 'tools' / name).write_bytes((HERE / name).read_bytes())
    from reproduce import write_docs
    write_docs(repo, built, left, log, tags, today, retrieved)
    git(repo, 'add', '-A')
    when = commit_date(today, seq)
    env = {**os.environ, 'GIT_AUTHOR_NAME': COMMITTER[0], 'GIT_AUTHOR_EMAIL': COMMITTER[1], 'GIT_AUTHOR_DATE': when,
           'GIT_COMMITTER_NAME': COMMITTER[0], 'GIT_COMMITTER_EMAIL': COMMITTER[1], 'GIT_COMMITTER_DATE': when}
    # the message goes as bytes: a text-mode pipe on Windows would turn every newline into CR LF
    msg = (f'Documentation, tables, tools and REPRODUCE.md\n\nNo law text changes.\n\nKind: documentation\n'
           f'In-force: {today}\n').encode('utf-8')
    out = subprocess.run(['git', 'commit', '-q', '--cleanup=verbatim', '-F', '-'], cwd=repo, env=env, input=msg,
                         capture_output=True)
    if out.returncode:
        raise SystemExit(f'git commit failed: {out.stderr.decode(errors="replace")}')
    return log, tags


# ---------------------------------------------------------------- main

def retrieval_day(urls: list[str] | None = None) -> str:
    """The day (UTC) the newest cached page was retrieved (of the given pages, or of the whole cache); the cache does not
    change on a rebuild. The statutes and the regulations each have their own: the regulations were fetched later, and
    the statutes' days do not depend on that."""
    files = [cache_path(u) for u in urls] if urls else [p for p in CACHE.iterdir() if p.name != '.gitignore']
    return dt.datetime.fromtimestamp(max(p.stat().st_mtime for p in files), dt.timezone.utc).date().isoformat()


STATUTE_INDEXES = [f'{SITE}/titleindex.htm', f'{SITE}/pointintime/pittitleindex.htm']
REG_INDEXES = [f'{SITE}/regulations/titleindex2.htm', f'{SITE}/pointintime/pittitleindex2.htm']


def merge_split_regs(regs: list[dict], pages: dict) -> list[dict]:
    """A regulation the site lists under two file names: one with only point-in-time versions (no current page), the
    other with only a current page, both pages printing the same regulation number (rc780018 and rc190078, NLR 78/19).
    They are one regulation: its past versions and its current one, merged into one record (the merge is noted on the
    regulation in data/regulations.csv)."""
    def key_of(a):
        for u in ([a['current']] if a['current'] else []) + [v['url'] for v in reversed(a['pit'])]:
            p = pages.get(u)
            if p and not p['missing'] and p.get('reg_key'):
                return p['reg_key']
        return None
    groups: dict[str, list[dict]] = {}
    for a in regs:
        k = key_of(a)
        if k:
            groups.setdefault(k, []).append(a)
    drop = set()
    out = []
    for a in regs:
        k = key_of(a)
        g = groups.get(k, [])
        if len(g) == 2 and a['id'] not in drop:
            cur = next((x for x in g if x['current'] and not x['pit']), None)
            pit = next((x for x in g if x['pit'] and not x['current']), None)
            if cur and pit and cur is not pit:
                drop |= {cur['id'], pit['id']}
                out.append({**cur, 'pit': pit['pit'], 'merged_from': pit['id'],
                            'merge_note': f'the site lists the point-in-time versions of this regulation ({k}) under the '
                                          f'file name {pit["id"]} and its current version under {cur["id"]}; they '
                                          'are one record'})
                continue
        if a['id'] not in drop:
            out.append(a)
    return out


def page_urls(acts: list[dict]) -> list[str]:
    return sorted({u for a in acts for u in [a['current']] + [v['url'] for v in a['pit']] if u})


def build_all(today: str) -> tuple[list[dict], list[dict], str]:
    """(acts and regulations in the record, those left out, retrieval day of the statutes). Each regulation's dict has
    domain 'reg'; RETRIEVED holds each domain's retrieval day."""
    man = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    acts = list(man['acts'])
    regs = []
    if (HERE / 'regmanifest.json').exists():
        # the regulations (manifest.py --regulations): every one of their pages must be cached too
        regs = [{**a, 'domain': 'reg'} for a in json.loads((HERE / 'regmanifest.json').read_text(encoding='utf-8'))['acts']]
    urls = page_urls(acts + regs)
    for u in urls:
        if not cache_path(u).exists():
            raise SystemExit(f'not cached: {u} (run manifest.py --fetch, or manifest.py --regulations --fetch)')
    retrieved = retrieval_day(page_urls(acts) + STATUTE_INDEXES)
    RETRIEVED['statute'] = retrieved
    if regs:
        RETRIEVED['reg'] = retrieval_day(page_urls(regs) + REG_INDEXES)
    pages = read_all(urls)
    regs = merge_split_regs(regs, pages)
    built, left = [], []
    for a in acts + regs:
        r = resolve_act(a, pages, RETRIEVED['reg'] if a.get('domain') == 'reg' else retrieved, today)
        if r['left_out'] or not r['versions']:
            left.append(r)
            continue
        if a.get('merge_note'):
            r['folder_note'] = a['merge_note']
        built.append(build_act(r))
    for b in built:
        if not b['folder']:
            if b.get('domain') == 'reg':
                b['folder'] = f'{REG_ROOT}/id-{b["id"]}'
                b['folder_note'] = ('no regulation number on any of its pages: the folder is named by the site\'s '
                                    'file name')
            else:
                b['folder'] = f'id-{b["id"]}'
                b['folder_note'] = 'no chapter citation on any of its pages: the folder is named by the site\'s file name'
    names: dict[str, list[dict]] = {}
    for b in built:
        names.setdefault(b['folder'], []).append(b)
    for f, bs in names.items():
        if len(bs) > 1:
            texts = {b['id']: {k: u['text'] for k, u in b['versions'][-1]['page']['units'].items()} for b in bs}
            keep = sorted(bs, key=lambda b: (b['index_title'] is None, len(b['versions']) * -1, b['id']))[0]
            same = [b for b in bs if b is not keep and texts[b['id']] == texts[keep['id']]
                    and len(b['versions']) == 1 and not b['end']]
            for b in same:
                b['left_out'] = (f'a second file name for {keep["id"]} on the site: the same chapter ({f}) and the same '
                                 'text; only the version in the title index is kept')
                built.remove(b)
                left.append(b)
            bs = [b for b in bs if b not in same]
            if len(bs) == 1:
                continue
            for b in bs:
                b['folder'] = f'{f}-{b["id"]}'
                if b.get('domain') == 'reg':
                    b['folder_note'] = (f'{len(bs)} regulations on the site share the number {f.split("/", 1)[1]}: '
                                        'the folder adds the site\'s file name')
                    continue
                b['folder_note'] = (f'{len(bs)} acts on the site share the chapter citation {f}: the folder adds the '
                                    f'site\'s file name')
    built.sort(key=lambda b: b['folder'])
    return built, left, retrieved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(DEFAULT_REPO))
    ap.add_argument('--today', required=True, help='the build day; versions in force after it go on the future branch')
    ap.add_argument('--offline', action='store_true', help='read only the cache (the build never fetches anyway)')
    ap.add_argument('--analyse', action='store_true', help='print the counts and stop')
    ap.add_argument('--verify-rebuild', action='store_true',
                    help='build again into a temporary folder, compare every hash with --repo, and record the result')
    args = ap.parse_args()
    if args.verify_rebuild:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, '-X', 'utf8', str(HERE / 'build.py'), '--offline', '--repo', tmp,
                                  '--today', args.today], capture_output=True, text=True)
            if out.returncode:
                raise SystemExit(out.stderr)

            def hashes(r):
                heads = {b: git(Path(r), 'rev-parse', b).strip() for b in ('main', 'future')
                         if git(Path(r), 'branch', '--list', b).strip()}
                tags = dict(x.split(' ', 1)[::-1] for x in git(Path(r), 'show-ref', '--tags').split('\n') if x)
                return heads, tags
            a, b = hashes(args.repo), hashes(tmp)
            rec = {'identical': a == b, 'main': a[0].get('main'), 'rebuilt_main': b[0].get('main'),
                   'future': a[0].get('future'), 'tags': len(a[1]), 'tags_identical': a[1] == b[1],
                   'history_tip': git(Path(args.repo), 'rev-parse', 'main~1').strip()}
            print(json.dumps(rec, indent=1))
            p = HERE / 'verification.json'
            ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
            # only what does not depend on the documentation commit, so recording it does not change the next build
            ver['rebuild'] = {k: rec[k] for k in ('identical', 'history_tip', 'tags')}
            p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
            for f in Path(tmp).rglob('*'):
                os.chmod(f, 0o700)
            raise SystemExit(0 if rec['identical'] else 1)
    built, left, retrieved = build_all(args.today)
    n_changes = sum(len(ks) for b in built for ch in b['changes'] for ks in ch['groups'].values())
    print(f'{len(built)} acts in the record, {len(left)} left out; {sum(len(b["versions"]) for b in built)} versions, '
          f'{n_changes} file changes; retrieved {retrieved}')
    if args.analyse:
        from collections import Counter
        why = Counter(w.split(';')[0] for b in built for ch in b['changes'] for w in ch['why'].values())
        print(dict(why))
        print('bases', Counter(v['basis'] for b in built for v in b['versions']))
        print('notes', Counter(n.split(':')[0][:40] for b in built for v in b['versions'] for n in v['notes']))
        print('excluded', Counter(e['reason'][:50] for b in built + left for e in b['excluded']))
        print('left', [(x['id'], x['left_out']) for x in left])
        print('ends', Counter((bool(b['end']['law']) if b['end'] else None) for b in built))
        print('folder notes', [(b['id'], b['folder']) for b in built if b.get('folder_note')])
        print('future', sum(1 for b in built for v in b['versions'] if v['start'] > args.today))
        return
    log, tags = write_repo(Path(args.repo), built, left, args.today, retrieved)
    print(f'repo: {len(log)} commits + 1 documentation commit, {len(tags)} tags -> {args.repo}')


if __name__ == '__main__':
    main()
