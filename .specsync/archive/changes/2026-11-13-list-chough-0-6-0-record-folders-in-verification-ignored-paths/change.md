---
id: list-chough-0-6-0-record-folders-in-verification-ignored-paths
state: archived
type: operations
base_commit: c44e1bc7733c5454c5b2a5e40b1dc8d3275f09e5
---

# List Chough 0.6.0 record folders in verification_ignored_paths

## Intent

List Chough 0.6.0 record folders in verification_ignored_paths

## Affected Canonical Specs

- None

## Acceptance Criteria

- .specsync/config.toml source_dirs names the real source folders instead of the whole repository; .specsync/sdd.json meaningful_paths names the tracked top-level paths instead of ".", without .chough/; verification_ignored_paths lists .chough/sessions/, .chough/archive/ and .chough/bugs/; chough doctor reports no records_in_verification finding; specsync check passes

## No-spec Rationale

Policy-only edit to .specsync/sdd.json that adds Chough's three record folders to verification_ignored_paths; no module contract changes
