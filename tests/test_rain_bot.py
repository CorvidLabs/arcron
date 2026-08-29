"""Unit tests for the rain bot's decision logic and allocation reader.

These do not touch a chain: `should_resolve`/`should_abandon` are pure
functions over decoded global state, and `read_allocation` is exercised
against a fake algod client that raises the same "box not found" shape a
real one does for a missing box. The live end-to-end proof (a real draw,
resolved against the real Foundation beacon) is `scripts.rain_testnet_deploy
--bootstrap-draw`, recorded in `docs/releases.md`.
"""

from algosdk.error import AlgodHTTPError

from scripts.rain_bot import (
    _read_pending,
    _write_pending,
    read_allocation,
    should_abandon,
    should_resolve,
)
from scripts.rain_bot import BEACON_WINDOW


def _state(*, draw_open: int, commit_round: int) -> dict:
    return {"draw_open": draw_open, "commit_round": commit_round}


def test_should_resolve_false_when_no_draw_is_open() -> None:
    assert should_resolve(_state(draw_open=0, commit_round=100), current_round=200) is False


def test_should_resolve_false_before_the_beacon_round() -> None:
    state = _state(draw_open=1, commit_round=100)
    assert should_resolve(state, current_round=100) is False
    assert should_resolve(state, current_round=99) is False


def test_should_resolve_true_once_the_beacon_round_has_passed() -> None:
    state = _state(draw_open=1, commit_round=100)
    assert should_resolve(state, current_round=101) is True
    assert should_resolve(state, current_round=100 + BEACON_WINDOW) is True


def test_should_resolve_false_once_the_window_has_closed() -> None:
    state = _state(draw_open=1, commit_round=100)
    assert should_resolve(state, current_round=100 + BEACON_WINDOW + 1) is False


def test_should_abandon_false_when_no_draw_is_open() -> None:
    assert should_abandon(_state(draw_open=0, commit_round=100), current_round=10_000) is False


def test_should_abandon_false_while_the_beacon_can_still_answer() -> None:
    state = _state(draw_open=1, commit_round=100)
    assert should_abandon(state, current_round=100 + BEACON_WINDOW) is False


def test_should_abandon_true_once_the_window_has_closed() -> None:
    state = _state(draw_open=1, commit_round=100)
    assert should_abandon(state, current_round=100 + BEACON_WINDOW + 1) is True


def test_resolve_and_abandon_are_mutually_exclusive_at_every_round() -> None:
    """A draw is never simultaneously ready to resolve and ready to abandon."""
    state = _state(draw_open=1, commit_round=1_000)
    for current in range(1_000, 1_000 + 2 * BEACON_WINDOW):
        assert not (should_resolve(state, current) and should_abandon(state, current))


class _MissingBox:
    """A stand-in for `AlgodClient` whose box lookup always 404s."""

    def application_box_by_name(self, app_id: int, name: bytes):
        raise AlgodHTTPError("box not found")


class _PresentBox:
    def __init__(self, value: int) -> None:
        self._raw = value.to_bytes(8, "big")

    def application_box_by_name(self, app_id: int, name: bytes):
        import base64

        return {"value": base64.b64encode(self._raw).decode()}


def test_read_allocation_is_zero_for_a_missing_box() -> None:
    address = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
    assert read_allocation(_MissingBox(), app_id=1, address=address) == 0


def test_read_allocation_decodes_an_existing_box() -> None:
    address = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
    assert read_allocation(_PresentBox(981_100), app_id=1, address=address) == 981_100


def test_pending_deposit_is_zero_with_no_state_path() -> None:
    assert _read_pending(None) == 0


def test_pending_deposit_round_trips_through_a_file(tmp_path) -> None:
    path = tmp_path / "nested" / "pending.json"
    _write_pending(path, 981_100)
    assert _read_pending(path) == 981_100


def test_pending_deposit_of_zero_removes_the_file(tmp_path) -> None:
    path = tmp_path / "pending.json"
    _write_pending(path, 981_100)
    assert path.exists()
    _write_pending(path, 0)
    assert not path.exists()
    assert _read_pending(path) == 0


def test_pending_deposit_survives_a_missing_or_corrupt_file(tmp_path) -> None:
    path = tmp_path / "pending.json"
    assert _read_pending(path) == 0
    path.write_text("not json")
    assert _read_pending(path) == 0
