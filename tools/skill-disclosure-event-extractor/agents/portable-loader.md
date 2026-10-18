# Portable Loader Prompt

Use this prompt in agents that do not natively discover `SKILL.md` folders.

```text
You have access to a local skill named disclosure-event-extractor at:
<DISCLOSURE_SKILL_ROOT>

When the user asks to scan A-share announcements, find 监管问询函/关注函、诉讼/对外担保、
重组/收购、停牌/控制权变更、增减持/质押/业绩预告 events, build a structured event table,
or backtest disclosure events:

1. Read <DISCLOSURE_SKILL_ROOT>/SKILL.md for the workflow, the Tier A / Tier B split, and the
   division of labour with skill-event-risk-alert.
2. Read <DISCLOSURE_SKILL_ROOT>/references/event-taxonomy.md before classifying. Classification
   is TITLE-driven — matching the PDF body produces heavy false positives.
3. Fetch announcements:
   python <DISCLOSURE_SKILL_ROOT>/scripts/fetch_announcements.py --symbols <codes> --start <YYYYMMDD> --end <YYYYMMDD> --out cache/
   or market-wide by keyword (Tier A discovery):
   python <DISCLOSURE_SKILL_ROOT>/scripts/fetch_announcements.py --keywords 关注函 诉讼 --start <..> --end <..> --out cache/ --no-text
4. Extract and validate:
   python <DISCLOSURE_SKILL_ROOT>/scripts/extract_events.py --cache cache/ --out events.csv
   python <DISCLOSURE_SKILL_ROOT>/scripts/validate_events.py events.csv
5. Read direction labels per the validated-signals table in SKILL.md: 利空 (especially
   监管问询) has real forward signal; 利好 is largely priced in; 控制权变更 is a positive-skew
   lottery, not a long signal.
6. Do not invent event types, fields, or Pandadata methods. Never emit an event row without
   source_url and disclosure_id. Output is research data, never investment advice.
```
