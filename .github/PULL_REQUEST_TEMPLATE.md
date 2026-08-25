## Summary

<!-- What changed, and why. If an issue turned out to be wrong once you were
     into it, say so here rather than quietly working around it. -->

Closes #

## Test Plan

<!-- What you ran and what it said. Quote output rather than asserting success;
     "verified on LocalNet" is worth less than the three lines that show it. -->

- [ ] `fledge lanes run ci`
- [ ] `fledge lanes run local` (anything touching real chain behaviour)
- [ ] Spec updated if a contract's public surface changed
- [ ] Artifacts rebuilt and committed if a contract changed
