"""The example an integrator copies must actually compile.

`examples/minimal_target.py` is not built by `python -m smart_contracts build`,
which only walks `smart_contracts/`. Without this it could rot quietly, and a
broken copy-paste template is worse than none at all.
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
    """The whole point of the v1 call shape.

    Arcron calls a target with exactly one application argument — the method
    selector — so the hook must declare no arguments. An example that broke
    this rule would teach the one thing an integrator must not do.
    """
    hook = next(method for method in compiled["methods"] if method["name"] == HOOK)
    assert hook["args"] == []
    assert hook["actions"]["call"] == ["NoOp"]
