"""Prints a statute or regulation, or one provision of it, as it stood on a given day, from this repository's history.

    python tools/in_force_on.py 2020-01-01 SNL2015-cA-1.2 2        # section 2 on 1 January 2020
    python tools/in_force_on.py 2020-01-01 SNL2015-cA-1.2 5.1      # section 5.1
    python tools/in_force_on.py 2015-07-01 RSNL1990-cH-3           # the whole act
    python tools/in_force_on.py 2015-07-01 RSNL1990-cH-3 schedule-a
    python tools/in_force_on.py 2020-01-01 regulations/NLR-45-15 2  # a regulation (folders under regulations/)

Each commit carries the day its change took effect in an `In-force:` trailer, which is the source of truth (not the
commit date, which is noon in St. John's on that day). The text in force on a day is the act's last commit on or
before it. Changes published but not yet in force would be on the branch "future"; a date after today reads from
there. Standard library only.
"""
import datetime as dt
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def order(path: str) -> tuple:
    """The order of the act's files: front matter, preamble, sections by number, then the rest by name."""
    name = path.rsplit('/', 1)[1][:-3]
    if name == 'front':
        return (0, 0, (), name)
    if name in ('preamble', 'enacting'):
        return (0, 1 if name == 'preamble' else 2, (), name)
    m = re.fullmatch(r's(\d+)((?:\.\d+)*)(?:-(\d+))?', name)
    if m:
        return (1 if not m.group(3) else 2, int(m.group(1)), tuple(int(x) for x in m.group(2).split('.')[1:]), name)
    return (3, 0, (), name)


def file_for(key: str) -> str:
    """'2' -> s2.md, '5.1' -> s5.1.md; anything else is a file name ('schedule-a', 'front')."""
    if re.fullmatch(r'\d+(?:\.\d+)*', key):
        return f's{key}.md'
    return key if key.endswith('.md') else f'{key}.md'


def commit_on(day: str, act: str) -> tuple[str, str]:
    """(commit, its In-force day): the act's last commit in force on or before the day."""
    future = day > dt.date.today().isoformat() and git('branch', '--list', 'future').strip()
    branch = 'future' if future else 'main'
    out = git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', branch, '--', f'{act}/')
    for line in out.splitlines():
        h, _, d = line.partition('\t')
        if d.strip() and d.strip() <= day:
            return h, d.strip()
    return '', ''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    day, act = sys.argv[1], sys.argv[2].rstrip('/')
    dt.date.fromisoformat(day)
    commit, since = commit_on(day, act)
    if not commit:
        sys.exit(f'{act} is not in the record on {day} (the record of an act starts with the first version the House '
                 'of Assembly publishes; see data/acts.csv).')
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, f'{act}/').split('\n') if f.endswith('.md')]
    if not files:
        sys.exit(f'{act} was no longer in the consolidation on {day} (it left it on {since}; see git show {commit}).')
    if len(sys.argv) > 3:
        name = file_for(sys.argv[3])
        try:
            sys.stdout.write(git('show', f'{commit}:{act}/{name}'))
        except subprocess.CalledProcessError:
            sys.exit(f'{act}/{name} was not in the text on {day}.')
        return
    files = sorted(files, key=order)
    # One `git cat-file --batch` for all the files, not one `git show` per file.
    data = subprocess.run(['git', 'cat-file', '--batch'], input=''.join(f'{commit}:{f}\n' for f in files).encode('utf-8'),
                          capture_output=True, check=True).stdout
    pos = 0
    out = []
    for _ in files:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        out.append(data[nl + 1:nl + 1 + size].decode('utf-8'))
        pos = nl + 1 + size + 1
    sys.stdout.write('\n'.join(out))


if __name__ == '__main__':
    main()
