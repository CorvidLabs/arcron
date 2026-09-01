# What is deployed on TestNet, and what it is doing

Everything Arcron runs on TestNet, in one place: which contracts exist, which
upkeeps drive them, how often, and how long each can pay for itself.

Read from the chain rather than remembered. Regenerate the tables with:

```bash
poetry run python -m scripts.keeper_bot --once --network testnet --app-id 769891898 --check
```

## The contracts

| what | app | state | what it is |
|---|---|---|---|
| **keeper** | [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | live, **not frozen** | Arcron itself. Upkeeps are boxes; anyone may execute a due one for its fee. |
| **pulse** | [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) | live | A heartbeat counter that exists to be called. No state worth protecting, cannot fail, which is what makes it the right first target. |

`not frozen` means the creator can still replace the programs. That is
deliberate while these iterate and it is a real power over anything escrowed;
see [`security.md`](security.md).

**The rain hub used to have four rows here.** It moved out of this repository
on 2026-08-31, to <https://github.com/CorvidLabs/arcron-rain>, and is served
from <https://corvidlabs.xyz/rain/>. The contract, its spec, its scripts, its
client and its page went; the write-ups on its deployments, its immutability
and what a draw proves on chain did not, and were dropped here rather than
rehomed, so there is nowhere to send a reader who wants them.

What stays here is two registry entries pointed at rain hubs, and they are not
the same kind of thing. Upkeep **113** drives the hub that is live, which is
the ordinary case for a permissionless registry: a target that happens to be
built somewhere else. Upkeep **91** drives `770130162`, the hub rain abandoned,
and that is a loose end.

## The upkeeps

Read from the chain at round 66,852,815 on 2026-08-31.

| id | target | every | escrow | runway | runs | policy |
|---|---|---|---|---|---|---|
| 19 | `769891902` pulse | 12 h | 0.3246 ALGO | ~39 days | 11 | catch up |
| 20 | `769891902` pulse | 12 h | 0.3246 ALGO | ~39 days | 11 | skip ahead |
| 21 | `769891902` pulse | 12 h | 0.3701 ALGO | ~44 days | 11 | skip ahead |
| 22 | `769891902` pulse | 12 h | 0.3246 ALGO | ~39 days | 11 | skip ahead |
| 81 | `770041460` (our agent) | 58 min | 3.9000 ALGO | ~16 days | 78 | skip ahead |
| 82 | `769891902` pulse | 58 min | 7.3253 ALGO | ~29 days | 61 | skip ahead |
| 91 | `770130162`, a **superseded** rain hub | 58 min | 2.9280 ALGO | ~29 days | 39 | skip ahead |
| 110 | `770734249`, our agent `CEPY52VZRWFL`'s | 7 days | 0.5000 ALGO | ~873 days | 0 | skip ahead |
| 113 | `770746178`, the live rain hub | 58 min | 0.0360 ALGO | **~0.4 days** | 11 | skip ahead |

A selection out of thirty-three, each row here because it shows something: the
`pulse` set is both catch-up policies side by side, **113** drives the rain hub
that is actually live and is about to starve, **91** still pays keepers to call
the hub rain abandoned, and **110** is on the longest cadence in the
registry, funded for more than two years of it. It was listed here as a
stranger's until `61d9a5a`: it belongs to `CEPY52VZRWFL`, an agent of ours
that funded itself from the public dispenser and so did not look like the
others. Nothing in this registry was registered by somebody who is not us.
The rest are agent registrations, twelve of them starved on a 20 round
cadence.
`fledge run health` is the live view and says which of the two kinds of overdue
each one is.

**Upkeep 91 is a loose end, stated because it is true.** It was registered
against `770130162` when that was the rain hub, and a target is fixed in the
box at registration exactly as a selector and a cadence are. Rain redeployed on
2026-08-31 as `770746178` — the old hub has no update path and predates the fix
that stops a ONE draw being aimed by tickets bought after the seed is public,
so nobody could repair it in place — and registered upkeep 113 against the new
one. 91 still holds 2.928 ALGO and still pays a keeper 4,000 µALGO an hour to
call `draw()` on a hub the repository that owns rain now refuses to adopt.
Nothing is stuck (`cancel` refunds escrow and box MBR in full), but every one
of those calls is escrow spent exercising an app this page says is superseded.
It wants cancelling, or re-registering against `770746178`, and this paragraph
stays until it is.

**Two rows this table used to carry are gone, both cancelled.** Upkeep 79 was
the same fault as 91 one hub earlier: it paid keepers to call a rain app
superseded on 2026-08-29, and was cancelled before the split rather than
exported into a new public repository. Upkeep 87 sat overdue with 5.75 ALGO in
it because its target reverts by its author's own configuration, which no
amount of escrow fixes. They are worth remembering together: escrow is not
health, and a target nobody can fix is a `cancel` rather than a top-up.

**Upkeep 82 replaced upkeep 73 on 2026-08-28**, and the reason is worth
recording because nothing on chain shows it. 73 paid `MIN_UPKEEP_FEE` and also
offered an ASA bonus. The bonus transfer is a third inner transaction, so a
keeper spent 4,000 microAlgos to earn 4,000 and cleared exactly nothing. It was
serviced 39 times for free before `fledge run health` was written and said so.

A fee cannot be edited: like the method selector, it is fixed in the box at
registration, so correcting it meant cancelling and re-registering. 82 pays
10,000, which is what the console suggests and what 81 already pays, and
carries a **fee cap of 20,000**, which 73 did not. That second part matters
beyond this upkeep: the contract escalates only when `cap > fee`, so with a cap
of zero our own dogfood had never once exercised the escalating fee, which is
the mechanism [`prior-art.md`](prior-art.md) identifies as the thing no
comparable system has anywhere.

**Upkeep 81 was registered by an agent we are running**, not by an outside
party. It came from `A3OZPORJ...`, a fresh account funded once by the TestNet
dispenser, which deployed its own target contract and registered against it.

It is worth recording anyway, for what it does show. The whole sequence took
**29 seconds**: deploy, configure, call twice, then register on Arcron. Nothing
in it was hand-held, and nobody adjusted the docs to make it work. An agent
given the public repository was able to go from an empty account to a
serviced upkeep without asking us anything, which is the closest thing to a
test of [`integrating.md`](integrating.md) that exists so far.

What it is not is evidence of adoption. The beta gate in
[`releases.md`](releases.md) asks for an upkeep registered by somebody who is
not us, and an agent we dispatched is us. That item is still open. An earlier
version of this page claimed it was met, on the strength of the address not
matching two we had hardcoded, which is not the same question.

**Runway** is escrow divided by burn rate at TestNet's measured 2.695 s/round. It is
what the upkeep can pay for, not a promise about keeper availability.

**Short cadences come back, and they starve the same way every time.** Five
upkeeps were cancelled on 2026-08-27 for running every 28 seconds to 9 minutes,
which at the 4,000 µALGO floor costs 0.6 to 13 ALGO a day each. They were not
underfunded, they were misconfigured, and cancelling recovered their box
minimum balance. Twelve more, upkeeps 98 to 109, were registered on a 20 round
cadence and are starved today: carrying one to thirty days costs 192 ALGO, so
`fledge run topup` refuses to fund them and says to cancel instead. The lesson
did not stick, because nothing enforces it. A cadence is fixed in the box at
registration, so the only remedy is a cancel.

## Where the rain analysis went

This page used to carry three sections on the rain hub: who may enter and how
often, what its gating was proved to do on chain on 2026-08-27, and why a block
seed is readable the round after it is committed. They were cut with the
contract on 2026-08-31 and **not** carried over. `arcron-rain` took the code,
the spec, the deployment scripts and the console; it has no prose of its own
for any of this, so there is no page to link to.

Recorded as a loss rather than as a forward, because saying "it moved" of
writing that did not move is the kind of pointer a reader can check. The
decoy-asset proof and the 800-round resolve window are the sort of thing
somebody comes back for, and until somebody ports them the only copy is this
repository's own history.

## Keeping this honest

The tables are generated. If a number here disagrees with the chain, the chain
is right and this file is stale; regenerate it rather than editing a figure.
