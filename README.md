# archon

**Archon** is a permissionless keeper network for Algorand — anyone registers
a scheduled contract call, any keeper executes it for the fee. By
[CorvidLabs](https://github.com/CorvidLabs), built with Algorand Python
(Puya) and AlgoKit. Live on TestNet.

| Contract | What it is | Status |
|----------|-----------|--------|
| [`smart_contracts/keeper`](smart_contracts/keeper/contract.py) | The Archon network: upkeep scheduling with ALGO escrow and keeper rewards | **Live on TestNet** — app [`769772891`](https://testnet.explorer.perawallet.app/application/769772891) |
| [`smart_contracts/pulse`](smart_contracts/pulse/contract.py) | Demo upkeep target (heartbeat counter) | Live on TestNet — app `769772906` |
| [`smart_contracts/corvid_vault`](smart_contracts/corvid_vault/contract.py) | Earlier experiment: CORVID ASA vault + stake-gated sealed-envelope relay (AlgoChat) | Built, tested, LocalNet-only (parked) |

## The keeper network

Smart contracts can't wake themselves. Everything time-based on Algorand —
vesting unlocks, subscription charges, prize settlements, limit orders,
"execute this after the vote" — needs *someone* to poke the chain. EVM chains
have Chainlink Automation and Gelato; Algorand had nothing productized. This
is that missing piece.

**How it works:**

1. **Register** — anyone calls `register` with a target app, call data
   (typically a method selector), a round interval, and a per-execution fee,
   escrowing ALGO in the contract (one box per upkeep).
2. **Execute** — once the due round passes, *any* account can call `execute`.
   The contract performs the registered call as an **inner app call** and pays
   the keeper from the escrow — atomically, so the fee is only paid if the
   upkeep actually ran.
3. **Top up / cancel** — anyone can add funding; only the creator can cancel
   and reclaim the remaining escrow.

Ownerless, no protocol rake, no token required — plain ALGO escrow so any
group can use it. Upkeep records are `arc4.Struct`s in boxes, so reading the
registry is a free algod query.

**Constraints (v1):** registered calls are NoOp app calls with exactly one app
arg and no foreign arrays — the standard "tick/settle/harvest" hook shape.
Fees ≥ 4000 µALGO (keepers pay ~3000 µALGO in group fees per execution).
Interval ≥ 10 rounds.

**Proven end-to-end on TestNet**: upkeeps registered against `Pulse.tick`
(both by the demo script and the `examples/` flow) have been executed by
permissionless callers at their due rounds — `Pulse.beats` incremented by
every execution (rounds 66610411, 66611741, 66625540+, all verifiable on the
explorer). Full reference: [`docs/archon.md`](docs/archon.md).

## Development

Pre-requisites: Python 3.13, [AlgoKit](https://github.com/algorandfoundation/algokit-cli),
Poetry, Docker (LocalNet only).

```bash
poetry install

fledge lanes run ci      # build all contracts → 29 unit tests → spec-sync check
fledge lanes run local   # ci + LocalNet e2e smoke (needs: algokit localnet start)
```

Individual tasks (also in `fledge.toml`):

```bash
poetry run python -m smart_contracts build   # Puya compile + typed clients
poetry run pytest tests/ -q                  # unit tests (algorand-python-testing)
specsync check --strict                      # spec drift check
```

## Layout

```
smart_contracts/
  keeper/            # the keeper network (contract.py, deploy_config.py)
  pulse/             # demo target
  corvid_vault/      # vault + operator relay (parked experiment)
  artifacts/         # compiled TEAL, ARC-56 specs, typed clients (generated)
tests/               # unit tests (algorand-python-testing mocks + bot decoder vectors)
specs/               # spec-sync specs (keeper, pulse, vault) — strict mode
docs/
  archon.md          # hand-off reference: API, box encoding, economics, operations
examples/
  register_upkeep.py # minimal: register an upkeep on the TestNet keeper app
  README.md          # the two integration paths (automate your app / earn fees)
scripts/
  keeper_testnet_demo.py  # full TestNet e2e: deploy, register, execute, verify
  keeper_bot.py           # permissionless keeper bot: scans boxes, executes due upkeeps
  smoke_localnet.py       # vault LocalNet e2e
fledge.toml          # fledge lanes (ci, local)
.specsync/           # spec-sync config
AGENTS.md / CLAUDE.md # agent guidance (keep in sync)
```

## TestNet

The demo is end-to-end and self-funding (prints the deployer address; fund it
with ~2 TestNet ALGO from [Lora](https://lora.algokit.io/testnet/fund) or the
[bank](https://bank.testnet.algorand.network)):

```bash
cp .env.testnet.template .env.testnet   # or: algokit generate env-file -a target_network testnet
# add DEPLOYER_MNEMONIC for a TestNet account (throwaway — never reuse on mainnet)
poetry run python -m scripts.keeper_testnet_demo
```

### Running a keeper bot

The bot services the live keeper app: it scans the upkeep boxes every round
and calls `execute` on anything due and funded, collecting the fees. It signs
as `KEEPER_MNEMONIC` if set, else `DEPLOYER_MNEMONIC` — that's the account
fees are paid to, and it pays the ~1,000 µALGO outer txn fee per execution.

```bash
poetry run python -m scripts.keeper_bot --once   # single scan (cron-friendly)
poetry run python -m scripts.keeper_bot          # loop block-by-block
```

Defaults to the canonical TestNet app `769772891`; override with `--app-id`
or `KEEPER_APP_ID`. An upkeep that fails to execute is skipped for the rest
of the run (retrying would burn the outer fee every round). Note the contract
schedules from the *scheduled* round, so an upkeep that was missed for many
intervals stays due until it has caught up one execution per interval.

### Hard-won TestNet notes (already handled in code)

- **App account MBR**: the keeper app account escrows ALGO and holds box MBR,
  so it must be funded the base 0.1 ALGO account MBR first — `deploy_config`
  does this idempotently.
- **Suggested-params cache**: public TestNet endpoints are slow enough that
  algokit-utils' cached suggested params can expire before simulate/broadcast.
  Deploy configs disable the cache (`set_suggested_params_cache_timeout(0)`)
  and the demo pins explicit validity rounds.

## Spec-driven development

This repo is managed with [spec-sync](https://github.com/CorvidLabs/spec-sync)
(strict) and [fledge](https://github.com/CorvidLabs/fledge) lanes. Every
contract has a spec under `specs/` — requirements, module contract, invariants,
error cases, testing. `specsync check --strict` runs in the `ci` lane and
fails if code drifts from the documented public API.

## Roadmap

- [x] Off-chain keeper bot (watches rounds, executes due upkeeps) — `scripts/keeper_bot.py`
- [ ] ASA-denominated upkeep fees (e.g. CORVID)
- [x] ~~Cancel leftover demo upkeeps 0–3 on TestNet~~ — done, 0.08 ALGO escrow reclaimed
- [ ] Multi-arg / foreign-array call shapes, if real use cases demand them
