"""领用凭证与时点回放引擎（纯标准库，无第三方依赖）。

设计目标（对应业务诉求）：
1. 每张凭证登记 领用发生日(occur_date) / 入账时间(posted_at, epoch 秒) /
   更正版本(version：1 首发，>1 更正；corrected_id 指向被更正凭证)。
2. 任一时点 T 的"已入账版本"只取 posted_at <= T 且未被同口径更正的凭证；
   后来才补录/更正的数据不进入该时点预测，杜绝前视偏差。
3. 会议时点重放：按会议当时的已入账版本重算预测。
4. 逐日回测：每个预测时点 t 只使用 posted_at <= t 的已入账版本，
   实际值同样取 t 当天会议口径下可见的版本；缺少历史依据标 computable=false。
5. 工作台对照：会议当时预测 vs 用现有全部凭证回算的结果，
   逐条注明差异来自哪些凭证。
"""
from datetime import date, datetime, timedelta
import math

from src.services.engine import croston_rate, validate_params, baseline_mean

MIN_HISTORY_DAYS = 45          # 与 BUSINESS.md 失败态契约一致
BACKTEST_WINDOW = 28           # 末 28 日滚动一步回测
DEMAND_LIMIT = 1_000_000_000


class ReplayError(ValueError):
    """回放口径不满足时抛出，携带可展示的中文原因。"""


# ---------- 时间工具 ----------

def parse_epoch(value):
    """接受 epoch 秒（int/float）或 ISO 字符串，统一转 epoch 秒 float。"""
    if isinstance(value, bool):
        raise ReplayError("时间不能是布尔值")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value < 0:
            raise ReplayError("入账时间必须是非负有限时间戳")
        return float(value)
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise ReplayError("入账时间须为 ISO 日期时间或 epoch 秒") from None


def day_ordinal(occur):
    if isinstance(occur, date):
        return occur.toordinal()
    try:
        return date.fromisoformat(str(occur)).toordinal()
    except (ValueError, TypeError):
        raise ReplayError("领用发生日须为 YYYY-MM-DD 格式") from None


def day_start_epoch(occur):
    d = occur if isinstance(occur, date) else date.fromisoformat(str(occur))
    return datetime(d.year, d.month, d.day).timestamp()


def iso_from_ordinal(ordinal):
    return date.fromordinal(ordinal).isoformat()


# ---------- 凭证校验与版本解析 ----------

def validate_voucher(v):
    """登记侧校验，返回规范化字典；不改写 id（由持久层分配）。"""
    if not isinstance(v, dict):
        raise ReplayError("凭证必须是对象")
    part = str(v.get("part", "")).strip()
    if not part:
        raise ReplayError("凭证缺少备件编码 part")
    occur_ord = day_ordinal(v.get("occur_date"))
    posted_at = parse_epoch(v.get("posted_at"))
    try:
        demand = float(v.get("demand"))
    except (TypeError, ValueError):
        raise ReplayError("领用件数 demand 必须是数值") from None
    if not math.isfinite(demand) or not 0 <= demand <= DEMAND_LIMIT:
        raise ReplayError("单日领用量必须是0至10亿之间的有限数值")
    version = v.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise ReplayError("更正版本 version 必须是 >=1 的整数")
    entry_type = v.get("entry_type", "normal")
    if entry_type not in ("normal", "backfill", "correction"):
        raise ReplayError("entry_type 仅支持 normal/backfill/correction")
    corrected_id = v.get("corrected_id")
    if corrected_id is not None:
        try:
            corrected_id = int(corrected_id)
        except (TypeError, ValueError):
            raise ReplayError("corrected_id 必须是凭证整数 id 或 null") from None
    return {
        "part": part,
        "occur_date": iso_from_ordinal(occur_ord),
        "posted_at": posted_at,
        "demand": demand,
        "version": version,
        "entry_type": entry_type,
        "corrected_id": corrected_id,
    }


def visible_vouchers(vouchers, as_of_epoch, part):
    """时点 as_of_epoch 对某备件可见的有效凭证。

    规则：
    - posted_at <= as_of_epoch 才已入账；
    - 被更高版本凭证更正的旧版本剔除（同 part、同 occur_date、同 corrected_id 链）；
    - 同一 (part, occur_date) 取 version 最大的有效凭证；
    - 结果按发生日排序。
    """
    candidates = []
    for v in vouchers:
        if v["part"] != part:
            continue
        if float(v["posted_at"]) > as_of_epoch:
            continue
        candidates.append(v)
    # 更正关系：corrected_id 指向的旧凭证失效（前提是更正凭证本身已入账）。
    superseded_ids = {v["corrected_id"] for v in candidates if v.get("corrected_id")}
    latest_by_day = {}
    for v in candidates:
        if v.get("id") in superseded_ids:
            continue
        day = day_ordinal(v["occur_date"])
        old = latest_by_day.get(day)
        if old is None or v["version"] > old["version"]:
            latest_by_day[day] = v
    return [latest_by_day[k] for k in sorted(latest_by_day)]


def materialize_series(visible):
    """把可见凭证展开为连续自然日序列 (ordinal, demand, voucher_id)。

    断档日补零（零需求日本就保留）；重复日已在 visible_vouchers 去重。
    返回 (start_ord, rows)，rows 每项 (ordinal, demand, voucher_id)。
    """
    if not visible:
        return None, []
    start = day_ordinal(visible[0]["occur_date"])
    end = day_ordinal(visible[-1]["occur_date"])
    by_day = {day_ordinal(v["occur_date"]): v for v in visible}
    rows = []
    for ordinal in range(start, end + 1):
        v = by_day.get(ordinal)
        rows.append((ordinal, v["demand"] if v else 0.0, v["id"] if v else None))
    return start, rows


# ---------- 会议时点重放 ----------

def forecast_series(rows, alpha, variant, horizon):
    """对已物化的连续日序列做预测，返回业务结果字典。"""
    values = [r[1] for r in rows]
    sba = variant == "sba"
    rate = croston_rate(values, alpha, sba)
    last_ord = rows[-1][0]
    if last_ord + horizon > date.max.toordinal():
        raise ReplayError("预测末日超出日历支持范围，请检查最后观测日期")
    future = [
        {
            "day": iso_from_ordinal(last_ord + i),
            "expected": round(rate, 4),
            "cumulative": round(rate * i, 3),
        }
        for i in range(1, horizon + 1)
    ]
    return {"rate": rate, "future": future}


def replay_at_meeting(vouchers, meeting_epoch, params):
    """按会议时点已入账版本重放预测。

    历史不足 MIN_HISTORY_DAYS 个连续日时，不硬算，标注无法计算。
    """
    alpha, horizon, variant = validate_params(params)
    part = str(params.get("part", "R17"))
    visible = visible_vouchers(vouchers, meeting_epoch, part)
    start, rows = materialize_series(visible)
    result = {
        "part": part,
        "meeting_epoch": meeting_epoch,
        "visible_voucher_ids": [v["id"] for v in visible],
        "visible_count": len(visible),
        "computable": False,
        "reason": "",
        "rate": None,
        "future": [],
        "last_occur_date": None,
    }
    if len(rows) < MIN_HISTORY_DAYS:
        result["reason"] = (
            f"会议时点仅 {len(rows)} 个自然日已入账，不足 {MIN_HISTORY_DAYS} 日，无法计算"
        )
        return result
    fc = forecast_series(rows, alpha, variant, horizon)
    result.update(
        computable=True,
        rate=round(fc["rate"], 4),
        future=fc["future"],
        cumulative=round(fc["rate"] * horizon, 3),
        last_occur_date=iso_from_ordinal(rows[-1][0]),
    )
    return result


# ---------- 逐日严格向前回测 ----------

def daily_backtest(vouchers, meeting_epoch, params):
    """末 28 日逐日回测，每个预测时点只取当时已入账版本。

    对回测窗口内每个目标日 d（预测 d 当天），预测时点 cutoff = d 日 00:00。
    - 训练数据：posted_at <= cutoff 的已入账凭证，且发生日 < d；
    - 实际值：d 当天凭证必须在 meeting_epoch 前已入账，否则该日无法核对；
    - 训练历史 < 45 日：无法计算，明确标注原因。
    """
    alpha, _horizon, variant = validate_params(params)
    part = str(params.get("part", "R17"))
    sba = variant == "sba"
    meeting_visible = visible_vouchers(vouchers, meeting_epoch, part)
    start, rows = materialize_series(meeting_visible)
    # 现有全部凭证口径，用于在无法核对时给出参考实际值（不参与会议口径计算）。
    current_visible = visible_vouchers(vouchers, float("inf"), part)
    current_by_day = {day_ordinal(v["occur_date"]): v["demand"] for v in current_visible}
    points = []
    if not rows or len(rows) < 2:
        return {"computable_points": 0, "points": points,
                "mae": None, "baseline_mae": None,
                "note": "会议时点可见数据不足，无法回测"}
    window = rows[-BACKTEST_WINDOW:] if len(rows) >= BACKTEST_WINDOW else rows
    for target_ord, actual, actual_vid in window:
        cutoff = datetime.fromordinal(target_ord).timestamp()  # 目标日 00:00
        cutoff_visible = visible_vouchers(vouchers, cutoff, part)
        _s, cutoff_rows = materialize_series(cutoff_visible)
        train = [r for r in cutoff_rows if r[0] < target_ord]
        point = {
            "day": iso_from_ordinal(target_ord),
            "cutoff_epoch": cutoff,
            "actual": actual,
            "reference_actual": current_by_day.get(target_ord, 0.0),
            "actual_voucher_id": actual_vid,
            "computable": False,
            "reason": "",
            "predicted": None,
            "baseline": None,
        }
        if actual_vid is None:
            point["reason"] = "该零需求日无独立凭证，按零领用核对"
        # 实际值凭证若会议时尚未入账，则无法核对（前视防护）。
        actual_posted = actual_vid is None or any(
            v["id"] == actual_vid and float(v["posted_at"]) <= meeting_epoch
            for v in vouchers if v["part"] == part
        )
        if not actual_posted:
            point["reason"] = "实际领用凭证在会议时点后才入账，无法计算"
            points.append(point)
            continue
        # 会议可见序列里该日被零填充，但台账中存在会后才入账的凭证：
        # 会议口径无法知道真实领用，同样不能核对。
        hidden = [
            v for v in vouchers
            if v["part"] == part
            and day_ordinal(v["occur_date"]) == target_ord
            and float(v["posted_at"]) > meeting_epoch
        ]
        if actual_vid is None and hidden:
            point["reason"] = (
                "该日凭证在会议时点后才入账（补记 "
                + "、".join(f"#{v['id']}" for v in hidden) + "），无法计算"
            )
            points.append(point)
            continue
        if len(train) < MIN_HISTORY_DAYS:
            point["reason"] = (
                f"预测时点前仅 {len(train)} 个自然日已入账，不足 {MIN_HISTORY_DAYS} 日，无法计算"
            )
            points.append(point)
            continue
        train_values = [r[1] for r in train]
        point["predicted"] = round(croston_rate(train_values, alpha, sba), 4)
        point["baseline"] = round(baseline_mean(train_values), 4)
        point["computable"] = True
        points.append(point)
    valid = [p for p in points if p["computable"]]
    mae = (
        round(sum(abs(p["actual"] - p["predicted"]) for p in valid) / len(valid), 4)
        if valid else None
    )
    base_mae = (
        round(sum(abs(p["actual"] - p["baseline"]) for p in valid) / len(valid), 4)
        if valid else None
    )
    return {
        "computable_points": len(valid),
        "total_points": len(points),
        "points": points,
        "mae": mae,
        "baseline_mae": base_mae,
        "note": "" if valid else "回测窗口内没有满足 45 日历史依据的预测时点",
    }


# ---------- 工作台对照：会议口径 vs 现有凭证回算 ----------

def current_recompute(vouchers, params):
    """用现有全部凭证（无视入账时间）回算同一口径，作为对照基线。"""
    alpha, horizon, variant = validate_params(params)
    part = str(params.get("part", "R17"))
    visible = visible_vouchers(vouchers, float("inf"), part)
    _start, rows = materialize_series(visible)
    if len(rows) < MIN_HISTORY_DAYS:
        return {"computable": False,
                "reason": f"现有凭证仅覆盖 {len(rows)} 个自然日，不足 {MIN_HISTORY_DAYS} 日，无法计算",
                "rate": None, "future": [], "visible_voucher_ids": [v["id"] for v in visible]}
    fc = forecast_series(rows, alpha, variant, horizon)
    return {
        "computable": True,
        "rate": round(fc["rate"], 4),
        "cumulative": round(fc["rate"] * horizon, 3),
        "future": fc["future"],
        "last_occur_date": iso_from_ordinal(rows[-1][0]),
        "visible_voucher_ids": [v["id"] for v in visible],
    }


def compare_meeting_vs_current(vouchers, meeting_epoch, params):
    """生成对照结果与差异归因。

    差异凭证 = 现有有效凭证中，会议时点尚未入账，或在会议时点后被更正的凭证。
    """
    meeting = replay_at_meeting(vouchers, meeting_epoch, params)
    current = current_recompute(vouchers, params)
    part = meeting["part"]
    meeting_ids = set(meeting["visible_voucher_ids"])
    current_ids = set(current["visible_voucher_ids"])
    diff_ids = current_ids - meeting_ids
    diff_vouchers = []
    for v in vouchers:
        if v["part"] != part or v.get("id") not in diff_ids:
            continue
        diff_vouchers.append({
            "id": v["id"],
            "occur_date": v["occur_date"],
            "posted_at": v["posted_at"],
            "demand": v["demand"],
            "version": v["version"],
            "entry_type": v["entry_type"],
            "corrected_id": v.get("corrected_id"),
            "lag_days": _lag_label(v["posted_at"], meeting_epoch),
            "reason": _diff_reason(v, meeting_epoch),
        })
    diff_vouchers.sort(key=lambda x: (x["occur_date"], x["version"]))
    rate_delta = None
    if meeting["computable"] and current["computable"]:
        rate_delta = round(current["rate"] - meeting["rate"], 4)
    return {
        "meeting": meeting,
        "current": current,
        "rate_delta": rate_delta,
        "diff_voucher_count": len(diff_vouchers),
        "diff_vouchers": diff_vouchers,
    }


def _lag_label(posted_at, meeting_epoch):
    if posted_at <= meeting_epoch:
        return 0
    return max(1, math.ceil((posted_at - meeting_epoch) / 86400))


def _diff_reason(v, meeting_epoch):
    """区分：会后更正 / 已发生但晚入账的补记 / 会后才发生的常规凭证。"""
    if v.get("entry_type") == "correction" or v["version"] > 1:
        return "会议后入账的更正版本"
    occur_ord = day_ordinal(v["occur_date"])
    meeting_day = datetime.fromtimestamp(meeting_epoch).date().toordinal()
    if occur_ord <= meeting_day:
        if v.get("entry_type") == "backfill":
            return "领用已发生但晚入账的补记（会议时点不可见）"
        return "会议时点前已发生但当时未入账"
    return "会议之后才发生的常规凭证（会议时点尚不存在）"


# ---------- 回放清单（运行恢复 / 并发补录留待下次） ----------

def build_manifest(vouchers, meeting_epochs, params):
    """固化一次回放运行的输入清单快照。

    清单只引用运行创建时已存在的凭证 id；运行开始后新并发补录的凭证
    不在清单内，留待下次运行（worker 按 manifest 恢复，不重复归档）。
    """
    frozen_ids = sorted(v["id"] for v in vouchers)
    return {
        "voucher_ids": frozen_ids,
        "meeting_epochs": sorted(float(t) for t in meeting_epochs),
        "params": dict(params),
        "voucher_checksum_len": len(frozen_ids),
    }


def run_manifest(vouchers_by_id, manifest, progress=None):
    """按固化清单执行整次回放。

    progress: 已完成的 {meeting_epoch: point_result}，中断恢复时传入，
    已完成的会议点直接跳过，保证重试不重复计算/归档。
    """
    progress = progress or {}
    points = []
    skipped_concurrent = []
    allowed = set(manifest["voucher_ids"])
    frozen = [v for v in vouchers_by_id.values() if v["id"] in allowed]
    for meeting_epoch in manifest["meeting_epochs"]:
        key = str(meeting_epoch)
        if key in progress:
            points.append(progress[key])
            continue
        point = {
            "meeting_epoch": meeting_epoch,
            "meeting_at": datetime.fromtimestamp(meeting_epoch).isoformat(sep=" "),
            "forecast": replay_at_meeting(frozen, meeting_epoch, manifest["params"]),
            "backtest": daily_backtest(frozen, meeting_epoch, manifest["params"]),
            "comparison": compare_meeting_vs_current(frozen, meeting_epoch, manifest["params"]),
        }
        points.append(point)
        progress[key] = point
    # 清单之外的新补录凭证（运行期间并发到达）显式留档，留给下次运行。
    for v in vouchers_by_id.values():
        if v["id"] not in allowed:
            skipped_concurrent.append({"id": v["id"], "occur_date": v["occur_date"],
                                       "posted_at": v["posted_at"], "part": v["part"]})
    return {"points": points, "skipped_concurrent": skipped_concurrent,
            "manifest_size": len(allowed)}
