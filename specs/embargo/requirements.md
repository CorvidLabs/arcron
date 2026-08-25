---
spec: embargo.spec.md
---

## User Stories

- As an author, I want to commit a statement to a future round so that it becomes official whether or not I am available, willing or able at that moment.
- As a reader, I want to verify that a release could not have been published early, delayed, or quietly withdrawn.
- As a keeper, I want the release to be an ordinary paid upkeep, with no special integration.

## Acceptance Criteria

### REQ-embargo-001
Anyone SHALL be able to schedule content against a future round exactly once per app instance, paying the box MBR that content costs.

### REQ-embargo-002
The contract SHALL reject `publish` before the release round, and SHALL accept it from any caller at or after that round.

### REQ-embargo-003
After scheduling, no method SHALL exist that alters the content, the release round or the author, including for the author.

### REQ-embargo-004
`publish` SHALL succeed at most once and SHALL record the round it happened in.

### REQ-embargo-005
`publish` SHALL take no arguments beyond its selector, so that an Arcron upkeep can call it in the v1 shape.

## Constraints

- Content is public from the moment it is stored. The contract guarantees the publication event, not secrecy; anything requiring secrecy needs a commitment here and a payload elsewhere.
- Content is bounded at 2,048 bytes so the MBR a scheduler must cover stays predictable. Larger payloads belong off-chain behind a CID.
- One scheduled release per app instance; a second release means a second instance.

## Out of Scope

- Encryption, threshold reveal, or any mechanism that would make the content unreadable before release.
- Editing, retraction or cancellation. Their absence is the feature.
- Multiple releases, or a release schedule, in one instance.
