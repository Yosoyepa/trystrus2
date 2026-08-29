# 0021 — Escalation TTL by level: 120 s standard, 300 s with passkey UV

Date: 2026-08-29 · Status: accepted · Workstream: 2 + 4
Supersedes: the single 120 s timeout of decision #13 (L3 behavior unchanged)

## Context

The fraud research (D-02/D-04) surfaced a tension: 120 s fail-closed is right
for a bot tap-approval, but an L3+ escalation requires a WebAuthn ceremony
with user verification reached via deep-link from Telegram/WhatsApp into the
web app — a channel hop that routinely exceeds 120 s. Regulator precedents
(RBI 12 h cooling-off, UK 72 h delays) show high-risk approvals get longer
windows, not weaker closure.

## Chose

- **L3 (standard escalation):** 120 s fail-closed, unchanged — bot
  out-of-band approval with the decision diff.
- **L3+ (high-risk escalation: amount ≥ 0.7 × `max_per_txn`, budget ≥ 80 %,
  first escalation of a mandate, or fresh agent key):** 300 s window with
  RFC 9470 semantics (`max_age`), requiring WebAuthn
  `userVerification: "required"` signing the hash of the diff (SPC pattern).
- Both levels fail closed: no answer at deadline ⇒ REJECT + compensation +
  `escalation.expired`. Silence never approves anything.

## Rejected

- Uniform 120 s: the UV ceremony would time out on latency, converting a
  security feature into a denial-of-service on legitimate high-value buys.
- Uniform 300 s: doubles the exposure window for the common case for no
  security gain.
- Longer windows (minutes/hours cooling-off): a hackathon demo cannot afford
  them; documented as Fase 4 (R-COOLING) for first-beneficiary scenarios.

## Why

Fail-closed is the invariant that matters; the window length is a per-level
UX parameter. Splitting it keeps the judges' live-revocation story instant
(L3) while giving the passkey ceremony a realistic budget (L3+). RFC 9470
gives us standard language (`insufficient_user_authentication`, `max_age`)
for the challenge between gate and UI.

## Does not solve

Coercion during the 300 s window is only mitigated (out-of-band channel, diff
display, UV biometrics), not eliminated. Latency of the channel hop must be
measured at M3 — if it exceeds 300 s in practice, the level thresholds need
retuning, not the TTL.

## Consequences for contracts

`api.yaml`: `Escalation.level` (`L3|L3+`) + both TTLs documented on
`/escalations/{id}/resolve`. `schemas.md` §5 (resume semantics note) and §7
(`STEPUP_*` reason codes). Realization: plan F1.5.
