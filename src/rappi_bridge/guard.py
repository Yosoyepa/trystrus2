"""Pure guards: the second, independent enforcement point (decision 0030).
The kernel enforces the mandate; the bridge enforces reality — the machine
that holds the money button checks the cap, the address, the cart and the
price again, by code, regardless of what any agent says."""

from __future__ import annotations

from decimal import Decimal

from .errors import AddressMismatch, CapExceeded, CartNotClean, PriceDrift


def parse_money(value: str | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def check_cap(total: Decimal, cap: Decimal) -> None:
    """Hardcoded machine cap: refuses to arm checkout above it even against
    a valid kernel approval (protects against misconfiguration)."""
    if total > cap:
        raise CapExceeded(
            f"checkout total {total} exceeds the bridge cap {cap}",
            detail={"total": str(total), "cap": str(cap)},
        )


def check_drift(approved_amount: Decimal, checkout_total: Decimal) -> None:
    """Cent-exact. A live run measured search 9,400 vs checkout 10,300 —
    drift is real, so the approved amount must equal the checkout total or
    the flow aborts with a re-quote requirement."""
    if approved_amount != checkout_total:
        raise PriceDrift(
            f"checkout total {checkout_total} differs from approved {approved_amount}",
            detail={
                "approved": str(approved_amount),
                "checkout_total": str(checkout_total),
            },
        )


def check_address(
    delivery_address_id: str | None, expected_address_id: str | None
) -> None:
    """A real run split the delivery between local coords and the account's
    active address. If the mandate authorizes an address and the checkout
    points elsewhere, abort."""
    if expected_address_id is None:
        return
    if delivery_address_id != expected_address_id:
        raise AddressMismatch(
            f"checkout delivers to {delivery_address_id!r}, mandate authorizes "
            f"{expected_address_id!r}",
            detail={
                "checkout_address_id": delivery_address_id,
                "expected_address_id": expected_address_id,
            },
        )


def check_cart_clean(carts: list[dict]) -> None:
    """The run proved PUT replaces cart contents and DELETE is broken; a
    residual cart from another session must fail the run, not ride along."""
    for cart in carts:
        for store in cart.get("stores", []) or []:
            if store.get("products"):
                raise CartNotClean(
                    f"cart for store_type {cart.get('store_type')!r} is not empty",
                    detail={"store": store.get("name")},
                )
