# Disclosure Event Extractor

[简体中文](README.md) | **English**

> Turn A-share filings (CNINFO / exchange announcements) into a **traceable, structured
> event table**.

Every row carries `source_url` + `disclosure_id`, so any extracted field can be walked
back to the sentence it came from.

## Two tiers of events

| Tier | Event types | Why |
| --- | --- | --- |
| **A (primary)** | regulatory inquiry, litigation & guarantees, restructuring/M&A, governance resolutions, suspension & control change | **Pandadata does not structure these** — this is the skill's unique value |
| **B (supplement)** | shareholding changes, pledges, earnings pre-announcements | Pandadata already has them; only `detail` text is added |

If you want events you cannot get elsewhere, take **Tier A** — regulatory inquiries in
particular backtest with a measurable underperformance signal.

## Pipeline

```bash
pip install -r requirements.txt

python scripts/fetch_announcements.py --symbols 000001.SZ 600519.SH \
    --start 20250101 --end 20250630 --out cache/
python scripts/extract_events.py --cache cache/ --out events.csv
python scripts/validate_events.py events.csv
python scripts/backtest_events.py events.csv --out backtest.json   # optional
```

`extract_events.py` is **deterministic and rules-only** by default — it runs fully
offline against a cache and needs no LLM. The `--llm` flag is an optional, pluggable
backfill for fields the rules leave blank.

Use `--no-text` to classify from titles alone; that path never opens a PDF, so
`pdfplumber` is not required.

## Scope

Text → structured events. It does **not** make investment judgements and does not
generate buy/sell recommendations. `direction` and `severity` are rule-derived labels,
not opinions.

## Compliance when fetching

`fetch_announcements.py` sends a descriptive User-Agent, rate-limits itself to a minimum
interval between requests, and backs off exponentially on failure. Even so:

- **You are responsible for complying with the terms of service and `robots.txt` of any
  site you point it at**, and with local law on automated access.
- Keep the request rate polite; do not remove the interval guard to go faster.
- Cached filings are third-party copyrighted material. The cache is git-ignored on
  purpose — do not redistribute it.

## Validation

`python -B -m unittest discover -s tests -v`

The suite pins monetary-amount extraction, which previously mis-parsed thousands
separators — the dominant style in Chinese filings — and returned silently wrong
numbers (`50,000万元` → `0.0`; `12,345.67万元` → exactly 100x low), which then
propagated into the severity grade.

`runnable` is a community self-validation level, not official verification.

## License and disclaimer

GPL-3.0-only. Copyright (C) 2026 the QuantSkills contributors.

Research tooling only. Extracted labels are rule-derived and require human review.
**Nothing here is investment advice.**
