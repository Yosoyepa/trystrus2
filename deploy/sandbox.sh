#!/usr/bin/env bash
# Run a TryTrust command inside a bubblewrap sandbox.
#
#   deploy/sandbox.sh tick
#   deploy/sandbox.sh ask "flight to Cordoba"
#
# What the agent process can still reach:
#   read-only  /usr /lib /bin /etc/ssl   (interpreter + CA bundle)
#   read-only  the repo
#   writable   var/  only
#   network    yes -- it must reach the model and the merchant
#
# What it cannot reach: your home directory, SSH keys, cloud credentials,
# other processes, or any path outside the two mounts above. Containment is
# not prevention: it is the assumption that one day something gets through,
# and a decision made in advance about how far it gets.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec bwrap \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
  --ro-bind /bin /bin --ro-bind /sbin /sbin \
  --ro-bind /etc/ssl /etc/ssl --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --ro-bind /etc/ca-certificates /etc/ca-certificates \
  --ro-bind "$REPO" "$REPO" \
  --bind "$REPO/var" "$REPO/var" \
  --proc /proc --dev /dev --tmpfs /tmp \
  --unshare-pid --unshare-ipc --unshare-uts \
  --die-with-parent --new-session \
  --chdir "$REPO" \
  --setenv PYTHONDONTWRITEBYTECODE 1 \
  /usr/bin/uv run python -m src.agent.cli "$@"
