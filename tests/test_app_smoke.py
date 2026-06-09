from __future__ import annotations


def test_login_page_loads() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/login")
    assert response.status_code == 200
    assert "login" in response.text.lower() or "password" in response.text.lower()


def test_multipart_parser_kwargs() -> None:
    from app.http_multipart import multipart_parser_kwargs

    kwargs = multipart_parser_kwargs()
    assert kwargs["max_files"] > 0
    assert kwargs["max_fields"] > 0
    assert kwargs["max_part_size"] > 0
