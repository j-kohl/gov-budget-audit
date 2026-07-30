"""Shared HTTP client construction.

Two things here that matter for these particular sources.

**Trust store.** Some Government of Canada hosts — `tpsgc-pwgsc.gc.ca` among
them — serve certificates issued under Entrust roots. Mozilla distrusted Entrust
in 2024 and certifi mirrors Mozilla, so the bundled roots no longer verify these
hosts even though every OS trust store and browser still does. Where the
`truststore` package is available we defer to the platform trust store, which
keeps verification fully enabled while matching what a browser would accept.
Verification is never disabled.

**Chain ordering.** The same hosts sometimes serve their chain out of order
(leaf, root, intermediate). OpenSSL is stricter about this than schannel; using
the OS store sidesteps it on Windows and macOS.
"""

from __future__ import annotations

import logging
import ssl

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "gov-budget-audit (+https://github.com/j-kohl/gov-budget-audit)"

_ssl_context: ssl.SSLContext | bool | None = None


def ssl_context() -> ssl.SSLContext | bool:
    """Return an SSL context backed by the OS trust store when possible."""
    global _ssl_context
    if _ssl_context is not None:
        return _ssl_context

    try:
        import truststore

        _ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        log.debug("using OS trust store for TLS verification")
    except ImportError:
        # certifi defaults still work for every source except the Entrust ones.
        log.debug("truststore unavailable; falling back to certifi")
        _ssl_context = True
    return _ssl_context


def build_client(
    *,
    timeout: httpx.Timeout | float = 60.0,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    """Build an httpx client with the project's trust and header defaults."""
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=merged,
        verify=ssl_context(),
    )
