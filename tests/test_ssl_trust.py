"""OS SSL trust store wiring (Phase 8A.1)."""

from __future__ import annotations

import app.core.ssl_trust as ssl_trust


def test_configure_os_ssl_trust_calls_inject_once(monkeypatch) -> None:
    ssl_trust._configured = False
    calls: list[int] = []

    def fake_inject() -> None:
        calls.append(1)

    monkeypatch.setattr("truststore.inject_into_ssl", fake_inject)

    ssl_trust.configure_os_ssl_trust()
    ssl_trust.configure_os_ssl_trust()

    assert len(calls) == 1
    assert ssl_trust.is_os_ssl_trust_configured() is True


def test_main_import_configures_os_ssl_trust() -> None:
    """app.main must configure truststore before serving outbound HTTPS."""
    assert ssl_trust.is_os_ssl_trust_configured() is True
