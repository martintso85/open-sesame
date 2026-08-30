import unittest
from unittest.mock import patch

from scripts.update_data import (
    analyze_market,
    analyze_stock_history,
    fetch_base_stocks,
    sma,
    to_float,
)


class IndicatorTests(unittest.TestCase):
    @patch("scripts.update_data.fetch_json")
    def test_candidate_universe_and_ranking(self, fetch_json):
        fetch_json.return_value = [
            {"Code": "3008", "Name": "大立光", "ClosingPrice": "2,000", "Change": "+20", "TradeVolume": "1000", "TradeValue": "2000000"},
            {"Code": "2330", "Name": "台積電", "ClosingPrice": "1,000", "Change": "+50", "TradeVolume": "2000", "TradeValue": "2000000"},
            {"Code": "0050", "Name": "元大台灣50", "ClosingPrice": "200", "Change": "+10", "TradeVolume": "3000", "TradeValue": "2000000"},
        ]
        stocks = fetch_base_stocks()
        self.assertEqual([stock["code"] for stock in stocks], ["2330", "3008"])
        self.assertIn("trade_value", stocks[0])

    def test_to_float_accepts_twse_number_format(self):
        self.assertEqual(to_float("+1,234.5"), 1234.5)
        self.assertIsNone(to_float("--"))

    def test_sma_excludes_requested_offset(self):
        values = [1, 2, 3, 4, 5]
        self.assertEqual(sma(values, 3), 4)
        self.assertEqual(sma(values, 3, offset=1), 3)

    def test_market_analysis_detects_uptrend_and_volume_surge(self):
        rows = []
        for day in range(1, 66):
            rows.append({
                "date": f"2026-07-{day:02d}",
                "index": 20_000 + day * 20,
                "change": 20,
                "amount": 1_000 if day < 65 else 1_500,
            })
        market = analyze_market(rows)
        self.assertTrue(market["trend_up"])
        self.assertTrue(market["above_ma60"])
        self.assertTrue(market["vol_surge"])

    def test_stock_analysis_reports_relative_strength(self):
        rows = []
        for day in range(1, 31):
            close = 100 + day
            rows.append({
                "date": f"2026-08-{day:02d}",
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "volume": 1_000,
            })
        result = analyze_stock_history(rows, market_ret20=5)
        self.assertTrue(result["outperform"])
        self.assertGreater(result["rel_pct"], 0)


if __name__ == "__main__":
    unittest.main()
