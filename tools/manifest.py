"""Reads the House of Assembly's two statute indexes and writes manifest.json: every consolidated statute with its
current page and its point-in-time (PIT) versions, each with the days it applies from and to.

    python -X utf8 manifest.py [--offline] [--fetch]

--fetch then fetches every page in the manifest (at most 4 requests at a time, through fetchlib's cache).

Sources:
- https://www.assembly.nl.ca/legislation/sr/titleindex.htm: the current consolidated statutes.
- https://www.assembly.nl.ca/legislation/sr/pointintime/pittitleindex.htm: per act, "PIT - From A to B" links to the
  past versions (pitstatutes/pit<id>.<yyyymmdd of B>.htm) and, for the current version, an unlinked "PIT - From A".
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetchlib import NOT_FOUND, fetch, fetch_all  # noqa: E402

BASE = 'https://www.assembly.nl.ca/legislation/sr'
TITLE_INDEX = f'{BASE}/titleindex.htm'
PIT_INDEX = f'{BASE}/pointintime/pittitleindex.htm'
# --regulations: the same two indexes for the consolidated regulations (regmanifest.json)
KINDS = {
    'statutes': {'title': TITLE_INDEX, 'pit': PIT_INDEX, 'cur': '/statutes/', 'pitdir': 'pitstatutes',
                 'out': 'manifest.json'},
    'regulations': {'title': f'{BASE}/regulations/titleindex2.htm', 'pit': f'{BASE}/pointintime/pittitleindex2.htm',
                    'cur': '/regulations/', 'pitdir': 'pitregulations', 'out': 'regmanifest.json'},
}
KIND = KINDS['statutes']

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov',
                                          'dec'])}


def text(h: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', h)).replace('\xa0', ' ')).strip()


def parse_day(s: str) -> str | None:
    """'December 16, 2008', 'Dec. 16, 2008', 'Aug. 8/25' -> ISO day; None when there is no date, or the label's month
    or day cannot be read ('Janaury', 'September 31'): the builder then takes the day from the page."""
    try:
        return _parse_day(s)
    except (KeyError, ValueError):
        return None


def _parse_day(s: str) -> str | None:
    s = s.strip().lower().replace('sept', 'sep')
    s = re.sub(r'^gazette date +', '', s)    # regulations: 'From Gazette Date March 8, 2013 to ...'
    m = re.match(r'([a-z]{3})[a-z]*\.? *(\d{1,2}) *, *(\d{4})', s)
    if m:
        return dt.date(int(m[3]), MONTHS[m[1]], int(m[2])).isoformat()
    m = re.match(r'([a-z]{3})[a-z]*\.? *(\d{1,2}) */ *(\d{2})\b', s)
    if m:
        y = int(m[3])
        return dt.date(2000 + y if y < 50 else 1900 + y, MONTHS[m[1]], int(m[2])).isoformat()
    return None


def act_id(url: str) -> str:
    """The site's file identifier of an act, lower case: 'a01-2' from .../statutes/A01-2.htm or pita01-2.2016....htm."""
    name = url.rsplit('/', 1)[-1].lower()
    if name.startswith('pit'):
        return re.match(r'pit(.+?)\.\d{8,9}[^./]*\.htm$', name)[1]   # 'pitrc000032.20150129interim.htm' too
    return name[:-4]


def norm_url(u: str) -> str:
    u = u.strip().replace(' ', '%20')
    u = re.sub(r'(?i)^https?://(www\.)?assembly\.nl\.ca', 'https://www.assembly.nl.ca', u)
    if u.startswith('/'):
        u = 'https://www.assembly.nl.ca' + u
    if KIND is KINDS['regulations']:    # the regulation index writes some links with a doubled slash
        u = re.sub(r'(?<!:)//+', '/', u)
    return u


def current_acts(offline: bool) -> dict[str, dict]:
    page = fetch(KIND['title'], offline).decode('latin-1')
    acts = {}
    for m in re.finditer(r'(?is)<a[^>]+href="([^"]*' + KIND['cur'] + r'[^"]+\.htm)"[^>]*>(.*?)</a>', page):
        url, title = norm_url(m[1]), text(m[2])
        aid = act_id(url)
        if not title or aid in acts:
            continue
        acts[aid] = {'id': aid, 'title': title, 'current': url}
    return acts


REPS: dict[str, str] = {}


def pit_entries(offline: bool) -> tuple[dict[str, list[dict]], dict[str, dict], list[str]]:
    """Per act: the PIT versions; and per current act, the first day of its current version (when given)."""
    page = fetch(KIND['pit'], offline).decode('latin-1')
    pits: dict[str, list[dict]] = {}
    cur: dict[str, dict] = {}
    notes: list[str] = []
    for li in re.findall(r'(?is)<li>(.*?)</li>', page):
        # Walk the item: links and the text between them, in order.
        last_current = None
        pos = 0
        tokens = []
        for m in re.finditer(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', li):
            tokens.append(('text', text(li[pos:m.start()])))
            tokens.append(('link', norm_url(m[1]), text(m[2])))
            pos = m.end()
        tokens.append(('text', text(li[pos:])))
        rep = None
        for t in tokens:
            if t[0] == 'link':
                url, label = t[1], t[2]
                low = url.lower()
                if KIND['pitdir'] in low:
                    aid = act_id(url)
                    m = re.match(r'(?i)(?:from\s+)+(.+?)\s+to\s+(.+)$', label)
                    end = re.search(r'\.(\d{8})\d?[^./]*\.htm$', low)[1]
                    end_iso = f'{end[:4]}-{end[4:6]}-{end[6:]}'
                    frm = parse_day(m[1]) if m else None
                    to = parse_day(m[2]) if m else None
                    if to and to != end_iso:
                        notes.append(f'{aid}: label end {to} differs from file date {end_iso} ({label})')
                    pits.setdefault(aid, []).append({'url': url, 'label': label, 'from': frm, 'to': to or end_iso,
                                                     'file_to': end_iso})
                    if rep:
                        REPS.setdefault(aid, rep)
                        rep = None
                    last_current = None
                elif KIND['cur'] in low and '/pointintime/' not in low:
                    last_current = act_id(url)
                    cur.setdefault(last_current, {'url': url})
            else:
                s = t[1]
                m = re.search(r'\[\s*(Rep\b[^\]]*)\]', s)
                if m:      # a regulation no longer in force: '[Rep. on Jul. 17/15]', printed before its PIT links
                    rep = m[1].strip()
                for m in re.finditer(r'(?i)PIT\s*-\s*(?:From\s+)+(.*?)(?=PIT\s*-|$)', s):
                    if last_current and 'to' not in m[1].lower().split():
                        d = parse_day(m[1])
                        if d:
                            cur[last_current]['from'] = d
                    last_current = None
    return pits, cur, notes


def build_manifest(offline: bool) -> dict:
    acts = current_acts(offline)
    pits, cur, notes = pit_entries(offline)
    for aid, c in cur.items():
        if aid not in acts:
            acts[aid] = {'id': aid, 'title': None, 'current': c['url']}
        if c.get('from'):
            acts[aid]['current_from_index'] = c['from']
    for aid in pits:
        acts.setdefault(aid, {'id': aid, 'title': None, 'current': None})
    if KIND is KINDS['regulations']:
        for aid, rep in REPS.items():
            acts[aid]['repealed_index'] = rep
            acts[aid]['repealed_index_day'] = parse_day(re.sub(r'(?i)^rep\.?\s*(on\s+)?', '', rep))
    for aid, a in acts.items():
        vs = []
        seen = set()
        for v in pits.get(aid, []):
            if v['url'].lower() in seen:
                continue
            seen.add(v['url'].lower())
            vs.append(v)
        vs.sort(key=lambda v: (v['file_to'], v['url']))
        a['pit'] = vs
    return {'acts': [acts[k] for k in sorted(acts)], 'notes': notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--regulations', action='store_true', help='the regulations instead (regmanifest.json)')
    ap.add_argument('--start', type=int, default=0, help='with --fetch: start at this page (in URL order)')
    ap.add_argument('--limit', type=int, default=0, help='with --fetch: fetch at most this many pages')
    a = ap.parse_args()
    global KIND
    KIND = KINDS['regulations' if a.regulations else 'statutes']
    man = build_manifest(a.offline)
    (HERE / KIND['out']).write_text(json.dumps(man, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    acts = man['acts']
    urls = sorted({u for x in acts for u in [x['current']] + [v['url'] for v in x['pit']] if u})
    print(f'{len(acts)} acts, {sum(1 for x in acts if x["current"])} current, '
          f'{sum(len(x["pit"]) for x in acts)} PIT versions, {len(urls)} pages; {len(man["notes"])} notes')
    if a.fetch:
        done = 0
        sel = urls[a.start:a.start + a.limit] if a.limit else urls[a.start:]
        urls = sel
        for i in range(0, len(urls), 40):
            got = fetch_all(urls[i:i + 40], a.offline)
            done += len(got)
            missing = sum(1 for b in got.values() if b == NOT_FOUND)
            print(f'fetched {done}/{len(urls)} (not found in batch: {missing})', flush=True)


if __name__ == '__main__':
    main()
