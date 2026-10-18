#!/usr/bin/env python3
"""Validate a structured event table against output-schema.md.

Enforces the validation contract: required fields, enum legality, date format,
traceability (source_url / disclosure_id), magnitude/unit pairing, confidence
range. Exits non-zero on any violation so it can gate report publishing.

Usage:
    python validate_events.py reports/events/20260630.parquet
    python validate_events.py reports/events/20260630.csv
"""
from __future__ import annotations

import argparse
import re
import sys

REQUIRED = [
    "symbol", "sec_name", "ann_date", "event_type", "event_subtype",
    "direction", "severity", "summary", "source_url", "disclosure_id",
    "confidence", "fetched_at",
]

EVENT_TYPES = {
    "holding_change", "pledge", "earnings_preview", "regulatory_inquiry",
    "litigation_guarantee", "restructuring", "governance", "suspension",
}
DIRECTIONS = {"利好", "利空", "中性"}
SEVERITIES = {"高", "中", "低"}
UNITS = {"amount", "ratio", "shares"}
TIERS = {"primary", "supplement"}

DATE_RE = re.compile(r"^\d{8}$")


def _load(path: str):
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("pip install pandas to run the validator")
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str)


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() == ""


def validate(path: str) -> list[str]:
    df = _load(path)
    errors: list[str] = []

    missing_cols = [c for c in REQUIRED if c not in df.columns]
    if missing_cols:
        return [f"missing required columns: {missing_cols}"]

    for i, row in df.iterrows():
        rid = row.get("disclosure_id", f"row{i}")

        for col in REQUIRED:
            if _is_blank(row.get(col)):
                errors.append(f"[{rid}] blank required field: {col}")

        et = str(row.get("event_type", ""))
        if et and et not in EVENT_TYPES:
            errors.append(f"[{rid}] illegal event_type: {et}")

        if "tier" in df.columns and not _is_blank(row.get("tier")) \
                and str(row.get("tier")) not in TIERS:
            errors.append(f"[{rid}] illegal tier: {row.get('tier')}")

        if str(row.get("direction", "")) not in DIRECTIONS and not _is_blank(row.get("direction")):
            errors.append(f"[{rid}] illegal direction: {row.get('direction')}")

        if str(row.get("severity", "")) not in SEVERITIES and not _is_blank(row.get("severity")):
            errors.append(f"[{rid}] illegal severity: {row.get('severity')}")

        ann_date = str(row.get("ann_date", ""))
        if ann_date and not DATE_RE.match(ann_date):
            errors.append(f"[{rid}] ann_date not YYYYMMDD: {ann_date}")

        url = str(row.get("source_url", ""))
        if url and not url.startswith("http"):
            errors.append(f"[{rid}] source_url must start with http: {url}")

        # magnitude present -> magnitude_unit required and legal
        if not _is_blank(row.get("magnitude")):
            unit = str(row.get("magnitude_unit", ""))
            if _is_blank(row.get("magnitude_unit")):
                errors.append(f"[{rid}] magnitude present but magnitude_unit missing")
            elif unit not in UNITS:
                errors.append(f"[{rid}] illegal magnitude_unit: {unit}")

        conf = row.get("confidence")
        try:
            if conf is not None and not (0.0 <= float(conf) <= 1.0):
                errors.append(f"[{rid}] confidence out of [0,1]: {conf}")
        except (TypeError, ValueError):
            errors.append(f"[{rid}] confidence not numeric: {conf}")

    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="event table (.parquet or .csv)")
    args = ap.parse_args()
    errors = validate(args.path)
    if errors:
        print(f"FAIL: {len(errors)} validation error(s)")
        for e in errors[:200]:
            print("  -", e)
        sys.exit(1)
    print("OK: event table passed all checks")


if __name__ == "__main__":
    main()
