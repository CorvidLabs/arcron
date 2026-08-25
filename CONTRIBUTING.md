# Contributing to Arcron

The most useful contribution is not code: **run a keeper**. The network only
works if somebody executes due upkeeps, and that is deliberately open to
anyone. See [Operating a bot](docs/arcron.md#operating-a-bot).

If you do want to change the code, here is everything that will otherwise bite
you.

## Setup, and the one thing that trips everyone

```bash
poetry install
```

**Python 3.12 or 3.13, never 3.14.** coincurve publishes no 3.14 wheels, and
the source build fails with an unhelpful message about a missing LICENSE file
in a cffi distribution. `pyproject.toml` pins `>=3.12,<3.14` so Poetry refuses
3.14 and finds a supported interpreter itself; if you see it building
coincurve from source, something has overridden that.

You will also want [AlgoKit](https://github.com/algorandfoundation/algokit-cli)
and Docker for anything touching a chain, and [Bun](https://bun.sh) for the
console.

## The gate

```bash
fledge lanes run ci      # what CI runs: build, tests, spec drift, console
fledge lanes run local   # the above plus LocalNet end-to-end (needs algokit localnet start)
```

`fledge lanes run ci` must be green before you open a pull request. CI runs
exactly these tasks — every step shells out to `fledge.toml`, so the two cannot
drift.

## Spec-sync will fail you on something you did not know existed

Every contract has a spec under `specs/<name>/`, and `specsync check --strict`
runs in CI. **If you change a contract's public surface — a method, a
parameter, a return type, an exported constant — you must update its spec in
the same commit**, including the Public API tables and the Change Log. Strict
mode treats an undocumented export as an error.

This catches real drift, but it is an unpleasant surprise if nobody warned
you. Now you are warned. Run `specsync check --strict` locally before pushing.

## After changing a contract

```bash
poetry run python -m smart_contracts build
```

Artifacts are committed, and CI fails if they differ from a fresh build — a
contract change with stale artifacts means the bot and console are compiled
against source that no longer exists.

**If you change the `Upkeep` struct, five things move in lockstep** or the bot
and console will silently misread the registry:

1. `smart_contracts/keeper/contract.py`
2. `scripts/keeper_bot.py::_decode_upkeep`
3. `web/src/app/core/upkeep.ts`
4. the pinned box vector in `tests/test_keeper_bot.py`
5. `specs/keeper/`

## What tests can and cannot tell you

Unit tests use `algorand-python-testing` mocks, which **record inner
transactions without executing them** and **do not enforce minimum balances**.
An upkeep that fails when actually invoked, or an app that cannot pay out what
it owes, passes its unit tests happily.

Anything depending on either belongs in a LocalNet end-to-end test —
`scripts/keeper_e2e.py` is the reference, and each demo script is a smaller
worked example. That is not a style preference: the box-MBR bug that forced a
TestNet redeploy was invisible to unit tests and obvious on LocalNet.

## The console

```bash
cd web && bun install && bun run ng serve
bun test
```

Styling comes **only** from the CorvidLabs design system vendored in
`web/public/brand/`. No hardcoded colours, no hand-rolled theme toggle. Amounts
display in ALGO; cadences display as time as well as rounds. Accessibility is
checked with axe-core and must stay at zero violations.

Note the brand files are trademarked and not covered by the Apache licence —
see [NOTICE](NOTICE). If you fork Arcron as your own project, replace them.

## Pull requests

One issue per pull request, small enough to review. Say what you measured
rather than what you expect: this repository has a habit of checking claims
against a real chain, and PR descriptions quote the output.

If an issue turns out to be wrong or a bad idea once you are into it, say so on
the issue rather than forcing it through. Several issues here have been
reshaped that way, and the result was better each time.

## Security

Do not open a public issue for a vulnerability — see [SECURITY.md](SECURITY.md).
