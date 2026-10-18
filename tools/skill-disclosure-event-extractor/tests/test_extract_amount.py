"""Regression suite for monetary-amount extraction.

Run from the repository root:
    python -B -m unittest discover -s tests -v

The bug this pins: `(\\d+(?:\\.\\d+)?)\\s*万元` cannot span a thousands separator,
which A-share filings use as the default style (「担保金额人民币50,000万元」). It did
not fail loudly -- re.search latched onto the digits after the last comma and
returned a *wrong number*:

    "50,000万元"     -> 0.0          (not None, so callers cannot detect it)
    "12,345.67万元"  -> 3,456,700    (exactly 100x low)

Both then propagated into the severity grade: the same 5亿 guarantee was graded
「高」 when written without separators and 「中」 when written with them.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import extract_events as E  # noqa: E402


def event(title, body):
    ann = dict(symbol="300750.SZ", sec_name="X", ann_date="20250318",
               title=title, source_url="u", disclosure_id="1", fetched_at="t")
    return E.build_event(ann, body, lambda *_: {})


class ThousandsSeparators(unittest.TestCase):
    def test_separated_and_unseparated_amounts_agree(self):
        pairs = [
            ("50000万元", "50,000万元", 5e8),
            ("1234.5亿元", "1,234.5亿元", 1.2345e11),
            ("12345.67万元", "12,345.67万元", 1.234567e8),
            ("1000000万元", "1,000,000万元", 1e10),
        ]
        for plain, separated, expected in pairs:
            self.assertAlmostEqual(E.extract_amount(plain), expected, delta=1,
                                   msg=plain)
            self.assertAlmostEqual(E.extract_amount(separated), expected, delta=1,
                                   msg=separated)

    def test_the_exact_regressions_that_were_silently_wrong(self):
        # returned 0.0 before the fix
        self.assertAlmostEqual(E.extract_amount("50,000万元"), 5e8, delta=1)
        # returned 3_456_700 before the fix -- exactly 100x low
        self.assertAlmostEqual(E.extract_amount("12,345.67万元"), 1.234567e8, delta=1)

    def test_a_wrong_amount_is_never_returned_as_zero(self):
        # 0.0 is indistinguishable from a real zero amount downstream, so any
        # unparseable input must come back as None instead.
        for text in ("金额待定", "本次交易无对价", "担保金额人民币", ""):
            self.assertIsNone(E.extract_amount(text), text)

    def test_currency_prefixes_and_spacing_are_tolerated(self):
        for text in ("人民币50,000.00万元", "担保金额人民币 50,000 万元",
                     "金额为50000万元。", "计人民币50,000万元整"):
            self.assertAlmostEqual(E.extract_amount(text), 5e8, delta=1, msg=text)

    def test_unit_precedence_prefers_yi_over_wan(self):
        self.assertAlmostEqual(E.extract_amount("1.5亿元，折合15000万元"),
                               1.5e8, delta=1)

    def test_bare_yuan_is_still_not_matched(self):
        # Deliberate: 元 appears in 每股收益/面值/股价 far more often than in deal
        # sizes; matching it would swap a wrong-number bug for a false-positive one.
        self.assertIsNone(E.extract_amount("每股收益0.5元"))
        self.assertIsNone(E.extract_amount("股票面值1.00元"))


class SeverityPropagation(unittest.TestCase):
    def test_the_same_guarantee_grades_the_same_either_way(self):
        plain = event("关于对外担保的公告",
                      "公司为子公司提供担保，担保金额人民币50000万元。")
        separated = event("关于对外担保的公告",
                          "公司为子公司提供担保，担保金额人民币50,000万元。")
        self.assertEqual(plain.magnitude, separated.magnitude)
        self.assertEqual(plain.magnitude_unit, separated.magnitude_unit)
        self.assertEqual(plain.severity, separated.severity,
                         "thousands separators must not change the severity grade")

    def test_a_large_separated_guarantee_is_not_downgraded(self):
        ev = event("关于对外担保的公告",
                   "公司为子公司提供担保，担保金额人民币50,000万元。")
        self.assertAlmostEqual(ev.magnitude, 5e8, delta=1)
        self.assertEqual(ev.severity, "高")


class RatioExtractionUnaffected(unittest.TestCase):
    def test_ratios_still_parse_and_still_take_precedence(self):
        self.assertAlmostEqual(E.extract_ratio("占净资产的12.50%"), 0.125)
        ev = event("关于对外担保的公告",
                   "担保金额人民币50,000万元，占最近一期经审计净资产的12.50%。")
        self.assertEqual(ev.magnitude_unit, "ratio")
        self.assertAlmostEqual(ev.magnitude, 0.125)


if __name__ == "__main__":
    unittest.main()
