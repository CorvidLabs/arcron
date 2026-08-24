---
spec: embargo.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_embargo.py` | unit (`algorand-python-testing`) | schedule stores content and round, box MBR derived from the stored box, short MBR rejected, past release round rejected, content bounds, reschedule rejected, publish before/at/after the release round, double publish, publish with nothing scheduled, `rounds_remaining` countdown, and the compiled ABI containing no method that would let an author retract |
| `scripts/embargo_demo.py` | e2e (LocalNet or TestNet) | the full path with a real keeper: a keeper arriving early is refused and pays nothing, the release is published at or after its round by someone who is not the author, and afterwards it can be neither re-published nor rescheduled |

## Manual Testing

- [ ] `poetry run python -m scripts.embargo_demo --network localnet`
- [ ] `fledge lanes run local` — includes the demo as `smoke-embargo`

## Edge Cases & Boundary Conditions

| Scenario | Expected Behavior |
|----------|-------------------|
| Publish in exactly the release round | Allowed (`>=`) |
| Nobody publishes for a long time | Still publishable; the release is late, never lost |
| An upkeep comes due before the release round | The execution fails harmlessly and costs the keeper nothing; the next due round succeeds |
| Content of exactly `MAX_CONTENT` bytes | Allowed; one byte more is rejected |
| The author is also the keeper | Allowed and unremarkable — publishing is permissionless, so the author has no special power either way |
