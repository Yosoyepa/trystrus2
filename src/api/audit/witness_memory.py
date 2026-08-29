"""In-memory fake external witness storage for testing."""

from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any

from .models import RootCheckpoint


class InMemoryWitness:
    """Thread-safe fake external witness storage with test tamper and deletion hooks."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._checkpoints: dict[tuple[int, int], RootCheckpoint] = {}

    def put(self, checkpoint: RootCheckpoint) -> None:
        """Store immutable root checkpoint."""
        with self._lock:
            key = (checkpoint.seq_start, checkpoint.seq_end)
            if key in self._checkpoints:
                raise ValueError(
                    f"witness object already exists for range "
                    f"{checkpoint.seq_start}-{checkpoint.seq_end}"
                )
            self._checkpoints[key] = checkpoint

    def get(self, seq_start: int, seq_end: int) -> RootCheckpoint | None:
        """Retrieve root checkpoint for range, if witnessed."""
        with self._lock:
            return self._checkpoints.get((seq_start, seq_end))

    def tamper(self, seq_start: int, seq_end: int, field_name: str, value: Any) -> None:
        """Testing hook to mutate a witnessed checkpoint."""
        with self._lock:
            key = (seq_start, seq_end)
            cp = self._checkpoints.get(key)
            if cp is None:
                raise ValueError(f"Checkpoint for range {seq_start}-{seq_end} not found in witness")

            d = cp.to_dict()
            d[field_name] = value
            try:
                tampered = RootCheckpoint(
                    seq_start=d["seq_start"],
                    seq_end=d["seq_end"],
                    root_hash=d["root_hash"],
                    root_sig=d["root_sig"],
                    cardinality=d["cardinality"],
                    created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
                )
            except Exception:
                tampered = object.__new__(RootCheckpoint)
                for k, v in d.items():
                    object.__setattr__(tampered, k, v)

            self._checkpoints[key] = tampered

    def delete(self, seq_start: int, seq_end: int) -> None:
        """Testing hook to delete a witnessed checkpoint."""
        with self._lock:
            self._checkpoints.pop((seq_start, seq_end), None)


__all__ = ["InMemoryWitness"]
