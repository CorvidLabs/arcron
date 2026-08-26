"""The release record must describe the chain, and drift must be visible.

For a full day the live TestNet app was missing the payer-binding fix. Two
commits carrying it landed after alpha-2 was deployed, nothing redeployed, and
nothing ran the command that would have noticed. `verify_build` would have
caught it in one second and no schedule invoked it.

These tests pin the parsing and the drift detection. They deliberately do not
touch a chain: the network half is exercised by the scheduled workflow, and a
unit test that needs algod is a unit test that gets skipped.
"""

import textwrap

import pytest

from scripts.verify_release import REPO, Release, changed_since, latest_release


def _history_is_available(ref: str) -> bool:
    """Whether git can resolve `ref` here.

    A shallow clone, which is what actions/checkout does by default, cannot.
    The tests below say so explicitly instead of failing on a confusing
    assertion about commit subjects, because that is what happened the first
    time this ran in CI and it read as a broken test rather than a missing
    checkout option. They do NOT skip: a check that quietly opts out in CI is
    the failure mode this whole file exists to prevent.
    """
    import subprocess

    return (
        subprocess.run(
            ["git", "cat-file", "-t", ref], cwd=REPO, capture_output=True, text=True
        ).returncode
        == 0
    )


SHALLOW = "git history is not available here; CI needs `fetch-depth: 0` on actions/checkout"


def test_the_newest_release_row_is_parsed(tmp_path, monkeypatch) -> None:
    """The table is the source of truth, so reading it wrongly is silent.

    The app ids here are deliberately fake. Real superseded ids in a fixture
    are indistinguishable by grep from a live pointer at a dead deployment,
    and `test_app_id_consistency` rightly refuses them.
    """
    table = textwrap.dedent(
        """\
        | Stage | Date | Commit | Contract sha256 | App id | Notes |
        |---|---|---|---|---|---|
        | alpha-1 | 2026-08-24 | `0e4de44` | `bb466d63…` | TestNet [`111111111`](https://x) | first |
        | alpha-2 | 2026-08-25 | `10ecd54` | `0afab368…` | TestNet [`222222222`](https://x) | second |
        """
    )
    path = tmp_path / "releases.md"
    path.write_text(table)
    monkeypatch.setattr("scripts.verify_release.RELEASES", path)

    release = latest_release()
    assert release.stage == "alpha-2"
    assert release.commit == "10ecd54"
    assert release.sha256 == "0afab368"
    assert release.app_id == 222222222


def test_a_table_with_no_rows_is_an_error_not_an_empty_pass(tmp_path, monkeypatch) -> None:
    """A parser that silently finds nothing reports every deployment as fine."""
    path = tmp_path / "releases.md"
    path.write_text("# Releases\n\nNothing yet.\n")
    monkeypatch.setattr("scripts.verify_release.RELEASES", path)

    with pytest.raises(SystemExit):
        latest_release()


def test_the_alpha_2_drift_would_have_been_caught() -> None:
    """The regression this whole script exists for.

    `8b9bb05` is the commit deployed as alpha-2. Two commits carrying security
    fixes landed after it and were never deployed. If this ever returns empty
    for that range, the check has stopped working.
    """
    assert _history_is_available("8b9bb05"), SHALLOW

    drift = changed_since("8b9bb05")
    assert drift, "no drift reported for a range that definitely has some"

    subjects = " ".join(drift).lower()
    assert "theft path" in subjects, "the Fable theft-path fix is not being reported"
    assert "payer" in subjects, "the payer-binding fix is not being reported"


def test_no_drift_is_reported_against_head() -> None:
    """HEAD against itself must be empty, or every run cries wolf."""
    assert changed_since("HEAD") == []


def test_the_release_record_and_the_repo_agree_on_the_live_app() -> None:
    """The newest row must name a commit this repository actually contains.

    A row naming a commit that was rebased away, or mistyped, points a third
    party at bytecode they cannot reproduce.
    """
    release = latest_release()
    # HEAD is always resolvable, even shallow; if it is not, git itself is the
    # problem and every other assertion here is meaningless.
    assert _history_is_available("HEAD"), SHALLOW
    assert _history_is_available(release.commit), (
        f"releases.md names {release.commit} for {release.stage}, which is not a "
        f"commit in this repository. Either the row is wrong, the commit was "
        f"rebased away, or {SHALLOW}"
    )


def test_release_str_names_what_an_operator_needs() -> None:
    """The log line is what someone reads at 3am; it must carry the app id."""
    release = Release("alpha-3", "2026-08-26", "13d38bb", "c94c6e0c", 769891898)
    rendered = str(release)
    assert "alpha-3" in rendered
    assert "769891898" in rendered
    assert "13d38bb" in rendered
