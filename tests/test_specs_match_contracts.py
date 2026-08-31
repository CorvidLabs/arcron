"""Every contract has a spec, and every spec matches the contract it describes.

`specsync check --strict` already runs in CI. It proves a spec is *well formed*:
the sources it names exist, the required sections are present, the exports it
lists are documented. It cannot prove the spec is *true*, because it never reads
the compiled contract.

That gap was not hypothetical. `specs/resource_probe` enumerated eight global
state keys while the contract declared ten, and `--strict` passed for as long
as it existed: `keeper_app` appeared in the document, but as an argument to
`configure_reentry` rather than as state, and `keeper_upkeep` did not appear at
all. Nothing was wrong with the spec's shape. It was just no longer accurate.

So this compares each spec against the ARC-56 artifact the compiler produced,
which is the contract's actual public surface: every ABI method name and every
global state key has to appear somewhere in the spec.

Deliberately a presence check rather than a schema comparison. A spec is prose
with tables, and demanding a machine-readable mirror of the ARC-56 would make
it a worse document to read while catching little more. What it does catch is
the failure that actually happens: a method or a state key added to a contract
and never written down.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs"
ARTIFACTS = ROOT / "smart_contracts" / "artifacts"
CONTRACTS = ROOT / "smart_contracts"


def _contract_dirs() -> list[pathlib.Path]:
    """Directories under smart_contracts that hold an actual contract."""
    return sorted(
        p
        for p in CONTRACTS.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "contract.py").is_file()
    )


def _artifact(name: str) -> dict | None:
    found = sorted(ARTIFACTS.glob(f"{name}/*.arc56.json"))
    return json.loads(found[0].read_text()) if found else None


def _spec_text(name: str) -> str | None:
    spec_dir = SPECS / name
    if not spec_dir.is_dir():
        return None
    return "\n".join(p.read_text() for p in sorted(spec_dir.glob("*.md")))


def _expand_ranges(text: str) -> str:
    """Let a spec write `s0`..`s6` instead of enumerating seven near-identical keys.

    `sim_probe` legitimately does this: seven configurable subject accounts
    whose only difference is the digit. Writing them out would be noise, and a
    checker that forced it would be making the document worse to satisfy
    itself. So the range is expanded before the presence check rather than
    treated as a gap.
    """
    expanded = [text]
    for prefix, start, end in re.findall(r"`([A-Za-z_]+)(\d+)`\s*(?:\.\.|…)\s*`?[A-Za-z_]*(\d+)`?", text):
        lo, hi = int(start), int(end)
        if 0 <= lo <= hi <= 64:
            expanded.extend(f"`{prefix}{n}`" for n in range(lo, hi + 1))
    return " ".join(expanded)


def test_there_are_contracts_to_check() -> None:
    # A checker that matches nothing passes for ever.
    #
    # Seven contracts until 2026-08-31, when `rain` and `beacon_stub` left for
    # CorvidLabs/arcron-rain (docs/design/split.md D4). Five is the whole set
    # now — keeper, pulse, subscription, resource_probe, sim_probe — so the
    # floor is re-based onto it rather than left where a sixth deletion could
    # pass unnoticed. The number happening to be unchanged is a coincidence of
    # the old floor having had two of margin.
    assert len(_contract_dirs()) >= 5


@pytest.mark.parametrize("contract", _contract_dirs(), ids=lambda p: p.name)
def test_every_contract_has_a_spec(contract: pathlib.Path) -> None:
    assert _spec_text(contract.name) is not None, (
        f"smart_contracts/{contract.name}/contract.py has no specs/{contract.name}/. "
        "CLAUDE.md requires a strict spec-sync spec for every contract."
    )


@pytest.mark.parametrize("contract", _contract_dirs(), ids=lambda p: p.name)
def test_every_abi_method_appears_in_the_spec(contract: pathlib.Path) -> None:
    artifact = _artifact(contract.name)
    if artifact is None:
        pytest.skip(f"{contract.name} is not built; run python -m smart_contracts build")
    text = _spec_text(contract.name) or ""
    missing = sorted(m["name"] for m in artifact["methods"] if m["name"] not in text)
    assert not missing, (
        f"specs/{contract.name} does not mention these ABI methods, which the "
        f"compiled contract exposes: {missing}"
    )


@pytest.mark.parametrize("contract", _contract_dirs(), ids=lambda p: p.name)
def test_every_global_state_key_appears_in_the_spec(contract: pathlib.Path) -> None:
    artifact = _artifact(contract.name)
    if artifact is None:
        pytest.skip(f"{contract.name} is not built; run python -m smart_contracts build")
    keys = artifact.get("state", {}).get("keys", {}).get("global", {})
    text = _expand_ranges(_spec_text(contract.name) or "")
    missing = sorted(key for key in keys if key not in text)
    assert not missing, (
        f"specs/{contract.name} does not mention these global state keys, which "
        f"the compiled contract declares: {missing}. This is the resource_probe "
        "failure: a key added to the contract and never written down."
    )


def test_no_spec_describes_a_contract_that_no_longer_exists() -> None:
    """The other direction, which is how litter accumulates.

    `smart_contracts/corvid_vault/` survived as a directory holding nothing but
    `__pycache__` after its source was removed, and nothing noticed.
    """
    contracts = {p.name for p in _contract_dirs()}
    orphans = sorted(
        d.name for d in SPECS.iterdir() if d.is_dir() and d.name not in contracts
    )
    assert not orphans, (
        f"these specs describe contracts that no longer exist: {orphans}. "
        "Remove the spec, or restore the contract."
    )


def test_no_contract_directory_is_an_empty_shell() -> None:
    # The corvid_vault failure from the other side: a directory that looks like
    # a contract in a listing and contains no source at all.
    shells = sorted(
        p.name
        for p in CONTRACTS.iterdir()
        if p.is_dir()
        and not p.name.startswith("_")
        and p.name != "artifacts"
        and not (p / "contract.py").is_file()
    )
    assert not shells, (
        f"these look like contract directories but hold no contract.py: {shells}"
    )
