"""
Certificat TLS auto-signé (Nginx / iPXE TRUST) — lecture expiration et renouvellement.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)

_RENEW_SCRIPT = Path("/usr/local/sbin/ipxe-renew-tls-cert")
_EXPIRY_SOON_DAYS = 7
_NOT_AFTER_RE = re.compile(
    r"notAfter\s*=\s*(?P<date>.+)", re.IGNORECASE
)


@dataclass(frozen=True)
class TlsCertStatus:
    present: bool
    cert_path: str
    not_after: datetime | None
    days_remaining: int | None
    expires_soon: bool
    error: str | None = None

    @property
    def is_https(self) -> bool:
        return self.present


def _server_cert_path() -> Path:
    return Path(settings.ssl_dir) / "server.crt"


def _parse_openssl_enddate(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    m = _NOT_AFTER_RE.search(text)
    if not m:
        return None
    date_str = m.group("date").strip()
    for fmt in (
        "%b %d %H:%M:%S %Y %Z",
        "%b %d %H:%M:%S %Y GMT",
    ):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Format notAfter OpenSSL non reconnu : %r", date_str)
    return None


def get_tls_cert_status() -> TlsCertStatus:
    cert = _server_cert_path()
    if not cert.is_file():
        return TlsCertStatus(
            present=False,
            cert_path=str(cert),
            not_after=None,
            days_remaining=None,
            expires_soon=False,
            error="absent",
        )

    try:
        proc = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(cert)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return TlsCertStatus(
            present=True,
            cert_path=str(cert),
            not_after=None,
            days_remaining=None,
            expires_soon=False,
            error=str(exc),
        )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "openssl x509 failed").strip()
        return TlsCertStatus(
            present=True,
            cert_path=str(cert),
            not_after=None,
            days_remaining=None,
            expires_soon=False,
            error=err,
        )

    not_after = _parse_openssl_enddate(proc.stdout)
    if not_after is None:
        return TlsCertStatus(
            present=True,
            cert_path=str(cert),
            not_after=None,
            days_remaining=None,
            expires_soon=False,
            error="parse",
        )

    now = datetime.now(timezone.utc)
    days = max(0, (not_after.date() - now.date()).days)
    return TlsCertStatus(
        present=True,
        cert_path=str(cert),
        not_after=not_after,
        days_remaining=days,
        expires_soon=days <= _EXPIRY_SOON_DAYS,
    )


def host_for_tls_renewal(server_base_url: str) -> str:
    """Hôte SAN pour le certificat (IP ou FQDN depuis SERVER_BASE_URL)."""
    parsed = urlparse(server_base_url.strip())
    host = (parsed.hostname or parsed.netloc or "").strip()
    if host:
        return host
    from app.config import detect_primary_ipv4

    return detect_primary_ipv4()


def renew_tls_certificate(server_base_url: str) -> tuple[bool, str]:
    """
    Renouvelle server.crt (même CA — pas de recompile iPXE).
    Nécessite sudoers : ipxe NOPASSWD /usr/local/sbin/ipxe-renew-tls-cert
    """
    host = host_for_tls_renewal(server_base_url)
    script = _RENEW_SCRIPT
    if not script.is_file():
        fallback = (
            Path(settings.ssl_dir).parent.parent
            / "app"
            / "deploy"
            / "ipxe-renew-tls-cert.sh"
        )
        if fallback.is_file():
            script = fallback
        else:
            return False, "script_missing"

    cmd = ["sudo", "-n", "bash", str(script), host]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.exception("renew_tls_certificate")
        return False, str(exc)

    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        if "password is required" in out.lower() or "a password is required" in out.lower():
            return False, "sudo_denied"
        return False, out[-500:] if out else f"exit {proc.returncode}"

    return True, out
