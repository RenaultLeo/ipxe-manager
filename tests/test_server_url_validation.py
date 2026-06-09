from __future__ import annotations

import pytest

from app.server_url_validation import (
    InvalidServerBaseUrlError,
    normalize_server_base_url,
    validate_tls_san_host,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://10.86.90.72/", "https://10.86.90.72"),
        ("http://pxe.local", "http://pxe.local"),
        ("https://pxe.example.com:8443/", "https://pxe.example.com:8443"),
    ],
)
def test_normalize_server_base_url_ok(url: str, expected: str) -> None:
    assert normalize_server_base_url(url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "ftp://10.0.0.1",
        "https://10.0.0.1;id",
        "https://$(id).evil.com",
        "https://host/extra",
        "https://user@host",
        "https://host?x=1",
    ],
)
def test_normalize_server_base_url_rejects(bad: str) -> None:
    with pytest.raises(InvalidServerBaseUrlError):
        normalize_server_base_url(bad)


def test_validate_tls_san_host_rejects_shell_metacharacters() -> None:
    with pytest.raises(InvalidServerBaseUrlError):
        validate_tls_san_host("10.0.0.1;rm")
