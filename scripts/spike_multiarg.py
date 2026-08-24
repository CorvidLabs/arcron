"""Spike: what would a multi-argument call shape cost Archon?

Archon executes exactly one call shape — a NoOp app call carrying a single app
arg. An ARC-4 method with arguments of its own needs the selector *and* each
argument in an app arg of its own, so today only zero-argument hooks are
reachable. Issue #8 proposes storing the whole argument list instead.

This measures the three things that decide whether that is worth a struct
change, none of which can be settled by reading the compiler docs:

1. **Whether the obvious implementation is even correct.** Building the args
   array in a loop compiles. It does not work — Puya hoists the inner
   transaction out of the loop, so only the last assignment survives. Part B
   proves that on-chain rather than by reading TEAL.
2. **Program size.** The correct construction is a fan-out over the argument
   count, and each branch builds a larger tuple than the last. Part A compiles
   the keeper at a range of ceilings and reports where it stops fitting in one
   2,048-byte program page.
3. **Runtime cost.** Decoding `byte[][]` spends opcode budget that would
   otherwise reach the target, and the extra encoding bytes raise box MBR.
   Part C registers the same hook through today's keeper and through the
   variant and compares, then calls a hook that takes real arguments — which
   today's keeper cannot reach at all.

The variant is derived from `smart_contracts/keeper/contract.py` at run time
and compiled to a temporary directory, so it is never a second contract to
maintain and the numbers stay honest as the keeper changes.

Run:  poetry run python -m scripts.spike_multiarg [--network localnet]
"""

import argparse
import base64
import json
import logging
import pathlib
import subprocess
import tempfile

import algokit_utils
from algosdk import abi, transaction

from scripts import network as net
from smart_contracts.resource_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
KEEPER_SOURCE = REPO / "smart_contracts" / "keeper" / "contract.py"
KEEPER_SPEC = REPO / "smart_contracts" / "artifacts" / "keeper" / "Keeper.arc56.json"

FEE = 4_000
INTERVAL = 10
# The group carries Archon's inner call to the target and the keeper's payment.
EXECUTE_FEE = 8_000
# One TEAL program page. Crossing it costs the deployer another 100,000 µALGO
# of app minimum balance, permanently.
PAGE_BYTES = 2_048
# Ceilings worth pricing: 1 is the encoding change alone, 2 is "selector plus
# one argument", 16 is the protocol's own MaxAppArgs.
CEILINGS = (1, 2, 3, 4, 6, 8, 16)
# The ceiling Part C measures at runtime — 3, the setting 1.0 takes: a
# selector plus two ABI arguments, chosen because it is what keeps the whole
# batch on one program page (see `scripts/spike_asa_fee.py`, Part D).
MEASURED_CEILING = 3

BOX_NAME_BYTES = 9
UPKEEP_HEAD_BYTES = 106


# ---------------------------------------------------------------- variants


def _variant_source(max_args: int, source: str | None = None) -> str:
    """Today's keeper, with `call_data: byte[]` replaced by `call_args: byte[][]`.

    Text substitution rather than a checked-in copy: if the keeper changes and
    a substitution stops matching, this raises instead of quietly measuring a
    contract nobody has. `source` lets another spike stack its own patch first,
    which is how the combined cost of #8 and #9 is measured.
    """
    source = KEEPER_SOURCE.read_text() if source is None else source
    substitutions = [
        ("    call_data: arc4.DynamicBytes\n", "    call_args: arc4.DynamicArray[arc4.DynamicBytes]\n"),
        ("        call_data: arc4.DynamicBytes,\n", "        call_args: arc4.DynamicArray[arc4.DynamicBytes],\n"),
        ("        size = call_data.native.length\n", "        size = call_args.bytes.length\n"),
        ("            call_data=call_data.copy(),\n", "            call_args=call_args.copy(),\n"),
        (
            "            balance=arc4.UInt64(new_balance), call_data=upkeep.call_data.copy()\n",
            "            balance=arc4.UInt64(new_balance), call_args=upkeep.call_args.copy()\n",
        ),
        ("            + 400 * upkeep.call_data.native.length\n", "            + 400 * upkeep.call_args.bytes.length\n"),
        ("            call_data=upkeep.call_data.copy(),\n", "            call_args=upkeep.call_args.copy(),\n"),
    ]
    for old, new in substitutions:
        if old not in source:
            raise SystemExit(f"keeper contract has moved on; this spike cannot patch:\n{old!r}")
        source = source.replace(old, new)

    call = """        itxn.ApplicationCall(
            app_id=upkeep.target_app.as_uint64(),
            app_args=(upkeep.call_data.native,),
            on_completion=OnCompleteAction.NoOp,
        ).submit()"""
    if call not in source:
        raise SystemExit("keeper contract has moved on; this spike cannot patch the inner call")
    return source.replace(call, _fan_out(max_args))


def _fan_out(max_args: int) -> str:
    """The only construction that works: one static branch per argument count."""
    lines = [
        "        target: UInt64 = upkeep.target_app.as_uint64()",
        "        arg_count: UInt64 = upkeep.call_args.length",
    ]
    for count in range(1, max_args + 1):
        args = ", ".join(f"upkeep.call_args[{i}].native" for i in range(count))
        lines += [
            f"        {'if' if count == 1 else 'elif'} arg_count == {count}:",
            "            itxn.ApplicationCall(",
            "                app_id=target,",
            f"                app_args=({args},),",
            "                on_completion=OnCompleteAction.NoOp,",
            "            ).submit()",
        ]
    lines += ["        else:", '            assert False, "Unsupported argument count"']
    return "\n".join(lines)


LOOP_SOURCE = '''# pyright: reportMissingModuleSource=false
"""The implementation of a multi-arg call that looks obvious and is wrong."""

from algopy import ARC4Contract, OnCompleteAction, UInt64, arc4, itxn, urange
from algopy.arc4 import abimethod


class LoopArgs(ARC4Contract):
    @abimethod()
    def call_it(self, target: UInt64, args: arc4.DynamicArray[arc4.DynamicBytes]) -> UInt64:
        txn = itxn.ApplicationCall(app_id=target, on_completion=OnCompleteAction.NoOp)
        for index in urange(args.length):
            txn.set(app_args=(args[index].native,))
        txn.submit()
        return UInt64(1)
'''


def _compile(source: str, stem: str, out_root: pathlib.Path) -> pathlib.Path:
    """Compile a contract to its own directory; returns the ARC-56 spec path."""
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
    spec = json.loads(spec_path.read_text())
    return len(base64.b64decode(spec["byteCode"]["approval"]))


# ---------------------------------------------------------------- chain


def _factory(algorand, spec_path: pathlib.Path, sender: str, name: str):
    return algokit_utils.AppFactory(
        algokit_utils.AppFactoryParams(
            algorand=algorand,
            app_spec=spec_path.read_text(),
            app_name=name,
            default_sender=sender,
        )
    )


def _encode_args(parts: list[bytes]) -> bytes:
    """The ARC-4 `byte[][]` an upkeep would store."""
    return abi.ABIType.from_string("byte[][]").encode([list(part) for part in parts])


def _predicted_mbr(parts: list[bytes], multi: bool) -> int:
    tail = len(_encode_args(parts)) if multi else 2 + len(parts[0])
    return 2_500 + 400 * (BOX_NAME_BYTES + UPKEEP_HEAD_BYTES + tail)


def _actual_mbr(algorand, app_id: int, upkeep_id: int) -> tuple[int, int]:
    """Derive the box cost from the box itself, never from the formula."""
    name = b"u" + upkeep_id.to_bytes(8, "big")
    box = algorand.client.algod.application_box_by_name(app_id, name)
    size = BOX_NAME_BYTES + len(base64.b64decode(box["value"]))
    return size, 2_500 + 400 * size


def _register(algorand, client, deployer, target_app: int, parts: list[bytes], *, multi: bool) -> int:
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

    signature = (
        "register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64)uint64"
        if multi
        else "register(pay,pay,uint64,byte[],uint64,uint64,uint64,uint64)uint64"
    )
    # The variant charges MBR from a fixed component that is two bytes stale
    # for the array encoding; overpay, since the point here is what the box
    # actually costs, not what a draft formula thinks it costs.
    return client.send.call(
        algokit_utils.AppClientMethodCallParams(
            method=signature,
            args=[
                payment(_predicted_mbr(parts, multi) + 4_000),
                payment(FEE * 3),
                target_app,
                [list(part) for part in parts] if multi else list(parts[0]),
                INTERVAL,
                FEE,
                0,  # CATCH_UP
                0,  # no escalation
            ],
        )
    ).abi_return


def _execute(algorand, app_id: int, account, upkeep_id: int, target_app: int) -> None:
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
    )
    signed = account.signer.sign_transactions([txn], [0])
    txid = algorand.client.algod.send_transactions(signed)
    transaction.wait_for_confirmation(algorand.client.algod, txid, 6)


def _global_state(algorand, app_id: int) -> dict[bytes, int | bytes]:
    info = algorand.client.algod.application_info(app_id)
    state: dict[bytes, int | bytes] = {}
    for entry in info["params"].get("global-state", []):
        key = base64.b64decode(entry["key"])
        value = entry["value"]
        state[key] = value["uint"] if value["type"] == 2 else base64.b64decode(value["bytes"])
    return state


def _run_due(algorand, deployer, keeper_app: int, upkeep_id: int, target_app: int) -> None:
    due = algorand.client.algod.status()["last-round"] + INTERVAL + 1
    net.wait_for_round(algorand, due, deployer)
    _execute(algorand, keeper_app, deployer, upkeep_id, target_app)


# ---------------------------------------------------------------- parts


def part_a(out_root: pathlib.Path) -> list[tuple[int, int, int]]:
    """Compile the keeper at each ceiling and price the fan-out in bytes."""
    rows = [(0, _approval_bytes(KEEPER_SPEC), 1)]
    for ceiling in CEILINGS:
        spec = _compile(_variant_source(ceiling), f"keeper_{ceiling}", out_root)
        size = _approval_bytes(spec)
        rows.append((ceiling, size, -(-size // PAGE_BYTES)))
    logger.info("")
    logger.info("Part A — program size against the fan-out ceiling")
    logger.info(f"{'max args':>9}  {'approval':>9}  {'pages':>5}  vs today")
    baseline = rows[0][1]
    for ceiling, size, pages in rows:
        label = "today" if ceiling == 0 else str(ceiling)
        delta = "—" if ceiling == 0 else f"+{size - baseline}"
        logger.info(f"{label:>9}  {size:>9}  {pages:>5}  {delta}")
    return rows


def part_b(algorand, deployer, probe_app: int, out_root: pathlib.Path) -> None:
    """Show that a loop-built args array keeps only its last element."""
    spec = _compile(LOOP_SOURCE, "loop_args", out_root)
    client, _ = _factory(algorand, spec, deployer.address, "LoopArgs").send.bare.create()
    selector = abi.Method.from_signature("report_budget()uint64").get_selector()
    junk = b"\xde\xad\xbe\xef"

    logger.info("")
    logger.info("Part B — what a loop-built app_args array actually sends")
    # A rejection is the result here, not a failure; keep algokit's traceback
    # for it out of the transcript.
    noisy = logging.getLogger("algokit_utils")
    noisy.setLevel(logging.CRITICAL)
    for label, parts in (
        ("junk first, real selector last", [junk, selector]),
        ("real selector first, junk last", [selector, junk]),
    ):
        before = _global_state(algorand, probe_app).get(b"probes_run", 0)
        try:
            client.send.call(
                algokit_utils.AppClientMethodCallParams(
                    method="call_it(uint64,byte[][])uint64",
                    args=[probe_app, [list(part) for part in parts]],
                    app_references=[probe_app],
                    extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
                )
            )
            after = _global_state(algorand, probe_app).get(b"probes_run", 0)
            logger.info(f"  {label:<32} accepted — probe ran {after != before}")
        except Exception as exc:  # noqa: BLE001 — the rejection is the result
            text = str(exc).replace("\n", " ")
            index = text.find("err opcode")
            logger.info(f"  {label:<32} rejected — {text[index:index + 60] or text[:60]}")
    noisy.setLevel(logging.NOTSET)


def part_c(algorand, deployer, probe_app: int, out_root: pathlib.Path) -> None:
    """Price the encoding in box MBR and in budget the target does not get."""
    spec = _compile(_variant_source(MEASURED_CEILING), "keeper_measured", out_root)
    # Fresh apps each run: both contracts change whenever the keeper does, and
    # a redeployment against a stale one would compare the wrong bytecode.
    base, _ = _factory(algorand, KEEPER_SPEC, deployer.address, "KeeperToday").send.bare.create()
    multi, _ = _factory(algorand, spec, deployer.address, "KeeperMultiArg").send.bare.create()
    for client in (base, multi):
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=200_000),
            )
        )

    report = abi.Method.from_signature("report_budget()uint64").get_selector()
    absorb = abi.Method.from_signature("absorb(uint64,string)uint64")
    absorb_parts = [
        absorb.get_selector(),
        (7_777).to_bytes(8, "big"),
        abi.ABIType.from_string("string").encode("archon"),
    ]

    rows = []
    for label, client, parts, multi_flag in (
        ("today's keeper, zero-arg hook", base, [report], False),
        ("multi-arg keeper, zero-arg hook", multi, [report], True),
        ("multi-arg keeper, absorb(uint64,string)", multi, absorb_parts, True),
    ):
        upkeep_id = _register(algorand, client, deployer, probe_app, parts, multi=multi_flag)
        _run_due(algorand, deployer, client.app_id, upkeep_id, probe_app)
        box_size, mbr = _actual_mbr(algorand, client.app_id, upkeep_id)
        state = _global_state(algorand, probe_app)
        rows.append((label, len(parts), box_size, mbr, state.get(b"last_reading", 0)))

    state = _global_state(algorand, probe_app)
    logger.info("")
    logger.info("Part C — what the encoding costs, measured on chain")
    logger.info(f"{'case':<40} {'args':>5} {'box':>5} {'MBR':>7} {'target budget':>14}")
    for label, count, box_size, mbr, budget in rows:
        logger.info(f"{label:<40} {count:>5} {box_size:>5} {mbr:>7} {budget:>14}")
    logger.info("")
    logger.info(
        f"absorb received number={state.get(b'last_number')} "
        f"text={state.get(b'last_text')!r} — every argument arrived"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    logger.info(f"algod build: {algorand.client.algod.versions()['build']}")

    with tempfile.TemporaryDirectory(prefix="archon-multiarg-") as tmp:
        out_root = pathlib.Path(tmp)
        part_a(out_root)
        probe = deploy_probe()
        part_b(algorand, deployer, probe.app_id, out_root)
        part_c(algorand, deployer, probe.app_id, out_root)


if __name__ == "__main__":
    main()
