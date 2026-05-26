#!/usr/bin/env python3
"""Compile le firmware iPXE (setup / enable-https)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile iPXE avec HTTPS + TRUST local.")
    parser.add_argument(
        "--menu-url",
        required=True,
        help="URL chainload embed (ex. https://192.168.2.8/menus/menu.ipxe)",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Patch + vérifie general.h / embed.ipxe sans lancer make.",
    )
    args = parser.parse_args()

    if args.preflight_only:
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
        patch_ipxe_graphical_console_headers(src_dir, logs)
        patch_ipxe_https_support(src_dir, logs)
        verify_ipxe_https_config(src_dir, logs)
        embed_path = src_dir / "src" / "embed.ipxe"
        embed_path.write_text(build_embed_ipxe(args.menu_url), encoding="utf-8")
        verify_embed_script(embed_path, args.menu_url, logs)
        print("\n".join(logs))
        print("Pré-vol OK.")
        return 0

    from app.services.ipxe_compiler import compile_ipxe_firmware

    result = compile_ipxe_firmware(args.menu_url)
    print(result.get("logs", ""))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
