# 0021 — The DDL has no table for passkeys

Date: 2026-08-29 · Status: accepted
Workstream: 3
Supersedes: none (fills a gap in schemas.md §6)

## Context

Decision #3 makes the passkey the proof of human intent: the WebAuthn challenge
is the mandate's canonical hash, so the biometric gesture signs the exact
permission. It gates mandate creation, limit changes and revocation. It is the
heart of gate G1.

`schemas.md` §6 defines `mandates`, `escalations`, `payment_instruments`,
`offers`, and Dev 2's and Dev 1's tables. **None of them can hold a WebAuthn
credential.** A passkey assertion is verified against a stored credential id,
public key and signature counter; without somewhere to keep those, registration
cannot happen and no assertion can ever be checked.

The gap blocks the workstream's first deliverable, so it is being closed before
Fase 1 rather than discovered mid-build.

## Chose

A new table owned by Dev 3, added in Dev 3's migration:

```sql
CREATE TABLE webauthn_credentials (
  credential_id TEXT PRIMARY KEY,          -- base64url, from the authenticator
  user_id       TEXT NOT NULL,
  public_key    BYTEA NOT NULL,            -- COSE key
  sign_count    BIGINT NOT NULL DEFAULT 0, -- monotonic; clone detection
  transports    TEXT[],
  aaguid        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ
);
CREATE INDEX ON webauthn_credentials (user_id);
```

Plus a short-lived challenge store, so a challenge is single-use and cannot be
replayed:

```sql
CREATE TABLE webauthn_challenges (
  challenge   TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  mandate_id  TEXT,                        -- set when the challenge is a mandate hash
  purpose     TEXT NOT NULL,               -- register | activate | revoke
  expires_at  TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ
);
```

`sign_count` is stored and enforced to be non-decreasing: a replayed or cloned
authenticator shows up as a counter that went backwards.

## Rejected

* **Keep credentials in `mandates.claims`.** Credentials belong to a person and
  outlive any one mandate; embedding them would duplicate them per mandate and
  corrupt the signed claims object.
* **A single table with the challenge as a column.** Challenges are ephemeral
  and single-use, credentials are durable. One lifetime per table.
* **No challenge table, sign the mandate hash statelessly.** Then a challenge is
  replayable, and "the gesture signs this exact permission" becomes "the gesture
  signed this permission at some point".

## Why

Cheap and confined: both tables are Dev 3's, in Dev 3's migration, read by
nobody else. No other workstream's tables or queries change. It closes a gap
that would otherwise surface as "the passkey ceremony cannot be built" halfway
through day 1.

## Does not solve

**Lost-passkey recovery**, already declared out of scope by decision #3. A user
who loses their authenticator cannot revoke their own mandate through the
normal path — the demo's answer would be an operator-side revocation, which we
have not built.

**Credential portability.** Credentials are bound to `rpId`; changing the domain
invalidates every stored passkey. Relevant because ADR-018 makes the domain a
day-0 purchase and the fallback is `localhost`.

## Consequences for contracts

* `schemas.md` §6 — two tables added under the `[3]` block. **Additive** (v1.1);
  no existing table or column changes.
* No `api.yaml` change: `PasskeyAssertion` is already `additionalProperties:
  true` and carries the WebAuthn response as-is.
* Dev 3's alembic migration creates both.
