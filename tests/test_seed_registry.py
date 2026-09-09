"""`seed_registry --commit` pays the network minimum, whatever the node advises.

On MainNet it is step three of the ceremony (`docs/design/mainnet-rollout.md`,
"After, in this order"), so until 2026-09-08 the create was bounded and the
first MainNet registration was not: two payments and the `register` call per
seed, built by algokit from the node's suggested params, and algokit's
composer multiplies a non-flat per-byte figure by each transaction's size just
as algosdk does (issue #250 F14). These drive `main` against a fake node and a
recording stand-in for the generated client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from algosdk import transaction

from scripts import govern, seed_registry

TESTNET_KEEPER = 769891898
PULSE = 769891902
DEPLOYER = "E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E"


class _RecordingClient:
    """Stands in for the generated KeeperClient: records what it is told to send."""

    calls: list = []

    def __init__(self, **kwargs) -> None:
        self.app_address = "APP" * 19
        self.send = self

    def register(self, *, args, params=None):
        _RecordingClient.calls.append((args, params))
        return SimpleNamespace(abi_return=len(_RecordingClient.calls))


class _Algod:
    def __init__(self, fee: int) -> None:
        self.fee = fee

    def suggested_params(self):
        return transaction.SuggestedParams(fee=self.fee, first=1, last=1000, gh="", gen="testnet-v1.0",
                                           flat_fee=False, min_fee=1000)

    def account_info(self, address: str) -> dict:
        return {"amount": 100_000_000, "min-balance": 100_000}

    def status(self) -> dict:
        return {"last-round": 1}


def _algorand(fee: int, payments: list):
    return SimpleNamespace(
        client=SimpleNamespace(algod=_Algod(fee)),
        account=SimpleNamespace(from_environment=lambda name: SimpleNamespace(address=DEPLOYER, signer=None)),
        create_transaction=SimpleNamespace(payment=lambda p: payments.append(p) or p),
    )


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setattr(seed_registry, "KeeperClient", _RecordingClient)
    _RecordingClient.calls = []
    payments: list = []

    def run(fee: int, *extra: str) -> None:
        monkeypatch.setattr(seed_registry.net, "connect", lambda network: _algorand(fee, payments))
        seed_registry.main(["--network", "testnet", "--app-id", str(TESTNET_KEEPER),
                            "--target", str(PULSE), "--commit", *extra])

    return run, payments


def test_a_node_advising_too_much_registers_nothing(seeded, caplog) -> None:
    """50,000 per byte: no payment is built, no register is sent, and the exit code is 1."""
    run, payments = seeded
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as refused:
        run(50_000)
    assert refused.value.code == 1
    assert payments == [] and _RecordingClient.calls == []
    assert "Refusing to register" in caplog.text and str(govern.MAX_SIGNABLE_FEE) in caplog.text


def test_every_transaction_in_every_group_is_flat_at_the_minimum(seeded) -> None:
    """1,000 per byte would be real money three times per seed. Each pays 1,000, flat."""
    run, payments = seeded
    run(1000, "--only", "skip-ahead")
    assert len(_RecordingClient.calls) == 1, "--only skip-ahead is one seed, the ceremony's"
    args, params = _RecordingClient.calls[0]
    assert params.static_fee.micro_algo == 1000
    assert len(payments) == 2 and all(p.static_fee.micro_algo == 1000 for p in payments)
    assert args.mbr_payment is payments[0] and args.funding_payment is payments[1]


def test_pricing_without_commit_asks_the_node_nothing_about_fees(seeded, monkeypatch) -> None:
    """The plan is read-only; a node that would be refused on --commit still prices."""
    monkeypatch.setattr(seed_registry.net, "connect", lambda network: _algorand(50_000, []))
    seed_registry.main(["--network", "testnet", "--app-id", str(TESTNET_KEEPER), "--target", str(PULSE)])
    assert _RecordingClient.calls == []
