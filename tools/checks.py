"""The shared checks on a built repository, recorded in verification.json ('suite') unless --no-record.

    python -X utf8 checks.py --repo <build folder> --today 2026-10-29 [--no-record]

1. scripts/global-law/check_corpus.py (corpus.json, REPRODUCE.md, In-force order, append-only against any pin).
2. scripts/test_corpus.py with tools/in_force_on.py (authors, trailers, tags, blame, log --follow, the on-date tool).
3. scripts/test_noon.py (every commit at 12:00 in its own offset).
4. No merge commits; In-force and commit times never go backwards on main; nothing after --today on main.
5. No carriage return in any commit message.
6. The on-date tool against the tags: for three provisions of the statutes and two of the regulations, on each day a version began and the day before, the tool
   prints the file as it is at the tag of the version in force that day (read from data/versions.csv).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')


def git(repo, *a):
    return run(['git', *a], cwd=repo).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--today', required=True)
    ap.add_argument('--no-record', action='store_true')
    args = ap.parse_args()
    repo = args.repo
    res = []

    def check(name, ok, detail):
        res.append({'name': name, 'ok': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name + ': ' + detail, flush=True)

    # check_corpus.py's checks, run in-process: its command line also looks up global-law's pin on GitHub, and this
    # corpus is not pinned (it has never been published), so the append-only check has nothing to compare with.
    sys.path.insert(0, str(SCRIPTS / 'global-law'))
    import check_corpus
    fatal, warn = check_corpus.problems(Path(repo).resolve(), args.today, None)
    check('check_corpus.py problems()', not fatal, f'{len(fatal)} problems, {len(warn)} warnings'
          + ''.join(f'; FAIL {x}' for x in fatal) + ''.join(f'; warning {x}' for x in warn))
    out = run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'test_corpus.py'), repo, 'tools/in_force_on.py'])
    lines = out.stdout.strip().splitlines()
    check('test_corpus.py', out.returncode == 0,
          f'{sum(1 for x in lines if x.startswith("PASS"))} of {sum(1 for x in lines if x.startswith(("PASS", "FAIL")))} '
          f'passed' + ''.join(f'; {x}' for x in lines if x.startswith('FAIL')))
    out = run([sys.executable, str(SCRIPTS / 'test_noon.py'), repo])
    check('test_noon.py', out.returncode == 0, out.stdout.strip().split('commits not at 12:00 local: ', 1)[-1][:120])
    merges = git(repo, 'rev-list', '--merges', '--count', '--all').strip()
    check('no merge commits', merges == '0', f'{merges} merges on all branches')
    rows = [x.split('\t') for x in git(repo, 'log', '--first-parent', '--reverse', '--format=%ct\t%(trailers:key=In-force,'
                                        'valueonly,separator=)', 'main').splitlines()]
    back_t = sum(1 for a, b in zip(rows, rows[1:]) if int(b[0]) < int(a[0]))
    back_d = sum(1 for a, b in zip(rows, rows[1:]) if b[1] < a[1])
    future = sum(1 for r in rows if r[1] > args.today)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    check('dates in order, none in the future', back_t == back_d == future == 0 and all(int(r[0]) <= now for r in rows),
          f'{len(rows)} commits on main; {back_d} In-force and {back_t} commit times going backwards; {future} after '
          f'{args.today}')
    msgs = subprocess.run(['git', 'log', '--all', '--format=%B%x00'], cwd=repo, capture_output=True).stdout
    check('no carriage returns in commit messages', b'\r' not in msgs, f'{msgs.count(b"\r")} CRs')
    versions = list(csv.DictReader(io.StringIO(git(repo, 'show', 'HEAD:data/versions.csv'))))
    samples = [('SNL2015-cA-1.2', 's2.md'), ('RSNL1990-cH-3', 's2.md'), ('RSNL1990-cL-2', 's1.md')]
    if any(v['folder'] == 'regulations/NLR-45-15' for v in versions):     # and two regulations
        samples += [('regulations/NLR-45-15', 's2.md'), ('regulations/NLR-11-07', 's2.md')]
    tried = bad = 0
    for folder, f in samples:
        starts = sorted(v['in_force'] for v in versions if v['folder'] == folder)
        days = sorted({d for s in starts for d in (s, (dt.date.fromisoformat(s) - dt.timedelta(days=1)).isoformat())})
        for day in days:
            inforce = [s for s in starts if s <= day]
            want = run(['git', 'show', f'{folder}/{inforce[-1]}:{folder}/{f}'], cwd=repo) if inforce else None
            got = run([sys.executable, '-X', 'utf8', 'tools/in_force_on.py', day, folder, f[:-3].lstrip('s')], cwd=repo)
            tried += 1
            if inforce and want.returncode == 0:
                ok = got.returncode == 0 and got.stdout == want.stdout
            else:
                ok = got.returncode != 0
            bad += not ok
    check('on-date tool against the tags', tried > 20 and bad == 0,
          f'{tried} queries on {len(samples)} provisions ({", ".join(a + "/" + b for a, b in samples)}), {bad} wrong')
    if not args.no_record:
        p = HERE / 'verification.json'
        ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        ver['suite'] = res
        p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    sys.exit(0 if all(r['ok'] for r in res) else 1)


if __name__ == '__main__':
    main()
