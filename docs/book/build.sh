#!/usr/bin/env bash
# Build the Arcron Working Guide to PDF and EPUB from the single Markdown source.
# Requires: pandoc, and (for PDF) tectonic. Both installable via Homebrew:
#   brew install pandoc tectonic
#
# The outputs are gitignored on purpose (see .gitignore here): they are build
# products of arcron-working-guide.md, which is itself derived from `docs/`.
# Committing them would put a fourth, undateable copy of every figure in a tree
# that is otherwise entirely text.
set -euo pipefail
cd "$(dirname "$0")"
SRC="arcron-working-guide.md"
COMMON=(--toc --toc-depth=3 --top-level-division=part
        -V documentclass=report -V fontsize=11pt -V geometry=margin=1in
        -V colorlinks=true -V linkcolor=RoyalBlue -V urlcolor=RoyalBlue)

# EPUB keeps the Unicode math glyphs (the reader supplies the font).
pandoc "$SRC" -o arcron-working-guide.epub "${COMMON[@]}"

# The default PDF (Latin Modern) lacks a few math glyphs; swap them for ASCII,
# and wrap long code lines via pdf-header.tex.
python3 - "$SRC" > .guide.pdf.md <<'PY'
import sys
s = open(sys.argv[1], encoding='utf-8').read()
for a, b in (('≤','<='),('≥','>='),('≈','~'),('→','->')):
    s = s.replace(a, b)
sys.stdout.write(s)
PY
pandoc .guide.pdf.md -o arcron-working-guide.pdf --pdf-engine=tectonic \
  -H pdf-header.tex "${COMMON[@]}"
rm -f .guide.pdf.md
echo "Built: arcron-working-guide.pdf  arcron-working-guide.epub"
