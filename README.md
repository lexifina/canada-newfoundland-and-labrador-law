# Newfoundland and Labrador statutes and regulations, change by change

The consolidated statutes and regulations of Newfoundland and Labrador as a git repository: 550 acts and 1829
regulations, each a folder, each section a file. Every amending law is one commit, credited to that law and dated the
day the version it produced took effect, at noon in St. John's. The text is the consolidated text published by the
House of Assembly (<https://www.assembly.nl.ca/legislation/sr/>), in each of its point-in-time versions and the current
version: for the statutes 1629 versions (1214 point-in-time, 415 current) from 1970-06-18 to 2026-10-29,
retrieved on 2026-10-28; for the regulations, in [`regulations/`](regulations), 2597 versions (909
point-in-time, 1688 current) from 1996-01-17, retrieved on 2026-10-29.

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
- `retrieval day`: 72 acts have one version only and no first day anywhere on the site; they are dated
  2026-10-28, the day the text was retrieved and was in force. It may have applied for years before. A `Date-note:`
  says so on each.

The site's dates are written by hand, and some contradict each other. Every contradiction is written into the commit
as a `Date-note:`, never silently resolved: where the page and the index disagree on the first day (the page is used);
14 gaps, where the site has no version for some days between two versions, so the changes of the later version
may have been made on any day in the gap; 9 overlaps, where a version is said to end after the next begins;
and the versions the publisher itself dates by Royal Assent rather than commencement (it says so on the page). Where
two pages give the same first day, one is kept and the other is listed in `data/left-out.csv`.

An act that is no longer in the consolidation leaves it on the day its last version ends (+1), or on the day its last
page says "Repealed on <day>". 68 such pages name the repealing act, which is then the commit's author; the
other 67 removals are the publisher's.

## The regulations

The consolidated regulations are in [`regulations/`](regulations), one folder per regulation named by its number:
`regulations/NLR-45-15/` (Newfoundland and Labrador Regulation 45/15, from 2002), `regulations/NfldReg-11-98/`
(Newfoundland Regulation 11/98, before 2002), `regulations/CNLR-1151-96/` (the Consolidated Newfoundland and Labrador
Regulations of 1996); 7 have no number on the site, or share one, and are named with the site's file name.
Their pages are printed like the statutes' (a history note under every section), so they are read, dated and
credited by the same rules. Their history notes cite amending regulations (`53/23 s2` is `NLR 53/23`) and acts
(`2024 c23 s39`): 793 commits by 754 laws (731 commits by regulations, 62 by acts).
On a day when one law changed statutes and regulations, the statutes' commit comes first and the regulations' has its
own commit, with its own tags (`regulations/law/<law>`), so the statutes' history is the same as without the
regulations. The publisher's commits on the regulations are by `House of Assembly of Newfoundland and Labrador (consolidated regulations)`.

140 regulations are no longer in the consolidation. Each leaves it on the day its last page says it was repealed
("Repealed on July 17, 2015 by 45/15", "[Rep. on Mar. 28/13 by 39/13]": 61 regulations, 40 naming
the repealing law, which is then the commit's author), else on the day the index of past versions gives ("[Rep. on
Jul. 17/15]", 73; it names no law), else on the day after its last version ends (6). The
100 removals with no law named are the publisher's. 418 regulations have one version only and no first
day on the site, but were never amended (no Amended list, and their history notes cite no other law) and print the
day they were filed ("(Filed October 25, 2004)"): they are dated by that day, with the trailers
`In-force-basis: <folder>: filed` and `Date-source: filed` (a regulation may come into force after it is filed, as its
own text then says). 731 other regulations have one version only and no first day on the site, and are
dated 2026-10-29, the day they were retrieved.

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
  are new, the change is committed by the publisher, `House of Assembly of Newfoundland and Labrador (consolidated statutes)`, "credited to neither", with each law in a
  `Candidate:` trailer (80 commits);
- a change no new citation explains is the publisher's, "no amending law cited" (9 commits);
- a change in spacing, punctuation, quotes or case only (the letters and digits are the same) is the publisher's
  reformatting, "spacing and punctuation only" (507 commits), never credited to a law;
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
  there, and of 72 acts only at 2026-10-28.
- Regulations before the site's first version of each: most point-in-time versions of regulations start after 2006,
  418 at their filing day, and 731 only at 2026-10-29. 5 regulations listed on the site have no usable
  text (in `data/left-out.csv`). Amending regulations are not separate folders: their changes are in the regulations
  they amend.
- The day each provision commenced, where an amending act commenced in parts: the site makes one version per
  consolidation, not per commencement.
- Which of two laws of the same day made a change (above).
- 9 acts on the site with no usable text (listed in `data/left-out.csv`), and annual Loan, Supply and similar
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
