"""Croston/SBA 核心算法（无第三方依赖，便于独立单测）。

业务口径见 BUSINESS.md：对非零需求量 z 与间隔 p 做指数平滑，
f = z / p；SBA 再乘 (1 - α/2)。每个回测点只使用此前数据。
"""
import math
import statistics


def number(value, name, low, high):
    try:
        answer = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}必须是数值") from None
    if not math.isfinite(answer) or not low <= answer <= high:
        raise ValueError(f"{name}必须在 {low} 至 {high} 之间")
    return answer


def validate_params(params):
    """统一参数校验，返回 (alpha, horizon, variant)。"""
    alpha = number(params.get("alpha", 0.2), "α", 0.05, 0.8)
    horizon_value = number(params.get("horizon", 14), "预测天数", 3, 45)
    if not horizon_value.is_integer():
        raise ValueError("预测天数必须是整数")
    variant = params.get("variant", "sba")
    if variant not in ("sba", "croston"):
        raise ValueError("未知的 Croston 变体")
    return alpha, int(horizon_value), variant


def croston_rate(values, alpha, sba):
    """给定截至当前已知的序列，返回平滑领用率。全零返回 0。"""
    nonzero = [index for index, value in enumerate(values) if value > 0]
    if not nonzero:
        return 0.0
    first = nonzero[0]
    size = float(values[first])
    gap = float(first + 1)
    last = first
    for index in nonzero[1:]:
        size += alpha * (values[index] - size)
        gap += alpha * ((index - last) - gap)
        last = index
    return size / gap * (1 - alpha / 2 if sba else 1)


def baseline_mean(values):
    return statistics.mean(values) if values else 0.0
