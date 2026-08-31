"""Prove the live TestNet hub pays ALGO and an ASA.

Funds the Corvid daily/GM rains, enters them with a TestNet Corvid NFT,
opens short-interval ONE (ALGO) and SPLIT (ASA) rains, calls `draw`, claims.
Prints explorer links. Does not print mnemonics.

Tops the rain deployer up from the grok-4.6 agent account when spendable
ALGO is too low to cover boxes, pots, and fees.

Run:  poetry run python -m scripts.rain_testnet_live_proof --network testnet
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import algokit_utils
from algosdk import transaction
from algosdk.account import address_from_private_key
from algosdk.mnemonic import to_private_key
from dotenv import dotenv_values

from scripts import network as net
from smart_contracts.artifacts.rain.rain_client import (
    AllocationOfArgs,
    ClaimArgs,
    CreateRainArgs,
    DepositArgs,
    DepositAssetArgs,
    EnterArgs,
    OptInPrizeAssetArgs,
    RainClient,
    RainOfArgs,
    ResolveArgs,
    SetRainArgs,
)
from smart_contracts.rain.contract import (
    ASSET_OPT_IN_MBR,
    COMMIT_DELAY,
    INDEX_MBR,
    MIN_INTERVAL_ROUNDS,
    ONE,
    RAIN_BOX_MBR,
    SPLIT,
    TICKET_MBR,
    WAVE,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HUB = 770_130_162
DAILY_ROUNDS = 30_857
DAILY_DRIP = 50_000
CORVID_NFT = 749_830_809
CORVID_MINTER = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
ZERO_ADDRESS = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
EXPLORER = "https://testnet.explorer.perawallet.app"
#: Where to find a funder when the deployer is short. This repository is
#: public, so it names no path in anyone's home directory: an absolute path to
#: a private key file is a map to it, and it also made the script unrunnable by
#: anyone who is not its author. Set RAIN_FUNDER_ENV, or pass --funder-env.
FUNDER_ENV_VAR = "RAIN_FUNDER_ENV"
TOP_UP_MICRO = 4_000_000
NEED_SPENDABLE = 1_500_000
INNER = algokit_utils.AlgoAmount(micro_algo=2_000)
# Four rain boxes in one draw blow the 700 opcode budget. Extra fee pools more.
DRAW_FEE = algokit_utils.AlgoAmount(micro_algo=9_000)


def _label(text: str) -> bytes:
    return text.encode()[:32].ljust(32, b"\x00")


def _pay(algorand, sender: str, receiver: str, amount: int):
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
        )
    )


def _txid(result) -> str:
    tx_id = getattr(result, "tx_id", None)
    if isinstance(tx_id, str) and tx_id:
        return tx_id
    ids = getattr(result, "tx_ids", None)
    if ids:
        return ids[-1]
    return ""


def _link(tx_id: str) -> str:
    return f"{EXPLORER}/tx/{tx_id}" if tx_id else "(no tx id)"


def _spendable(algod, address: str) -> int:
    info = algod.account_info(address)
    return int(info["amount"]) - int(info["min-balance"])


#: Set by --funder-env when given; otherwise read from the environment.
_FUNDER_OVERRIDE: Path | None = None


def funder_env_path() -> Path | None:
    """The file holding a funder mnemonic, or None if none is configured."""
    if _FUNDER_OVERRIDE is not None:
        return _FUNDER_OVERRIDE
    from os import environ

    raw = environ.get(FUNDER_ENV_VAR)
    return Path(raw).expanduser() if raw else None


def _top_up_deployer(algod, deployer: str) -> None:
    have = _spendable(algod, deployer)
    if have >= NEED_SPENDABLE:
        logger.info(f"  deployer spendable {have / 1e6:.4f} ALGO, no top-up")
        return
    funder_env = funder_env_path()
    if funder_env is None:
        raise SystemExit(
            f"Deployer spendable {have / 1e6:.4f} ALGO and no funder configured. "
            f"Set {FUNDER_ENV_VAR} to a file holding DEPLOYER_MNEMONIC, or pass "
            f"--funder-env, or fund the deployer directly."
        )
    if not funder_env.is_file():
        raise SystemExit(f"Funder env {funder_env} does not exist")
    vals = dotenv_values(funder_env)
    mnemonic = vals.get("DEPLOYER_MNEMONIC")
    if not mnemonic:
        raise SystemExit(f"{funder_env} has no DEPLOYER_MNEMONIC")
    secret = to_private_key(mnemonic)
    sender = address_from_private_key(secret)
    agent_spend = _spendable(algod, sender)
    send_amt = min(TOP_UP_MICRO, max(0, agent_spend - 600_000))
    if send_amt < 1_000_000:
        raise SystemExit(
            f"Deployer spendable {have / 1e6:.4f} ALGO; agent cannot cover a top-up"
        )
    params = algod.suggested_params()
    txn = transaction.PaymentTxn(sender, params, deployer, send_amt)
    tx_id = algod.send_transaction(txn.sign(secret))
    transaction.wait_for_confirmation(algod, tx_id, 8)
    logger.info(f"  topped deployer +{send_amt / 1e6:.2f} ALGO from {sender}")
    logger.info(f"  {_link(tx_id)}")


def _params(extra: bool = False, sender: str | None = None) -> algokit_utils.CommonAppCallParams:
    kwargs: dict = {}
    if extra:
        kwargs["extra_fee"] = INNER
    if sender is not None:
        kwargs["sender"] = sender
    return algokit_utils.CommonAppCallParams(**kwargs)


def _enter(algorand, rain: RainClient, who: str, rain_id: int, gate: int, mode: int) -> str:
    needed = TICKET_MBR + INDEX_MBR if mode == ONE else TICKET_MBR
    try:
        result = rain.send.enter(
            args=EnterArgs(
                mbr_payment=_pay(algorand, who, rain.app_address, needed),
                rain_id=rain_id,
                gate_asset=gate,
            ),
            params=_params(extra=True, sender=who),
        )
        logger.info(f"  entered rain {rain_id}  {_link(_txid(result))}")
        return _txid(result)
    except Exception as exc:
        text = str(exc)
        if "Already entered" in text:
            logger.info(f"  already in rain {rain_id}")
            return ""
        raise


def _claim(rain: RainClient, who: str, rain_id: int, gate: int) -> tuple[int, str]:
    result = rain.send.claim(
        args=ClaimArgs(rain_id=rain_id, gate_asset=gate),
        params=_params(extra=True, sender=who),
    )
    amount = int(result.abi_return or 0)
    tx_id = _txid(result)
    logger.info(f"  claimed {amount} from rain {rain_id}  {_link(tx_id)}")
    return amount, tx_id


def _owed(rain: RainClient, rain_id: int, who: str) -> int:
    return int(
        rain.send.allocation_of(args=AllocationOfArgs(rain_id=rain_id, who=who)).abi_return or 0
    )


def _draw(rain: RainClient) -> tuple[int, str]:
    # DRAW_SCAN is 4. Two box *writes* of a RainRec plus the other scans
    # can exceed the 700 opcode budget; extra_fee is how the working
    # ABI draw gets more. Do not mark two heavy rains due at once.
    result = rain.send.draw(
        params=algokit_utils.CommonAppCallParams(extra_fee=DRAW_FEE),
    )
    n = int(result.abi_return or 0)
    tx_id = _txid(result)
    logger.info(f"  draw fired {n}  {_link(tx_id)}")
    return n, tx_id


def _fire_and_claim_corvid(rain: RainClient, who: str, rain_id: int, proofs: list[str]) -> int:
    rec = rain.send.rain_of(args=RainOfArgs(rain_id=rain_id)).abi_return
    owed = _owed(rain, rain_id, who)
    if rec.draw_id > 0 and owed == 0:
        logger.info(f"  rain {rain_id} already fired and claimed")
        return rec.cumulative if rec.mode == SPLIT else DAILY_DRIP
    rain.send.set_rain(
        args=SetRainArgs(rain_id=rain_id, drip=DAILY_DRIP, interval_rounds=MIN_INTERVAL_ROUNDS)
    )
    try:
        n, tx_id = _draw(rain)
        proofs.append(_link(tx_id))
        amount, claim_tx = _claim(rain, who, rain_id, CORVID_NFT)
        proofs.append(_link(claim_tx))
        if amount <= 0:
            raise SystemExit(f"rain {rain_id} claim paid 0")
        return amount
    finally:
        rain.send.set_rain(args=SetRainArgs(rain_id=rain_id, drip=DAILY_DRIP, interval_rounds=DAILY_ROUNDS))
        logger.info(f"  restored rain {rain_id} to daily cadence")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--funder-env",
        type=Path,
        default=None,
        help=(
            f"file holding DEPLOYER_MNEMONIC for a funder, used only when the "
            f"deployer is short (default: ${FUNDER_ENV_VAR})"
        ),
    )
    args = parser.parse_args(argv)
    if args.funder_env is not None:
        global _FUNDER_OVERRIDE
        _FUNDER_OVERRIDE = args.funder_env.expanduser()
    if args.network != net.TESTNET:
        raise SystemExit("This proof is TestNet only.")

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    who = deployer.address

    logger.info("── Accounts ──")
    logger.info(f"  deployer {who}")
    _top_up_deployer(algod, who)
    logger.info(f"  spendable { _spendable(algod, who) / 1e6:.4f} ALGO")

    rain = RainClient(algorand=algorand, app_id=HUB, default_sender=who, default_signer=deployer.signer)
    proofs: list[str] = []

    logger.info("── Fund existing Corvid rains ──")
    rec1 = rain.send.rain_of(args=RainOfArgs(rain_id=1)).abi_return
    if rec1.pot < 500_000:
        result = rain.send.deposit(
            args=DepositArgs(payment=_pay(algorand, who, rain.app_address, 450_000), rain_id=1)
        )
        logger.info(f"  rain 1 pot +0.45 ALGO  {_link(_txid(result))}")
        proofs.append(_link(_txid(result)))
    rec2 = rain.send.rain_of(args=RainOfArgs(rain_id=2)).abi_return
    if rec2.pot < 500_000:
        result = rain.send.deposit(
            args=DepositArgs(payment=_pay(algorand, who, rain.app_address, 500_000), rain_id=2)
        )
        logger.info(f"  rain 2 pot +0.50 ALGO  {_link(_txid(result))}")
        proofs.append(_link(_txid(result)))

    logger.info("── Enter Corvid-gated rains ──")
    _enter(algorand, rain, who, 1, CORVID_NFT, SPLIT)
    _enter(algorand, rain, who, 2, CORVID_NFT, WAVE)

    next_id = int(rain.state.global_state.next_rain_id)
    if next_id >= 4:
        one_id, asa_id = 3, 4
        prize = int(rain.send.rain_of(args=RainOfArgs(rain_id=asa_id)).abi_return.prize_asset)
        logger.info(f"── Reusing short rains {one_id} (ONE) and {asa_id} (ASA {prize}) ──")
    else:
        logger.info("── ASA prize ──")
        prize = algorand.send.asset_create(
            algokit_utils.AssetCreateParams(
                sender=who,
                total=1_000_000,
                decimals=0,
                asset_name="Rain Drops",
                unit_name="DROP",
                manager="",
                freeze="",
                clawback="",
            )
        ).asset_id
        logger.info(f"  minted ASA {prize}  {EXPLORER}/asset/{prize}")
        rain.send.opt_in_prize_asset(
            args=OptInPrizeAssetArgs(
                prize=prize,
                mbr_payment=_pay(algorand, who, rain.app_address, ASSET_OPT_IN_MBR),
            ),
            params=_params(extra=True),
        )
        algorand.send.asset_opt_in(algokit_utils.AssetOptInParams(sender=who, asset_id=prize))

        logger.info("── Short-interval rains (first fire waits 10 rounds) ──")
        one_id = rain.send.create_rain(
            args=CreateRainArgs(
                mbr_payment=_pay(algorand, who, rain.app_address, RAIN_BOX_MBR),
                label=_label("live ALGO one"),
                gate_creator=ZERO_ADDRESS,
                prize_asset=0,
                drip=DAILY_DRIP,
                interval_rounds=MIN_INTERVAL_ROUNDS,
                mode=ONE,
                wave_cap=0,
            ),
            params=_params(extra=True),
        ).abi_return
        asa_id = rain.send.create_rain(
            args=CreateRainArgs(
                mbr_payment=_pay(algorand, who, rain.app_address, RAIN_BOX_MBR),
                label=_label("live ASA split"),
                gate_creator=CORVID_MINTER,
                prize_asset=prize,
                drip=1_000,
                interval_rounds=MIN_INTERVAL_ROUNDS,
                mode=SPLIT,
                wave_cap=0,
            ),
            params=_params(extra=True),
        ).abi_return
        logger.info(f"  ONE ALGO rain {one_id}, ASA SPLIT rain {asa_id}")
        rain.send.deposit(
            args=DepositArgs(payment=_pay(algorand, who, rain.app_address, 200_000), rain_id=one_id)
        )
        rain.send.deposit_asset(
            args=DepositAssetArgs(
                transfer=algorand.create_transaction.asset_transfer(
                    algokit_utils.AssetTransferParams(
                        sender=who,
                        receiver=rain.app_address,
                        asset_id=prize,
                        amount=5_000,
                    )
                ),
                rain_id=asa_id,
            )
        )
        start = algod.status()["last-round"]
        net.wait_for_round(algorand, start + MIN_INTERVAL_ROUNDS)

    _enter(algorand, rain, who, one_id, 0, ONE)
    _enter(algorand, rain, who, asa_id, CORVID_NFT, SPLIT)

    logger.info("── Draw short rains (ONE + ASA); daily rains stay on their own clock ──")
    rec_asa0 = rain.send.rain_of(args=RainOfArgs(rain_id=asa_id)).abi_return
    rec_one0 = rain.send.rain_of(args=RainOfArgs(rain_id=one_id)).abi_return
    if rec_asa0.draw_id == 0 or rec_one0.draw_id == 0 or rec_one0.prize_locked > 0:
        n, tx_id = _draw(rain)
        proofs.append(_link(tx_id))
    else:
        logger.info("  short rains already fired")

    logger.info("── Claim ASA ──")
    asa_amt = _owed(rain, asa_id, who)
    if asa_amt > 0:
        asa_amt, asa_tx = _claim(rain, who, asa_id, CORVID_NFT)
        proofs.append(_link(asa_tx))
    else:
        rec_asa = rain.send.rain_of(args=RainOfArgs(rain_id=asa_id)).abi_return
        if rec_asa.draw_id == 0:
            raise SystemExit("ASA rain never fired")
        logger.info("  ASA already claimed")
        asa_amt = rec_asa.cumulative

    rec_one = rain.send.rain_of(args=RainOfArgs(rain_id=one_id)).abi_return
    if rec_one.prize_locked == 0 and _owed(rain, one_id, who) == 0 and rec_one.draw_id == 0:
        n, tx_id = _draw(rain)
        proofs.append(_link(tx_id))
        rec_one = rain.send.rain_of(args=RainOfArgs(rain_id=one_id)).abi_return
    if rec_one.prize_locked > 0:
        resolve_at = rec_one.commit_round + 1
        logger.info(f"── ONE: wait for commit round {rec_one.commit_round} then resolve ──")
        net.wait_for_round(algorand, resolve_at)
        resolved = rain.send.resolve(
            args=ResolveArgs(rain_id=one_id),
            params=_params(extra=True),
        )
        logger.info(f"  resolved index {resolved.abi_return}  {_link(_txid(resolved))}")
        proofs.append(_link(_txid(resolved)))
    one_amt = _owed(rain, one_id, who)
    if one_amt > 0:
        one_amt, one_tx = _claim(rain, who, one_id, 0)
        proofs.append(_link(one_tx))
    elif rec_one.draw_id == 0:
        raise SystemExit("ONE rain never fired")
    else:
        logger.info("  ONE already claimed")
        one_amt = DAILY_DRIP

    logger.info("── Fire Corvid daily, then GM, one rain per draw ──")
    algo_amt = _fire_and_claim_corvid(rain, who, 1, proofs)
    wave_amt = _fire_and_claim_corvid(rain, who, 2, proofs)

    logger.info("Live rain proof passed.")
    logger.info(f"  Hub {HUB}  {EXPLORER}/application/{HUB}")
    logger.info(f"  Corvid SPLIT claimed {algo_amt} µALGO")
    logger.info(f"  Corvid WAVE claimed {wave_amt} µALGO")
    logger.info(f"  ASA {prize} claimed {asa_amt}")
    logger.info(f"  ONE claimed {one_amt} µALGO")
    logger.info("  Explorer:")
    for url in proofs:
        logger.info(f"    {url}")


if __name__ == "__main__":
    main()
