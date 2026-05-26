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

    args = parser.parse_args()



    from app.services.ipxe_compiler import compile_ipxe_firmware



    result = compile_ipxe_firmware(args.menu_url)

    print(result.get("logs", ""))

    return 0 if result.get("status") == "success" else 1





if __name__ == "__main__":

    raise SystemExit(main())

