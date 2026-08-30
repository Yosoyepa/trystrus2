"""Typed bridge failures. Every guard rejection maps to a contract reason
(`aval/contracts/rappi-bridge.yaml`): 409 family, 402, 423, 502."""

from __future__ import annotations


class BridgeError(Exception):
    """Base class; `reason` is the contract-facing reason code."""

    reason = "BRIDGE_ERROR"
    http_status = 409

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class Disabled(BridgeError):
    reason = "BRIDGE_DISABLED"
    http_status = 423


class CapExceeded(BridgeError):
    reason = "BRIDGE_CAP_EXCEEDED"
    http_status = 409


class CartNotClean(BridgeError):
    reason = "BRIDGE_CART_NOT_CLEAN"
    http_status = 409


class PriceDrift(BridgeError):
    reason = "BRIDGE_PRICE_DRIFT"
    http_status = 409


class AddressMismatch(BridgeError):
    reason = "BRIDGE_ADDRESS_MISMATCH"
    http_status = 409


class ApprovalInvalid(BridgeError):
    reason = "BRIDGE_APPROVAL_INVALID"
    http_status = 409


class ApprovalExpired(BridgeError):
    reason = "BRIDGE_APPROVAL_EXPIRED"
    http_status = 409


class DryRunMismatch(BridgeError):
    reason = "BRIDGE_DRY_RUN_MISMATCH"
    http_status = 423


class SessionExpired(BridgeError):
    reason = "BRIDGE_SESSION_EXPIRED"
    http_status = 409


class MinAmountRejected(BridgeError):
    """Rappi refused pre-capture (e.g. store minimum); no money moved."""

    reason = "MERCHANT_MIN_AMOUNT"
    http_status = 402


class UncertainState(BridgeError):
    """Clicked without confirmation: NEVER re-click; reconcile by hand."""

    reason = "BRIDGE_UNCERTAIN"
    http_status = 502


class CardRequires3ds(BridgeError):
    """Fraud-flagged cards need a bank 3DS challenge only the app/browser
    can complete — placing the order would create-then-cancel it."""

    reason = "BRIDGE_CARD_3DS_REQUIRED"
    http_status = 409


class CashPaymentRefused(BridgeError):
    """Cash orders sit outside the mandate story and get cancelled by Rappi."""

    reason = "BRIDGE_CASH_NOT_ALLOWED"
    http_status = 409


class PaymentUnresolved(BridgeError):
    """No usable payment method matching the mandate's instrument."""

    reason = "BRIDGE_PAYMENT_UNRESOLVED"
    http_status = 409


class ExecutionConflict(BridgeError):
    """Two workers raced to the click; the optimistic guard let one win."""

    reason = "BRIDGE_EXECUTION_CONFLICT"
    http_status = 409


class RappiError(BridgeError):
    reason = "BRIDGE_RAPPI_ERROR"
    http_status = 502
