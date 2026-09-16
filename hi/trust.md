---
hi: 1
families: [TRUST]
---

# Knowing what you are trusting

## Intent

Anybody asked to escrow money here should be able to check the claims rather than believe them, and the project should make that cheap. Every load-bearing claim ought to be answerable by a command a stranger can run against the chain, and what has not been proven should be said at least as plainly as what has. Somebody arriving to attack it is doing the most valuable work available, so they should be handed what is already known and told what would actually be new.

## Criteria

- **TRUST-1**  Every load-bearing claim about a deployment can be checked with a command I run myself rather than taken on trust.
- **TRUST-2**  I can prove that a deployment is running the source in a given checkout, byte for byte.
  - **TRUST-2.a**  The proof compares what is deployed against a build of that source, not two pieces of text that look alike.
- **TRUST-3**  I can read from the chain whether a deployment's programs can still be replaced.
- **TRUST-4**  A deployment that is unaudited, unfrozen and TestNet-only says so in the first thing anybody reads.
- **TRUST-5**  Every accepted risk is written down with the reason it is accepted.
- **TRUST-6**  Every attack a review has found stays in a suite that keeps being run, so a fix cannot quietly come undone.
  - **TRUST-6.a**  Those attacks are run against a real chain rather than against a mock.
- **TRUST-7**  Somebody arriving to break it is handed what is already known, so they do not spend their time rediscovering it.
  - **TRUST-7.a**  They are told what would actually be new, ranked by how much it would matter.
- **TRUST-8**  A vulnerability that puts live funds at risk has a private route rather than a public thread.
- **TRUST-9**  What has not been proven is said as plainly as what has.
  - **TRUST-9.a**  Whether anybody outside the project has ever registered an upkeep is stated outright rather than left to be inferred.
  - **TRUST-9.b**  An example that has never run where money was at stake says so rather than borrowing the registry's evidence.
- **TRUST-10**  Numbers are derived from one stated basis, so no two pages can quietly disagree.
- **TRUST-11**  A superseded deployment is named as superseded, so a stale link cannot quietly take somebody's money.
