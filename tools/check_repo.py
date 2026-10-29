"""Checks the repository against every page it was built from (cache only, no network).

    python -X utf8 check_repo.py --today 2026-10-29 [--repo ...] [--no-record] [--only FOLDER,...]

For every act and every version in force on or before --today:
1. Files: the act's folder at the tag <folder>/<day> holds exactly the files the build's reader makes from that
   version's page (same names, same bytes), and the tagged commit's In-force trailer is that day.
2. Letters and digits (independent): check_letters.py reads the page with its own code (html.parser; no build code);
   the files' letters and digits, case-folded, must tile the page's law text exactly.
An act that left the consolidation must have no files at its last tag. Writes verification.json unless --no-record.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import check_letters  # noqa: E402
from fetchlib import cache_path  # noqa: E402


def files_at(repo: str, ref: str, folder: str) -> dict[str, str]:
    out = subprocess.run(['git', 'ls-tree', '-r', ref, f'{folder}/'], cwd=repo, capture_output=True).stdout.decode()
    blobs = {}
    for line in out.split('\n'):
        if line:
            info, f = line.split('\t', 1)
            blobs[f[len(folder) + 1:]] = info.split()[2]
    names = sorted(blobs)
    data = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, input=''.join(f'{blobs[f]}\n' for f in names).encode(),
                          capture_output=True).stdout
    res = {}
    pos = 0
    for f in names:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        res[f] = data[nl + 1:nl + 1 + size].decode('utf-8')
        pos = nl + 1 + size + 1
    return res


def check_act(args) -> dict:
    folder, versions, end_day, repo, today = args
    bad, letters, n = [], 0, 0
    for day, url in versions:
        if day > today:
            continue
        n += 1
        tag = f'{folder}/{day}'
        trailer = subprocess.run(['git', 'log', '-1', '--format=%(trailers:key=In-force,valueonly)', tag], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
        if trailer != day:
            bad.append(f'{tag}: In-force {trailer!r}')
        _, page = build.read_page(url)
        want = {f'{k}.md': u['text'] for k, u in page['units'].items()}
        got = files_at(repo, tag, folder)
        if want != got:
            miss = sorted(set(want) - set(got))[:3]
            extra = sorted(set(got) - set(want))[:3]
            diff = sorted(k for k in set(want) & set(got) if want[k] != got[k])[:3]
            bad.append(f'{tag}: files differ (missing {miss}, extra {extra}, changed {diff})')
        ok, detail = check_letters.tile(cache_path(url).read_bytes(), list(got.values()))
        if not ok:
            bad.append(f'{tag}: letters: {detail}')
        else:
            letters += int(detail)
    if end_day and end_day <= today:
        got = files_at(repo, f'{folder}/{end_day}', folder)
        if got:
            bad.append(f'{folder}/{end_day}: the act left the consolidation, but {len(got)} files remain')
    return {'folder': folder, 'versions': n, 'bad': bad, 'letters': letters}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(build.DEFAULT_REPO))
    ap.add_argument('--today', required=True)
    ap.add_argument('--only', default=None)
    ap.add_argument('--no-record', action='store_true')
    args = ap.parse_args()
    built, _, _ = build.build_all(args.today)
    jobs = [(b['folder'], [(v['start'], v['url']) for v in b['versions']], b['end']['day'] if b['end'] else None,
             args.repo, args.today) for b in built if not args.only or b['folder'] in args.only.split(',')]
    with cf.ProcessPoolExecutor(build.WORKERS) as ex:
        res = list(ex.map(check_act, jobs, chunksize=1))
    bad = [x for r in res for x in r['bad']]
    rec = {'acts': sum(1 for r in res if r['versions']), 'versions': sum(r['versions'] for r in res),
           'file_mismatches': sum(1 for x in bad if 'files differ' in x or 'In-force' in x or 'remain' in x),
           'letter_mismatches': sum(1 for x in bad if 'letters:' in x), 'letters': sum(r['letters'] for r in res),
           'checked_on': args.today}
    print(json.dumps(rec, indent=1))
    for x in bad[:60]:
        print('  ', x)
    if not args.no_record and not args.only:
        p = HERE / 'verification.json'
        ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        ver['check'] = rec
        p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
