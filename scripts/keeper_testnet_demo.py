"""TestNet demo for the Keeper network — an alias kept for existing docs.

The flow now lives in `scripts/keeper_e2e.py`, which runs against LocalNet or
TestNet and asserts far more than this script did.

Run:  poetry run python -m scripts.keeper_e2e --network testnet
"""

from scripts import network as net
from scripts.keeper_e2e import main


if __name__ == "__main__":
    main(["--network", net.TESTNET])
