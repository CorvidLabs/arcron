---
spec: pulse.spec.md
---

## User Stories

- As a keeper-network developer, I want a trivial permissionless target app so upkeep registration and execution can be demonstrated end-to-end.

## Acceptance Criteria

### REQ-pulse-001
`tick` SHALL increment `beats` by exactly 1, record the current round in `last_beat_round`, and return the new count; it SHALL be callable by any account or app.

## Constraints

- Demo contract only; permissionless by design, no gating.

## Out of Scope

- Any access control, rate limiting, or production use.
