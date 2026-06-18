from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def boot_upload_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, int]:
    from app.database import SessionLocal
    from app.main import app
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
        version_label="test-upload",
        status="ready",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    db.add(
        BootEntry(
            iso_version_id=v.id,
            kernel_path="boot/debian/test-upload/vmlinuz",
            initrd_path="boot/debian/test-upload/initrd.gz",
        )
    )
    db.commit()
    vid = v.id
    db.close()

    with TestClient(app) as client:
        login = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        assert login.status_code == 302, login.text
        yield client, vid


def test_boot_file_upload_multipart(boot_upload_client: tuple[TestClient, int]) -> None:
    client, vid = boot_upload_client

    empty = client.post(f"/boot-files/{vid}/upload")
    assert empty.status_code == 400

    files = {"file": ("vmlinuz", BytesIO(b"fake kernel"), "application/octet-stream")}
    data = {"file_role": "kernel"}
    ok = client.post(
        f"/boot-files/{vid}/upload",
        data=data,
        files=files,
        follow_redirects=False,
    )
    assert ok.status_code == 302, ok.text
    loc = ok.headers.get("location") or ""
    assert loc.startswith("/boot-files")
    assert "upload_ok=" in loc
    assert f"upload_vid={vid}" in loc
