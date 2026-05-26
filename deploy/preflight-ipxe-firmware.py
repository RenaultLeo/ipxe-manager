#!/usr/bin/env python3
"""Patch HTTPS + vérifie config/embed sur disque (sans make)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pré-vol firmware iPXE (general.h, local/, embed.ipxe)."
    )
    parser.add_argument(
        "--menu-url",
        required=True,
        help="URL menu (ex. https://192.168.1.54/menus/menu.ipxe)",
    )
    args = parser.parse_args()

    from app.config import settings
    from app.services.ipxe_compiler import (
        build_embed_ipxe,
        patch_ipxe_graphical_console_headers,
        patch_ipxe_https_support,
        verify_embed_script,
        verify_ipxe_https_config,
    )

    src_dir = settings.ipxe_src_dir
    logs: list[str] = []
    if not (src_dir / "src" / "config" / "general.h").is_file():
        print(f"KO : sources iPXE absentes ({src_dir})", file=sys.stderr)
        return 1

    patch_ipxe_graphical_console_headers(src_dir, logs)
    patch_ipxe_https_support(src_dir, logs)
    verify_ipxe_https_config(src_dir, logs)

    embed_path = src_dir / "src" / "embed.ipxe"
    embed_path.write_text(build_embed_ipxe(args.menu_url), encoding="utf-8")
    verify_embed_script(embed_path, args.menu_url, logs)

    print("\n".join(logs))
    print("OK — pré-vol terminé (fichiers prêts pour make).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
