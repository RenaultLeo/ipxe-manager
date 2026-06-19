from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app.main import app

    monkeypatch.setattr(
        "app.services.menu_generator.queue_regenerate_all",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.tasks.jobs.regenerate_menus_task.delay",
        MagicMock(),
    )
    with TestClient(app) as client:
        login = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        assert login.status_code == 302, login.text
        yield client


def test_chain_add_multipart_formdata(admin_client: TestClient) -> None:
    """Ajout serveur distant (FormData fetch) — pas de 422 Field required."""
    r = admin_client.post(
        "/ipxe-menus/chains/add",
        data={"name": "Lab distant", "url": "http://10.0.0.5/menus/menu.ipxe"},
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("chain", {}).get("name") == "Lab distant"
    assert "10.0.0.5" in body.get("chain", {}).get("url", "")


def test_settings_server_url_form(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/settings/server-url",
        data={"server_base_url": "http://192.168.1.10"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers.get("location", "").startswith("/settings")


def test_boot_file_upload_multipart(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import SessionLocal
    from app.models.models import BootEntry, IsoVersion, OsType

    monkeypatch.setattr(
        "app.routers.boot_files.regenerate_menus_task.delay",
        MagicMock(),
    )

    db = SessionLocal()
    ot = db.query(OsType).filter(OsType.slug == "debian").first()
    if not ot:
        ot = OsType(
            slug="debian",
            label="Debian",
            icon="bi-debian",
            boot_type="linux",
            is_builtin=True,
        )
        db.add(ot)
        db.commit()
        db.refresh(ot)
    v = IsoVersion(
        os_type_id=ot.id,
        version_label="test-upload-mp",
        status="ready",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    db.add(
        BootEntry(
            iso_version_id=v.id,
            kernel_path="boot/debian/test-upload-mp/vmlinuz",
            initrd_path="boot/debian/test-upload-mp/initrd.gz",
        )
    )
    db.commit()
    vid = v.id
    db.close()

    files = {"file": ("vmlinuz", BytesIO(b"fake kernel"), "application/octet-stream")}
    ok = admin_client.post(
        f"/boot-files/{vid}/upload",
        data={"file_role": "kernel"},
        files=files,
        follow_redirects=False,
    )
    assert ok.status_code == 302, ok.text
    loc = ok.headers.get("location") or ""
    assert "upload_ok=" in loc
