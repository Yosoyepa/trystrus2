"""Where this process is allowed to talk to.

`deploy/SECURITY.md` named an egress allowlist as an open gap and pointed at
VPC rules. That is the right production answer and it is also a promise nobody
can test, so this is the part that can be enforced here: every outbound call
the agent makes goes through one of two places -- the model client and the MCP
transport -- and both ask this module first.

It is not a substitute for network-level egress control; a compromised process
can still open a socket directly. It is a substitute for *nothing*, which is
what we had. An injected instruction that talks the agent into calling an
attacker's URL now fails at the allowlist and leaves an audit event.
"""
from __future__ import annotations
import os
from urllib.parse import urlparse

DEFAULT_HOSTS = ("api.openai.com", "localhost", "127.0.0.1", "::1")


class EgressDenied(Exception):
    def __init__(self, host: str, url: str):
        super().__init__(f"egress to {host!r} is not allowed")
        self.host = host
        self.url = url
        self.code = "EGRESS_DENIED"


def allowed_hosts() -> set[str]:
    extra = os.environ.get("TT_ALLOWED_HOSTS", "")
    hosts = set(DEFAULT_HOSTS)
    hosts.update(h.strip().lower() for h in extra.split(",") if h.strip())
    for var in ("LLM_BASE_URL", "TT_MCP_URL", "TT_VUELAYA_MCP_URL",
                "TT_MAMI_MCP_URL", "TT_WEBHOOK_URL"):
        configured = os.environ.get(var, "")
        if configured:
            host = urlparse(configured).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def check(url: str, *, conn=None, reason: str = "") -> str:
    """Raise unless `url`'s host is allowed. Returns the host on success."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise EgressDenied(host, url)
    if host not in allowed_hosts():
        if conn is not None:
            from . import audit
            audit.append(conn, "egress.denied",
                         {"host": host, "reason": reason}, relay=False)
        raise EgressDenied(host, url)
    return host
