# Lesson bundle: list-chough-0-6-0-record-folders-in-verification-ignored-paths

Material for folding this change's lessons into each affected module's lesson file.
Synthesise from what actually happened below; do not restate the change description.

## What this change was

- **Title**: List Chough 0.6.0 record folders in verification_ignored_paths
- **Kind**: Operations
- **Paths**: .specsync/sdd.json, .specsync/config.toml
- **Acceptance**: .specsync/config.toml source_dirs names the real source folders instead of the whole repository; .specsync/sdd.json meaningful_paths names the tracked top-level paths instead of ".", without .chough/; verification_ignored_paths lists .chough/sessions/, .chough/archive/ and .chough/bugs/; chough doctor reports no records_in_verification finding; specsync check passes

## Evidence

- Verification commit: `8add4ab9e6ffda40e77c245772ccdcc3298296da`
- Base commit: `c44e1bc7733c5454c5b2a5e40b1dc8d3275f09e5`
- Verified by: `specsync check (no spec in scope)`

## From the change's context.md

# Context

Chough 0.6.0 adoption in arcron (2026-11-12). After `chough migrate`, `chough doctor`
and the migration receipt report `records_in_verification`: `.specsync/sdd.json` has no
`verification_ignored_paths` entry for Chough's record folders, so a Chough record
written after `specsync change check --commit` would stale the recorded verification.
Chough never writes this policy itself; its docs say adding the entries is an ordinary
reviewed change to `.specsync/sdd.json`. Leif's adoption instructions name this
follow-up as a small SpecSync change. `.specsync/sdd.json` is a meaningful path here,
so the edit needs its own native lifecycle.

## From the change's testing.md

# Testing

- `specsync check` passes with the edited policy.
- `chough doctor` reports no `records_in_verification` finding.

## Where these lessons go

This change declared no affected specs, so there is no module context to fold into.
