import unittest
from unittest.mock import patch

from scripts.update_data import (
    analyze_market,
    analyze_stock_history,
    evaluate_conditions,
    fetch_base_stocks,
    parse_mi_margn,
    sma,
    to_float,
)


class IndicatorTests(unittest.TestCase):
    def test_mi_margn_accepts_twse_code_alias_and_first_balance_group(self):
        response = {
            "stat": "OK",
            "tables": [{
                "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "限額", "買進", "賣出", "現券償還", "前日餘額", "今日餘額"],
                "data": [["2330", "台積電", "10", "5", "0", "100", "105", "0", "1", "2", "0", "9", "8"]],
            }, {
                "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
                "data": [["融資金額(仟元)", "0", "0", "0", "100,000", "110,000"]],
            }],
        }
        summary, stocks = parse_mi_margn(response)
        self.assertEqual(stocks["2330"], {"prev": 100.0, "today": 105.0})
        self.assertEqual(summary, {"prev": 1.0, "today": 1.1})

    def test_six_conditions_and_market_gate(self):
        stock = {
            "month_up": True, "week_up": True, "kd_golden": True,
            "trust_net": 100, "vol_surge": True, "outperform": True,
            "margin_healthy": False,
        }
        evaluate_conditions(stock, market_gate=True)
        self.assertEqual(stock["cond_count"], 5)
        self.assertEqual(stock["available_count"], 6)
        self.assertTrue(stock["priority"])
        self.assertEqual(set(stock["conditions"]), {"ma", "kd", "trust", "volume", "relative", "margin"})

    def test_missing_condition_is_unknown_not_failure(self):
        stock = {
            "month_up": None, "week_up": None, "kd_golden": True,
            "trust_net": None, "vol_surge": True, "outperform": None,
            "margin_healthy": True,
        }
        evaluate_conditions(stock, market_gate=None)
        self.assertIsNone(stock["conditions"]["ma"])
        self.assertIsNone(stock["conditions"]["trust"])
        self.assertEqual(stock["available_count"], 3)
        self.assertFalse(stock["priority"])

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

    def test_kd_requires_strict_current_cross(self):
        rows = [{
            "date": f"2026-08-{day:02d}", "close": 100 + day,
            "high": 102 + day, "low": 98 + day, "volume": 1_000,
        } for day in range(1, 31)]
        with patch("scripts.update_data.calc_kd", return_value=([50, 50.2, 51], [50, 50, 50.5])):
            self.assertFalse(analyze_stock_history(rows, 0)["kd_golden"])
        with patch("scripts.update_data.calc_kd", return_value=([50, 49.9, 51], [50, 50, 50.5])):
            self.assertTrue(analyze_stock_history(rows, 0)["kd_golden"])


if __name__ == "__main__":
    unittest.main()
