#!/usr/bin/env python3
"""Affiche un résumé CWE à partir du rapport JSON Bandit."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "bandit-cwe.json")
    if not path.is_file():
        print(f"Rapport introuvable : {path}", file=sys.stderr)
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results") or []
    if not results:
        print("Aucune alerte Bandit dans le rapport.")
        return 0

    by_cwe: Counter[str] = Counter()
    for item in results:
        cwe = item.get("issue_cwe") or {}
        cwe_id = cwe.get("id") or "CWE-?"
        label = cwe.get("link") or cwe_id
        by_cwe[label] += 1

    print("Résumé CWE (Bandit) :")
    for link, count in by_cwe.most_common():
        print(f"  {count:3d}  {link}")

    print(f"\nTotal : {len(results)} finding(s)")
    for item in results:
        sev = item.get("issue_severity", "?")
        conf = item.get("issue_confidence", "?")
        test = item.get("test_id", "?")
        cwe = (item.get("issue_cwe") or {}).get("id", "?")
        loc = item.get("filename", "?")
        line = item.get("line_number", "?")
        print(f"  [{sev}/{conf}] {test} {cwe} {loc}:{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
