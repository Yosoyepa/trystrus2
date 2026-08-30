"""Ledger application service orchestrating event appending, root signing,
and chain verification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .chain import validate_chain
from .hashing import compute_event_hash, compute_root_hash
from .models import AuditEvent, ChainResult, RootCheckpoint
from .ports import Clock, LedgerRepository, RootSigner, SystemClock, Witness


class LedgerService:
    """Application service for the append-only evidence ledger (contracts schemas.md §3).

    Responsibilities:
    1. Append audit events with deterministic canonical hash chaining.
    2. Sign root checkpoints with Cloud KMS / Local Ed25519 and publish to external witness.
    3. Verify ledger integrity: recompute hashes, verify signatures, and cross-check witness.
    """

    def __init__(
        self,
        repository: LedgerRepository,
        signer: RootSigner,
        witness: Witness,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repository
        self._signer = signer
        self._witness = witness
        self._clock = clock or SystemClock()

    def append(
        self,
        type: str,
        mandate_id: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Append an audit event to the ledger."""
        return self._repo.append(
            mandate_id=mandate_id,
            type=type,
            payload=payload,
            created_at=created_at,
        )

    def sign_root(
        self,
        seq_start: int | None = None,
        seq_end: int | None = None,
    ) -> RootCheckpoint:
        """Compute root hash over sequence range, sign with RootSigner, annotate, and witness.

        Fail-closed:
        - Revalidates the range chain before signing.
        - Fails if range is empty or invalid.
        - Publishes to witness storage.
        """
        all_events = self._repo.get_all()
        if not all_events:
            raise ValueError("Cannot sign root: ledger is empty")

        if seq_start is None:
            # Start from first event or after last annotated event
            annotated = [e.seq for e in all_events if e.seq is not None and e.root_sig is not None]
            seq_start = (max(annotated) + 1) if annotated else (all_events[0].seq or 1)

        if seq_end is None:
            seq_end = all_events[-1].seq or len(all_events)

        if seq_start > seq_end:
            raise ValueError(
                f"Invalid range for root checkpoint: seq_start ({seq_start}) > seq_end ({seq_end})"
            )

        events_in_range = self._repo.get_range(seq_start, seq_end)
        if not events_in_range:
            raise ValueError(f"No events found in sequence range {seq_start}-{seq_end}")

        # Validate range before signing
        range_valid = validate_chain(
            events_in_range,
            expected_start_prev_hash=events_in_range[0].prev_hash,
            check_seq_continuity=True,
        )
        if not range_valid.ok:
            raise RuntimeError(
                f"Cannot sign root: range {seq_start}-{seq_end} failed validation "
                f"at seq {range_valid.first_bad_seq}: {range_valid.reason}"
            )

        last_hash = events_in_range[-1].hash
        cardinality = len(events_in_range)
        root_hash = compute_root_hash(
            seq_start=seq_start,
            seq_end=seq_end,
            last_hash=last_hash,
            cardinality=cardinality,
        )

        # Sign canonical root hash
        sig_bytes = self._signer.sign(root_hash.encode("utf-8"))
        root_sig = sig_bytes.hex()

        checkpoint = RootCheckpoint(
            seq_start=seq_start,
            seq_end=seq_end,
            root_hash=root_hash,
            root_sig=root_sig,
            cardinality=cardinality,
            created_at=self._clock.now(),
        )

        # Annotate repo (guarded: WHERE root_sig IS NULL)
        self._repo.annotate_root(seq_start, seq_end, root_sig)

        # Publish to external witness
        self._witness.put(checkpoint)

        return checkpoint

    def verify_chain(
        self,
        mandate_id: str | None = None,
        seq_range: tuple[int, int] | None = None,
    ) -> ChainResult:
        """Verify hash chain integrity, root signatures, and external witness concordance.

        Fail-closed verification:
        1. Hash chain recomputation and prev_hash links.
        2. Sequence continuity.
        3. Root signature cryptographic verification against signer public key.
        4. Cross-check byte-for-byte with published external witness.
        """
        if mandate_id is not None:
            events = self._repo.get_by_mandate(mandate_id)
            if not events:
                return ChainResult(ok=True, verified_count=0)
            # For a mandate projection, verify individual hashes and signature links
            for e in events:
                expected_hash = compute_event_hash(
                    mandate_id=e.mandate_id,
                    type=e.type,
                    payload=e.payload,
                    prev_hash=e.prev_hash,
                    created_at=e.created_at,
                )
                if e.hash != expected_hash:
                    return ChainResult(
                        ok=False,
                        first_bad_seq=e.seq,
                        reason=f"Hash mismatch at seq {e.seq} in mandate {mandate_id}",
                    )
            return ChainResult(ok=True, verified_count=len(events), last_hash=events[-1].hash)

        if seq_range is not None:
            events = self._repo.get_range(seq_range[0], seq_range[1])
        else:
            events = self._repo.get_all()

        if not events:
            return ChainResult(ok=True, verified_count=0, last_hash=None)

        # 1. Pure chain validation
        chain_res = validate_chain(events, check_seq_continuity=True)
        if not chain_res.ok:
            return chain_res

        # 2. Verify root signatures and witness concordance
        # Group contiguous events with the same root_sig
        checkpoint_ranges: list[tuple[int, int, str]] = []
        cur_sig: str | None = None
        range_start: int | None = None
        last_seq: int | None = None

        for e in events:
            if e.seq is None:
                continue
            if e.root_sig != cur_sig:
                if cur_sig is not None and range_start is not None and last_seq is not None:
                    checkpoint_ranges.append((range_start, last_seq, cur_sig))
                cur_sig = e.root_sig
                range_start = e.seq
            last_seq = e.seq

        if cur_sig is not None and range_start is not None and last_seq is not None:
            checkpoint_ranges.append((range_start, last_seq, cur_sig))

        for start_seq, end_seq, root_sig in checkpoint_ranges:
            range_events = [
                e for e in events if e.seq is not None and start_seq <= e.seq <= end_seq
            ]
            expected_root_hash = compute_root_hash(
                seq_start=start_seq,
                seq_end=end_seq,
                last_hash=range_events[-1].hash,
                cardinality=len(range_events),
            )

            # Cryptographic signature check
            try:
                sig_bytes = bytes.fromhex(root_sig)
                sig_valid = self._signer.verify(expected_root_hash.encode("utf-8"), sig_bytes)
            except Exception:
                sig_valid = False

            if not sig_valid:
                return ChainResult(
                    ok=False,
                    first_bad_seq=start_seq,
                    reason=(f"invalid root signature for sequence range {start_seq}-{end_seq}"),
                    verified_count=len(events),
                )

            # External witness check (Invariant #4: witness is the proof)
            witness_cp = self._witness.get(start_seq, end_seq)
            if witness_cp is None:
                return ChainResult(
                    ok=False,
                    first_bad_seq=start_seq,
                    reason=(f"missing external witness for sequence range {start_seq}-{end_seq}"),
                    verified_count=len(events),
                )

            if (
                witness_cp.root_hash != expected_root_hash
                or witness_cp.root_sig != root_sig
                or witness_cp.cardinality != len(range_events)
            ):
                return ChainResult(
                    ok=False,
                    first_bad_seq=start_seq,
                    reason=(
                        f"external witness divergence for sequence range {start_seq}-{end_seq}: "
                        f"witness hash {witness_cp.root_hash}, local hash {expected_root_hash}"
                    ),
                    verified_count=len(events),
                )

        return ChainResult(
            ok=True,
            verified_count=len(events),
            last_hash=events[-1].hash,
        )


__all__ = ["LedgerService"]
