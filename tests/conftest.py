"""Variables d'environnement CI avant tout import ``app.*``."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_CI_ROOT = Path(tempfile.mkdtemp(prefix="ipxe-ci-"))
os.environ["SECRET_KEY"] = "ci-test-secret-key-" + ("0" * 32)
os.environ["DATABASE_URL"] = f"sqlite:///{(_CI_ROOT / 'test.db').as_posix()}"
os.environ["TFTP_ROOT"] = str(_CI_ROOT / "tftp")
os.environ["HTTP_ROOT"] = str(_CI_ROOT / "http")
os.environ["ISO_ROOT"] = str(_CI_ROOT / "isos")
os.environ["BUILD_DIR"] = str(_CI_ROOT / "build")
os.environ["SSL_DIR"] = str(_CI_ROOT / "ssl")
os.environ["SERVER_BASE_URL"] = "http://127.0.0.1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
