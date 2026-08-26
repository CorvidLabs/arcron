"""Network selection for the Arcron scripts.

Every script picks its network with `--network` (or `ARCRON_NETWORK`), which
loads the matching `.env.<network>` file *before* algokit-utils reads the
environment, then verifies the node it reached really is that network.

LocalNet needs no secrets: accounts come from KMD, funded by the LocalNet
dispenser. TestNet needs `.env.testnet` with `DEPLOYER_MNEMONIC`.
"""

import argparse
import logging
import os

import algokit_utils
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

LOCALNET = "localnet"
TESTNET = "testnet"
MAINNET = "mainnet"
NETWORKS = (LOCALNET, TESTNET, MAINNET)

# Genesis ids a node may report for each network. AlgoKit LocalNet reports
# "dockernet-v1"; the older sandbox reported "sandnet-v1".
_GENESIS_IDS = {
    LOCALNET: ("dockernet-v1", "sandnet-v1", "devnet-v1"),
    TESTNET: ("testnet-v1.0",),
    MAINNET: ("mainnet-v1.0",),
}


def genesis_ids(network: str) -> tuple[str, ...]:
    """Every genesis id that counts as this network.

    LocalNet answers to several depending on how it was started, which is why
    this is a tuple rather than a single string.
    """
    return _GENESIS_IDS[network]


def default_network() -> str:
    """The network to use when no flag is given (`ARCRON_NETWORK`, else TestNet)."""
    return os.environ.get("ARCRON_NETWORK", TESTNET)


def add_network_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--network",
        choices=NETWORKS,
        default=default_network(),
        help="network to talk to (default: %(default)s; env ARCRON_NETWORK)",
    )


def load_network(network: str) -> str:
    """Load `.env.<network>`; returns the network name.

    Exported environment variables win over the file, as dotenv normally
    behaves — `assert_network` catches the case where that points the script
    at the wrong chain. A deployment that configures everything through the
    environment (a container, a systemd unit) needs no file at all.
    """
    if network not in NETWORKS:
        raise ValueError(f"Unknown network {network!r}; expected one of {NETWORKS}")
    if network == MAINNET and os.environ.get("ARCRON_ALLOW_MAINNET") != "1":
        # A typo in --network should not reach real money. Nothing in this repo
        # sets this, so choosing MainNet has to be a separate, deliberate act.
        raise RuntimeError(
            "Refusing to talk to MainNet unless ARCRON_ALLOW_MAINNET=1 is set. "
            "See docs/releases.md: MainNet is gated behind the rc clock, and "
            "nothing here should reach it by accident."
        )
    env_file = f".env.{network}"
    loaded = load_dotenv(env_file)
    if not loaded and network != LOCALNET and not os.environ.get("ALGOD_SERVER"):
        raise FileNotFoundError(
            f"{env_file} not found and ALGOD_SERVER is not set — copy "
            f".env.testnet.template and add DEPLOYER_MNEMONIC, or supply the "
            f"configuration through the environment"
        )
    return network


def assert_network(algod: object, network: str) -> None:
    """Fail loudly if the connected node is not the network we asked for."""
    genesis = algod.suggested_params().gen  # type: ignore[attr-defined]
    expected = _GENESIS_IDS[network]
    if genesis not in expected:
        raise RuntimeError(
            f"Asked for {network} but the node at ALGOD_SERVER reports genesis "
            f"{genesis!r} (expected one of {expected}). Check .env.{network} "
            f"and any exported ALGOD_* variables."
        )
    logger.info(f"Network: {network} ({genesis})")


def is_dev_mode(algod: object) -> bool:
    """True on a dev-mode node, where a block is only produced per transaction."""
    genesis = algod.suggested_params().gen  # type: ignore[attr-defined]
    return genesis in _GENESIS_IDS[LOCALNET]


def connect(network: str) -> "algokit_utils.AlgorandClient":
    """Load the network's env file and return a verified AlgorandClient."""
    load_network(network)
    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never build transactions from stale
    # cached suggested params.
    algorand.set_suggested_params_cache_timeout(0)
    assert_network(algorand.client.algod, network)
    return algorand


def wait_for_round(
    algorand: "algokit_utils.AlgorandClient",
    target_round: int,
    poker: "algokit_utils.SigningAccount | None" = None,
) -> int:
    """Block until the chain reaches `target_round`; returns the round reached.

    On a dev-mode node no blocks are produced on their own, so `poker` sends
    zero-amount self-payments to advance the chain one round at a time.
    """
    algod = algorand.client.algod
    dev_mode = is_dev_mode(algod)
    while True:
        current = algod.status()["last-round"]
        if current >= target_round:
            return current
        if dev_mode:
            if poker is None:
                raise ValueError("A poker account is required to advance dev-mode rounds")
            algorand.send.payment(
                algokit_utils.PaymentParams(
                    sender=poker.address,
                    receiver=poker.address,
                    amount=algokit_utils.AlgoAmount(micro_algo=0),
                    note=b"arcron: advance round",
                )
            )
        else:
            logger.info(f"  round {current}, waiting for {target_round}…")
            algod.status_after_block(current + 1)
