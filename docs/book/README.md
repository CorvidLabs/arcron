# Arcron — The Working Guide

A single, ordered book that turns this repository's documentation into one thing
a newcomer can read front to back and learn to *use* Arcron: the concept, the
console, integrating a contract, running a keeper, the security model, the
economics, and a full technical reference.

- **[`arcron-working-guide.md`](arcron-working-guide.md)** — the source of truth,
  readable directly on GitHub.
- **`arcron-working-guide.pdf`** / **`arcron-working-guide.epub`** — generated
  reading copies (see the note on these below).

It is compiled *from* the docs in this repo; where the docs and the source
disagree, the guide follows the code and says so. Every load-bearing figure was
fact-checked against `smart_contracts/keeper/contract.py`, `docs/`, and the
scripts. Treat any number as a checkable claim, not gospel — the guide says as
much in its preface.

## Contents

Preface · **Part I** Understanding Arcron (Ch. 1–3) · **Part II** Using it as a
creator — first upkeep, integration, scheduling & fees (Ch. 4–6) · **Part III**
Running a keeper (Ch. 7–8) · **Part IV** Trust, security, economics (Ch. 9–11) ·
**Part V** Reference — API, box encoding, commands, governance, design decisions,
glossary, numbers (Appendices A–G).

## Building the PDF and EPUB

```sh
brew install pandoc tectonic   # one-time; tectonic fetches TeX packages on first run
./build.sh
```

`build.sh` renders `arcron-working-guide.md` to both formats. The EPUB keeps the
Unicode math glyphs (the reader supplies the font); for the PDF the script swaps a
few glyphs the default LaTeX font lacks (`≤ ≥ ≈ →`) for ASCII and wraps long code
lines via `pdf-header.tex`. Nothing else is transformed.

## A note on the committed PDF/EPUB

This repository is otherwise all text. The generated `.pdf` and `.epub` are
committed here only so a reader can download a finished copy without a toolchain —
they are **build products of `arcron-working-guide.md`** and can be regenerated at
any time with `./build.sh`. If the project would rather keep the tree text-only,
delete the two binaries and build them on demand (or attach them to a release);
the Markdown source is the thing that matters.
