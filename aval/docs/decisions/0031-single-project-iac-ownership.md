# 0031 — One owner for project-wide IaC resources

Date: 2026-08-30 · Status: accepted
Workstream: 4
Supersedes: none

## Context

`dev` and `prod` use separate GCS state prefixes but currently share the GCP
project `trytrust`. The first production plan therefore tried to create APIs,
the `aval` Artifact Registry repository and signing-secret names that already
belonged to the dev state. Applying that plan would either fail on name
collisions or leave one real resource under two independent state owners.

## Chose

The dev state is the single bootstrap owner of project-wide APIs and the
`aval` Artifact Registry repository. Production reads the existing repository
as data and creates only `aval-prod-*` resources in its isolated state. Signing
keys are not shared: production creates `aval-prod-{issuer,merchant,yuno-*}`
secrets and each service receives its exact Secret Manager key name. Existing
dev secret IDs remain unchanged to avoid an unsafe key migration.

## Rejected

- Importing the same APIs/repository/secrets into both states: two owners can
  silently undo each other's lifecycle decisions.
- Sharing signing keys between dev and prod: compromise of a dev runtime would
  expose a key trusted in production.
- Creating a second GCP project now: it gives stronger isolation, but no
  production project/billing/WIF boundary currently exists and it would turn
  this release into a platform migration.

## Why

OpenTofu state is an ownership boundary, not merely an inventory. A resource
must have one writer. Keeping the already-applied bootstrap resources in dev
avoids recreation, while environment-prefixed databases, services, jobs,
runtime identities and keys give production an independent lifecycle and
cryptographic boundary inside the project available to the team.

## Does not solve

Both environments still share project-level quotas, billing, API enablement
and some IAM blast radius. Production also depends on the bootstrap state
having created Artifact Registry first. A separate GCP project remains the
post-hackathon hardening path.

## Consequences for contracts

None (deployment process only).
