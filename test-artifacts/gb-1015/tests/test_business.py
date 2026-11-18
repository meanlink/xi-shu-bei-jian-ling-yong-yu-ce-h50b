"""原有 Croston/SBA 分析契约的回归测试（纯标准库 unittest）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")))

from src.services.business import analyze, seed_records  # noqa: E402


class AnalyzeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = seed_records()

    def test_seed_shape(self):
        self.assertEqual(len(self.records), 360)
        for part in ("R17", "G22"):
            days = sorted(r["day"] for r in self.records if r["part"] == part)
            self.assertEqual(len(days), 180)

    def test_analyze_sba_outputs(self):
        result = analyze(self.records, {"part": "R17", "alpha": 0.2,
                                        "horizon": 14, "variant": "sba"})
        self.assertEqual(len(result["table"]["rows"]), 14)
        self.assertEqual(len(result["chart"]["series"][1]["values"]), 28)
        self.assertTrue(all(m["value"] >= 0 for m in result["metrics"]))

    def test_reject_bad_alpha_and_horizon(self):
        with self.assertRaises(ValueError):
            analyze(self.records, {"part": "R17", "alpha": 2, "horizon": 14})
        with self.assertRaises(ValueError):
            analyze(self.records, {"part": "R17", "alpha": 0.2, "horizon": 7.5})

    def test_missing_part_rejected(self):
        with self.assertRaises(ValueError):
            analyze(self.records, {"part": "NOPE", "alpha": 0.2, "horizon": 14})


if __name__ == "__main__":
    unittest.main(verbosity=2)
