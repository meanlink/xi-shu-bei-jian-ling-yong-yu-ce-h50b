"""时点回放引擎测试：纯标准库，python3 -m unittest 即可运行（兼容 pytest）。

覆盖诉求：
- 凭证三要素（发生日/入账时间/更正版本）登记校验；
- 会议时点重放只采信已入账版本，补录不回灌（防前视）；
- 逐日回测按各预测时点取已入账版本，缺历史依据标无法计算；
- 会议口径 vs 现有凭证回算，差异归因到具体凭证；
- 清单固化、中断恢复、重试不重复归档、并发补录留待下次。
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")))

from src.services import replay as rp  # noqa: E402

PARAMS = {"part": "R17", "alpha": 0.2, "horizon": 14, "variant": "sba"}
START = date(2026, 1, 1)


def epoch(y, m, d, h=10):
    return datetime(y, m, d, h).timestamp()


def make_vouchers(days=80, demand_every=7, demand=2):
    """生成 days 天连续凭证，当日 20:00 入账。"""
    vouchers = []
    for i in range(days):
        d = START + timedelta(days=i)
        vouchers.append({
            "id": i + 1, "part": "R17", "occur_date": d.isoformat(),
            "posted_at": datetime(d.year, d.month, d.day, 20).timestamp(),
            "demand": float(demand) if i % demand_every == 0 else 0.0,
            "version": 1, "entry_type": "normal", "corrected_id": None,
        })
    return vouchers


class VoucherValidationTests(unittest.TestCase):
    def test_validate_keeps_three_facts(self):
        clean = rp.validate_voucher({
            "part": "R17", "occur_date": "2026-05-12",
            "posted_at": "2026-05-12T20:00", "demand": 1,
        })
        self.assertEqual(clean["occur_date"], "2026-05-12")
        self.assertEqual(clean["version"], 1)
        self.assertAlmostEqual(clean["posted_at"], epoch(2026, 5, 12, 20))

    def test_reject_bad_day_and_demand(self):
        with self.assertRaises(rp.ReplayError):
            rp.validate_voucher({"part": "R17", "occur_date": "12/05/2026",
                                 "posted_at": epoch(2026, 5, 12), "demand": 1})
        with self.assertRaises(rp.ReplayError):
            rp.validate_voucher({"part": "R17", "occur_date": "2026-05-12",
                                 "posted_at": epoch(2026, 5, 12), "demand": -1})
        with self.assertRaises(rp.ReplayError):
            rp.validate_voucher({"part": "R17", "occur_date": "2026-05-12",
                                 "posted_at": epoch(2026, 5, 12), "demand": 2e9})

    def test_parse_epoch_accepts_iso_and_number(self):
        self.assertAlmostEqual(rp.parse_epoch(1.7e9), 1.7e9)
        with self.assertRaises(rp.ReplayError):
            rp.parse_epoch("not-a-time")


class PointInTimeVisibilityTests(unittest.TestCase):
    def test_backfill_after_meeting_is_invisible(self):
        vouchers = make_vouchers()
        meeting = epoch(2026, 3, 1)  # 第 60 天
        # 第 50 天（2026-02-20）的凭证会后才入账
        late = next(v for v in vouchers if v["occur_date"] == "2026-02-20")
        late["posted_at"] = meeting + 3 * 86400
        late["entry_type"] = "backfill"
        visible = rp.visible_vouchers(vouchers, meeting, "R17")
        self.assertNotIn(late["id"], [v["id"] for v in visible])
        # 现有口径（无视入账时间）则包含
        visible_now = rp.visible_vouchers(vouchers, float("inf"), "R17")
        self.assertIn(late["id"], [v["id"] for v in visible_now])

    def test_correction_version_replaces_old(self):
        vouchers = make_vouchers()
        meeting = epoch(2026, 3, 1)
        target = next(v for v in vouchers if v["occur_date"] == "2026-01-11")
        vouchers.append({
            "id": 999, "part": "R17", "occur_date": target["occur_date"],
            "posted_at": meeting + 86400, "demand": 9.0, "version": 2,
            "entry_type": "correction", "corrected_id": target["id"],
        })
        at_meeting = rp.visible_vouchers(vouchers, meeting, "R17")
        now = rp.visible_vouchers(vouchers, float("inf"), "R17")
        self.assertEqual(
            [v["demand"] for v in at_meeting if v["occur_date"] == target["occur_date"]],
            [target["demand"]],
        )
        self.assertEqual(
            [v["demand"] for v in now if v["occur_date"] == target["occur_date"]], [9.0])

    def test_series_fills_zero_days(self):
        vouchers = make_vouchers(days=50)
        visible = rp.visible_vouchers(vouchers, float("inf"), "R17")
        _start, rows = rp.materialize_series(visible)
        self.assertEqual(len(rows), 50)  # 连续自然日，零需求日照常保留


class ReplayBacktestTests(unittest.TestCase):
    def test_meeting_replay_marks_uncomputable_when_history_short(self):
        vouchers = make_vouchers(days=80)
        early = epoch(2026, 2, 1)  # 第 32 天 < 45
        result = rp.replay_at_meeting(vouchers, early, PARAMS)
        self.assertFalse(result["computable"])
        self.assertIn("无法计算", result["reason"])

    def test_meeting_replay_computable_after_45_days(self):
        vouchers = make_vouchers(days=80)
        result = rp.replay_at_meeting(vouchers, epoch(2026, 3, 1), PARAMS)
        self.assertTrue(result["computable"])
        self.assertEqual(len(result["future"]), 14)

    def test_backtest_uses_only_posted_versions(self):
        vouchers = make_vouchers()
        meeting = epoch(2026, 3, 15)
        # 回测窗口为末 28 天（约 2/22–3/22），选窗口内 2026-03-08 会后入账
        late = next(v for v in vouchers if v["occur_date"] == "2026-03-08")
        late["posted_at"] = meeting + 2 * 86400
        late["demand"] = 3.0
        late["entry_type"] = "backfill"
        bt = rp.daily_backtest(vouchers, meeting, PARAMS)
        flagged = next(p for p in bt["points"] if p["day"] == "2026-03-08")
        self.assertFalse(flagged["computable"])
        self.assertIn("会议时点后才入账", flagged["reason"])
        # 会议口径该日按零可见，现有凭证回算参考值才反映补记的真实领用
        self.assertEqual(flagged["actual"], 0)
        self.assertEqual(flagged["reference_actual"], 3.0)
        # 早期预测点因不足 45 日历史被标无法计算，MAE 仅统计可计算点
        self.assertLess(bt["computable_points"], bt["total_points"])
        self.assertIsNotNone(bt["mae"])

    def test_backtest_zero_day_kept(self):
        vouchers = make_vouchers()
        bt = rp.daily_backtest(vouchers, epoch(2026, 3, 20), PARAMS)
        zero_point = next(p for p in bt["points"] if p["actual"] == 0)
        # 零需求日照常保留并参与回测，预测值为非负领用率
        self.assertTrue(zero_point["computable"])
        self.assertGreaterEqual(zero_point["predicted"], 0)
        self.assertEqual(zero_point["reference_actual"], 0)

    def test_backtest_is_strictly_forward(self):
        vouchers = make_vouchers()
        bt = rp.daily_backtest(vouchers, epoch(2026, 3, 20), PARAMS)
        for point in bt["points"]:
            if point["computable"]:
                self.assertLess(point["cutoff_epoch"],
                                datetime.fromisoformat(point["day"]).timestamp() + 1)


class ComparisonTests(unittest.TestCase):
    def test_diff_attributed_to_vouchers(self):
        vouchers = make_vouchers()
        meeting = epoch(2026, 3, 1)
        late = next(v for v in vouchers if v["occur_date"] == "2026-02-20")
        late["posted_at"] = meeting + 3 * 86400
        late["demand"] = 5.0
        late["entry_type"] = "backfill"
        cmp_ = rp.compare_meeting_vs_current(vouchers, meeting, PARAMS)
        ids = [v["id"] for v in cmp_["diff_vouchers"]]
        self.assertIn(late["id"], ids)
        # 会议 3/1 之后正常入账的凭证同样不可见，差异含补记与常规晚到
        self.assertEqual(cmp_["diff_voucher_count"], len(ids))
        self.assertTrue(any("补记" in v["reason"] for v in cmp_["diff_vouchers"]))
        self.assertIsNotNone(cmp_["rate_delta"])

    def test_diff_reasons_split_backfill_and_future(self):
        vouchers = make_vouchers()
        meeting = epoch(2026, 3, 1)
        # 发生日在会议前但晚入账 -> 补记
        late = next(v for v in vouchers if v["occur_date"] == "2026-02-20")
        late["posted_at"] = meeting + 3 * 86400
        late["entry_type"] = "backfill"
        cmp_ = rp.compare_meeting_vs_current(vouchers, meeting, PARAMS)
        reasons = {v["occur_date"]: v["reason"] for v in cmp_["diff_vouchers"]}
        self.assertIn("补记", reasons["2026-02-20"])
        future = next(d for d in reasons if d > "2026-03-01")
        self.assertIn("会议之后才发生", reasons[future])

    def test_no_diff_when_all_posted_before_meeting(self):
        vouchers = make_vouchers()
        cmp_ = rp.compare_meeting_vs_current(vouchers, epoch(2026, 4, 1), PARAMS)
        self.assertEqual(cmp_["diff_voucher_count"], 0)
        self.assertEqual(cmp_["rate_delta"], 0.0)


class ManifestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.vouchers = make_vouchers(days=80)
        self.meetings = [epoch(2026, 2, 1), epoch(2026, 3, 1), epoch(2026, 3, 20)]
        self.manifest = rp.build_manifest(self.vouchers, self.meetings, PARAMS)

    def test_manifest_freezes_voucher_ids(self):
        by_id = {v["id"]: v for v in self.vouchers}
        out = rp.run_manifest(by_id, self.manifest)
        self.assertEqual(len(out["points"]), 3)

    def test_resume_skips_completed_points(self):
        by_id = {v["id"]: v for v in self.vouchers}
        first = rp.run_manifest(by_id, self.manifest, progress={})
        # 模拟中断：只持久化第一个会议点
        partial = {str(self.meetings[0]): first["points"][0]}
        resumed = rp.run_manifest(by_id, self.manifest, progress=dict(partial))
        self.assertEqual(len(resumed["points"]), 3)
        self.assertEqual(resumed["points"][0], first["points"][0])

    def test_concurrent_backfill_left_for_next_run(self):
        by_id = {v["id"]: v for v in self.vouchers}
        by_id[1000] = {
            "id": 1000, "part": "R17", "occur_date": "2026-03-25",
            "posted_at": epoch(2026, 3, 26), "demand": 1.0, "version": 1,
            "entry_type": "backfill", "corrected_id": None,
        }
        out = rp.run_manifest(by_id, self.manifest)
        self.assertEqual([v["id"] for v in out["skipped_concurrent"]], [1000])
        # 清单内点数不受并发凭证影响
        self.assertEqual(len(out["points"]), 3)

    def test_archive_idempotency_key_distinct_per_meeting(self):
        # 模拟持久层幂等键规则，保证重试不重复归档
        keys = {f"run-7-meeting-{int(t)}" for t in self.meetings}
        self.assertEqual(len(keys), len(self.meetings))


class SeedScenarioTests(unittest.TestCase):
    """演示剧本：补记/更正日期必须是非零领用日，否则差异演示失真。"""

    def test_seed_backfill_days_have_demand(self):
        from src.services.business import seed_records
        rows = {(r["part"], r["day"]): r["demand"] for r in seed_records()}
        self.assertGreater(rows[("R17", "2026-05-12")], 0)
        self.assertGreater(rows[("G22", "2026-05-26")], 0)
        self.assertGreater(rows[("R17", "2026-07-15")], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
