from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient


def test_boot_file_upload_multipart() -> None:
    from app.auth import hash_password
    from app.database import SessionLocal
    from app.main import app
    from app.models.models import BootEntry, IsoVersion, OsType, User

    client = TestClient(app)

    db = SessionLocal()
    user = db.query(User).first()
    if not user:
        user = User(username="test", password_hash=hash_password("test"), role="admin")
        db.add(user)
        db.commit()
        db.refresh(user)
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
        owner_user_id=user.id,
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

    client.post("/login", data={"username": "test", "password": "test"})

    empty = client.post(f"/boot-files/{vid}/upload")
    assert empty.status_code == 422

    files = {"file": ("vmlinuz", BytesIO(b"fake kernel"), "application/octet-stream")}
    data = {"file_role": "kernel"}
    ok = client.post(
        f"/boot-files/{vid}/upload",
        data=data,
        files=files,
        follow_redirects=False,
    )
    assert ok.status_code == 302, ok.text
    assert ok.headers.get("location") == "/boot-files"
