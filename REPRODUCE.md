# How this history was made, and how to make it again

This file records exactly how the repository was built: the sources, the commands, the software, the decisions made
by hand, and what an AI model did and did not do. The build writes it, so it describes the build that made this
commit.

## Short version

```sh
# in Lexifina's repository, scripts/canada-newfoundland-and-labrador-law/ (the same files are in tools/ here)
pip install lxml
python -X utf8 manifest.py --fetch                  # the two statute indexes and every page, into .cache/
python -X utf8 manifest.py --regulations --fetch    # the two regulation indexes and every page (regmanifest.json)
python -X utf8 build.py --today 2026-10-29 --repo ../../../canada-newfoundland-and-labrador-law-build
python -X utf8 check_repo.py --repo ../../../canada-newfoundland-and-labrador-law-build --today 2026-10-29
python -X utf8 checks.py --repo ../../../canada-newfoundland-and-labrador-law-build --today 2026-10-29
python -X utf8 build.py --today 2026-10-29 --verify-rebuild --repo ../../../canada-newfoundland-and-labrador-law-build
```

`build.py` needs Python 3.12 or later with `lxml`, and git. It reads only its cache, and writes the repository into
`--repo`, which must be empty or a git repository without a remote (it is emptied first). With the same data and the
same `--today` it gives the same commits with the same hashes. `data/versions.csv` lists every page it read with the
sha256 of the HTML; the site may change its pages, and then the hashes change too.

## Sources

All legal text and all information about versions and amending laws come from the **House of Assembly of
Newfoundland and Labrador** (www.assembly.nl.ca). Nothing was typed in, summarised or rewritten.

| What | Where |
| --- | --- |
| The current consolidated statutes | `https://www.assembly.nl.ca/legislation/sr/titleindex.htm`, each `.../sr/statutes/<file>.htm` |
| The point-in-time versions of each statute, "PIT - From A to B", and the current version's "PIT - From A" | `https://www.assembly.nl.ca/legislation/sr/pointintime/pittitleindex.htm`, each `.../pointintime/pitstatutes/pit<file>.<yyyymmdd>.htm` |
| The current consolidated regulations | `https://www.assembly.nl.ca/legislation/sr/regulations/titleindex2.htm`, each `.../sr/regulations/rc<file>.htm` |
| The point-in-time versions of each regulation, and "[Rep. on <day>]" for one no longer in force | `https://www.assembly.nl.ca/legislation/sr/pointintime/pittitleindex2.htm`, each `.../pointintime/pitregulations/pitrc<file>.<yyyymmdd>.htm` |

1642 pages in all, plus the two indexes, retrieved on 2026-10-28 by `manifest.py --fetch` (`fetchlib.py`): at most
four requests at a time, a request started at most every 0.25 seconds, gzip, user agent
`lexifina-watch/1.0 (+https://lexifina.com/watch; corpus@lexifina.com)`. The site's robots.txt (`User-agent: *`,
Pages the site answers with "this page could not be found" are cached as not found. Every page declares
windows-1252 and is decoded as such.

The regulations: 2611 pages of the 1835 regulations in the two regulation indexes, plus the indexes,
retrieved on 2026-10-29 the same way (the robots.txt and the Copyright & Privacy Statement were read again that
day, unchanged).

550 acts are in the record, with 1629 versions (1214 point-in-time, 415 current), and 1828
regulations, with 2596 versions (909 point-in-time, 1687 current). 40 pages, acts or regulations
are left out, each with its reason, in `data/left-out.csv` (9 whole acts, 6 whole regulations).

## The build, step by step

1. `manifest.py` read the two indexes into `manifest.json` (each act's current page, and its point-in-time versions
   with the index's label and the date in the file name); `manifest.py --fetch` fetched every page into the cache.
   `manifest.py --regulations` did the same for the regulations, into `regmanifest.json`, with the index's
   "[Rep. on <day>]" for each regulation no longer in force.
2. `build.py` (git blob `96c5d4801046861d743e5c9dd69ac3def1667080`; the same file as `tools/build.py` here) read every page with `nlread.py` and
   `blocks.py` (and the regulations with `regread.py`, which adds their number, title and banner to `nlread.py`; a pool
   of 4 processes), chose each version's first day, compared consecutive versions file by file,
   credited each change and wrote this repository with one `git fast-import`.
3. `check_repo.py` compared every version in git with its page: 4226 versions of 2379 acts and regulations checked: 0 file mismatches; 13 letter mismatches over 124,386,233 letters and digits.
4. `checks.py` ran the shared checks on the build folder:
   - pass: check_corpus.py problems(): 0 problems, 0 warnings
   - pass: test_corpus.py: 11 of 11 passed
   - pass: test_noon.py: 0 [] | on clock-change Sundays (check offset): ['c9fb969 2024-03-31 -02:30']
   - pass: no merge commits: 0 merges on all branches
   - pass: dates in order, none in the future: 5030 commits on main; 0 In-force and 0 commit times going backwards; 0 after 2026-10-29
   - pass: no carriage returns in commit messages: 0 CRs
   - pass: on-date tool against the tags: 138 queries on 5 provisions (SNL2015-cA-1.2/s2.md, RSNL1990-cH-3/s2.md, RSNL1990-cL-2/s1.md, regulations/NLR-45-15/s2.md, regulations/NLR-11-07/s2.md), 0 wrong
5. `build.py --verify-rebuild` built the repository again from the same cache into a temporary folder: identical: the last law commit `16e748620e51190e6853dc0ad8eae0d051704cb3` and all 2385 tags had the same hashes in both builds.
6. The build was run once more to write these results into this file.

It ran with Python 3.12.10, lxml 6.1.3 and git version 2.46.0.windows.1 on Windows 11. No network is used by the build.

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
  otherwise the index's (1195 versions from the page, 103 from the index). The last day: whichever
  of the page, the index label and the file name agree (two of three), else the page's. Versions are ordered by first
  day; two with the same first day keep one. The current version starts on the index's "PIT - From" day, or the day
  after the last point-in-time version ends (259), or, with neither, the retrieval day (72,
  labelled). Labelled on the commits: 37 versions whose page and index disagree; 14 gaps; 9
  overlaps; 23 versions dated by Royal Assent (the publisher's own note); 6 listed out of order.
- **Changes.** Consecutive versions are compared file by file.
- **Cause.** History note items are read as laws: `2017 c10 s3` is SNL2017 c10, `RSN1990 cA-2 s3` RSNL1990 cA-2,
  `1971 No14 s2` SNL1971 No14, `10/18 s2` NLR 10/18 (regulations from 2002; before then Nfld. Reg., or CNLR where the
  note says so), `OC94-204` an order in council. Citations of the act itself are not amendments. Tried in order:
  1. letters and digits unchanged: the publisher's reformatting, credited to no law (3216 file changes);
  2. the file's note gains exactly one law: that law (5514);
  3. the note gains two or more laws: credited to neither, publisher, `Candidate:` trailers (92);
  4. no new citation, and exactly one law new in the version (notes and Amended list): that law, by date only,
     `Evidence: date only` (150);
  5. no new citation, two or more laws new: credited to neither (0);
  6. nothing new anywhere: no law, publisher (13).
  An act no longer in the current consolidation leaves it: its files are removed by the repealing law its last page
  names (68 acts, 2777 files) or by the publisher (67 acts, 2916 files).
- **Commits.** 1854 on `main` before this documentation: one per amending law per day (621 commits by
  528 laws), covering every act it changed that day; 550 first versions; 80 with candidate laws;
  9 with no law cited; 507 reformatting; 20 versions with no change in the text;
  67 acts leaving the consolidation with no law named. 0 on a `future` branch. 0 before 1970.
  - Author: the law (`SNL2017 c10 <snl2017-c10@assembly-nl.invalid>`) or the publisher. Committer: `Lexifina`.
  - Dates: noon in St. John's, written into the code because the build machine has no time-zone database:
    UTC-3:30; daylight time UTC-2:30 from the second Sunday in March to the first Sunday in November since 2007, from
    the first Sunday in April to the last Sunday in October 1987-2006 (1988: double daylight time, UTC-1:30), from
    the last Sunday in April to the last Sunday in October before.
  - Trailers (one line per value): `Evidence:`, `Kind:`, `In-force:`, `In-force-basis:`, `Date-note:`,
    `Candidate:`, `Affecting:` (`<folder> s. 2`), `Consolidated:` (the page the text came from).
  - Tags: each act's days (`RSNL1990-cH-3/2019-04-02`) and each law (`law/snl2017-c10`, or
    `law/snl2017-c10/<day>` for a law with several days); 5914 in all.
- **Regulations.** Read, dated and credited by the same rules, in `regulations/<number>/` (the number the page prints,
  "NEWFOUNDLAND AND LABRADOR REGULATION 45/15", is the folder `NLR-45-15` and the regulation's own citation, not an
  amendment). File changes: 1403 reformatting, 3199 by citation, 56 with two or more new
  citations, 140 by date only, 0 with two or more laws new that day, 25 with nothing new;
  2367 removed with regulations leaving the consolidation (140 regulations, 73 on the
  index's "[Rep. on <day>]"). Commits: 793 by 754 laws, 1828 first versions, 119 with
  candidate laws, 5 with no law cited, 310 reformatting, 18 with no change,
  100 removals; 10 gaps and 25 overlaps labelled. On each day the statutes' commits come first;
  a law's regulation commits carry the tags `regulations/law/<law>`. 5027 commits on `main` in all.
- **Documentation commit.** Dated 2026-10-29, the build day, with `In-force: 2026-10-29` and `Kind: documentation`.

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

This repository was built on 2026-10-29 in **Claude Code**, Anthropic's coding tool, by the model **Claude Opus 5.5**
(model id `claude-opus-5-5`), working from briefs by the user at Lexifina; the regulations were added on 2026-10-29.

The model researched the source, wrote all the code and documentation (`manifest.py`, `fetchlib.py`, `blocks.py`,
`nlread.py`, `regread.py`, `build.py`, `reproduce.py`, `check_repo.py`, `check_letters.py`, `checks.py`, `tools/in_force_on.py`,
the README, this file) and ran the commands above. It did **not** write or edit any legal text, date or citation:
every word is copied by the scripts from the House of Assembly's pages, and no AI model is called when the history is
built or checked. The work was not reviewed line by line by a lawyer.
