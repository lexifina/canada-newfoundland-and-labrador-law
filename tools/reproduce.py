"""Writes the documentation of the Newfoundland and Labrador repository from the facts of the build that made it:
README.md, REPRODUCE.md, corpus.json and the tables in data/. Called by build.py.

The results of the checks (check_repo.py, checks.py and the rebuild that compares hashes) are read from
verification.json next to this file. They describe the build before the one that writes this file, from the same
data; a rebuild with the same data and the same verification.json gives the same hashes."""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_NAME = 'lexifina/canada-newfoundland-and-labrador-law'

DEFAULT_REACH = {
    'rule': 'presumption against retrospectivity; immediate application to procedure',
    'meaning': 'an enactment is presumed not to apply to facts completed before it came into force, or to interfere '
               'with vested rights, unless the Legislature says so expressly or by necessary implication; procedural '
               'provisions apply immediately to pending matters; a repeal does not affect rights acquired or '
               'proceedings begun under the repealed enactment',
    'authority': 'Interpretation Act, RSNL1990 cI-19, s. 29 (effect of repeal); Canadian Charter of Rights and '
                 'Freedoms, s. 11(g) and 11(i); R. v. Dineley, 2012 SCC 58; Tran v. Canada (Public Safety and '
                 'Emergency Preparedness), 2017 SCC 50',
    'decided_here': False,
}


def table(rows: list[dict]) -> str:
    buf = io.StringIO()
    if not rows:
        return ''
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write(repo: Path, rel: str, text: str):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8', newline='\n')


def write_source_sums(repo: Path, man: dict, rman: dict) -> int:
    """data/source-sha256.tsv: every index and page the build reads, with the sha256 of the retrieved file (or
    'not found'); returns the number of Rules of the Supreme Court pages among them."""
    from build import REG_INDEXES, STATUTE_INDEXES, page_urls
    from cachelib import NOT_FOUND, cache_path
    from rules import is_rules, rule_links
    pages = page_urls(man['acts']) + page_urls(rman['acts'])
    rules = [x for u in pages if is_rules(u) for x, _ in rule_links(cache_path(u).read_bytes())]
    rows = []
    for u in STATUTE_INDEXES + REG_INDEXES + sorted(set(pages)) + rules:
        b = cache_path(u).read_bytes()
        rows.append(f'{u}\t{"not found" if b == NOT_FOUND else hashlib.sha256(b).hexdigest()}\n')
    write(repo, 'data/source-sha256.tsv', 'url\tsha256\n' + ''.join(dict.fromkeys(rows)))
    return len(rules)


def write_docs(repo: Path, built: list[dict], left: list[dict], log: list[dict], tags: dict, today: str,
               retrieved: str):
    from build import PUBLISHER, REG_PUBLISHER, law_kind, law_order
    main_log = [c for c in log if c['branch'] == 'main']
    regs_built = [b for b in built if b.get('domain') == 'reg']
    regs_left = [x for x in left if x.get('domain') == 'reg']
    built_all, left_all = built, left
    built = [b for b in built_all if b.get('domain') != 'reg']
    left = [x for x in left_all if x.get('domain') != 'reg']
    reg_retrieved = next((b['retrieved'] for b in regs_built + regs_left if b.get('retrieved')), retrieved)
    # ---- tables
    acts = []
    regs = []
    versions = []
    excluded = []
    for b in built_all:
        vs = b['versions']
        (regs if b.get('domain') == 'reg' else acts).append({'folder': b['folder'], 'site_file': b['id'], 'title': b['title'], 'versions': len(vs),
                     'first_day': vs[0]['start'], 'first_day_basis': vs[0]['basis'],
                     'last_version_from': vs[-1]['start'], 'current': 'yes' if vs[-1]['kind'] == 'current' else 'no',
                     'left_consolidation': b['end']['day'] if b['end'] else '',
                     'repealed_by': (b['end'] or {}).get('law') or '', 'note': b.get('folder_note', '')
                     or b.get('end_note', '')})
        for v in vs:
            versions.append({'folder': b['folder'], 'kind': v['kind'], 'in_force': v['start'], 'to': v['end'] or '',
                             'basis': v['basis'], 'index_label': v['label'] if v['kind'] == 'pit' else '',
                             'page_range': ' to '.join(v['page']['range']) if v['page'].get('range') else '',
                             'url': v['url'], 'sha256': v['page']['sha256'], 'notes': ' | '.join(v['notes'])})
        for e in b['excluded']:
            excluded.append({'folder': b['folder'], 'site_file': b['id'], 'url': e['url'], 'index_label': e['label'],
                             'reason': e['reason']})
    for x in left_all:
        excluded.append({'folder': '', 'site_file': x['id'], 'url': '', 'index_label': f'whole {x.get("noun", "act")}',
                         'reason': x['left_out']})
        for e in x['excluded']:
            excluded.append({'folder': '', 'site_file': x['id'], 'url': e['url'], 'index_label': e['label'],
                             'reason': e['reason']})
    laws: dict[str, dict] = {}
    labelled = []
    why = collections.Counter()
    rwhy = collections.Counter()
    for b in built_all:
        for ch in b['changes']:
            for k, w in ch['why'].items():
                base = w.split(';')[0]
                (rwhy if b.get('domain') == 'reg' else why)[base] += 1
                c = ch['cur']['page']['units'].get(k) or ch['prev']['page']['units'][k]
                cause = next(g for g, ks in ch['groups'].items() if k in ks)
                if base not in ('citation', 'repeal', 'letters and digits unchanged (spacing, punctuation or quotes only)'):
                    labelled.append({'in_force': ch['from'], 'folder': b['folder'], 'provision': c['label'],
                                     'file': f'{b["folder"]}/{k}.md', 'credited_to': cause if cause not in (
                                         'candidates', 'unexplained', 'removed') else '',
                                     'reason': w, 'candidates': '; '.join(sorted(ch['new'][k] or ch['candidates']))
                                     if cause == 'candidates' else ''})
            for law, ks in ch['groups'].items():
                if law in ('candidates', 'unexplained', 'removed', 'editorial'):
                    continue
                d = laws.setdefault(law, {'law': law, 'kind': law_kind(law), 'days': set(), 'acts': set(),
                                          'provisions': 0})
                d['days'].add(ch['from'])
                d['acts'].add(b['folder'])
                d['provisions'] += len(ks)
    law_rows = [{'law': d['law'], 'kind': d['kind'], 'in_force': ' '.join(sorted(d['days'])),
                 'acts': ' '.join(sorted(d['acts'])), 'provisions': d['provisions']}
                for d in sorted(laws.values(), key=lambda d: law_order(d['law']))]
    labelled.sort(key=lambda r: (r['in_force'], r['folder'], r['file']))
    write(repo, 'data/acts.csv', table(acts))
    write(repo, 'data/regulations.csv', table(regs))
    write(repo, 'data/versions.csv', table(versions))
    write(repo, 'data/left-out.csv', table(excluded))
    write(repo, 'data/laws.csv', table(law_rows))
    write(repo, 'data/labelled.csv', table(labelled))

    # ---- corpus.json
    record_to = max(c['day'] for c in main_log)
    first = min(b['versions'][0]['start'] for b in built_all)
    first_acts = min(b['versions'][0]['start'] for b in built)
    first_regs = min((b['versions'][0]['start'] for b in regs_built), default='')
    corpus = {
        'corpus': 'ca/newfoundland-and-labrador/acts',
        'parent': 'ca/federal/acts',
        'name': 'Newfoundland and Labrador: consolidated statutes and regulations, change by change',
        'label': 'Newfoundland and Labrador',
        'language': 'en',
        'history': 'in-force',
        'history_from': first,
        'record_to': record_to,
        'built': today,
        'checked_on': max(retrieved, reg_retrieved),
        'record_note': (f'Every statute the House of Assembly publishes in its consolidated statutes ({len(built)} acts, '
                        f'retrieved on {retrieved}) and every regulation in its consolidated regulations '
                        f'({len(regs_built)} regulations under regulations/, retrieved on {reg_retrieved}), with their '
                        'point-in-time versions, checked version by version against the pages file by file and '
                        'letter by letter. Point-in-time versions are not official copies (Statutes Act, RSNL1990 '
                        'cS-26, s. 11(2)). Versions start at different days for different acts and regulations (most '
                        f'after 2006); {sum(1 for b in built if b["versions"][0]["basis"] == "retrieval day")} acts and '
                        f'{sum(1 for b in regs_built if b["versions"][0]["basis"] == "retrieval day")} regulations are '
                        'dated only by the retrieval day because the site gives no first day for their only version; '
                        f'{sum(1 for b in regs_built if b["versions"][0]["basis"] == "filed")} regulations never amended '
                        'are dated by the filing day their page prints (Date-source: filed).'),
        'in_force_trailer': 'In-force',
        'has_future_branch': any(c['branch'] == 'future' for c in log),
        'dates': {
            'commit': 'in-force',
            'other_trailers': ['In-force-basis', 'Date-source', 'Date-note'],
            'time': '12:00 America/St_Johns (UTC-3:30, UTC-2:30 in daylight time)',
        },
        'scope': {
            'trailer': 'Applies',
            'when_absent': 'this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                           'statement that the change reaches every case',
            'default_reach': DEFAULT_REACH,
        },
        'tool': 'tools/in_force_on.py',
        'citation': {
            'file': r'((?:RSNL|SNL)[0-9]{4}-c[A-Z0-9.-]+|regulations/[A-Za-z0-9.-]+)/s([0-9]+(?:\.[0-9]+)*)\.md',
            'cites': r'([Ss]ections?|[Ss]ubsections?|[Pp]aragraphs?|ss?\.) ?{n}([^0-9.]|\.[^0-9]|$)',
        },
        'path_pattern': '<chapter citation, e.g. SNL2015-cA-1.2 or RSNL1990-cH-3>/s<section>.md; front.md (title, '
                        'chapter, long title, assent, enacting words); preamble.md; schedule-<x>.md; form-<x>.md; '
                        'regulations/<regulation number, e.g. NLR-45-15, CNLR-1151-96 or NfldReg-11-98>/s<section>.md '
                        '(front.md: number, title, filing, authority)',
        'acts': len(built),
        'regulations': len(regs_built),
    }
    write(repo, 'corpus.json', json.dumps(corpus, ensure_ascii=False, indent=2) + '\n')

    # ---- counts for README and REPRODUCE
    kinds = collections.Counter(c['kind'] for c in main_log if c.get('domain') != 'reg')
    n_laws = len({c['law'] for c in main_log if c['kind'] == 'law' and c.get('domain') != 'reg'})
    rkinds = collections.Counter(c['kind'] for c in main_log if c.get('domain') == 'reg')
    r_laws = len({c['law'] for c in main_log if c['kind'] == 'law' and c.get('domain') == 'reg'})
    rbases = collections.Counter(v['basis'] for b in regs_built for v in b['versions'])
    r_versions = sum(len(b['versions']) for b in regs_built)
    r_pit = sum(1 for b in regs_built for v in b['versions'] if v['kind'] == 'pit')
    r_cur = sum(1 for b in regs_built for v in b['versions'] if v['kind'] == 'current')
    r_ends = sum(1 for b in regs_built if b['end'])
    r_ends_index = sum(1 for b in regs_built if b['end'] and b['end']['basis'].startswith('the index'))
    r_ends_page = sum(1 for b in regs_built if b['end'] and b['end']['basis'].startswith('the last version'))
    r_ends_law = sum(1 for b in regs_built if b['end'] and b['end']['law'])
    r_ends_after = r_ends - r_ends_index - r_ends_page
    r_gaps = sum(1 for b in regs_built for v in b['versions'] for n in v['notes'] if n.startswith('gap'))
    r_overlaps = sum(1 for b in regs_built for v in b['versions'] for n in v['notes'] if n.startswith('overlap'))
    reg_excluded = sum(1 for x in regs_left) + sum(len(x['excluded']) for x in regs_built + regs_left)
    reg_laws_kinds = collections.Counter(law_kind(c['law']) for c in main_log if c['kind'] == 'law'
                                         and c.get('domain') == 'reg')
    reg = dict(regs=len(regs_built), r_versions=r_versions, r_pit=r_pit, r_cur=r_cur, r_first=first_regs,
               reg_retrieved=reg_retrieved, r_law_commits=rkinds['law'], r_laws=r_laws, r_start=rkinds['start'],
               r_candidates=rkinds['candidates'], r_unexplained=rkinds['unexplained'], r_editorial=rkinds['editorial'],
               r_nochange=rkinds['nochange'], r_removed=rkinds['removed'], r_ends=r_ends, r_ends_index=r_ends_index,
               r_retrieval=rbases['retrieval day'], r_filed=rbases['filed'], r_gaps=r_gaps, r_overlaps=r_overlaps, r_left=len(regs_left),
               r_excluded=reg_excluded, r_by_reg=reg_laws_kinds['Regulation'],
               r_by_act=reg_laws_kinds['Act of the House of Assembly'] + reg_laws_kinds['Revised Statutes'],
               r_why_citation=rwhy['citation'], r_why_date=rwhy['date only'],
               r_why_two=rwhy['the note gains citations of two or more laws'],
               r_why_cand=rwhy['no new citation in its note'],
               r_why_none=rwhy['no new citation in its note or anywhere in the version'],
               r_why_edit=rwhy['letters and digits unchanged (spacing, punctuation or quotes only)'],
               r_why_removed=rwhy['not in the current consolidation'], reg_publisher=REG_PUBLISHER[0],
               r_folders=sum(1 for b in regs_built if b.get('folder_note')), r_ends_page=r_ends_page,
               r_ends_law=r_ends_law, r_ends_after=r_ends_after)
    bases = collections.Counter(v['basis'] for b in built for v in b['versions'])
    notes = collections.Counter()
    for b in built:
        for v in b['versions']:
            for n in v['notes']:
                key = n.split(':')[0] if n.startswith(('gap', 'overlap')) else (
                    'page and index disagree on the first day' if 'the index' in n and n.startswith('the page gives')
                    else 'Royal Assent dating (publisher\'s note)' if 'Royal Assent' in n
                    else 'no first day given (retrieval day)' if n.startswith('the site gives no first day')
                    else 'listed out of order in the index' if 'out of order' in n else 'other')
                notes[key] += 1
    ends_law = sum(1 for b in built if b['end'] and b['end']['law'])
    ends_pub = sum(1 for b in built if b['end'] and not b['end']['law'])
    n_versions = sum(len(b['versions']) for b in built)
    n_pit = sum(1 for b in built for v in b['versions'] if v['kind'] == 'pit')
    n_cur = sum(1 for b in built for v in b['versions'] if v['kind'] == 'current')
    n_files = sum(len(b['versions'][-1]['page']['units']) for b in built if not b['end'])
    ver = json.loads((HERE / 'verification.json').read_text(encoding='utf-8')) if (HERE / 'verification.json').exists() else {}
    man = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    n_pages = len({u for a in man['acts'] for u in [a['current']] + [v['url'] for v in a['pit']] if u})
    rman = json.loads((HERE / 'regmanifest.json').read_text(encoding='utf-8')) if (HERE / 'regmanifest.json').exists()         else {'acts': []}
    reg['r_pages'] = len({u for a in rman['acts'] for u in [a['current']] + [v['url'] for v in a['pit']] if u})
    reg['r_index'] = len(rman['acts'])
    reg['rules_pages'] = write_source_sums(repo, man, rman)
    blob = subprocess.run(['git', 'hash-object', str(HERE / 'build.py')], capture_output=True, text=True).stdout.strip()
    pyv = sys.version.split()[0]
    gitv = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip()
    try:
        import lxml.etree
        lxmlv = '.'.join(str(x) for x in lxml.etree.LXML_VERSION[:3])
    except Exception:   # noqa: BLE001
        lxmlv = '?'
    chk = ver.get('check')
    reb = ver.get('rebuild')
    suite = ver.get('suite')
    check_line = (f'{chk["versions"]} versions of {chk["acts"]} acts and regulations checked: {chk["file_mismatches"]} file mismatches; '
                  f'{chk["letter_mismatches"]} letter mismatches over {chk["letters"]:,} letters and digits'
                  if chk else 'not yet run')
    rebuild_line = (f'identical: the last law commit `{reb["history_tip"]}` and all {reb["tags"]} tags had the same '
                    f'hashes in both builds' if reb and reb.get('identical') else 'not yet run' if not reb else
                    'NOT identical')
    suite_lines = '\n'.join(f'   - {"pass" if r["ok"] else "FAIL"}: {r["name"]}: {r["detail"]}' for r in suite) if suite \
        else '   - not yet run'
    write(repo, 'README.md', README.format(
        acts=len(built), versions=n_versions, n_pit=n_pit, n_cur=n_cur, law_commits=kinds['law'], laws=n_laws,
        first=first, record_to=record_to, retrieved=retrieved, files=n_files, today=today,
        retrieval_dated=bases['retrieval day'], ends_law=ends_law, ends_pub=ends_pub, left=len(left),
        editorial=kinds['editorial'], candidates=kinds['candidates'], unexplained=kinds['unexplained'],
        gaps=notes['gap'], overlaps=notes['overlap'], publisher=PUBLISHER[0], **reg))
    write(repo, 'REPRODUCE.md', REPRODUCE.format(
        today=today, retrieved=retrieved, pages=n_pages, acts=len(built), versions=n_versions, n_pit=n_pit,
        n_cur=n_cur, blob=blob, pyv=pyv, lxmlv=lxmlv, gitv=gitv, check_line=check_line, rebuild_line=rebuild_line,
        suite_lines=suite_lines, commits=len(main_log) - len([c for c in main_log if c.get('domain') == 'reg']), law_commits=kinds['law'], laws=n_laws, start=kinds['start'],
        candidates=kinds['candidates'], unexplained=kinds['unexplained'], editorial=kinds['editorial'],
        nochange=kinds['nochange'], removed=kinds['removed'], tags=len(tags), ends_law=ends_law, ends_pub=ends_pub,
        why_citation=why['citation'], why_date=why['date only'],
        why_two=why['the note gains citations of two or more laws'],
        why_cand=why['no new citation in its note'],
        why_none=why['no new citation in its note or anywhere in the version'],
        why_edit=why['letters and digits unchanged (spacing, punctuation or quotes only)'],
        why_repeal=why['repeal'], why_removed=why['not in the current consolidation'],
        basis_page=bases['page'], basis_index=bases['index'], basis_after=bases['day after the previous version ends'],
        basis_retrieval=bases['retrieval day'], gaps=notes['gap'], overlaps=notes['overlap'],
        disagree=notes['page and index disagree on the first day'], assent=notes['Royal Assent dating (publisher\'s note)'],
        out_of_order=notes['listed out of order in the index'], excluded=len(excluded), left=len(left),
        clamped=sum(1 for c in log if c['clamped']), future=sum(1 for c in log if c['branch'] == 'future'),
        all_commits=len(main_log), **reg))


README = """# Newfoundland and Labrador statutes and regulations, change by change

The consolidated statutes and regulations of Newfoundland and Labrador as a git repository: {acts} acts and {regs}
regulations, each a folder, each section a file. Every amending law is one commit, credited to that law and dated the
day the version it produced took effect, at noon in St. John's. The text is the consolidated text published by the
House of Assembly (<https://www.assembly.nl.ca/legislation/sr/>), in each of its point-in-time versions and the current
version: for the statutes {versions} versions ({n_pit} point-in-time, {n_cur} current) from {first} to {record_to},
retrieved on {retrieved}; for the regulations, in [`regulations/`](regulations), {r_versions} versions ({r_pit}
point-in-time, {r_cur} current) from {r_first}, retrieved on {reg_retrieved}.

**This is not an official version.** Point-in-time copies of legislation are not official copies (Statutes Act,
RSNL1990 cS-26, s. 11(2)); see [Licence](#licence).

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did s. 2 of the Access to Information and Protection of Privacy Act, 2015 say on 1 January 2020? | `python tools/in_force_on.py 2020-01-01 SNL2015-cA-1.2 2` |
| The whole Highway Traffic Act as it stood on 1 July 2015 | `python tools/in_force_on.py 2015-07-01 RSNL1990-cH-3` |
| Which law wrote each line of a section? | `git blame RSNL1990-cH-3/s2.md` |
| Every change to a section, with the law and the day | `git log --follow RSNL1990-cH-3/s2.md` |
| Everything one law changed, in every act | `git log --author="SNL2017 c10"` (or the tag `law/snl2017-c10`) |
| What changed in an act on one day | `git show --stat RSNL1990-cH-3/2019-04-02` |
| A regulation's section on a day | `python tools/in_force_on.py 2020-01-01 regulations/NLR-45-15 2` |
| Everything one law changed in the regulations | `git log --author="NLR 53/23" -- regulations/` (or the tags `regulations/law/nlr-53-23/<day>`) |
| Which regulations are in the record | [`data/regulations.csv`](data/regulations.csv) |
| Which acts and versions are in the record, and from when | [`data/acts.csv`](data/acts.csv), [`data/versions.csv`](data/versions.csv) |
| The amending laws with their days | [`data/laws.csv`](data/laws.csv) |
| Every change not credited to exactly one law by its citation | [`data/labelled.csv`](data/labelled.csv) |
| Every day chosen with a caveat (gaps, conflicting dates) | `git log --grep="^Date-note:"` |
| What the record leaves out, and why | [`data/left-out.csv`](data/left-out.csv) |

`git log -p --word-diff <file>` shows the words each law changed. With raw git and a date, give a full time:
`--before="2020-01-01T23:59:59-03:30"`; `tools/in_force_on.py` reads the `In-force:` trailer instead, so it does not
depend on the reader's time zone.

## The dates, and which one we use

**Each commit is dated the day the version it produced applies from**, as the House of Assembly gives it: each
point-in-time page heads itself "POINT IN TIME / June 27, 2012 to December 9, 2013", and the index of past versions
labels it "From June 27, 2012 to December 9, 2013". Commits are at noon in St. John's (UTC-3:30, or UTC-2:30 in
daylight time). The `In-force-basis:` trailer says where each day came from:

- `page`: the version's own page;
- `index`: the index of past versions, where the page gives no date, or a range that cannot be right (a first day
  after the last), or, for the current version, the index's "PIT - From <day>";
- `day after the previous version ends`: for a current version the index gives no first day for;
- `retrieval day`: {retrieval_dated} acts have one version only and no first day anywhere on the site; they are dated
  {retrieved}, the day the text was retrieved and was in force. It may have applied for years before. A `Date-note:`
  says so on each.

The site's dates are written by hand, and some contradict each other. Every contradiction is written into the commit
as a `Date-note:`, never silently resolved: where the page and the index disagree on the first day (the page is used);
{gaps} gaps, where the site has no version for some days between two versions, so the changes of the later version
may have been made on any day in the gap; {overlaps} overlaps, where a version is said to end after the next begins;
and the versions the publisher itself dates by Royal Assent rather than commencement (it says so on the page). Where
two pages give the same first day, one is kept and the other is listed in `data/left-out.csv`.

An act that is no longer in the consolidation leaves it on the day its last version ends (+1), or on the day its last
page says "Repealed on <day>". {ends_law} such pages name the repealing act, which is then the commit's author; the
other {ends_pub} removals are the publisher's.

## The regulations

The consolidated regulations are in [`regulations/`](regulations), one folder per regulation named by its number:
`regulations/NLR-45-15/` (Newfoundland and Labrador Regulation 45/15, from 2002), `regulations/NfldReg-11-98/`
(Newfoundland Regulation 11/98, before 2002), `regulations/CNLR-1151-96/` (the Consolidated Newfoundland and Labrador
Regulations of 1996); {r_folders} have no number on the site, or share one, and are named with the site's file name.
Their pages are printed like the statutes' (a history note under every section), so they are read, dated and
credited by the same rules. Their history notes cite amending regulations (`53/23 s2` is `NLR 53/23`) and acts
(`2024 c23 s39`): {r_law_commits} commits by {r_laws} laws ({r_by_reg} commits by regulations, {r_by_act} by acts).
On a day when one law changed statutes and regulations, the statutes' commit comes first and the regulations' has its
own commit, with its own tags (`regulations/law/<law>`), so the statutes' history is the same as without the
regulations. The publisher's commits on the regulations are by `{reg_publisher}`.

{r_ends} regulations are no longer in the consolidation. Each leaves it on the day its last page says it was repealed
("Repealed on July 17, 2015 by 45/15", "[Rep. on Mar. 28/13 by 39/13]": {r_ends_page} regulations, {r_ends_law} naming
the repealing law, which is then the commit's author), else on the day the index of past versions gives ("[Rep. on
Jul. 17/15]", {r_ends_index}; it names no law), else on the day after its last version ends ({r_ends_after}). The
{r_removed} removals with no law named are the publisher's. {r_filed} regulations have one version only and no first
day on the site, but were never amended (no Amended list, and their history notes cite no other law) and print the
day they were filed ("(Filed October 25, 2004)"): they are dated by that day, with the trailers
`In-force-basis: <folder>: filed` and `Date-source: filed` (a regulation may come into force after it is filed, as its
own text then says). {r_retrieval} other regulations have one version only and no first day on the site, and are
dated {reg_retrieved}, the day they were retrieved.

The Rules of the Supreme Court, 1986 (`regulations/id-rc86rules/`, SNL1986 c42 Schedule D) are printed as a table of
contents linking one page per rule: the text is those pages joined in the table's order, as one current version
(`front.md` the table's head, `rule-01.md` each rule's heading, `s1.01.md`, `s4a.01.md`, `sf37a.02.md` its sections);
each page is listed as a `Consolidated:` trailer. The forms (PDF files) are not included.

## How it is checked

`tools/check_repo.py` rebuilds every version of every act from git, at the tag `<folder>/<day>`, and compares it with
the page it came from: file by file with the text the build's reader makes of the page, and letter by letter (all the
letters and digits of the page's law text, read by `tools/check_letters.py`, a separate reader that shares no code with
the build) with all the letters and digits of the files. Then: the shared corpus checks, the dates (no merge, no date
going backwards, none in the future, noon on every commit), the on-date tool, and a second build from the same data
compared hash for hash. The results are in [`REPRODUCE.md`](REPRODUCE.md).

## What "author" means

The author of a commit is the law that made the change, in the publisher's own citation style: `SNL2017 c10`
(Statutes of Newfoundland and Labrador 2017, chapter 10), `SNL2015 cA-1.2` (a statute with its own chapter, which
amends others in its consequential sections), `RSNL1990 cH-3` (the Revised Statutes of 1990), `SNL1971 No14`
(statutes before 1977 were numbered), a regulation (`NLR 10/18`, `CNLR 1139/96`, `Nfld. Reg. 64/97`) or an order in
council (`OC94-204`), at an address ending `@assembly-nl.invalid`, which cannot exist, so `git blame` names the law. The
committer is always `Lexifina`.

The evidence is the consolidation's own history notes: under each section the page lists the provisions that enacted
and amended it ("2015 cA-1.2 s4; 2017 c10 s3"). A changed section is credited to the law whose citation is new in its
note in the version from that day. A citation of the act itself is not an amendment. Everything else is labelled:

- a section that changed with no new citation in its note, in a version where exactly one law is new (in the notes or
  in the act's "Amended:" list), is credited to that law by date only (`Evidence: date only`);
- where a note gains two or more laws, or a section changed with no new citation in a version where two or more laws
  are new, the change is committed by the publisher, `{publisher}`, "credited to neither", with each law in a
  `Candidate:` trailer ({candidates} commits);
- a change no new citation explains is the publisher's, "no amending law cited" ({unexplained} commits);
- a change in spacing, punctuation, quotes or case only (the letters and digits are the same) is the publisher's
  reformatting, "spacing and punctuation only" ({editorial} commits), never credited to a law;
- the first version of each act is the act as enacted and amended up to then, credited to the publisher.

Blame credits whole paragraphs. A paragraph rewritten by one law and then touched by another is credited to the later.

## Files

Each act is a folder named by its chapter citation (each regulation one in `regulations/`, above): `SNL2015-cA-1.2/`, `RSNL1990-cH-3/` (two acts on the site share one
citation and carry the site's file name as well; two have none on any page and are named `id-<file name>`). Each section
is one file, `s2.md`, `s5.1.md`, its paragraphs one per line (a blank line between): the Part and Division headings
that begin with it, its sidenote, its text, and last its history note. `front.md` holds the chapter citation, title,
long title, day of assent and enacting words; `preamble.md` a preamble; `schedule-a.md`, `form-1.md` schedules and
forms. Where a schedule holds a text with its own sections (an agreement), the section files repeat as `s1-2.md`.

## What it does not show

- Versions the site does not publish. Most acts' first version is from 2006 or later; the record of each act starts
  there, and of {retrieval_dated} acts only at {retrieved}.
- Regulations before the site's first version of each: most point-in-time versions of regulations start after 2006,
  {r_filed} at their filing day, and {r_retrieval} only at {reg_retrieved}. {r_left} regulations listed on the site have no usable
  text (in `data/left-out.csv`). Amending regulations are not separate folders: their changes are in the regulations
  they amend.
- The day each provision commenced, where an amending act commenced in parts: the site makes one version per
  consolidation, not per commencement.
- Which of two laws of the same day made a change (above).
- {left} acts on the site with no usable text (listed in `data/left-out.csv`), and annual Loan, Supply and similar
  acts the site does not consolidate.
- French: Newfoundland and Labrador legislates in English only.

## How it is built

`tools/build.py` reads the retrieved pages of the House of Assembly's site and writes this repository from scratch; the
same data gives the same hashes. Every step, the software, the decisions made by hand and what an AI model did are in
[`REPRODUCE.md`](REPRODUCE.md). [`corpus.json`](corpus.json) describes the corpus for tools that read several of
Lexifina's law repositories. `data/source-sha256.tsv` lists every source page with its sha256.

## Licence

The statutes are published by the House of Assembly of Newfoundland and Labrador (King's Printer). Its Copyright &
Privacy Statement: "the Speaker of the House of Assembly grants permission to excerpt and cite the publications or web
content ... for use in educational institutions and for other purposes, such as private study, academic papers,
research, criticism, review or journalistic fair comment. Copies, reproductions or rebroadcast of publications, web
content and audio/visual proceedings should acknowledge the source. Use of publications, web content, or portions
thereof, for commercial purpose or financial gain requires the express written approval of the Speaker of the House of
Assembly." Source: House of Assembly of Newfoundland and Labrador, <https://www.assembly.nl.ca/legislation/sr/>.
This repository is kept for research and is not used commercially.

**This is not an official version.** Under s. 11 of the Statutes Act, RSNL1990 cS-26, a copy of consolidated
legislation accessed from the House of Assembly's website is an official copy, but "a copy of point in time
legislation accessed from this website is not an official copy". This repository is neither: consult the House of
Assembly's site or the King's Printer. The history, commit messages, tables and tools are released under
[CC0 1.0](LICENSE).
"""

REPRODUCE = """# How this history was made, and how to make it again

This file records exactly how the repository was built: the sources, the commands, the software, the decisions made
by hand, and what an AI model did and did not do. The build writes it, so it describes the build that made this
commit.

## Short version

```sh
# in Lexifina's repository, scripts/canada-newfoundland-and-labrador-law/ (the same files are in tools/ here),
# with the retrieved pages in place: each where tools/cachelib.py's cache_path(url) puts it, each matching its sha256
# in data/source-sha256.tsv
pip install lxml
python -X utf8 manifest.py                          # manifest.json from the two statute indexes
python -X utf8 manifest.py --regulations            # regmanifest.json from the two regulation indexes
python -X utf8 build.py --today {today} --repo ../../../canada-newfoundland-and-labrador-law-build
python -X utf8 check_repo.py --repo ../../../canada-newfoundland-and-labrador-law-build --today {today}
python -X utf8 checks.py --repo ../../../canada-newfoundland-and-labrador-law-build --today {today}
python -X utf8 build.py --today {today} --verify-rebuild --repo ../../../canada-newfoundland-and-labrador-law-build
```

`build.py` needs Python 3.12 or later with `lxml`, and git. It reads only the retrieved pages, and writes the
repository into `--repo`, which must be empty or a git repository without a remote (it is emptied first). With the
same data and the same `--today` it gives the same commits with the same hashes. The retrieval day of the statutes
and that of the regulations are the newest modification day (UTC) of the files each reads, so copies of the pages
must keep their times. `data/source-sha256.tsv` lists every page and index it read with the sha256 of the HTML (and
`data/versions.csv` each version's page); the site may change its pages, and then the hashes change too.
Outside Lexifina's repository, set `GITLAW_PUBLIC_REBUILD=1` (the build otherwise runs a last check of the files it
publishes, which is kept with Lexifina's scripts).

## Sources

All legal text and all information about versions and amending laws come from the **House of Assembly of
Newfoundland and Labrador** (www.assembly.nl.ca). Nothing was typed in, summarised or rewritten.

| What | Where |
| --- | --- |
| The current consolidated statutes | `https://www.assembly.nl.ca/legislation/sr/titleindex.htm`, each `.../sr/statutes/<file>.htm` |
| The point-in-time versions of each statute, "PIT - From A to B", and the current version's "PIT - From A" | `https://www.assembly.nl.ca/legislation/sr/pointintime/pittitleindex.htm`, each `.../pointintime/pitstatutes/pit<file>.<yyyymmdd>.htm` |
| The current consolidated regulations | `https://www.assembly.nl.ca/legislation/sr/regulations/titleindex2.htm`, each `.../sr/regulations/rc<file>.htm` |
| The point-in-time versions of each regulation, and "[Rep. on <day>]" for one no longer in force | `https://www.assembly.nl.ca/legislation/sr/pointintime/pittitleindex2.htm`, each `.../pointintime/pitregulations/pitrc<file>.<yyyymmdd>.htm` |

The statutes: {pages} pages, plus the two indexes, retrieved on {retrieved}. Pages the site answers with "this page
could not be found" are kept as not found. Every page declares windows-1252 and is decoded as such.

The regulations: {r_pages} pages of the {r_index} regulations in the two regulation indexes, plus the indexes, and the
{rules_pages} pages the table of contents of the Rules of the Supreme Court, 1986 links, retrieved on {reg_retrieved}
(the Copyright & Privacy Statement was read again that day, unchanged).

{acts} acts are in the record, with {versions} versions ({n_pit} point-in-time, {n_cur} current), and {regs}
regulations, with {r_versions} versions ({r_pit} point-in-time, {r_cur} current). {excluded} pages, acts or regulations
are left out, each with its reason, in `data/left-out.csv` ({left} whole acts, {r_left} whole regulations).

## The build, step by step

1. `manifest.py` read the two indexes into `manifest.json` (each act's current page, and its point-in-time versions
   with the index's label and the date in the file name). `manifest.py --regulations` did the same for the
   regulations, into `regmanifest.json`, with the index's
   "[Rep. on <day>]" for each regulation no longer in force.
2. `build.py` (git blob `{blob}`; the same file as `tools/build.py` here) read every page with `nlread.py` and
   `blocks.py` (and the regulations with `regread.py`, which adds their number, title and banner to `nlread.py`; a pool
   of 4 processes), chose each version's first day, compared consecutive versions file by file,
   credited each change and wrote this repository with one `git fast-import`.
3. `check_repo.py` compared every version in git with its page: {check_line}.
4. `checks.py` ran the shared checks on the build folder:
{suite_lines}
5. `build.py --verify-rebuild` built the repository again from the same pages into a temporary folder: {rebuild_line}.
6. The build was run once more to write these results into this file.

It ran with Python {pyv}, lxml {lxmlv} and {gitv} on Windows 11. No network is used by the build.

## How the text and the commits are derived

Everything below is done by `build.py`, deterministically.

- **Reading a page.** The pages are Word documents saved as HTML. `blocks.py` streams each page into blocks (a
  paragraph, heading or list item is a block; a table row is a block, its cells joined by ` | `), keeping each
  paragraph's class. Only white space is normalised (runs become one space) and soft hyphens removed; a character
  reference to 128-159 (Word writes `&#146;` for a right quote) is read as its windows-1252 character, as browsers show
  it. Left out: the site banner and links, the point-in-time range, a "Repealed on" or "Important to Note" line (kept
  as metadata), the act's "Amended:" list (kept as evidence), the Analysis (table of contents) and the "Back to Top"
  links.
- **Files.** `nlread.py` splits the text into units. A section ends at its history note (class `RSN`); the headings
  printed before a section's sidenote go with that section. Inside schedules only history notes and schedule headings
  split. Each unit is a file named by its section number (`s5.1.md`), `front.md`, `preamble.md`, or the schedule's or
  form's label (`schedule-a.md`); a second unit with the same name (an agreement with its own sections, in a schedule)
  is `s1-2.md`; a unit with no number is `other.md`. Paragraphs are separated by blank lines.
- **Folders.** The chapter citation the latest page prints (`SNL2015 CHAPTER A-1.2` is `SNL2015-cA-1.2/`).
- **First days.** For each point-in-time version: the first day the page gives, if it is not after its last day;
  otherwise the index's ({basis_page} versions from the page, {basis_index} from the index). The last day: whichever
  of the page, the index label and the file name agree (two of three), else the page's. Versions are ordered by first
  day; two with the same first day keep one. The current version starts on the index's "PIT - From" day, or the day
  after the last point-in-time version ends ({basis_after}), or, with neither, the retrieval day ({basis_retrieval},
  labelled). Labelled on the commits: {disagree} versions whose page and index disagree; {gaps} gaps; {overlaps}
  overlaps; {assent} versions dated by Royal Assent (the publisher's own note); {out_of_order} listed out of order.
- **Changes.** Consecutive versions are compared file by file.
- **Cause.** History note items are read as laws: `2017 c10 s3` is SNL2017 c10, `RSN1990 cA-2 s3` RSNL1990 cA-2,
  `1971 No14 s2` SNL1971 No14, `10/18 s2` NLR 10/18 (regulations from 2002; before then Nfld. Reg., or CNLR where the
  note says so), `OC94-204` an order in council. Citations of the act itself are not amendments. Tried in order:
  1. letters and digits unchanged: the publisher's reformatting, credited to no law ({why_edit} file changes);
  2. the file's note gains exactly one law: that law ({why_citation});
  3. the note gains two or more laws: credited to neither, publisher, `Candidate:` trailers ({why_two});
  4. no new citation, and exactly one law new in the version (notes and Amended list): that law, by date only,
     `Evidence: date only` ({why_date});
  5. no new citation, two or more laws new: credited to neither ({why_cand});
  6. nothing new anywhere: no law, publisher ({why_none}).
  An act no longer in the current consolidation leaves it: its files are removed by the repealing law its last page
  names ({ends_law} acts, {why_repeal} files) or by the publisher ({ends_pub} acts, {why_removed} files).
- **Commits.** {commits} on `main` before this documentation: one per amending law per day ({law_commits} commits by
  {laws} laws), covering every act it changed that day; {start} first versions; {candidates} with candidate laws;
  {unexplained} with no law cited; {editorial} reformatting; {nochange} versions with no change in the text;
  {removed} acts leaving the consolidation with no law named. {future} on a `future` branch. {clamped} before 1970.
  - Author: the law (`SNL2017 c10 <snl2017-c10@assembly-nl.invalid>`) or the publisher. Committer: `Lexifina`.
  - Dates: noon in St. John's, written into the code because the build machine has no time-zone database:
    UTC-3:30; daylight time UTC-2:30 from the second Sunday in March to the first Sunday in November since 2007, from
    the first Sunday in April to the last Sunday in October 1987-2006 (1988: double daylight time, UTC-1:30), from
    the last Sunday in April to the last Sunday in October before.
  - Trailers (one line per value): `Evidence:`, `Kind:`, `In-force:`, `In-force-basis:`, `Date-source:` (`filed`), `Date-note:`,
    `Candidate:`, `Affecting:` (`<folder> s. 2`), `Consolidated:` (the page the text came from).
  - Tags: each act's days (`RSNL1990-cH-3/2019-04-02`) and each law (`law/snl2017-c10`, or
    `law/snl2017-c10/<day>` for a law with several days); {tags} in all.
- **Regulations.** Read, dated and credited by the same rules, in `regulations/<number>/` (the number the page prints,
  "NEWFOUNDLAND AND LABRADOR REGULATION 45/15", is the folder `NLR-45-15` and the regulation's own citation, not an
  amendment). File changes: {r_why_edit} reformatting, {r_why_citation} by citation, {r_why_two} with two or more new
  citations, {r_why_date} by date only, {r_why_cand} with two or more laws new that day, {r_why_none} with nothing new;
  {r_why_removed} removed with regulations leaving the consolidation ({r_ends} regulations, {r_ends_index} on the
  index's "[Rep. on <day>]"). Commits: {r_law_commits} by {r_laws} laws, {r_start} first versions, {r_candidates} with
  candidate laws, {r_unexplained} with no law cited, {r_editorial} reformatting, {r_nochange} with no change,
  {r_removed} removals; {r_gaps} gaps and {r_overlaps} overlaps labelled. On each day the statutes' commits come first;
  a law's regulation commits carry the tags `regulations/law/<law>`. {all_commits} commits on `main` in all.
- **Documentation commit.** Dated {today}, the build day, with `In-force: {today}` and `Kind: documentation`.

## Decisions made by hand

1. **The page's own dates first.** The text committed is the page's, so its own range is preferred; the index is used
   where the page has none or an impossible one. Every disagreement is labelled, none silently resolved.
2. **No version is invented, merged or re-dated by guesswork.** A gap stays a gap (labelled); a version without a
   readable first day is left out (listed).
3. **The history note is the evidence for authorship**; the amending acts were not read.
4. **Reformatting is not an amendment.** Changes that keep every letter and digit are the publisher's.
5. **Statutes and regulations**, English only (the province legislates in English). The regulations were added after
   the statutes were built and checked; they change none of the statutes' commits (their messages, authors, dates and
   files were compared with the statutes-only build).
6. **Duplicate file names.** Three acts appear on the site under two file names with the same text; the one in the
   title index is kept.

## What an AI model did

This repository was built on {today} in **Claude Code**, Anthropic's coding tool, by the model **Claude Opus 5.5**
(model id `claude-opus-5-5`), working from briefs by the user at Lexifina; the regulations were added on 2026-10-29.

The model researched the source, wrote all the code and documentation (`manifest.py`, `cachelib.py`, `blocks.py`,
`nlread.py`, `regread.py`, `rules.py`, `build.py`, `reproduce.py`, `check_repo.py`, `check_letters.py`, `checks.py`,
`tools/in_force_on.py`, the README, this file) and ran the commands above. It did **not** write or edit any legal text, date or citation:
every word is copied by the scripts from the House of Assembly's pages, and no AI model is called when the history is
built or checked. The work was not reviewed line by line by a lawyer.
"""
