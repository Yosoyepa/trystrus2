"""Single-flight order state: one Idempotency-Key, one paying click, ever.

SQLite checkpoint machine (decision 0030 §4.3). `clicked` without a
confirmed order becomes `uncertain` and the service refuses to re-click —
reconciliation is a human job via the account's "Mis pedidos".
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIVED = "received"
SESSION_OK = "session_ok"
CART_OK = "cart_ok"
APPROVAL_VERIFIED = "approval_verified"
ARMED = "armed"
DRY_RUN_CONFIRMED = "dry_run_confirmed"
CLICKED = "clicked"
CONFIRMED = "confirmed"
FAILED = "failed"
UNCERTAIN = "uncertain"

TERMINAL_OK = (DRY_RUN_CONFIRMED, CONFIRMED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_orders (
    idem_key     TEXT PRIMARY KEY,
    purchase_id  TEXT NOT NULL,
    cart_hash    TEXT NOT NULL,
    amount       TEXT NOT NULL,
    state        TEXT NOT NULL,
    order_id     TEXT,
    receipt_json TEXT,
    updated_at   TEXT NOT NULL
)
"""


class BridgeState:
    def __init__(self, db_path: str | Any) -> None:
        # RLock: claim() re-enters get() while holding the lock.
        self._lock = threading.RLock()
        path = Path(str(db_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def claim(
        self, idem_key: str, *, purchase_id: str, cart_hash: str, amount: str
    ) -> dict[str, Any] | None:
        """Single-flight: the first caller inserts and gets None; concurrent
        or retrying callers get the existing row."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO bridge_orders "
                "(idem_key, purchase_id, cart_hash, amount, state, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (idem_key, purchase_id, cart_hash, amount, RECEIVED, self._now()),
            )
            self._conn.commit()
            if cursor.rowcount:
                return None
            return self.get(idem_key)

    def get(self, idem_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT idem_key, purchase_id, cart_hash, amount, state, "
                "order_id, receipt_json, updated_at "
                "FROM bridge_orders WHERE idem_key = ?",
                (idem_key,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "idem_key",
            "purchase_id",
            "cart_hash",
            "amount",
            "state",
            "order_id",
            "receipt",
            "updated_at",
        )
        values = list(row[:7])
        values[6] = json.loads(row[6]) if row[6] else None
        values.append(row[7])
        return dict(zip(keys, values, strict=True))

    def transition(
        self,
        idem_key: str,
        state: str,
        *,
        order_id: str | None = None,
        receipt: dict[str, Any] | None = None,
        expect: str | None = None,
    ) -> None:
        """Persist a checkpoint. `expect` implements an optimistic guard so
        two workers can never walk the machine past each other."""
        with self._lock:
            if expect is None:
                cursor = self._conn.execute(
                    "UPDATE bridge_orders SET state = ?, order_id = COALESCE(?, order_id), "
                    "receipt_json = COALESCE(?, receipt_json), updated_at = ? "
                    "WHERE idem_key = ?",
                    (
                        state,
                        order_id,
                        json.dumps(receipt) if receipt is not None else None,
                        self._now(),
                        idem_key,
                    ),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE bridge_orders SET state = ?, order_id = COALESCE(?, order_id), "
                    "receipt_json = COALESCE(?, receipt_json), updated_at = ? "
                    "WHERE idem_key = ? AND state = ?",
                    (
                        state,
                        order_id,
                        json.dumps(receipt) if receipt is not None else None,
                        self._now(),
                        idem_key,
                        expect,
                    ),
                )
            self._conn.commit()
            if cursor.rowcount != 1:
                msg = f"state transition to {state} lost the race on {idem_key}"
                raise RuntimeError(msg)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
