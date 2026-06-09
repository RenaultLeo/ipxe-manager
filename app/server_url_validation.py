"""Validation de SERVER_BASE_URL et des hôtes passés aux outils TLS (subprocess)."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_HOSTNAME_LABEL_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_FORBIDDEN_HOST_CHARS = frozenset(" \t\n\r/\\@;,${}|&<>\"'`()!#%^*=")


class InvalidServerBaseUrlError(ValueError):
    """URL ou hôte refusé avant persistance ou invocation système."""


def validate_tls_san_host(host: str) -> str:
    """
    Hôte SAN sûr pour ``ipxe-renew-tls-cert`` : IPv4/IPv6 ou FQDN ASCII, sans métacaractères shell.
    """
    host = (host or "").strip()
    if not host or len(host) > 253:
        raise InvalidServerBaseUrlError("invalid host")
    if any(ch in _FORBIDDEN_HOST_CHARS for ch in host):
        raise InvalidServerBaseUrlError("invalid host characters")

    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    normalized = host.rstrip(".")
    labels = normalized.split(".")
    if not labels or any(not lbl for lbl in labels):
        raise InvalidServerBaseUrlError("invalid hostname")
    if not all(_HOSTNAME_LABEL_RE.match(lbl) for lbl in labels):
        raise InvalidServerBaseUrlError("invalid hostname")
    return normalized.lower()


def normalize_server_base_url(url: str) -> str:
    """Normalise et valide une URL de base ``http(s)://hôte[:port]`` sans chemin ni query."""
    raw = (url or "").strip()
    if not raw:
        raise InvalidServerBaseUrlError("empty url")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise InvalidServerBaseUrlError("invalid scheme")
    if parsed.username or parsed.password:
        raise InvalidServerBaseUrlError("userinfo forbidden")
    if parsed.query or parsed.fragment:
        raise InvalidServerBaseUrlError("query or fragment forbidden")
    if parsed.path not in ("", "/"):
        raise InvalidServerBaseUrlError("path forbidden")

    host = parsed.hostname
    if not host:
        raise InvalidServerBaseUrlError("missing host")
    safe_host = validate_tls_san_host(host)

    port = parsed.port
    if port is not None:
        if (parsed.scheme == "http" and port == 80) or (
            parsed.scheme == "https" and port == 443
        ):
            port = None

    try:
        ipaddress.ip_address(safe_host)
        is_ipv6 = ":" in safe_host
    except ValueError:
        is_ipv6 = False

    if is_ipv6:
        netloc = f"[{safe_host}]" + (f":{port}" if port else "")
    else:
        netloc = safe_host + (f":{port}" if port else "")

    return f"{parsed.scheme}://{netloc}"
