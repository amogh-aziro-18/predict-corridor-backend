"""OS-native TLS trust store for outbound HTTPS (corporate Windows environments)."""

from __future__ import annotations

_configured = False


def configure_os_ssl_trust() -> None:
    """
    Inject the native OS certificate store into Python's ssl module once.

    Required on some Windows/corporate setups where the default Python CA bundle
    does not include the corporate root CA. Verification remains enabled — this
    does not disable TLS checks.
    """
    global _configured
    if _configured:
        return
    import truststore

    truststore.inject_into_ssl()
    _configured = True


def is_os_ssl_trust_configured() -> bool:
    """Test/diagnostic helper — whether inject_into_ssl has been applied."""
    return _configured
