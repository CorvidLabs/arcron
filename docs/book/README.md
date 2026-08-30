# Arcron: The Working Guide

A single, ordered book that turns this repository's documentation into one thing
a newcomer can read front to back and learn to *use* Arcron: the concept, the
console, integrating a contract, running a keeper, the security model, the
economics, and a full technical reference.

**[`arcron-working-guide.md`](arcron-working-guide.md)** is the whole book, and
the only thing checked in. It reads directly on GitHub.

## It is derived, and `docs/` wins

The guide restates the documents in [`docs/`](..); it does not replace them.
Where the guide and a doc disagree, **the doc is right and the guide has a bug**.
Its preface carries a chapter-by-chapter map of which document each part was
compiled from, and those are the copies that get corrected first.

That ordering is not a formality. The first draft of this guide was written on
2026-08-24, and by 2026-08-27 seven of its load-bearing figures had been
superseded by corrections landing in `docs/why.md` and `docs/first-upkeep.md`.
Those included a cost multiple, the creator crossover, the register-cost total,
and a claim about ALGO's trading history that was simply false. A book is the
easiest place in a repository for a number to go quietly stale.

**`tests/test_book.py` is what keeps it honest.** It runs in `fledge lanes run
ci` and pins the guide's load-bearing figures to the files that own them: the
contract constants to `smart_contracts/keeper/contract.py`, the suggested fee to
`js/src/upkeep.ts`, the economics to `docs/why.md`, the register cost to
`docs/first-upkeep.md`. Every intra-repo link is pinned too. If you change a
figure in a doc and not here, CI fails. Do not weaken that test to make a run
pass; fix the guide.

## Contents

Preface · **Part I** Understanding Arcron (Ch. 1–3) · **Part II** Using it as a
creator: first upkeep, integration, scheduling & fees (Ch. 4–6) · **Part III**
Running a keeper (Ch. 7–8) · **Part IV** Trust, security, economics (Ch. 9–11) ·
**Part V** Reference: API, box encoding, commands, governance, design decisions,
glossary, numbers (Appendices A–G).

## Building a PDF or EPUB

The generated `.pdf` and `.epub` are **not committed**: this repository is
otherwise entirely text, and a binary that restates a Markdown file is a fourth
copy of every number in it with no way to tell when it went stale. Build them
when you want them; they land in this directory and are gitignored.

```sh
brew install pandoc tectonic   # one-time; tectonic fetches TeX packages on first run
./build.sh
```

`build.sh` renders `arcron-working-guide.md` to both formats. The EPUB keeps the
Unicode math glyphs (the reader supplies the font); for the PDF the script swaps a
few glyphs the default LaTeX font lacks (`≤ ≥ ≈ →`) for ASCII and wraps long code
lines via `pdf-header.tex`. Nothing else is transformed.

If a reading copy should be downloadable without a toolchain, attach one to a
[release](../releases.md). A release artifact is dated and obviously a snapshot,
which is exactly what a rendered book is.
