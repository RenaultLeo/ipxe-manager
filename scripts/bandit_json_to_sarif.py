#!/usr/bin/env python3
"""Convertit un rapport JSON Bandit en SARIF 2.1.0 (contourne le bug du formateur natif)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SEVERITY_TO_LEVEL = {
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
    "UNDEFINED": "note",
}


def _rule_index(rules: list[dict], test_id: str, item: dict) -> int:
    for i, rule in enumerate(rules):
        if rule["id"] == test_id:
            return i
    cwe = item.get("issue_cwe") or {}
    cwe_id = cwe.get("id")
    tags = ["security"]
    if cwe_id:
        tags.append(f"external/cwe/cwe-{cwe_id}")
    rules.append(
        {
            "id": test_id,
            "name": item.get("test_name") or test_id,
            "shortDescription": {"text": item.get("issue_text", test_id)},
            "properties": {"tags": tags},
        }
    )
    return len(rules) - 1


def convert(data: dict) -> dict:
    results_in = data.get("results") or []
    rules: list[dict] = []
    sarif_results: list[dict] = []

    for item in results_in:
        test_id = item.get("test_id") or "B000"
        idx = _rule_index(rules, test_id, item)
        sev = str(item.get("issue_severity", "UNDEFINED")).upper()
        filename = str(item.get("filename", "")).replace("\\", "/")
        line = int(item.get("line_number") or 1)

        location: dict = {
            "physicalLocation": {
                "artifactLocation": {"uri": filename},
                "region": {"startLine": line, "endLine": line},
            }
        }
        code = item.get("code")
        if isinstance(code, str) and code.strip():
            location["physicalLocation"]["region"]["snippet"] = {"text": code}

        sarif_results.append(
            {
                "ruleId": test_id,
                "ruleIndex": idx,
                "level": SEVERITY_TO_LEVEL.get(sev, "note"),
                "message": {"text": item.get("issue_text", test_id)},
                "locations": [location],
                "properties": {
                    "issue_severity": sev,
                    "issue_confidence": item.get("issue_confidence", ""),
                },
            }
        )

    metrics = data.get("metrics") or {}
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Bandit",
                        "organization": "PyCQA",
                        "version": data.get("bandit_version", "unknown"),
                        "semanticVersion": data.get("bandit_version", "unknown"),
                        "rules": rules,
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "endTimeUtc": datetime.now(timezone.utc)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                ],
                "properties": {"metrics": metrics},
                "results": sarif_results,
            }
        ],
    }


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "bandit-cwe.json")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "bandit-cwe.sarif")
    if not src.is_file():
        print(f"Rapport introuvable : {src}", file=sys.stderr)
        return 2
    data = json.loads(src.read_text(encoding="utf-8"))
    sarif = convert(data)
    dst.write_text(json.dumps(sarif, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SARIF écrit : {dst} ({len(sarif['runs'][0]['results'])} finding(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
