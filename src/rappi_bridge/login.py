"""Rappi OTP login capture ("Config Rappi") — decision 0030 custody rules.

Opens a headful browser on THIS machine; the owner completes their normal
phone + WhatsApp OTP login; the session token (`Bearer ft.…`) is captured
passively from the `/ms/application-user/auth` response and written to the
session file (chmod 600). The token never leaves this host; the API only
ever exposes masked labels.
"""

from __future__ import annotations

import json
import stat
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import BridgeConfig
from .errors import BridgeError

AUTH_MARKER = "/ms/application-user/auth"
LOGIN_URL = "https://www.rappi.com.co/login"
LOGIN_TIMEOUT_S = 300
DEFAULT_COORDS = ("4.7110", "-74.0721")  # Bogotá


def mask_name(name: str) -> str:
    parts = name.split()
    if not parts:
        return "—"
    first = parts[0]
    rest = " ".join(f"{p[0]}." for p in parts[1:] if p)
    return f"{first} {rest}".strip()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "—"
    return f"{local[:1]}{'•' * max(len(local) - 1, 1)}@{domain}"


class LoginFlow:
    """Thread-safe status machine: idle → waiting_login → captured | error."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        launcher: Callable[[], dict[str, Any]] | None = None,
        prober: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._launches = 0
        self._status: dict[str, Any] = self._initial_status()
        # injectable for tests
        self._launcher = launcher or self._launch_browser
        self._prober = prober or self._probe_account

    def _initial_status(self) -> dict[str, Any]:
        return {
            "state": "idle",
            "has_token": self._config.session_file.exists(),
            "account_label": None,
            "address_label": None,
            "error": None,
            "started_at": None,
        }

    # -- public API ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._status["state"] == "waiting_login":
                return dict(self._status)  # already running: one window only
            self._status = {
                "state": "waiting_login",
                "has_token": self._config.session_file.exists(),
                "account_label": None,
                "address_label": None,
                "error": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
            self._launches += 1
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return dict(self._status)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            try:
                self._config.session_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._status = self._initial_status()
            return dict(self._status)

    @property
    def launches(self) -> int:
        return self._launches

    def connect_with_token(self, token: str, *, device_id: str | None = None) -> dict[str, Any]:
        """Plan B: paste a token by hand (DevTools → services.grability…
        request → Authorization header). Synchronous; raises on failure."""
        token = token.strip()
        with self._lock:
            self._status = {
                "state": "waiting_login",
                "has_token": self._config.session_file.exists(),
                "account_label": None,
                "address_label": None,
                "error": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        try:
            if not token.startswith("ft."):
                raise BridgeError(
                    "the token must start with 'ft.' (Authorization header of "
                    "services.grability.rappi.com)"
                )
            account = self._prober(token)
            self._write_session(
                token=token,
                device_id=device_id or uuid.uuid4().hex,
                lat=str(account.get("lat") or DEFAULT_COORDS[0]),
                lng=str(account.get("lng") or DEFAULT_COORDS[1]),
            )
            with self._lock:
                self._status = {
                    "state": "captured",
                    "has_token": True,
                    "account_label": account.get("account_label"),
                    "address_label": account.get("address_label"),
                    "error": None,
                    "started_at": self._status["started_at"],
                }
            return dict(self._status)
        except Exception as exc:
            with self._lock:
                self._status = {
                    "state": "error",
                    "has_token": self._config.session_file.exists(),
                    "account_label": None,
                    "address_label": None,
                    "error": str(exc)[:300],
                    "started_at": self._status["started_at"],
                }
            raise

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        try:
            captured = self._launcher()
            token = str(captured.get("token", ""))
            if not token.startswith("ft."):
                raise ValueError("no ft. session token captured")
            account = self._prober(token)
            self._write_session(
                token=token,
                device_id=str(captured.get("device_id") or uuid.uuid4()),
                lat=str(account.get("lat") or DEFAULT_COORDS[0]),
                lng=str(account.get("lng") or DEFAULT_COORDS[1]),
            )
            with self._lock:
                self._status = {
                    "state": "captured",
                    "has_token": True,
                    "account_label": account.get("account_label"),
                    "address_label": account.get("address_label"),
                    "error": None,
                    "started_at": self._status["started_at"],
                }
        except Exception as exc:
            with self._lock:
                self._status = {
                    "state": "error",
                    "has_token": self._config.session_file.exists(),
                    "account_label": None,
                    "address_label": None,
                    "error": str(exc)[:300],
                    "started_at": self._status["started_at"],
                }

    def _write_session(self, *, token: str, device_id: str, lat: str, lng: str) -> None:
        path: Path = self._config.session_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"token": token, "deviceId": device_id, "lat": lat, "lng": lng},
                indent=2,
            ),
            encoding="utf-8",
        )
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600

    def _probe_account(self, token: str) -> dict[str, Any]:
        """One authenticated whoami + active address for masked labels."""
        from .rappi import RappiClient, RappiSession

        client = RappiClient(
            RappiSession(token=token, device_id="aval-login", lat="", lng=""),
            base_url=self._config.rappi_base_url,
            timeout_s=self._config.http_timeout_s,
        )
        user = client.whoami()
        address = client.active_address()
        return {
            "account_label": f"{mask_name(str(user.get('name', '')))} · "
            f"{mask_email(str(user.get('email', '')))}",
            "address_label": (
                f"{(address or {}).get('tag') or 'Dirección'} "
                f"({(address or {}).get('address', '')})"
                if address
                else None
            ),
            "lat": str((address or {}).get("lat") or ""),
            "lng": str((address or {}).get("lng") or ""),
        }

    def _launch_browser(self) -> dict[str, Any]:
        """Headful capture; the OWNER performs the OTP login, we only listen.

        No user-agent spoofing: Rappi's antifraud rejects the login with
        400 "looks_bad" when the client hints (real desktop Chrome) mismatch
        a claimed mobile UA. The persistent profile keeps the device
        fingerprint stable so trust accumulates across attempts. This is a
        dedicated profile — the owner's personal Chrome profile is untouched.
        """
        from playwright.sync_api import sync_playwright

        captured: dict[str, Any] = {}

        def on_response(response: Any) -> None:
            if AUTH_MARKER not in response.url or response.status != 200:
                return
            request = response.request
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer ft."):
                captured["token"] = auth.removeprefix("Bearer ")
                captured["device_id"] = request.headers.get("deviceid", "")

        profile = self._config.login_profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        window = ["--window-size=460,900"]
        with sync_playwright() as p:
            context = None
            if self._config.login_browser:
                try:
                    context = p.chromium.launch_persistent_context(
                        str(profile),
                        headless=False,
                        channel=self._config.login_browser,
                        args=window,
                    )
                except Exception:
                    context = None  # channel unavailable: fall back below
            if context is None:
                context = p.chromium.launch_persistent_context(
                    str(profile), headless=False, args=window
                )
            try:
                page = context.new_page()
                page.on("response", on_response)
                page.goto(LOGIN_URL)
                deadline = datetime.now(UTC).timestamp() + LOGIN_TIMEOUT_S
                while "token" not in captured:
                    if datetime.now(UTC).timestamp() > deadline:
                        raise TimeoutError("login OTP window timed out (5 min)")
                    page.wait_for_timeout(1500)
            finally:
                context.close()
        return captured
