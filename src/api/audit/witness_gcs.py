"""Google Cloud Storage versioned witness adapter for immutable evidence roots."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from .models import RootCheckpoint


class GCSWitness:
    """GCS witness storage for published root checkpoints (decisions #7, #11).

    Roots are stored under `roots/{seq_start}-{seq_end}.json`.
    Invariants:
    - Immutability: uploads enforce `if_generation_match=0` (no overwriting).
    - Public accountability: external witness is verified against internal checkpoints.
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._bucket_name = bucket_name or os.environ.get("AVAL_WITNESS_BUCKET", "")
        if not self._bucket_name:
            raise ValueError("GCS witness bucket name required (set AVAL_WITNESS_BUCKET)")
        self._client: Any = None
        self._bucket: Any = None

    def _get_bucket(self) -> Any:
        if self._bucket is None:
            try:
                from google.cloud import storage

                self._client = storage.Client()
                self._bucket = self._client.bucket(self._bucket_name)
            except ImportError as exc:
                raise RuntimeError(
                    "google-cloud-storage package is required to use GCSWitness"
                ) from exc
        return self._bucket

    def put(self, checkpoint: RootCheckpoint) -> None:
        """Upload immutable checkpoint to versioned bucket."""
        bucket = self._get_bucket()
        blob_name = f"roots/{checkpoint.seq_start}-{checkpoint.seq_end}.json"
        blob = bucket.blob(blob_name)
        payload = json.dumps(checkpoint.to_dict(), indent=2)

        try:
            # Enforce that object does not exist yet (if_generation_match=0)
            blob.upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=0,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to publish checkpoint {blob_name} to GCS witness: {exc}"
            ) from exc

    def get(self, seq_start: int, seq_end: int) -> RootCheckpoint | None:
        """Download checkpoint from witness bucket."""
        bucket = self._get_bucket()
        blob_name = f"roots/{seq_start}-{seq_end}.json"
        blob = bucket.blob(blob_name)

        try:
            if not blob.exists():
                return None
            data_str = blob.download_as_text()
            d = json.loads(data_str)
            return RootCheckpoint(
                seq_start=int(d["seq_start"]),
                seq_end=int(d["seq_end"]),
                root_hash=str(d["root_hash"]),
                root_sig=str(d["root_sig"]),
                cardinality=int(d["cardinality"]),
                created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch checkpoint {blob_name} from GCS witness: {exc}"
            ) from exc


__all__ = ["GCSWitness"]
