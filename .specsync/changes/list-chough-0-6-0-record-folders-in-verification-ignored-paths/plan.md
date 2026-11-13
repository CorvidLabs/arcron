---
change: list-chough-0-6-0-record-folders-in-verification-ignored-paths
artifact: plan
---

# Plan

## Approach

Add a `verification_ignored_paths` array to `.specsync/sdd.json` listing
`.chough/sessions/`, `.chough/archive/` and `.chough/bugs/` (SpecSync 6.1.2 and later
honour it). Also, following Leif's decisions (2026-11-13, 7:14 AM and 8:15 AM MST), narrow `.specsync/config.toml` `source_dirs` from `["."]` to the real source folders, and replace `"."` in `meaningful_paths` with the tracked top-level paths except `.chough/`. SpecSync refuses a `verification_ignored_paths` entry that overlaps either. No other key changes.

## Out of scope

Every other policy key, the Chough migration commit itself, and any spec or code change.

## Steps

1. Edit `.specsync/sdd.json` (the three entries and `meaningful_paths`) and `.specsync/config.toml` (`source_dirs`).
2. Run `specsync check`, `chough doctor` and the repository's own checks.
3. Human scope approval and review, then finalize and archive in this PR.

## Risks

Low. The entries only exclude Chough record folders from verification freshness; they
do not exclude source, tests or specs.

## Constraints consulted

Chough docs/cli.md Doctor table (`records_in_verification`, REQ-records-014).
