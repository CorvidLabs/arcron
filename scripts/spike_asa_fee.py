"""Spike: what would ASA-denominated upkeep fees cost Archon?

Issue #9 wants keeper rewards denominated in an ASA (CORVID). Three things
decide the shape, and none of them can be settled by argument:

1. **Can the target pay the keeper itself?** If a target could, Archon would
   never need to hold an ASA and #9 would collapse to documentation. Part A
   asks the target who it thinks called it, directly and through an upkeep.
2. **What does holding an ASA cost the app account?** Every asset an app can
   hold raises its minimum balance permanently, and that ALGO has to come from
   somewhere. Part B measures it rather than quoting the constant.
3. **What do the fields and the payout path cost?** Part C derives an
   ASA-fee variant from `smart_contracts/keeper/contract.py`, compiles it,
   deploys it and runs an ASA-paying upkeep end to end — including the case
   where the keeper is not opted in to the asset, which is the failure mode
   the design has to answer.

The variant is generated at run time and compiled to a temporary directory,
so it is never a second contract to maintain and the numbers stay honest as
the keeper changes.

Run:  poetry run python -m scripts.spike_asa_fee [--network localnet]
"""

import argparse
import base64
import json
import logging
import pathlib
import re
import subprocess
import tempfile

import algokit_utils
from algosdk import abi, transaction

from scripts import network as net
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse
from smart_contracts.resource_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
KEEPER_SOURCE = REPO / "smart_contracts" / "keeper" / "contract.py"
KEEPER_SPEC = REPO / "smart_contracts" / "artifacts" / "keeper" / "Keeper.arc56.json"

FEE = 4_000
ASSET_FEE = 250_000
INTERVAL = 10
EXECUTE_FEE = 10_000
PAGE_BYTES = 2_048
BOX_NAME_BYTES = 9
# The Upkeep struct's ARC-4 head, as the contract stands today.
HEAD_BYTES = 106


# ---------------------------------------------------------------- variant

STRUCT_FIELDS = """    fee_asset: arc4.UInt64
    asset_fee: arc4.UInt64
    asset_balance: arc4.UInt64
"""

EXTRA_METHODS = '''
    @abimethod()
    def opt_in_asset(self, mbr_payment: gtxn.PaymentTransaction, asset: Asset) -> UInt64:
        """Let the app account hold `asset`. Permissionless; MBR is a deposit."""
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= ASSET_OPT_IN_MBR, "MBR payment too small"
        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()
        return UInt64(ASSET_OPT_IN_MBR)

    @abimethod()
    def top_up_asset(
        self, upkeep_id: UInt64, asset_funding: gtxn.AssetTransferTransaction
    ) -> UInt64:
        """Add ASA to an upkeep's bonus escrow; returns the new asset balance."""
        box = Box(Upkeep, key=op.concat(b"u", op.itob(upkeep_id)))
        assert box, "Upkeep not found"
        upkeep = box.value.copy()
        assert upkeep.fee_asset.as_uint64() > 0, "Upkeep is not asset-denominated"
        assert (
            asset_funding.asset_receiver == Global.current_application_address
        ), "Asset funding must go to the app account"
        assert (
            asset_funding.xfer_asset.id == upkeep.fee_asset.as_uint64()
        ), "Wrong asset"
        new_asset_balance: UInt64 = (
            upkeep.asset_balance.as_uint64() + asset_funding.asset_amount
        )
        box.value = upkeep._replace(
            asset_balance=arc4.UInt64(new_asset_balance),
            call_data=upkeep.call_data.copy(),
        )
        return new_asset_balance
'''

EXECUTE_DECISION = """        times: UInt64 = upkeep.times_executed.as_uint64() + 1
        # The ASA bonus is paid only when there is one, the escrow can cover it
        # and the keeper can receive it. A keeper who has not opted in is not a
        # failed execution — it forfeits the bonus and still takes the ALGO fee.
        asset_id: UInt64 = upkeep.fee_asset.as_uint64()
        asset_fee: UInt64 = upkeep.asset_fee.as_uint64()
        asset_balance: UInt64 = upkeep.asset_balance.as_uint64()
        pays_asset = (
            asset_id > 0
            and asset_balance >= asset_fee
            and Txn.sender.is_opted_in(Asset(asset_id))
        )
        if pays_asset:
            asset_balance = asset_balance - asset_fee
"""

CANCEL_GUARD = """        upkeep = box.value.copy()
        assert upkeep.creator.native == Txn.sender, "Only the creator can cancel"
        # The unspent bonus goes back too, which the creator can only receive
        # if they hold the asset. Checked before anything is refunded.
        cancel_asset: UInt64 = upkeep.fee_asset.as_uint64()
        cancel_amount: UInt64 = upkeep.asset_balance.as_uint64()
        if cancel_amount > 0:
            assert Txn.sender.is_opted_in(
                Asset(cancel_asset)
            ), "Opt in to the fee asset before cancelling"
"""

CANCEL_REFUND = """        itxn.Payment(receiver=Txn.sender, amount=refund).submit()
        if cancel_amount > 0:
            itxn.AssetTransfer(
                xfer_asset=Asset(cancel_asset),
                asset_receiver=Txn.sender,
                asset_amount=cancel_amount,
            ).submit()
        return refund"""

ASSET_PAYOUT = """        itxn.Payment(receiver=Txn.sender, amount=fee).submit()
        if pays_asset:
            itxn.AssetTransfer(
                xfer_asset=Asset(asset_id),
                asset_receiver=Txn.sender,
                asset_amount=asset_fee,
            ).submit()
        return next_due"""


def _variant_source(source: str | None = None) -> str:
    """Today's keeper, plus an ASA bonus alongside the ALGO fee.

    `source` lets another patch run first, which is how the whole 1.0 batch is
    measured in Part D.
    """
    source = KEEPER_SOURCE.read_text() if source is None else source

    def swap(old: str, new: str) -> None:
        nonlocal source
        if old not in source:
            raise SystemExit(f"keeper contract has moved on; cannot patch:\n{old!r}")
        source = source.replace(old, new, 1)

    swap(
        "from algopy import (\n    ARC4Contract,\n    Application,\n",
        "from algopy import (\n    ARC4Contract,\n    Application,\n    Asset,\n",
    )
    # Three uint64 fields is 24 more bytes of head, whatever the contract's
    # fixed component happens to be today.
    fixed = re.search(r"BOX_MBR_FIXED = 2_500 \+ 400 \* (\d+)", source)
    if not fixed:
        raise SystemExit("keeper contract has moved on; cannot find BOX_MBR_FIXED")
    swap(
        fixed.group(0),
        f"BOX_MBR_FIXED = 2_500 + 400 * {int(fixed.group(1)) + 24}\n"
        "# What an app account's minimum balance rises by per asset it can hold.\n"
        "ASSET_OPT_IN_MBR = 100_000",
    )
    swap("    times_executed: arc4.UInt64\n", "    times_executed: arc4.UInt64\n" + STRUCT_FIELDS)
    swap(REG_ANCHOR, "        fee_asset: UInt64,\n        asset_fee: UInt64,\n" + REG_ANCHOR)
    swap(
        "            times_executed=arc4.UInt64(0),\n",
        "            times_executed=arc4.UInt64(0),\n"
        "            fee_asset=arc4.UInt64(fee_asset),\n"
        "            asset_fee=arc4.UInt64(asset_fee),\n"
        "            asset_balance=arc4.UInt64(0),\n",
    )
    swap("        times: UInt64 = upkeep.times_executed.as_uint64() + 1\n", EXECUTE_DECISION)
    swap(
        "            times_executed=arc4.UInt64(times),\n",
        "            times_executed=arc4.UInt64(times),\n            asset_balance=arc4.UInt64(asset_balance),\n",
    )
    swap(
        "        itxn.Payment(receiver=Txn.sender, amount=fee).submit()\n        return next_due",
        ASSET_PAYOUT,
    )
    swap(
        '        upkeep = box.value.copy()\n'
        '        assert upkeep.creator.native == Txn.sender, "Only the creator can cancel"\n',
        CANCEL_GUARD,
    )
    swap(
        "        itxn.Payment(receiver=Txn.sender, amount=refund).submit()\n        return refund",
        CANCEL_REFUND,
    )
    return source.rstrip() + "\n" + EXTRA_METHODS



# The register signature and the struct construction are both extended by more
# than one patch, so both anchor on something the other patches leave alone.
REG_ANCHOR = '''    ) -> UInt64:
        """Register an upkeep'''


# ------------------------------------------- alternative: name the keeper

# Part A shows a target cannot pay the keeper because it never learns who the
# keeper is. The only fix is for Archon to tell it — append the keeper's
# address as a final app arg. That is priced here rather than dismissed,
# because it is the one design that needs Archon to hold no asset at all.


def _keeper_arg_source(max_args: int = 4) -> str:
    """The multi-arg keeper, optionally naming the keeper in the call."""
    from scripts.spike_multiarg import _fan_out, _variant_source as multi_arg_source

    source = multi_arg_source(max_args)

    def swap(old: str, new: str) -> None:
        nonlocal source
        if old not in source:
            raise SystemExit(f"cannot patch:\n{old!r}")
        source = source.replace(old, new, 1)

    swap("    times_executed: arc4.UInt64\n", "    times_executed: arc4.UInt64\n    names_keeper: arc4.UInt64\n")
    swap(REG_ANCHOR, "        names_keeper: UInt64,\n" + REG_ANCHOR)
    swap(
        "            times_executed=arc4.UInt64(0),\n",
        "            times_executed=arc4.UInt64(0),\n            names_keeper=arc4.UInt64(names_keeper),\n",
    )

    def branches(indent: str, extra: str) -> list[str]:
        lines = []
        for count in range(1, max_args + 1):
            args = ", ".join(f"upkeep.call_args[{i}].native" for i in range(count))
            lines += [
                f"{indent}{'if' if count == 1 else 'elif'} arg_count == {count}:",
                f"{indent}    itxn.ApplicationCall(",
                f"{indent}        app_id=target,",
                f"{indent}        app_args=({args}{extra},),",
                f"{indent}        on_completion=OnCompleteAction.NoOp,",
                f"{indent}    ).submit()",
            ]
        lines += [f"{indent}else:", f'{indent}    assert False, "Unsupported argument count"']
        return lines

    doubled = [
        "        target: UInt64 = upkeep.target_app.as_uint64()",
        "        arg_count: UInt64 = upkeep.call_args.length",
        "        if upkeep.names_keeper.as_uint64() > 0:",
        "            keeper: Bytes = arc4.Address(Txn.sender).bytes",
        *branches("            ", ", keeper"),
        "        else:",
        *branches("            ", ""),
    ]
    swap(_fan_out(max_args), "\n".join(doubled))
    swap("    UInt64,\n    arc4,", "    Bytes,\n    UInt64,\n    arc4,")
    return source



def _compile(source: str, stem: str, out_root: pathlib.Path) -> pathlib.Path:
    source_path = out_root / f"{stem}.py"
    source_path.write_text(source)
    out_dir = out_root / f"{stem}_out"
    result = subprocess.run(
        ["puyapy", "--out-dir", str(out_dir), str(source_path)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    specs = list(out_dir.glob("*.arc56.json")) if out_dir.exists() else []
    if not specs:
        raise SystemExit(f"puyapy failed for {stem}:\n{result.stdout}\n{result.stderr}")
    return specs[0]


def _approval_bytes(spec_path: pathlib.Path) -> int:
    return len(base64.b64decode(json.loads(spec_path.read_text())["byteCode"]["approval"]))


# ---------------------------------------------------------------- helpers


def _factory(algorand, spec_path: pathlib.Path, sender: str, name: str):
    return algokit_utils.AppFactory(
        algokit_utils.AppFactoryParams(
            algorand=algorand,
            app_spec=spec_path.read_text(),
            app_name=name,
            default_sender=sender,
        )
    )


def _min_balance(algorand, address: str) -> int:
    return algorand.client.algod.account_info(address)["min-balance"]


def _global_state(algorand, app_id: int) -> dict[bytes, int | bytes]:
    info = algorand.client.algod.application_info(app_id)
    state: dict[bytes, int | bytes] = {}
    for entry in info["params"].get("global-state", []):
        value = entry["value"]
        state[base64.b64decode(entry["key"])] = (
            value["uint"] if value["type"] == 2 else base64.b64decode(value["bytes"])
        )
    return state


def _execute(algorand, app_id: int, account, upkeep_id: int, target_app: int, assets=()) -> None:
    method = abi.Method.from_signature("execute(uint64)uint64")
    params = algorand.client.algod.suggested_params()
    params.flat_fee = True
    params.fee = EXECUTE_FEE
    txn = transaction.ApplicationNoOpTxn(
        sender=account.address,
        sp=params,
        index=app_id,
        app_args=[method.get_selector(), upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[target_app],
        foreign_assets=list(assets),
    )
    signed = account.signer.sign_transactions([txn], [0])
    txid = algorand.client.algod.send_transactions(signed)
    transaction.wait_for_confirmation(algorand.client.algod, txid, 6)


def _asset_balance(algorand, address: str, asset_id: int) -> int | None:
    info = algorand.client.algod.account_info(address)
    for holding in info.get("assets", []):
        if holding["asset-id"] == asset_id:
            return holding["amount"]
    return None


# ---------------------------------------------------------------- parts


def part_a(algorand, deployer, keeper, probe, pulse) -> None:
    """Who does the target think called it?"""
    selector = abi.Method.from_signature("report_caller()address").get_selector()

    probe.send.report_caller()
    direct = _global_state(algorand, probe.app_id).get(b"last_caller", b"")

    first_valid = algorand.client.algod.status()["last-round"]

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=keeper.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
                first_valid_round=first_valid,
                last_valid_round=first_valid + 1_000,
            )
        )

    upkeep_id = keeper.app_client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method=(
                "register(pay,pay,uint64,byte[],uint64,uint64,uint64,uint64)uint64"
            ),
            args=[
                payment(2_500 + 400 * (BOX_NAME_BYTES + HEAD_BYTES + 2 + len(selector))),
                payment(FEE * 3),
                probe.app_id,
                list(selector),
                INTERVAL,
                FEE,
                0,  # CATCH_UP
                0,  # no escalation
            ],
        )
    ).abi_return
    net.wait_for_round(algorand, algorand.client.algod.status()["last-round"] + INTERVAL + 1, deployer)
    _execute(algorand, keeper.app_id, deployer, upkeep_id, probe.app_id)
    via_keeper = _global_state(algorand, probe.app_id).get(b"last_caller", b"")

    from algosdk import encoding

    logger.info("")
    logger.info("Part A — who the target sees as its caller")
    logger.info(f"  called directly            {encoding.encode_address(direct)}")
    logger.info(f"  called through an upkeep   {encoding.encode_address(via_keeper)}")
    logger.info(f"  the keeper who sent it     {deployer.address}")
    logger.info(f"  Archon's app account       {keeper.app_address}")


def part_b(algorand, deployer) -> None:
    """What does holding an asset cost an app account?"""
    # A fresh app every run: a redeployed one still holds the previous run's
    # assets, and the base reading would be wrong.
    probe_spec = REPO / "smart_contracts" / "artifacts" / "resource_probe" / "ResourceProbe.arc56.json"
    probe, _ = _factory(algorand, probe_spec, deployer.address, "AssetMbrProbe").send.bare.create()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=probe.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=500_000),
        )
    )
    before = _min_balance(algorand, probe.app_address)
    readings = [("base app account", before, 0)]
    for index in range(2):
        asset_id = algorand.send.asset_create(
            algokit_utils.AssetCreateParams(sender=deployer.address, total=1_000_000)
        ).asset_id
        probe.send.call(
            algokit_utils.AppClientMethodCallParams(
                method="configure(address,uint64,uint64)void",
                args=[deployer.address, asset_id, probe.app_id],
            )
        )
        probe.send.call(
            algokit_utils.AppClientMethodCallParams(
                method="opt_in_to_asset()void",
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
                asset_references=[asset_id],
            )
        )
        now = _min_balance(algorand, probe.app_address)
        readings.append((f"holding {index + 1} asset(s)", now, now - before))

    logger.info("")
    logger.info("Part B — what an asset costs the app account")
    for label, value, delta in readings:
        logger.info(f"  {label:<22} min balance {value:>8}  (+{delta})")


def part_c(algorand, deployer, probe, out_root: pathlib.Path) -> None:
    """Field cost, program cost, and the un-opted-in keeper."""
    spec = _compile(_variant_source(), "keeper_asa", out_root)
    today = _approval_bytes(KEEPER_SPEC)
    variant = _approval_bytes(spec)

    logger.info("")
    logger.info("Part C — what the ASA path costs")
    logger.info(
        f"  approval program  {today} B -> {variant} B (+{variant - today}), "
        f"{-(-variant // PAGE_BYTES)} page(s)"
    )

    # A fresh app each run: the variant changes whenever the keeper does.
    client, _ = _factory(algorand, spec, deployer.address, "KeeperAsaFee").send.bare.create()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=300_000),
        )
    )
    asset_id = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(sender=deployer.address, total=100_000_000)
    ).asset_id

    first_valid = algorand.client.algod.status()["last-round"]

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
                first_valid_round=first_valid,
                last_valid_round=first_valid + 1_000,
            )
        )

    client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method="opt_in_asset(pay,uint64)uint64",
            args=[payment(100_000), asset_id],
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
            asset_references=[asset_id],
        )
    )

    selector = abi.Method.from_signature("report_budget()uint64").get_selector()
    upkeep_id = client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method=(
                "register(pay,pay,uint64,byte[],uint64,uint64,uint64,uint64,"
                "uint64,uint64)uint64"
            ),
            args=[
                payment(
                    2_500 + 400 * (BOX_NAME_BYTES + HEAD_BYTES + 24 + 2 + len(selector))
                ),
                payment(FEE * 3),
                probe.app_id,
                list(selector),
                INTERVAL,
                FEE,
                0,  # CATCH_UP
                0,  # no escalation
                asset_id,
                ASSET_FEE,
            ],
        )
    ).abi_return

    name = b"u" + upkeep_id.to_bytes(8, "big")
    box = algorand.client.algod.application_box_by_name(client.app_id, name)
    box_size = BOX_NAME_BYTES + len(base64.b64decode(box["value"]))
    logger.info(f"  box               {box_size} bytes, MBR {2_500 + 400 * box_size} µALGO")

    client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method="top_up_asset(uint64,axfer)uint64",
            args=[
                upkeep_id,
                algorand.create_transaction.asset_transfer(
                    algokit_utils.AssetTransferParams(
                        sender=deployer.address,
                        receiver=client.app_address,
                        asset_id=asset_id,
                        amount=ASSET_FEE * 4,
                    )
                ),
            ],
            box_references=[name],
        )
    )

    # Two fresh keepers. Neither advances rounds, so neither reading is
    # polluted by the poker transactions LocalNet needs.
    stranger = algorand.account.random()
    holder = algorand.account.random()
    for account in (stranger, holder):
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=account.address,
                amount=algokit_utils.AlgoAmount(micro_algo=1_000_000),
            )
        )
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=holder.address, asset_id=asset_id)
    )

    logger.info("")
    logger.info("  execution                     ALGO fee   ASA bonus")
    for label, account in (("keeper not opted in", stranger), ("keeper opted in", holder)):
        before_algo = algorand.client.algod.account_info(account.address)["amount"]
        before_asset = _asset_balance(algorand, account.address, asset_id)
        net.wait_for_round(
            algorand, algorand.client.algod.status()["last-round"] + INTERVAL + 1, deployer
        )
        _execute(algorand, client.app_id, account, upkeep_id, probe.app_id, assets=[asset_id])
        after_algo = algorand.client.algod.account_info(account.address)["amount"]
        after_asset = _asset_balance(algorand, account.address, asset_id)
        gained = (after_asset or 0) - (before_asset or 0)
        logger.info(
            f"  {label:<28} {after_algo - before_algo + EXECUTE_FEE:>8}   "
            f"{gained if before_asset is not None else 'cannot receive':>11}"
        )


def part_d(out_root: pathlib.Path) -> None:
    """The number the batching decision needs: the whole 1.0 batch, compiled."""
    from scripts.spike_multiarg import _variant_source as multi_arg_source

    today = _approval_bytes(KEEPER_SPEC)
    rows = [("the contract today (#7 + #14)", today)]
    rows.append(("+ #9 (ASA bonus)", _approval_bytes(_compile(_variant_source(), "d_asa", out_root))))
    rows.append(
        ("+ #8 (4 arguments)", _approval_bytes(_compile(multi_arg_source(4), "d_args", out_root)))
    )
    rows.append(
        (
            "the whole 1.0 batch",
            _approval_bytes(
                _compile(multi_arg_source(4, source=_variant_source()), "d_all", out_root)
            ),
        )
    )

    logger.info("")
    logger.info("Part D — program size, stacked")
    logger.info(f"{'contract':<34} {'approval':>9} {'pages':>6} {'page headroom':>14}")
    for label, size in rows:
        pages = -(-size // PAGE_BYTES)
        logger.info(f"{label:<34} {size:>9} {pages:>6} {pages * PAGE_BYTES - size:>14}")
    # With everything else in, the fan-out ceiling is the only dial left, so
    # price the whole batch at each setting rather than the fan-out alone.
    logger.info("")
    logger.info("  the whole batch, by fan-out ceiling")
    for ceiling in (2, 3, 4, 6):
        size = _approval_bytes(
            _compile(
                multi_arg_source(ceiling, source=_variant_source()),
                f"d_all_{ceiling}",
                out_root,
            )
        )
        pages = -(-size // PAGE_BYTES)
        headroom = pages * PAGE_BYTES - size
        fits = "" if pages == 1 else "  ← second page"
        logger.info(f"    ceiling {ceiling}: {size:>5} B, {headroom:>5} spare{fits}")

    alternative = _approval_bytes(_compile(_keeper_arg_source(4), "d_named", out_root))
    pages = -(-alternative // PAGE_BYTES)
    logger.info("")
    logger.info(
        f"  alternative — #8 plus naming the keeper in the call, no ASA held: "
        f"{alternative} B, {pages} page(s)"
    )
    logger.info("")
    logger.info(
        "  Every row is compiled, not estimated: #7 and #14 are in the contract,"
    )
    logger.info("  and #8 and #9 are patched onto it and built by puyapy.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    logger.info(f"algod build: {algorand.client.algod.versions()['build']}")

    keeper = deploy_keeper()
    probe = deploy_probe()
    pulse = deploy_pulse()

    with tempfile.TemporaryDirectory(prefix="archon-asa-") as tmp:
        out_root = pathlib.Path(tmp)
        part_a(algorand, deployer, keeper, probe, pulse)
        part_b(algorand, deployer)
        part_c(algorand, deployer, probe, out_root)
        part_d(out_root)


if __name__ == "__main__":
    main()
