"""The examples an integrator copies must actually work.

Nothing else walks `examples/`: `python -m smart_contracts build` only covers
`smart_contracts/`, and no lane runs the scripts, so both files here could rot
quietly. A broken copy-paste template is worse than none at all, and these are
the first two files anyone opens.

`register_upkeep.py` did rot exactly this way. It kept calling `register` with
the pre-1.0 `call_data` argument long after the contract took a `call_args`
list plus four more fields, so it raised TypeError before reaching a chain.
That is what the argument-shape test below exists to catch.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path("examples/minimal_target.py")
# The hook Arcron calls: no arguments of its own, so a keeper can call it with
# just the selector.
HOOK = "run"


@pytest.fixture(scope="module")
def compiled(tmp_path_factory) -> dict:
    out_dir = tmp_path_factory.mktemp("minimal_target")
    result = subprocess.run(
        [sys.executable, "-m", "puyapy", str(EXAMPLE), "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"the example failed to compile:\n{result.stderr}"
    spec = json.loads((out_dir / "MinimalTarget.arc56.json").read_text())
    shutil.rmtree(out_dir, ignore_errors=True)
    return spec


def test_the_example_compiles(compiled: dict) -> None:
    assert compiled["name"] == "MinimalTarget"


def test_the_hook_takes_no_arguments_of_its_own(compiled: dict) -> None:
    """The simplest call shape, and the one the example should teach.

    A target can take up to `MAX_CALL_ARGS` arguments, but the minimal example
    should show the minimal thing: a hook Arcron can call with nothing but the
    selector. An example that needed arguments to work would put a step in
    front of the reader that the network does not require.
    """
    hook = next(method for method in compiled["methods"] if method["name"] == HOOK)
    assert hook["args"] == []
    assert hook["actions"]["call"] == ["NoOp"]


def test_register_upkeep_passes_every_argument_register_requires() -> None:
    """The example calls the real generated client, so its shape must match.

    A missing field is a TypeError on the first run, which is the worst place
    for an integrator to meet it: they cannot tell whether they misconfigured
    something or the example was already broken. Reading the fields off the
    generated client means this fails here the next time `register`'s surface
    moves, rather than in somebody else's terminal.
    """
    import ast
    import dataclasses

    from smart_contracts.artifacts.keeper.keeper_client import RegisterArgs

    source = Path("examples/register_upkeep.py").read_text()
    tree = ast.parse(source)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RegisterArgs"
    )
    passed = {keyword.arg for keyword in call.keywords}
    required = {field.name for field in dataclasses.fields(RegisterArgs)}
    assert passed == required, (
        f"missing {sorted(required - passed)}, unexpected {sorted(passed - required)}"
    )
