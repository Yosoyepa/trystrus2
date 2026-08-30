"""VuelaYa — the merchant deployable owned by Dev 3.

It deliberately contains catalogue, checkout and inbound rail-webhook code,
but no policy gate and no direct agent payment tool.  The only route to the
rail is the charge service after the kernel's verify response says APPROVED.
"""
