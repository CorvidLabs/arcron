"""Deploy `rain` for the TestNet dogfood: a recurring draw that would
actually annoy someone if it stopped, per `docs/design/1.0.md`.

Creates the app (if it does not already exist for this run), configures it
against the network's real Foundation randomness beacon
(`scripts.network.FOUNDATION_BEACON`, never a stub or a hardcoded id), enters
one ticket for the dedicated `RAIN_MNEMONIC` account so a draw has somebody
to pay, seeds the pot, and registers an Arcron upkeep on the canonical
TestNet keeper calling `draw()` every two hours (SKIP_AHEAD: a missed window
is dropped, not replayed; see the module docstring of
`smart_contracts/keeper/contract.py` and `docs/releases.md` for why).

Every step checks whether it already happened before spending anything, so
running this again after a partial failure is safe: it only does what is
still undone.

`--bootstrap-draw` additionally proves the whole lifecycle end to end, right
now, instead of waiting for the registered upkeep's first scheduled
execution (~2 hours away): it calls `draw()` directly, which any account may
since it is permissionless, waits the ~8 rounds for the beacon's committed
round to pass, then runs `scripts.rain_bot`'s own resolve/claim/deposit logic
once. This exercises the exact code path the two-hour schedule will use from
then on; it does not replace or interfere with it (`draw` is idempotent while
a draw is open, and Arcron's own schedule tracking is unaffected by who else
calls `draw`).

Requires `RAIN_MNEMONIC` to already be set in `.env.<network>`, pointing at a
funded account dedicated to this role. This script tops it up to
`RAIN_ACCOUNT_FUNDING_MICROALGO` but does not generate the key itself.

Run:  poetry run python -m scripts.rain_testnet_deploy --network testnet [--bootstrap-draw]
"""

import argparse
import logging

import algokit_utils

from scripts import network as net
from scripts.keeper_bot import scan_upkeeps
from scripts.keeper_e2e import _box_mbr, _selector
from scripts.rain_bot import default_pending_path, scan_once as rain_scan_once
from smart_contracts.artifacts.keeper.keeper_client import KeeperClient, RegisterArgs
from smart_contracts.artifacts.rain.rain_client import (
    ConfigureArgs,
    DepositArgs,
    EnterArgs,
    RainClient,
)
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.rain.contract import TICKET_MBR
from smart_contracts.rain.deploy_config import deploy as deploy_rain

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# algosdk's encoding of the all-zero address, which means "no gate": this
# draw is open to anyone, matching 1.0's decision that CORVID (or any other
# asset) is not required. See docs/design/1.0.md.
ZERO_ADDRESS = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"

DRAW_SIGNATURE = "draw()uint64"
# The live Arcron keeper network; see README.md and docs/releases.md.
KEEPER_APP_ID = 769891898
# Every Algorand account needs this before it can send anything, and rain's
# app account pays out by inner payment; `configure` collects it once.
APP_BASE_MBR = 100_000
# A seed prize. Small enough not to matter if TestNet ALGO were ever scarce,
# large enough that the draw obviously does something.
POT_SEED_MICROALGO = 1_000_000
# What the rain bot account is topped up to. A cycle (resolve + claim +
# deposit) costs about 6,000 µALGO in transaction fees; this is roughly two
# months of headroom at the registered two-hour cadence, well past the
# 30-day minimum the dogfood needs, without parking more TestNet ALGO than
# the exercise calls for.
RAIN_ACCOUNT_FUNDING_MICROALGO = 3_000_000
# ~2 hours at Algorand's nominal 2.8-second block time (7,200 / 2.8 = 2,571.4).
INTERVAL_ROUNDS = 2_571
# MIN_UPKEEP_FEE from smart_contracts/keeper/contract.py, restated so this
# module has no import-time dependency on the keeper contract's internals.
FEE_PER_EXECUTION = 4_000
# ~41.6 days of draws (500 * 2 hours), comfortably past the 30-day minimum.
EXECUTIONS_TO_FUND = 500
UPKEEP_FUNDING_MICROALGO = FEE_PER_EXECUTION * EXECUTIONS_TO_FUND


def _payment(algorand, sender: str, receiver: str, amount: int):
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    net.add_network_argument(parser)
    parser.add_argument(
        "--gate-creator",
        default="",
        help=(
            "Only holders of an asset this account minted may enter. Omit for open "
            "entry. A minting account is usually a working wallet, so pair it with "
            "--gate-unit-prefix."
        ),
    )
    parser.add_argument(
        "--gate-unit-prefix",
        default="",
        help=(
            "Also require the gate token's unit name to start with this. Bytes, so "
            "case-sensitive. Empty accepts anything --gate-creator minted."
        ),
    )
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="wrap an already-created rain app instead of creating a new one "
        "(no indexer lookup either way; see smart_contracts/rain/deploy_config.py)",
    )
    parser.add_argument(
        "--bootstrap-draw",
        action="store_true",
        help="also open, resolve, claim and redeposit one draw right now, "
        "rather than waiting for the registered upkeep's first schedule",
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")

    # ------------------------------------------------------------------
    logger.info("── App ──")
    rain = deploy_rain(args.app_id)

    beacon_app = net.FOUNDATION_BEACON.get(args.network)
    if beacon_app is None:
        parser.error(f"no Foundation randomness beacon recorded for {args.network}")

    state = rain.state.global_state
    if state.beacon_app == 0:
        rain.send.configure(
            args=ConfigureArgs(
                mbr_payment=_payment(algorand, deployer.address, rain.app_address, APP_BASE_MBR),
                beacon_app=beacon_app,
                gate_creator=args.gate_creator or ZERO_ADDRESS,
                gate_unit_prefix=args.gate_unit_prefix.encode(),
                prize_asset=0,
            )
        )
        if args.gate_creator:
            logger.info(
                f"  Configured against beacon {beacon_app}, ALGO prize, gated on "
                f"{args.gate_creator[:12]}… with unit prefix "
                f"{args.gate_unit_prefix!r}"
            )
        else:
            logger.info(f"  Configured against beacon {beacon_app}, open entry, ALGO prize")
    elif state.beacon_app == beacon_app:
        logger.info(f"  Already configured against beacon {beacon_app}; skipping")
    else:
        raise SystemExit(
            f"rain app {rain.app_id} is already configured against beacon "
            f"{state.beacon_app}, not {beacon_app}. This script never reconfigures "
            f"an app: `configure` is creator-only and one-time, by design."
        )

    # ------------------------------------------------------------------
    logger.info("── The rain bot's own account ──")
    try:
        rain_account = algorand.account.from_environment("RAIN")
    except Exception as cause:
        parser.error(
            f"RAIN_MNEMONIC is not set: {cause}. Generate a dedicated account and add "
            f"its mnemonic to .env.{args.network} before running this; see "
            f"docs/releases.md for how the deployed dogfood is set up."
        )
    balance = algod.account_info(rain_account.address)["amount"]
    if balance < RAIN_ACCOUNT_FUNDING_MICROALGO:
        top_up = RAIN_ACCOUNT_FUNDING_MICROALGO - balance
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=rain_account.address,
                amount=algokit_utils.AlgoAmount(micro_algo=top_up),
            )
        )
        logger.info(f"  Funded {rain_account.address} with {top_up} µALGO")
    else:
        logger.info(f"  {rain_account.address} already holds {balance} µALGO; not topping up")

    bot_client = RainClient(
        algorand=algorand,
        app_id=rain.app_id,
        default_sender=rain_account.address,
        default_signer=rain_account.signer,
    )

    # ------------------------------------------------------------------
    logger.info("── A ticket, so a draw has somebody to pay ──")
    state = rain.state.global_state
    if state.tickets == 0:
        ticket = bot_client.send.enter(
            args=EnterArgs(
                mbr_payment=_payment(
                    algorand, rain_account.address, rain.app_address, TICKET_MBR
                ),
                gate_asset=0,
            )
        ).abi_return
        logger.info(f"  {rain_account.address} holds ticket {ticket}")
    else:
        logger.info(f"  {state.tickets} ticket(s) already exist; not entering again")

    # ------------------------------------------------------------------
    logger.info("── The pot ──")
    state = rain.state.global_state
    # `pot == 0` is not, on its own, "never seeded": `draw` legitimately zeroes
    # it every time one opens, right up until the next `deposit`. Gate on
    # `draw_id == 0` as well, which only ever increments and never resets, so
    # this can seed at most once per app's whole lifetime. Found the hard way:
    # an interrupted first run re-entered this block after `draw` had already
    # emptied the pot it had just seeded, and deposited a second time.
    if state.pot == 0 and state.draw_id == 0:
        pot = rain.send.deposit(
            args=DepositArgs(
                payment=_payment(
                    algorand, deployer.address, rain.app_address, POT_SEED_MICROALGO
                )
            )
        ).abi_return
        logger.info(f"  Seeded {POT_SEED_MICROALGO} µALGO (pot now {pot})")
    else:
        logger.info(f"  Pot already holds {state.pot} µALGO; not reseeding")

    # ------------------------------------------------------------------
    logger.info("── The Arcron upkeep ──")
    upkeeps = [u for u in scan_upkeeps(algod, KEEPER_APP_ID) if u.target_app == rain.app_id]
    if upkeeps:
        upkeep_id = upkeeps[0].upkeep_id
        logger.info(
            f"  Upkeep {upkeep_id} already targets rain app {rain.app_id}; "
            f"not re-registering"
        )
    else:
        call_data = _selector(DRAW_SIGNATURE)
        keeper_client = KeeperClient(
            algorand=algorand,
            app_id=KEEPER_APP_ID,
            default_sender=deployer.address,
            default_signer=deployer.signer,
        )
        upkeep_id = keeper_client.send.register(
            args=RegisterArgs(
                mbr_payment=_payment(
                    algorand, deployer.address, keeper_client.app_address, _box_mbr([call_data])
                ),
                funding_payment=_payment(
                    algorand,
                    deployer.address,
                    keeper_client.app_address,
                    UPKEEP_FUNDING_MICROALGO,
                ),
                target_app=rain.app_id,
                call_args=[call_data],
                interval_rounds=INTERVAL_ROUNDS,
                fee_per_execution=FEE_PER_EXECUTION,
                # A missed draw is not worth replaying; only the latest matters,
                # and CATCH_UP on a long gap would fire one execution per missed
                # interval at one fee each. See docs/releases.md, upkeep 18.
                policy=SKIP_AHEAD,
                fee_cap=0,
                fee_asset=0,
                asset_fee=0,
            ),
        ).abi_return
        logger.info(
            f"  Registered upkeep {upkeep_id}: draw() every {INTERVAL_ROUNDS} rounds "
            f"(~2 hours), funded for {EXECUTIONS_TO_FUND} executions "
            f"({UPKEEP_FUNDING_MICROALGO} µALGO)"
        )

    if args.bootstrap_draw:
        logger.info("── Bootstrap: one draw, right now ──")
        state = rain.state.global_state
        if state.draw_open == 1:
            logger.info(f"  A draw is already open (id {state.draw_id}); not opening another")
        elif state.draws_resolved == 0:
            # Only open one the first time through: a re-run after this draw
            # has resolved and been claimed should not open a second one on
            # its own; that is the registered upkeep's job from here on.
            draw_id = rain.send.draw().abi_return
            if draw_id == 0:
                logger.warning(
                    "  draw() returned 0 (no-op): no tickets, or the pot does not "
                    "cover the allocation reserve. Nothing opened."
                )
            else:
                state = rain.state.global_state
                logger.info(
                    f"  Draw {draw_id} open: {state.prize} µALGO locked for "
                    f"{state.tickets_snapshot} ticket(s), decided at round "
                    f"{state.commit_round}"
                )
        else:
            logger.info(
                f"  {state.draws_resolved} draw(s) already resolved and no draw is "
                f"open; the bootstrap already ran. Not opening another."
            )

        # Whether this call opened the draw or an earlier, interrupted run
        # did, finish it: wait for the beacon round, then resolve/claim/
        # deposit. Safe to run again, since scan_once is idempotent: if the
        # draw was already resolved and claimed this is a no-op.
        state = rain.state.global_state
        if state.draw_open == 1:
            net.wait_for_round(algorand, state.commit_round + 1, poker=deployer)
        rain_scan_once(
            algod,
            bot_client,
            rain.app_id,
            rain_account.address,
            default_pending_path(args.network, rain.app_id),
        )

    # ------------------------------------------------------------------
    state = rain.state.global_state
    logger.info("")
    logger.info(f"rain app {rain.app_id} on {args.network}")
    logger.info(f"  address        {rain.app_address}")
    logger.info(f"  beacon_app     {state.beacon_app}")
    logger.info(f"  pot            {state.pot} µALGO")
    logger.info(f"  tickets        {state.tickets}")
    logger.info(f"  draws_resolved {state.draws_resolved}")
    logger.info(f"  upkeep         {upkeep_id} on keeper app {KEEPER_APP_ID}")
    logger.info(f"  rain bot acct  {rain_account.address}")
    logger.info("")
    logger.info("Record the app id in docs/releases.md and docs/status.md.")


if __name__ == "__main__":
    main()
