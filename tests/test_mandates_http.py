"""The identity endpoints over HTTP, against a real database.

Uses a software authenticator so the passkey ceremony runs for real rather
than being mocked out. That matters: the ceremony is decision #3's proof of
human intent, and a test that stubs the verification proves nothing about it.

What is *not* simulated is the human. A software authenticator can produce a
valid assertion because it holds the private key — which is precisely why the
demo needs a hardware gesture, and why an agent cannot forge consent.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import deps
from api.config import Settings
from api.services.keys import KeyStore
from api.services.mandate_registry import MandateRegistry
from api.services.passkey import PasskeyService
from trustlib import sdjwt
from trustlib.jose import jwk_from_dict

pytestmark = pytest.mark.asyncio

RP_ID = "localhost"
ORIGIN = "http://localhost:5173"


# ==========================================================================
# A software authenticator (soft-webauthn style, minimal)
# ==========================================================================
class SoftAuthenticator:
    """Signs WebAuthn assertions with an ES256 key, like a real authenticator.

    Enough of the ceremony to exercise our verification path: authenticator
    data with the UV flag set, clientDataJSON carrying the challenge, and a
    signature over their concatenation.
    """

    def __init__(self, rp_id: str = RP_ID) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key = ec.generate_private_key(ec.SECP256R1())
        self.rp_id = rp_id
        self.credential_id = b"soft-credential-0001"
        self.sign_count = 0

    def _authenticator_data(self, *, attested: bool) -> bytes:
        import hashlib
        import struct

        rp_id_hash = hashlib.sha256(self.rp_id.encode()).digest()
        # UP (0x01) | UV (0x04) — user present and user verified.
        flags = 0x01 | 0x04
        if attested:
            flags |= 0x40
        data = rp_id_hash + bytes([flags]) + struct.pack(">I", self.sign_count)

        if attested:
            aaguid = b"\x00" * 16
            cred_id_len = struct.pack(">H", len(self.credential_id))
            data += aaguid + cred_id_len + self.credential_id + self._cose_key()
        return data

    def _cose_key(self) -> bytes:
        import cbor2

        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, challenge: str, ceremony: str) -> bytes:
        return json.dumps(
            {
                "type": ceremony,
                "challenge": challenge,
                "origin": ORIGIN,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()

    def register(self, challenge: str) -> dict:
        import cbor2

        client_data = self._client_data(challenge, "webauthn.create")
        auth_data = self._authenticator_data(attested=True)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": _b64u(self.credential_id),
            "rawId": _b64u(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64u(client_data),
                "attestationObject": _b64u(attestation),
            },
            "clientExtensionResults": {},
        }

    def assert_(self, challenge: str) -> dict:
        import hashlib

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        self.sign_count += 1
        client_data = self._client_data(challenge, "webauthn.get")
        auth_data = self._authenticator_data(attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": _b64u(self.credential_id),
            "rawId": _b64u(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64u(client_data),
                "authenticatorData": _b64u(auth_data),
                "signature": _b64u(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest_asyncio.fixture
async def client(session, tmp_path, monkeypatch):
    """The real app, with a throwaway key store and the test's session."""
    from api.db import get_session
    from api.main import app

    config = Settings(secrets_dir=tmp_path, rp_id=RP_ID, rp_origin=ORIGIN, gcp_project=None)
    deps.reset()
    deps._registry = MandateRegistry(KeyStore(config), config)
    deps._passkey = PasskeyService(config)

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://kernel") as http:
        yield http
    app.dependency_overrides.clear()
    deps.reset()


@pytest.fixture
def mandate_body(user_id):
    now = datetime.now(UTC)
    return {
        "user_id": user_id,
        "agent_id": "agt_flights",
        "currency": "USD",
        "scope": {"categories": ["flights"], "merchants": ["vuelaya"]},
        "limits": {
            "max_per_txn": "150",
            "total_budget": "400",
            "max_txn": {"count": 3, "period": "month"},
        },
        "validity": {
            "not_before": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
        },
        "conditions": {"<": [{"var": "offer.price"}, 150]},
    }


async def enrol(client, user_id: str) -> SoftAuthenticator:
    authenticator = SoftAuthenticator()
    begin = await client.post("/passkeys/register/begin", json={"user_id": user_id})
    assert begin.status_code == 200
    challenge = begin.json()["challenge"]

    complete = await client.post(
        "/passkeys/register/complete",
        json={
            "user_id": user_id,
            "challenge": challenge,
            "credential": authenticator.register(challenge),
        },
    )
    assert complete.status_code == 200, complete.text
    return authenticator


# ==========================================================================
# JWKS
# ==========================================================================
async def test_jwks_is_public_and_carries_both_curves(client):
    """A stranger must be able to fetch this without credentials."""
    response = await client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    keys = {k["kid"]: k for k in response.json()["keys"]}
    assert keys["v1"]["alg"] == "EdDSA"  # mandates
    assert keys["m1"]["alg"] == "ES256"  # AP2 Checkout JWTs
    assert all("d" not in k for k in keys.values())


# ==========================================================================
# Creation requires a registered human
# ==========================================================================
async def test_creating_a_mandate_without_a_passkey_is_refused(client, mandate_body):
    """An agent cannot complete a WebAuthn ceremony, so consent has nowhere
    to come from. Refusing here is the point, not an inconvenience."""
    response = await client.post("/mandates", json=mandate_body)

    assert response.status_code == 412
    assert "no passkey registered" in response.json()["detail"]


async def test_create_returns_a_draft_and_a_mandate_bound_challenge(client, mandate_body, user_id):
    await enrol(client, user_id)

    response = await client.post("/mandates", json=mandate_body)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["jti"].startswith("mdt_")
    assert body["passkey_challenge"]["challenge"]

    # Nothing is signed yet: a draft cannot pay.
    view = await client.get(f"/mandates/{body['mandate_id']}")
    assert view.json()["status"] == "draft"


# ==========================================================================
# The full ceremony
# ==========================================================================
async def test_the_gesture_activates_and_signs_the_mandate(client, mandate_body, user_id):
    authenticator = await enrol(client, user_id)
    created = (await client.post("/mandates", json=mandate_body)).json()
    challenge = created["passkey_challenge"]["challenge"]

    response = await client.post(
        f"/mandates/{created['mandate_id']}/passkey/assert", json=authenticator.assert_(challenge)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["sd_jwt"]

    # And the signed mandate verifies against the published JWKS —
    # the whole point of decision #6.
    jwks = (await client.get("/.well-known/jwks.json")).json()
    keys = {k["kid"]: jwk_from_dict(k) for k in jwks["keys"]}
    claims = sdjwt.verify(body["sd_jwt"], keys)
    assert claims["jti"] == created["jti"]
    assert claims["limits"]["max_per_txn"] == "150"
    assert claims["vct"] == "mandate.payment.open.1"


async def test_the_same_assertion_cannot_be_replayed(client, mandate_body, user_id):
    authenticator = await enrol(client, user_id)
    created = (await client.post("/mandates", json=mandate_body)).json()
    challenge = created["passkey_challenge"]["challenge"]
    assertion = authenticator.assert_(challenge)

    first = await client.post(f"/mandates/{created['mandate_id']}/passkey/assert", json=assertion)
    second = await client.post(f"/mandates/{created['mandate_id']}/passkey/assert", json=assertion)

    assert first.status_code == 200
    assert second.status_code == 401


async def test_an_assertion_for_one_mandate_cannot_activate_another(client, mandate_body, user_id):
    """Collect a gesture for mandate A, present it against mandate B."""
    authenticator = await enrol(client, user_id)
    first = (await client.post("/mandates", json=mandate_body)).json()
    second = (await client.post("/mandates", json=mandate_body)).json()

    stolen = authenticator.assert_(first["passkey_challenge"]["challenge"])
    response = await client.post(f"/mandates/{second['mandate_id']}/passkey/assert", json=stolen)

    assert response.status_code == 401


# ==========================================================================
# Revocation
# ==========================================================================
async def test_revocation_requires_the_same_gesture_as_creation(client, mandate_body, user_id):
    """Taking authority away must not be easier to forge than granting it."""
    authenticator = await enrol(client, user_id)
    created = (await client.post("/mandates", json=mandate_body)).json()
    mandate_id = created["mandate_id"]
    await client.post(
        f"/mandates/{mandate_id}/passkey/assert",
        json=authenticator.assert_(created["passkey_challenge"]["challenge"]),
    )

    # No fresh challenge was issued for revocation, so a replayed one fails.
    stale = authenticator.assert_(created["passkey_challenge"]["challenge"])
    response = await client.post(f"/mandates/{mandate_id}/revoke", json=stale)

    assert response.status_code == 401


async def test_revocation_passkey_deletes_the_persisted_rail_token_under_two_seconds(
    client, mandate_body, user_id, session
):
    """M3's two kill switches, timed from the fresh revoke ceremony.

    The spy is important: a green 200 alone does not prove the opaque token
    actually travelled to the rail-side DELETE.
    """
    from time import perf_counter

    from trustlib.models import SetupToken

    class RailSpy:
        def __init__(self):
            self.deleted: list[str] = []

        async def create_setup_token(self, mandate_id):
            return SetupToken(
                setup_token_id="yst_test", approve_url="https://sim/approve", simulated=True
            )

        async def delete_payment_token(self, token_id):
            self.deleted.append(token_id)

    spy = RailSpy()
    deps._rail = spy
    authenticator = await enrol(client, user_id)
    body = {**mandate_body, "payment_method_ref": "ynt_live_token"}
    created = (await client.post("/mandates", json=body)).json()
    mandate_id = created["mandate_id"]
    active = await client.post(
        f"/mandates/{mandate_id}/passkey/assert",
        json=authenticator.assert_(created["passkey_challenge"]["challenge"]),
    )
    assert active.status_code == 200

    options = await client.post(f"/mandates/{mandate_id}/revoke/options")
    assert options.status_code == 200
    started = perf_counter()
    revoked = await client.post(
        f"/mandates/{mandate_id}/revoke",
        json=authenticator.assert_(options.json()["challenge"]),
    )
    elapsed = perf_counter() - started

    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    assert elapsed < 2
    assert spy.deleted == ["ynt_live_token"]
    from sqlalchemy import text

    instrument_status = (
        await session.execute(
            text("SELECT status FROM payment_instruments WHERE token_ref = 'ynt_live_token'")
        )
    ).scalar_one()
    assert instrument_status == "deleted"


async def test_reading_a_mandate_that_does_not_exist(client):
    assert (await client.get("/mandates/mdt_nope")).status_code == 404


async def test_listing_mandates_by_user(client, mandate_body, user_id):
    await enrol(client, user_id)
    await client.post("/mandates", json=mandate_body)
    await client.post("/mandates", json=mandate_body)

    response = await client.get("/mandates", params={"user_id": user_id})

    assert response.status_code == 200
    assert len(response.json()) == 2
