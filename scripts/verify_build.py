"""Check that a deployed app is running the source in this repository.

This rebuilds from the working tree and compares the compiled programs
against what algod reports for an app id.

Read the answer narrowly. A match proves the deployed programs are the ones
this tree compiles to, right now. It does not prove the app will keep running
them: that is `frozen`, which `govern status` reports and this does not. On an
unfrozen app a match means "matches today" and nothing more. It also says
nothing about who the creator is, how many program pages or state slots the
app was created with, or whether the working tree is the release tag you
think it is.

    poetry run python -m scripts.verify_build --network testnet --app-id N

With no `--app-id` it prints the hashes of the local build, which is what a
release should record so a third party can check a deployment later without
trusting us.

The comparison is on the compiled bytecode, not on TEAL text: comments and
formatting do not survive assembly, and two sources that assemble to the same
bytes are the same program by the only definition that matters on chain.
"""

import argparse
import base64
import hashlib
import json
import logging
import pathlib
import subprocess
import sys

from scripts import network as net

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
# Contracts this repo has ever deployed and can therefore re-verify. `"rain"`
# was here until 2026-08-31; it moved to CorvidLabs/arcron-rain, which forked
# this script for it. That fork is not optional there — a rain hub has no
# update path at all, so re-deriving its bytecode from source is its whole
# trust story, and the fork verifies 770746178, the hub that repository
# deployed. It deliberately does not verify 770130162, the immutable hub this
# repository deployed and upkeep 91 still calls: that one predates a security
# fix, cannot take one, and was replaced rather than adopted. This tuple also
# narrows `--contract` on scripts/mainnet_clock.py.
CONTRACTS = ("keeper", "pulse")


def _spec(name: str) -> dict:
    matches = sorted((REPO / "smart_contracts" / "artifacts" / name).glob("*.arc56.json"))
    if not matches:
        raise SystemExit(f"no ARC-56 spec for {name}; run `fledge run build` first")
    return json.loads(matches[0].read_text())


def _programs(spec: dict) -> tuple[bytes, bytes]:
    code = spec["byteCode"]
    return base64.b64decode(code["approval"]), base64.b64decode(code["clear"])


def _digest(approval: bytes, clear: bytes) -> str:
    """One hash over both programs, so neither can be swapped independently."""
    return hashlib.sha256(approval + b"\x00" + clear).hexdigest()


def rebuild() -> None:
    """Compile from source, so the comparison is against this tree and not a stale artifact."""
    result = subprocess.run(
        [sys.executable, "-m", "smart_contracts", "build"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"build failed:\n{result.stdout}\n{result.stderr}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id", type=int, default=None, help="compare against this deployed app"
    )
    parser.add_argument(
        "--contract",
        default="keeper",
        choices=CONTRACTS,
        help="which contract to verify (default: %(default)s)",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="trust the committed artifacts instead of compiling first",
    )
    args = parser.parse_args(argv)

    if not args.no_rebuild:
        logger.info("Rebuilding from source…")
        rebuild()

    approval, clear = _programs(_spec(args.contract))
    local = _digest(approval, clear)
    logger.info("")
    logger.info(f"{args.contract} built from this tree:")
    logger.info(f"  approval  {len(approval):>5} bytes  sha256 {hashlib.sha256(approval).hexdigest()}")
    logger.info(f"  clear     {len(clear):>5} bytes  sha256 {hashlib.sha256(clear).hexdigest()}")
    logger.info(f"  combined                 sha256 {local}")

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    logger.info(f"  commit    {commit}{' (working tree is dirty)' if dirty else ''}")

    if args.app_id is None:
        logger.info("")
        logger.info("No --app-id given, so nothing was compared. Record the combined")
        logger.info("hash with the release and anyone can check a deployment against it.")
        return

    algorand = net.connect(args.network)
    info = algorand.client.algod.application_info(args.app_id)
    params = info["params"]
    on_chain_approval = base64.b64decode(params["approval-program"])
    on_chain_clear = base64.b64decode(params["clear-state-program"])
    remote = _digest(on_chain_approval, on_chain_clear)

    logger.info("")
    logger.info(f"app {args.app_id} on {args.network}:")
    logger.info(f"  approval  {len(on_chain_approval):>5} bytes")
    logger.info(f"  clear     {len(on_chain_clear):>5} bytes")
    logger.info(f"  combined                 sha256 {remote}")
    logger.info("")

    if remote == local:
        logger.info("✔ The deployed app is this source, byte for byte.")
        return
    logger.error("✘ The deployed app is NOT this source.")
    logger.error("  Either the tree has moved on since the deployment, or the")
    logger.error("  deployment is not what this repository says it is.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
