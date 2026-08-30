"""Golden vectors for the canonical serialization (TX-10 groundwork).

The expected JSON strings below are hand-written literals and the digests were
computed from those literals independently of the implementation under test,
so a regression here cannot hide behind "the same code computed both sides".
"""

from __future__ import annotations

import datetime
import decimal
import enum
import hashlib

import pytest
from src.api.canonical import canonical_bytes, canonical_json, normalize_utc, sha256_hex

GOLDEN_V1_LITERAL = (
    '{"a":1,"amt":"10.50","at":"2026-08-29T12:00:00Z","flag":true,"nested":{"x":5,"y":10}}'
)
GOLDEN_V1_SHA256 = "8460b7fc52bd7405565bb5a4fcc7f04d73865ceef2cd2fe8ea66126ffd662c41"
GOLDEN_V2_LITERAL = '{"b":[1,2],"ciudad":"Bogotá"}'
GOLDEN_V2_SHA256 = "e89c3087429dfa390632f2c893f86163f7e4e0c6edd4445b077651c5a07993a7"

UTC = datetime.UTC


class _Side(enum.Enum):
    BUY = "buy"


def test_golden_v1_ordering_decimal_utc_bool():
    value = {
        "flag": True,
        "nested": {"y": 10, "x": 5},
        "at": datetime.datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        "amt": decimal.Decimal("10.50"),
        "a": 1,
    }
    assert canonical_json(value) == GOLDEN_V1_LITERAL
    assert sha256_hex(value) == GOLDEN_V1_SHA256


def test_golden_v2_unicode_and_array():
    value = {"ciudad": "Bogotá", "b": [1, 2]}
    assert canonical_json(value) == GOLDEN_V2_LITERAL
    digest = hashlib.sha256(GOLDEN_V2_LITERAL.encode("utf-8")).hexdigest()
    assert digest == GOLDEN_V2_SHA256
    assert sha256_hex(value) == GOLDEN_V2_SHA256


def test_non_utc_datetime_normalizes_to_z():
    bogota = datetime.timezone(datetime.timedelta(hours=-5))
    value = datetime.datetime(2026, 8, 29, 7, 0, tzinfo=bogota)
    assert normalize_utc(value) == "2026-08-29T12:00:00Z"
    assert canonical_json({"at": value}) == '{"at":"2026-08-29T12:00:00Z"}'


def test_key_order_and_nesting_do_not_change_the_digest():
    a = {"x": 1, "y": {"b": 2, "a": [1, {"z": 3, "w": 4}]}}
    b = {"y": {"a": [1, {"w": 4, "z": 3}], "b": 2}, "x": 1}
    assert canonical_json(a) == canonical_json(b)


def test_floats_are_rejected_at_depth():
    with pytest.raises(TypeError, match="floating point"):
        canonical_json({"ok": 1, "deep": {"list": [1, 2.5]}})


def test_nan_is_rejected():
    with pytest.raises(TypeError):
        canonical_json({"x": float("nan")})


def test_naive_datetime_is_rejected():
    naive = datetime.datetime(2026, 8, 29, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_json({"at": naive})


def test_sets_are_rejected_as_unordered():
    with pytest.raises(TypeError, match="unordered"):
        canonical_json({"tags": {"a", "b"}})


def test_non_string_keys_are_rejected():
    with pytest.raises(TypeError, match="strings"):
        canonical_json({1: "a"})


def test_enum_and_decimal_scalars():
    value = {"side": _Side.BUY, "price": decimal.Decimal("0.00000001")}
    assert canonical_json(value) == '{"price":"0.00000001","side":"buy"}'


def test_bytes_are_rejected():
    with pytest.raises(TypeError, match="bytes"):
        canonical_json({"raw": b"\x00"})


def test_canonical_bytes_is_utf8_of_canonical_json():
    value = {"ciudad": "Bogotá"}
    assert canonical_bytes(value) == '{"ciudad":"Bogotá"}'.encode()
