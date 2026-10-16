"""The server install path: what `package.sh` ships is what `install.sh` needs.

This path has broken without anyone running it end to end before. A packaged
install once died at the notifier unit, because `install.sh` installed a file
`package.sh` never put in the archive (`deploy/vps/package.sh` says so). And
until 2026-09-29 an upgrade stopped only the keeper, leaving the notifier
running old code out of a tree that had been replaced under it. G1 runs both
from a server for seven days and upgrades them from `main`, so these are
checked here rather than discovered there.

`package.sh` is run for real, into a temporary directory, with the dirty-tree
override set so the test does not depend on the checkout being clean.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "deploy" / "vps" / "install.sh"
PACKAGE = ROOT / "deploy" / "vps" / "package.sh"
HOSTING = ROOT / "docs" / "hosting.md"


def _installed_sources() -> set[str]:
    """Every path `install.sh` reads from the unpacked archive."""
    text = INSTALL.read_text()
    found = set(re.findall(r'"\$\{SOURCE\}/([A-Za-z0-9_./-]+)"', text))
    loop = re.search(r"for item in ([^;]+); do", text)
    assert loop, "the copy loop in install.sh moved; update this test"
    found |= set(loop.group(1).split())
    # BUILD is read only when present (`[[ -f ... ]]`), and package.sh writes it.
    return found


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> tarfile.TarFile:
    if not (shutil.which("bash") and shutil.which("git") and shutil.which("tar")):
        pytest.skip("needs bash, git and tar")
    out = tmp_path_factory.mktemp("package") / "arcron-keeper.tar.gz"
    subprocess.run(
        ["bash", str(PACKAGE), str(out)],
        cwd=ROOT, check=True, capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "ARCRON_PACKAGE_DIRTY": "1"},
    )
    return tarfile.open(out)


def test_every_file_install_sh_installs_is_in_the_archive(archive) -> None:
    names = set(archive.getnames())
    missing = [
        path for path in sorted(_installed_sources())
        if path not in names and not any(name.startswith(path.rstrip("/") + "/") for name in names)
    ]
    assert not missing, f"install.sh reads these, and package.sh does not ship them: {missing}"


def test_the_archive_says_which_commit_it_is(archive) -> None:
    build = archive.extractfile("BUILD").read().decode()
    fields = dict(line.split("=", 1) for line in build.strip().splitlines())
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert fields["commit"] == head
    assert fields["dirty"] in ("0", "1")
    assert fields["on_origin_main"] in ("yes", "no", "unknown")


def test_the_archive_carries_no_env_file(archive) -> None:
    """The mnemonic is typed on the host; an archive that carried a real env file would ship it."""
    assert not [n for n in archive.getnames() if re.search(r"(^|/)\.env(\.|$)|/keeper\.env$|/notifier\.env$", n)]


def test_an_upgrade_stops_the_notifier_before_replacing_code_and_restarts_it() -> None:
    text = INSTALL.read_text()
    stop_notifier = text.index('systemctl stop "$NOTIFIER_SERVICE"')
    stop_keeper = text.index('systemctl stop "$SERVICE"')
    replace = text.index("for item in ")
    assert stop_notifier < replace and stop_keeper < replace
    restart = text.index('systemctl start "$NOTIFIER_SERVICE"')
    assert restart > replace
    # Only the one that was running comes back: a notifier the operator never
    # enabled (no webhook yet) must not be started by an upgrade.
    guard = text.rindex('if [[ "$NOTIFIER_WAS_ACTIVE" == 1 ]]; then', 0, restart)
    assert "fi" not in text[guard:restart].split()


def test_the_documented_server_preflight_uses_the_units_virtualenv() -> None:
    """`poetry run` from another account looks for a different virtualenv: install.sh copies no poetry.toml."""
    text = HOSTING.read_text()
    assert ".venv/bin/python -m scripts.preflight" in text
    assert "poetry run python -m scripts.preflight" not in text


def test_the_keeper_is_enabled_only_when_it_is_started() -> None:
    """Enabled at install, a key written into keeper.env started the keeper at the next reboot, before the preflight."""
    text = INSTALL.read_text()
    enable = text.index('systemctl enable "$SERVICE"')
    start = text.index('systemctl start "$SERVICE"')
    empty_key = text.index("grep -q '^KEEPER_MNEMONIC=$'")
    assert empty_key < enable < start, "enable belongs in the branch that starts the keeper"
    assert text.count('systemctl enable "$SERVICE"') == 1
