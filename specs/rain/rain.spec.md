---
module: rain
version: 4
status: active
files:
  - smart_contracts/rain/contract.py

db_tables: []
depends_on: []
---

# Rain

## Purpose

A hub of rains. Anyone opens one: any NFT collection as the gate, ALGO or
any ASA as the prize, any drip, any interval, and one of three ways it falls.
Corvid Labs uses the same app for Corvid ASA and Corvid NFT rains; so can
anybody else.

This is Discord rain, on chain. People talk, then a drop goes to the people
who showed up — or, for the fill-and-forget shape, to everyone who entered
once. It is not a jackpot of the whole pot.

`draw` is the Arcron hook. It walks a few rain boxes, rains on the ones that
are due, and returns rather than failing when there is nothing to do. It
moves no money and makes no inner call. Holders pull `claim` for themselves.
A push to a closed account would fail the whole execution and stall the
schedule for everyone.

Cadence is not "whenever someone calls `draw`". Each rain no-ops until its
own `interval_rounds` have passed, so a permissionless extra call cannot
drain a pot. Arcron still fires the hook; the interval is also in the rain.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `RAIN_PREFIX` | `b"r"` | Box prefix: `b"r" + itob(id)` → `RainRec`. |
| `TICKET_PREFIX` | `b"t"` | Box prefix: `b"t" + itob(id) + address` → `Ticket`. |
| `INDEX_PREFIX` | `b"n"` | Box prefix: `b"n" + itob(id) + itob(index)` → address, ONE mode only. |
| `RAIN_BOX_MBR` | `2,500 + 400 × 233` (`95,700`) | What one rain box costs; paid by its creator. |
| `TICKET_MBR` | `2,500 + 400 × 65` (`28,500`) | What one ticket box costs; paid by its buyer. |
| `INDEX_MBR` | `2,500 + 400 × 49` (`22,100`) | Extra ONE-mode index box; paid at enter. |
| `ASSET_OPT_IN_MBR` | `100,000` | What holding one asset costs the hub, permanently. |
| `APP_BASE_MBR` | `100,000` | The app account's own floor, collected once at `bootstrap`. |
| `SPLIT` | `0` | Enter once; each fire splits `drip` across every ticket. |
| `ONE` | `1` | Enter once; each fire locks `drip` for one random ticket. |
| `WAVE` | `2` | Check in each interval; fire splits `drip` across up to `wave_cap`. |
| `DRAW_SCAN` | `4` | How many rain boxes `draw` opens per call. |
| `COMMIT_DELAY` | `8` | ONE: rounds after `draw` before the block seed is used. |
| `SEED_WINDOW` | `800` | ONE: after this, `abandon` rather than `resolve`. |
| `MIN_INTERVAL_ROUNDS` | `10` | Floor on a rain's interval. |
| `MAX_INTERVAL_ROUNDS` | `1,000,000,000` | Ceiling, matching the keeper. |

### Exported Types

| Type | Description |
|------|-------------|
| `Rain` | ARC-4 contract class; global state `next_rain_id`, `cursor`, `bootstrapped`. |
| `Label` | `byte[32]`; the name stored on a rain box, padded. |
| `RainRec` | One rain, stored in a box. |
| `Ticket` | Per (rain, account). SPLIT uses `credit` as debt; WAVE/ONE as owed. |
| `Rained` | ARC-28 event when a fire credits or locks. |

#### Rain Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `bootstrap` | `mbr_payment: pay` | `void` | Creator only, once. Funds `APP_BASE_MBR`. |
| `opt_in_prize_asset` | `prize: asset, mbr_payment: pay` | `uint64` | Opts the hub into an asset so rains can pay in it. Anyone, once per asset. Refuses clawback, freeze, manager, or default-frozen. |
| `create_rain` | `mbr_payment: pay, label: byte[32], gate_creator: address, prize_asset: uint64, drip: uint64, interval_rounds: uint64, mode: uint64, wave_cap: uint64` | `uint64` | Anyone, after bootstrap. Returns the new rain id. Zero `gate_creator` is open entry; zero `prize_asset` is ALGO (the hub must already be opted in otherwise). WAVE needs `wave_cap` > 0; the others need it 0. First fire waits one interval from create. |
| `set_rain` | `rain_id: uint64, drip: uint64, interval_rounds: uint64` | `void` | That rain's creator only. Tune the slice and the interval. |
| `enter` | `mbr_payment: pay, rain_id: uint64, gate_asset: uint64` | `uint64` | One ticket per account per rain. WAVE also checks in for the open drop if a seat remains. ONE refuses entry while a draw is open. |
| `gm` | `rain_id: uint64, gate_asset: uint64` | `uint64` | WAVE check-in. First `wave_cap` this interval. Settles an unclaimed last drop. Returns 0 if already in or full. |
| `deposit` | `payment: pay, rain_id: uint64` | `uint64` | Adds ALGO to that rain's pot. Anyone. |
| `deposit_asset` | `transfer: axfer, rain_id: uint64` | `uint64` | Adds the prize asset to that rain's pot. Anyone. |
| `draw` | — | `uint64` | Zero-argument, the shape Arcron calls. Opens up to `DRAW_SCAN` rains from `cursor`, fires every due one, advances the cursor. Returns how many fired, or 0. |
| `resolve` | `rain_id: uint64` | `uint64` | ONE: after the committed round, pick the winner from that round's block seed and credit their ticket. Returns the index. |
| `abandon` | `rain_id: uint64` | `uint64` | ONE: after `SEED_WINDOW`, return the lock to the pot. |
| `claim` | `rain_id: uint64, gate_asset: uint64` | `uint64` | Pull credited rain. Returns 0 when there is nothing. Gated rains still need a collection token. WAVE also settles the last drop. |
| `allocation_of` | `rain_id: uint64, who: address` | `uint64` | Readonly. What `who` can claim on that rain right now. |
| `rain_of` | `rain_id: uint64` | `RainRec` | Readonly. The rain box. |

## Invariants

1. `draw` never fails: an empty hub, a rain that is too soon, no tickets, a drip too small to split, or a pot that cannot cover, it returns `0` (or a count of the others that did fire) and does not reject.
2. `draw` moves no funds and makes no inner call, so it cannot fail for want of a resource. It opens at most `DRAW_SCAN` rain boxes.
3. SPLIT: each fire pays every ticket the same `share = drip // tickets`. `paid = share * tickets` leaves the pot. The remainder `drip % tickets` stays.
4. WAVE: each fire pays the people who checked in, `share = drip // wave_count`, at most `wave_cap`. Unclaimed shares from the last drop return to the pot at the next fire.
5. ONE: `draw` locks `drip` against a future round's block seed. `resolve` credits one ticket. `abandon` returns the lock if the seed is gone.
6. A late SPLIT arriver's ticket is initialised to the current `cumulative`, so they do not collect rains that fell before they entered.
7. One account, one ticket, per rain. A second `enter` from the same sender on the same rain fails.
8. Funds leave only to an account claiming its own credit, via `claim`.
9. Each rain is denominated one way or the other and never both.
10. Gating checks the asset's creator, not its id.
11. The prize asset cannot buy a ticket.
12. Every payment and asset transfer the contract accepts is checked for `rekey_to`, `close_remainder_to` and `asset_close_to`. MBR payments must come from the caller.
13. `bootstrap` requires a payment covering `APP_BASE_MBR`.
14. A rain will not run again until `last_rain_round + interval_rounds`, even if `draw` is called permissionlessly.
15. Creating a rain sets `last_rain_round` to now, so the first fire waits one interval.
16. ONE: no ticket can be taken while a draw is open (`prize_locked` > 0). The set the winner is drawn over is fixed at fire, `COMMIT_DELAY` rounds before the committed seed exists; a ticket taken later, against a seed already public, could be sized to catch the draw. `resolve` and the permissionless `abandon` both clear the lock, so entry always reopens.

## Behavioral Examples

### Scenario: Fill and forget

- **Given** a SPLIT rain with a 1B token pot and drip 10,000 per day, and N tickets
- **When** Arcron calls `draw` after the interval
- **Then** each ticket is credited `10000 // N`, the pot shrinks by that paid slice, and holders pull `claim` when they want it

### Scenario: Discord GM rain

- **Given** a WAVE rain with `wave_cap` 10
- **When** twelve people `gm` this interval
- **Then** the first ten are in; the fire splits `drip` ten ways; the other two are in for a later drop if they check in again

### Scenario: One random holder

- **Given** a ONE rain with tickets
- **When** `draw` runs, then `resolve` after the committed round
- **Then** one ticket is credited the drip, from that round's block seed

### Scenario: A late ticket cannot aim an open draw

- **Given** a ONE rain whose draw has locked, its committed round passed and that round's seed public
- **When** anyone tries to `enter`
- **Then** entry fails until `resolve` or `abandon` closes the draw, so the count the seed is reduced against is the one that existed before the seed did

### Scenario: Interval holds even if someone calls `draw` early

- **Given** a fire just ran
- **When** anyone calls `draw` before `interval_rounds` have passed
- **Then** that rain is skipped and its pot is untouched

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `bootstrap` by a non-creator, or twice | Fails with "Only the creator can bootstrap" / "Already bootstrapped" |
| `create_rain` before bootstrap | Fails with "Not bootstrapped" |
| `create_rain` with drip 0 | Fails with "Drip must be positive" |
| `create_rain` WAVE with cap 0 | Fails with "Wave cap must be positive" |
| `enter` twice from the same account on the same rain | Fails with "Already entered" |
| `enter` with an MBR payment below the ticket box cost | Fails with "MBR payment too small" |
| `deposit` of zero | Fails with "Amount must be positive" |
| `claim` on a gated rain without a collection token | Fails with "Hold a token from the collection". The credit stays. |
| `claim` with nothing owed, or no ticket | Returns 0 |
| `enter` on a ONE rain while a draw is open | Fails with "Draw open; enter after resolve or abandon" |
| `resolve` before the committed round | Fails with "Too early" |
| `abandon` while the seed window is open | Fails with "Window still open" |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `GlobalState`, `gtxn`, `itxn`, `arc4.emit`, `op.Block.blk_seed` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `js/src/rain.ts` | Decoder and box names for the console |
| Arcron keeper | Calls `draw()uint64` on the registered upkeep |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-30 | CorvidLabs | ONE: `enter` is refused while a draw is open. `resolve` draws over the live count and the committed seed is public for the whole `SEED_WINDOW`, so a late batch of tickets could be sized to catch it; the count now freezes at fire, `COMMIT_DELAY` rounds before the seed exists. |
| 2026-08-29 | CorvidLabs | Document `Label` (`byte[32]`), which specsync treats as an export. |
| 2026-08-29 | CorvidLabs | Rain is a hub. Anyone `create_rain`s; modes SPLIT / ONE / WAVE; one Arcron `draw` walks a cursor of rain boxes. Block seed replaces the Foundation beacon for ONE. |
| 2026-08-29 | CorvidLabs | Rain is a drip, not a jackpot. `draw` credits every ticket an equal share of `drip`; `claim` pulls. Superseded by the hub. |
| 2026-08-26 | CorvidLabs | #105: `configure` now takes an `mbr_payment` argument covering `APP_BASE_MBR`. |
| 2026-08-25 | CorvidLabs | #102: rekey/close checks on accepted payments. |
| 2026-08-24 | CorvidLabs | Initial scheduled draw (issue #25). Jackpot plus beacon; superseded. |
