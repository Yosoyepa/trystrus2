"""Single-flight state machine: one key, one click, replays and races."""

import threading

import pytest
from src.rappi_bridge.state import (
    ARMED,
    CONFIRMED,
    RECEIVED,
    BridgeState,
)


def test_claim_single_flight(tmp_path) -> None:
    state = BridgeState(tmp_path / "s.sqlite3")
    assert state.claim("k1", purchase_id="p", cart_hash="h", amount="1.00") is None
    existing = state.claim("k1", purchase_id="p", cart_hash="h", amount="1.00")
    assert existing is not None and existing["state"] == RECEIVED
    # a different key is independent
    assert state.claim("k2", purchase_id="p", cart_hash="h", amount="1.00") is None


def test_transitions_and_receipt(tmp_path) -> None:
    state = BridgeState(tmp_path / "s.sqlite3")
    state.claim("k", purchase_id="p", cart_hash="h", amount="1.00")
    state.transition("k", ARMED)
    receipt = {"state": CONFIRMED, "order_id": "123"}
    state.transition("k", CONFIRMED, order_id="123", receipt=receipt)
    row = state.get("k")
    assert row["state"] == CONFIRMED
    assert row["order_id"] == "123"
    assert row["receipt"] == receipt


def test_optimistic_guard_blocks_double_click(tmp_path) -> None:
    state = BridgeState(tmp_path / "s.sqlite3")
    state.claim("k", purchase_id="p", cart_hash="h", amount="1.00")
    state.transition("k", ARMED)
    state.transition("k", "clicked", expect=ARMED)  # the winner
    with pytest.raises(RuntimeError):
        state.transition("k", "clicked", expect=ARMED)  # a racer loses


def test_concurrent_claims_only_one_wins(tmp_path) -> None:
    state = BridgeState(tmp_path / "s.sqlite3")
    winners: list[bool] = []

    def worker() -> None:
        first = state.claim("k", purchase_id="p", cart_hash="h", amount="1.00")
        winners.append(first is None)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert winners.count(True) == 1
