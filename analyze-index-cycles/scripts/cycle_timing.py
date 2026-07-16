from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy

try:
    from .cycle_model import (
        CycleConfig,
        RedNoiseReference,
        _configure_plot_style,
        build_or_load_red_noise_references,
        load_price_data,
    )
    from .ma_short_cycle import analyze_ma_short_cycle, short_ma_config
except ImportError:
    from cycle_model import (
        CycleConfig,
        RedNoiseReference,
        _configure_plot_style,
        build_or_load_red_noise_references,
        load_price_data,
    )
    from ma_short_cycle import analyze_ma_short_cycle, short_ma_config


ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class TimingConfig:
    ma_windows: tuple[int, ...] = (34, 55, 89)
    trend_ma_window: int = 120
    trend_slope_lookback: int = 20
    agreement_tolerance: float = 0.15
    boundary_tolerance: float = 0.02
    stability_window: int = 15
    stability_min_hits: int = 12
    harmonic_min_r2: float = 0.10
    amplitude_cycles: float = 2.5
    bottom_arm_level: float = -0.60
    bottom_trigger_level: float = -0.40
    top_arm_level: float = 0.60
    top_trigger_level: float = 0.40
    max_overlay_weight: float = 0.15
    cooldown_fraction: float = 1.0 / 3.0
    cost_bps: float = 5.0
    forward_horizons: tuple[int, ...] = (5, 10, 20, 40)

    def validate(self) -> None:
        if len(self.ma_windows) < 3 or len(set(self.ma_windows)) != len(self.ma_windows):
            raise ValueError("均线共识至少需要 3 个互不重复的窗口。")
        if any(window < 2 for window in self.ma_windows):
            raise ValueError("均线窗口必须至少为 2。")
        if self.trend_ma_window < 2 or self.trend_slope_lookback < 1:
            raise ValueError("趋势均线和斜率回看窗口无效。")
        if not 0.0 < self.agreement_tolerance < 0.5:
            raise ValueError("周期一致容差必须位于 (0, 0.5)。")
        if not 0.0 <= self.boundary_tolerance < 0.25:
            raise ValueError("频段边界容差必须位于 [0, 0.25)。")
        if not 0 < self.stability_min_hits <= self.stability_window:
            raise ValueError("稳定命中数必须位于 1 与稳定窗口之间。")
        if not 0.0 <= self.harmonic_min_r2 <= 1.0:
            raise ValueError("谐波最低 R² 必须位于 [0, 1]。")
        if not self.bottom_arm_level < self.bottom_trigger_level < 0.0:
            raise ValueError("底部预备线必须低于底部触发线，且均小于 0。")
        if not 0.0 < self.top_trigger_level < self.top_arm_level:
            raise ValueError("顶部触发线必须低于顶部预备线，且均大于 0。")
        if not 0.0 < self.max_overlay_weight <= 1.0:
            raise ValueError("最大叠加仓位必须位于 (0, 1]。")
        if not 0.0 < self.cooldown_fraction <= 1.0:
            raise ValueError("冷却期比例必须位于 (0, 1]。")
        if self.cost_bps < 0.0:
            raise ValueError("交易成本不能为负。")
        if not self.forward_horizons or any(value < 1 for value in self.forward_horizons):
            raise ValueError("事件研究期限必须为正整数。")


def estimate_harmonic_phase(
    log_price: np.ndarray,
    end_index: int,
    period_days: float,
    amplitude_cycles: float,
) -> dict[str, float]:
    empty = {
        "phase_amplitude_log": np.nan,
        "phase_peak_to_trough_pct": np.nan,
        "cycle_component_pct": np.nan,
        "cycle_score": np.nan,
        "cycle_direction_score": np.nan,
        "phase_angle_rad": np.nan,
        "harmonic_r2": np.nan,
    }
    if not np.isfinite(period_days) or period_days <= 2.0:
        return empty
    lookback = int(math.ceil(amplitude_cycles * period_days))
    start_index = end_index - lookback + 1
    if start_index < 0 or lookback < 8:
        return empty

    values = np.asarray(log_price[start_index : end_index + 1], dtype=float)
    if values.size != lookback or not np.isfinite(values).all():
        return empty
    time_index = np.arange(lookback, dtype=float)
    scaled_time = (time_index - time_index.mean()) / max(float(lookback - 1), 1.0)
    angle = 2.0 * np.pi * time_index / period_days
    trend_design = np.column_stack([np.ones(lookback), scaled_time])
    full_design = np.column_stack([trend_design, np.cos(angle), np.sin(angle)])
    trend_coefficients, *_ = np.linalg.lstsq(trend_design, values, rcond=None)
    coefficients, *_ = np.linalg.lstsq(full_design, values, rcond=None)
    trend_residual = values - trend_design @ trend_coefficients
    full_residual = values - full_design @ coefficients
    trend_sse = float(trend_residual @ trend_residual)
    full_sse = float(full_residual @ full_residual)
    harmonic_r2 = 0.0 if trend_sse <= np.finfo(float).tiny else 1.0 - full_sse / trend_sse

    cosine_coefficient = float(coefficients[-2])
    sine_coefficient = float(coefficients[-1])
    amplitude = float(np.hypot(cosine_coefficient, sine_coefficient))
    if amplitude <= np.finfo(float).tiny:
        return {**empty, "harmonic_r2": harmonic_r2}
    current_angle = float(angle[-1])
    component_log = float(
        cosine_coefficient * math.cos(current_angle)
        + sine_coefficient * math.sin(current_angle)
    )
    direction = float(
        -cosine_coefficient * math.sin(current_angle)
        + sine_coefficient * math.cos(current_angle)
    )
    cycle_score = float(np.clip(component_log / amplitude, -1.0, 1.0))
    direction_score = float(np.clip(direction / amplitude, -1.0, 1.0))
    return {
        "phase_amplitude_log": amplitude,
        "phase_peak_to_trough_pct": 100.0 * math.expm1(2.0 * amplitude),
        "cycle_component_pct": 100.0 * math.expm1(component_log),
        "cycle_score": cycle_score,
        "cycle_direction_score": direction_score,
        "phase_angle_rad": float(math.atan2(direction_score, cycle_score)),
        "harmonic_r2": harmonic_r2,
    }


def _merge_ma_periods(
    results_by_window: dict[int, pd.DataFrame],
    timing_config: TimingConfig,
) -> pd.DataFrame:
    merged: pd.DataFrame | None = None
    for window in timing_config.ma_windows:
        source = results_by_window[window]
        columns = [
            "date",
            "code",
            "index_name",
            "close",
            "tracked_period_days",
            "raw_period_days",
            "spectral_strength_ratio",
            "red_noise_pvalue",
            "boundary_peak",
            "period_dispersion_pct_15",
        ]
        current = source[columns].copy()
        identifier_columns = {"date", "code", "index_name", "close"}
        current = current.rename(
            columns={
                column: f"ma{window}_{column}"
                for column in columns
                if column not in identifier_columns
            }
        )
        if merged is None:
            merged = current
        else:
            current = current.drop(columns=["code", "index_name", "close"])
            merged = merged.merge(current, on="date", how="inner", validate="one_to_one")
    if merged is None or merged.empty:
        raise ValueError("三均线结果没有共同日期。")
    return merged.sort_values("date").reset_index(drop=True)


def _add_consensus_fields(
    daily: pd.DataFrame,
    band_min: float,
    band_max: float,
    timing_config: TimingConfig,
) -> pd.DataFrame:
    result = daily.copy()
    period_columns = [f"ma{window}_tracked_period_days" for window in timing_config.ma_windows]
    strength_columns = [f"ma{window}_spectral_strength_ratio" for window in timing_config.ma_windows]
    periods = result[period_columns].to_numpy(dtype=float)
    strengths = result[strength_columns].to_numpy(dtype=float)
    middle = np.nanmedian(periods, axis=1)
    relative_to_middle = np.abs(periods / middle[:, None] - 1.0)
    agreeing = relative_to_middle <= timing_config.agreement_tolerance
    agreement_count = agreeing.sum(axis=1)
    consensus_period = np.nanmedian(np.where(agreeing, periods, np.nan), axis=1)
    consensus_period[agreement_count < 2] = np.nan
    valid_consensus_rows = agreement_count >= 2
    strength = np.full(len(result), np.nan, dtype=float)
    strength[valid_consensus_rows] = np.nanmedian(
        np.where(agreeing, strengths, np.nan)[valid_consensus_rows],
        axis=1,
    )
    relative_to_consensus = np.abs(periods / consensus_period[:, None] - 1.0)
    dispersion = np.full(len(result), np.nan, dtype=float)
    dispersion[valid_consensus_rows] = 100.0 * np.nanmedian(
        np.where(agreeing, relative_to_consensus, np.nan)[valid_consensus_rows],
        axis=1,
    )

    min_boundary = band_min * (1.0 + timing_config.boundary_tolerance)
    max_boundary = band_max * (1.0 - timing_config.boundary_tolerance)
    boundary = (consensus_period <= min_boundary) | (consensus_period >= max_boundary)
    base_valid = (agreement_count >= 2) & np.isfinite(consensus_period) & ~boundary

    stability_hits = np.zeros(len(result), dtype=int)
    stability_dispersion = np.full(len(result), np.nan, dtype=float)
    stable = np.zeros(len(result), dtype=bool)
    for row_index in range(len(result)):
        start = max(0, row_index - timing_config.stability_window + 1)
        recent_periods = consensus_period[start : row_index + 1]
        recent_valid = base_valid[start : row_index + 1] & np.isfinite(recent_periods)
        if not recent_valid.any():
            continue
        recent_median = float(np.median(recent_periods[recent_valid]))
        deviations = np.abs(recent_periods / recent_median - 1.0)
        hits = recent_valid & (deviations <= timing_config.agreement_tolerance)
        stability_hits[row_index] = int(hits.sum())
        stability_dispersion[row_index] = float(100.0 * np.median(deviations[recent_valid]))
        stable[row_index] = bool(
            base_valid[row_index]
            and stability_hits[row_index] >= timing_config.stability_min_hits
        )

    result["ma_agreement_count"] = agreement_count
    result["consensus_period_days"] = consensus_period
    result["consensus_period_dispersion_pct"] = dispersion
    result["consensus_strength_ratio"] = strength
    result["consensus_boundary_peak"] = boundary
    result["consensus_base_valid"] = base_valid
    result["consensus_stability_hits_15"] = stability_hits
    result["consensus_stability_dispersion_pct_15"] = stability_dispersion
    result["consensus_stable"] = stable
    return result


def build_indicators_from_ma_results(
    price_data: pd.DataFrame,
    results_by_window: dict[int, pd.DataFrame],
    cycle_config: CycleConfig,
    timing_config: TimingConfig | None = None,
) -> pd.DataFrame:
    active = timing_config or TimingConfig()
    active.validate()
    cycle_config.validate()
    if len(cycle_config.bands) != 1:
        raise ValueError("周期择时 V1 只支持一个短周期频段。")
    missing_windows = set(active.ma_windows).difference(results_by_window)
    if missing_windows:
        raise ValueError(f"缺少均线周期结果：{sorted(missing_windows)}")

    band = cycle_config.bands[0]
    daily = _merge_ma_periods(results_by_window, active)
    daily = _add_consensus_fields(
        daily,
        band.min_period_days,
        band.max_period_days,
        active,
    )

    price_dates = pd.Series(pd.to_datetime(price_data["date"])).reset_index(drop=True)
    date_to_index = pd.Series(price_dates.index.to_numpy(), index=price_dates).to_dict()
    log_price = np.log(price_data["close"].to_numpy(dtype=float))
    phase_records: list[dict[str, float]] = []
    for row in daily.itertuples(index=False):
        end_index = int(date_to_index[pd.Timestamp(row.date)])
        phase_records.append(
            estimate_harmonic_phase(
                log_price,
                end_index,
                float(row.consensus_period_days),
                active.amplitude_cycles,
            )
        )
    phase = pd.DataFrame.from_records(phase_records)
    daily = pd.concat([daily.reset_index(drop=True), phase], axis=1)

    close_series = pd.Series(price_data["close"].to_numpy(dtype=float))
    trend_ma = close_series.rolling(
        active.trend_ma_window,
        min_periods=active.trend_ma_window,
    ).mean()
    trend_slope = trend_ma / trend_ma.shift(active.trend_slope_lookback) - 1.0
    trend_frame = pd.DataFrame(
        {
            "date": price_dates,
            "trend_ma120": trend_ma.to_numpy(dtype=float),
            "trend_ma120_slope_20d": trend_slope.to_numpy(dtype=float),
        }
    )
    daily = daily.merge(trend_frame, on="date", how="left", validate="one_to_one")
    trend_up = (daily["close"] > daily["trend_ma120"]) & (
        daily["trend_ma120_slope_20d"] > 0.0
    )
    trend_down = (daily["close"] < daily["trend_ma120"]) & (
        daily["trend_ma120_slope_20d"] < 0.0
    )
    daily["trend_state"] = np.select(
        [trend_up, trend_down],
        ["up", "down"],
        default="neutral",
    )

    daily["cycle_ready"] = (
        daily["consensus_stable"]
        & (daily["ma_agreement_count"] >= 2)
        & ~daily["consensus_boundary_peak"]
        & (daily["harmonic_r2"] >= active.harmonic_min_r2)
    )
    consensus_score = daily["ma_agreement_count"].to_numpy(dtype=float) / float(
        len(active.ma_windows)
    )
    stability_score = np.clip(
        daily["consensus_stability_hits_15"].to_numpy(dtype=float)
        / float(active.stability_window),
        0.0,
        1.0,
    )
    strength_score = np.clip(
        daily["consensus_strength_ratio"].to_numpy(dtype=float),
        0.0,
        1.0,
    )
    fit_score = np.sqrt(np.clip(daily["harmonic_r2"].to_numpy(dtype=float), 0.0, 1.0))
    confidence = consensus_score * stability_score * strength_score * fit_score
    confidence[~daily["cycle_ready"].to_numpy(dtype=bool)] = 0.0
    daily["cycle_confidence"] = np.clip(confidence, 0.0, 1.0)
    return daily


def add_individual_ma_phase_scores(
    daily: pd.DataFrame,
    price_data: pd.DataFrame,
    timing_config: TimingConfig,
) -> pd.DataFrame:
    """为每条 MA 独立周期计算相位，供未形成共识时的可视化使用。"""
    result = daily.copy()
    price_dates = pd.Series(pd.to_datetime(price_data["date"])).reset_index(drop=True)
    date_to_index = pd.Series(price_dates.index.to_numpy(), index=price_dates).to_dict()
    log_price = np.log(price_data["close"].to_numpy(dtype=float))

    for window in timing_config.ma_windows:
        period_column = f"ma{window}_tracked_period_days"
        scores = np.full(len(result), np.nan, dtype=float)
        for row_index, (date_value, period_days) in enumerate(
            zip(result["date"], result[period_column], strict=True)
        ):
            end_index = date_to_index.get(pd.Timestamp(date_value))
            if end_index is None:
                continue
            phase = estimate_harmonic_phase(
                log_price,
                int(end_index),
                float(period_days),
                timing_config.amplitude_cycles,
            )
            scores[row_index] = phase["cycle_score"]
        result[f"ma{window}_cycle_score"] = scores
    return result


def build_timing_indicators(
    price_data: pd.DataFrame,
    cycle_config: CycleConfig,
    red_noise_reference: RedNoiseReference,
    timing_config: TimingConfig | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    active = timing_config or TimingConfig()
    active.validate()
    cycle_config.validate()
    if len(cycle_config.bands) != 1:
        raise ValueError("周期择时 V1 只支持一个短周期频段。")

    results_by_window: dict[int, pd.DataFrame] = {}
    for window in active.ma_windows:
        if progress:
            progress(f"计算 MA{window} 的短周期频谱。")
        result, _ = analyze_ma_short_cycle(
            price_data,
            cycle_config,
            red_noise_reference,
            ma_window=window,
            boundary_tolerance=active.boundary_tolerance,
            progress=progress,
        )
        if result.empty:
            raise ValueError(f"MA{window} 没有足够样本生成周期结果。")
        results_by_window[window] = result

    daily = build_indicators_from_ma_results(
        price_data,
        results_by_window,
        cycle_config,
        active,
    )
    return daily, results_by_window


def apply_timing_state_machine(
    indicators: pd.DataFrame,
    timing_config: TimingConfig | None = None,
) -> pd.DataFrame:
    active = timing_config or TimingConfig()
    active.validate()
    result = indicators.copy().sort_values("date").reset_index(drop=True)
    phase_events = np.full(len(result), "", dtype=object)
    actions = np.full(len(result), "", dtype=object)
    bottom_armed_column = np.zeros(len(result), dtype=bool)
    top_armed_column = np.zeros(len(result), dtype=bool)
    cooldown_column = np.zeros(len(result), dtype=int)
    overlay_target = np.zeros(len(result), dtype=float)

    bottom_armed = False
    top_armed = False
    cooldown_remaining = 0
    current_overlay = 0.0
    for row_index, row in enumerate(result.itertuples(index=False)):
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
        cycle_ready = bool(row.cycle_ready)
        score = float(row.cycle_score) if np.isfinite(row.cycle_score) else np.nan
        direction = (
            float(row.cycle_direction_score)
            if np.isfinite(row.cycle_direction_score)
            else np.nan
        )
        trend_state = str(row.trend_state)

        if current_overlay > 0.0 and trend_state != "up":
            current_overlay = 0.0
            actions[row_index] = "trend_exit_overweight"
        elif current_overlay < 0.0 and trend_state != "down":
            current_overlay = 0.0
            actions[row_index] = "trend_exit_underweight"

        if not cycle_ready or not np.isfinite(score) or not np.isfinite(direction):
            bottom_armed = False
            top_armed = False
        else:
            if score <= active.bottom_arm_level:
                bottom_armed = True
                top_armed = False
            elif score >= active.top_arm_level:
                top_armed = True
                bottom_armed = False

            if (
                bottom_armed
                and cooldown_remaining == 0
                and score >= active.bottom_trigger_level
                and direction > 0.0
            ):
                phase_events[row_index] = "bottom_turn"
                bottom_armed = False
                cooldown_remaining = max(
                    1,
                    int(math.ceil(float(row.consensus_period_days) * active.cooldown_fraction)),
                )
                if trend_state == "up":
                    current_overlay = active.max_overlay_weight * float(row.cycle_confidence)
                    actions[row_index] = "increase_above_base"
                elif current_overlay < 0.0:
                    current_overlay = 0.0
                    actions[row_index] = "bottom_return_to_base"

            elif (
                top_armed
                and cooldown_remaining == 0
                and score <= active.top_trigger_level
                and direction < 0.0
            ):
                phase_events[row_index] = "top_turn"
                top_armed = False
                cooldown_remaining = max(
                    1,
                    int(math.ceil(float(row.consensus_period_days) * active.cooldown_fraction)),
                )
                if trend_state == "down":
                    current_overlay = -active.max_overlay_weight * float(row.cycle_confidence)
                    actions[row_index] = "reduce_below_base"
                elif current_overlay > 0.0:
                    current_overlay = 0.0
                    actions[row_index] = "top_return_to_base"

        bottom_armed_column[row_index] = bottom_armed
        top_armed_column[row_index] = top_armed
        cooldown_column[row_index] = cooldown_remaining
        overlay_target[row_index] = current_overlay

    result["phase_event"] = phase_events
    result["timing_action"] = actions
    result["bottom_armed"] = bottom_armed_column
    result["top_armed"] = top_armed_column
    result["cooldown_remaining"] = cooldown_column
    result["overlay_target"] = overlay_target
    result["overlay_change"] = result["overlay_target"].diff().fillna(result["overlay_target"])

    close_return = result["close"].pct_change().fillna(0.0)
    result["close_return"] = close_return
    result["overlay_turnover"] = result["overlay_change"].abs()
    result["overlay_gross_return"] = result["overlay_target"].shift(1).fillna(0.0) * close_return
    result["overlay_cost"] = result["overlay_turnover"] * active.cost_bps / 10000.0
    result["overlay_net_return"] = result["overlay_gross_return"] - result["overlay_cost"]
    result["overlay_cumulative_net"] = (1.0 + result["overlay_net_return"]).cumprod() - 1.0
    return result


def build_event_study(
    timing_daily: pd.DataFrame,
    timing_config: TimingConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    active = timing_config or TimingConfig()
    active.validate()
    daily = timing_daily.copy().sort_values("date").reset_index(drop=True)
    close = daily["close"].to_numpy(dtype=float)
    ready = daily["cycle_ready"].to_numpy(dtype=bool)
    for horizon in active.forward_horizons:
        daily[f"forward_return_{horizon}d"] = daily["close"].shift(-horizon) / daily["close"] - 1.0
        mfe = np.full(len(daily), np.nan, dtype=float)
        mae = np.full(len(daily), np.nan, dtype=float)
        for row_index in range(0, len(daily) - horizon):
            path = close[row_index + 1 : row_index + horizon + 1] / close[row_index] - 1.0
            mfe[row_index] = float(np.max(path))
            mae[row_index] = float(np.min(path))
        daily[f"mfe_{horizon}d"] = mfe
        daily[f"mae_{horizon}d"] = mae

    event_mask = daily["phase_event"].isin(["bottom_turn", "top_turn"])
    event_columns = [
        "date",
        "code",
        "index_name",
        "close",
        "phase_event",
        "timing_action",
        "trend_state",
        "consensus_period_days",
        "cycle_score",
        "cycle_direction_score",
        "cycle_confidence",
        "harmonic_r2",
        "overlay_target",
    ]
    for horizon in active.forward_horizons:
        event_columns.extend(
            [f"forward_return_{horizon}d", f"mfe_{horizon}d", f"mae_{horizon}d"]
        )
    events = daily.loc[event_mask, event_columns].copy().reset_index(drop=True)

    records: list[dict] = []
    for event_type in ("bottom_turn", "top_turn"):
        subset = events.loc[events["phase_event"] == event_type]
        direction_sign = 1.0 if event_type == "bottom_turn" else -1.0
        for horizon in active.forward_horizons:
            return_column = f"forward_return_{horizon}d"
            valid_events = subset.loc[np.isfinite(subset[return_column])].copy()
            baseline_by_state = (
                daily.loc[ready & np.isfinite(daily[return_column]), ["trend_state", return_column]]
                .groupby("trend_state")[return_column]
                .mean()
            )
            if valid_events.empty:
                records.append(
                    {
                        "event_type": event_type,
                        "horizon_days": horizon,
                        "events": 0,
                        "mean_forward_return_pct": np.nan,
                        "median_forward_return_pct": np.nan,
                        "directional_hit_rate": np.nan,
                        "mean_regime_baseline_pct": np.nan,
                        "mean_directional_edge_pct": np.nan,
                        "mean_mfe_pct": np.nan,
                        "mean_mae_pct": np.nan,
                    }
                )
                continue
            baseline = valid_events["trend_state"].map(baseline_by_state).to_numpy(dtype=float)
            event_returns = valid_events[return_column].to_numpy(dtype=float)
            directional_returns = direction_sign * event_returns
            directional_edge = direction_sign * (event_returns - baseline)
            records.append(
                {
                    "event_type": event_type,
                    "horizon_days": horizon,
                    "events": int(len(valid_events)),
                    "mean_forward_return_pct": float(100.0 * np.mean(event_returns)),
                    "median_forward_return_pct": float(100.0 * np.median(event_returns)),
                    "directional_hit_rate": float(np.mean(directional_returns > 0.0)),
                    "mean_regime_baseline_pct": float(100.0 * np.nanmean(baseline)),
                    "mean_directional_edge_pct": float(100.0 * np.nanmean(directional_edge)),
                    "mean_mfe_pct": float(100.0 * valid_events[f"mfe_{horizon}d"].mean()),
                    "mean_mae_pct": float(100.0 * valid_events[f"mae_{horizon}d"].mean()),
                }
            )
    return events, pd.DataFrame.from_records(records)


def build_action_study(
    events: pd.DataFrame,
    timing_config: TimingConfig | None = None,
) -> pd.DataFrame:
    active = timing_config or TimingConfig()
    active.validate()
    records: list[dict] = []
    action_directions = {
        "increase_above_base": 1.0,
        "reduce_below_base": -1.0,
    }
    for action, direction_sign in action_directions.items():
        subset = events.loc[events["timing_action"] == action]
        for horizon in active.forward_horizons:
            return_column = f"forward_return_{horizon}d"
            valid = subset.loc[np.isfinite(subset[return_column])]
            if valid.empty:
                records.append(
                    {
                        "timing_action": action,
                        "horizon_days": horizon,
                        "events": 0,
                        "mean_forward_return_pct": np.nan,
                        "median_forward_return_pct": np.nan,
                        "directional_hit_rate": np.nan,
                        "mean_directional_return_pct": np.nan,
                        "mean_mfe_pct": np.nan,
                        "mean_mae_pct": np.nan,
                    }
                )
                continue
            forward = valid[return_column].to_numpy(dtype=float)
            records.append(
                {
                    "timing_action": action,
                    "horizon_days": horizon,
                    "events": int(len(valid)),
                    "mean_forward_return_pct": float(100.0 * np.mean(forward)),
                    "median_forward_return_pct": float(100.0 * np.median(forward)),
                    "directional_hit_rate": float(np.mean(direction_sign * forward > 0.0)),
                    "mean_directional_return_pct": float(
                        100.0 * np.mean(direction_sign * forward)
                    ),
                    "mean_mfe_pct": float(100.0 * valid[f"mfe_{horizon}d"].mean()),
                    "mean_mae_pct": float(100.0 * valid[f"mae_{horizon}d"].mean()),
                }
            )
    return pd.DataFrame.from_records(records)


def _overlay_performance(daily: pd.DataFrame) -> dict:
    returns = daily["overlay_net_return"].to_numpy(dtype=float)
    if returns.size == 0:
        return {}
    equity = np.cumprod(1.0 + returns)
    running_max = np.maximum.accumulate(equity)
    drawdown = equity / running_max - 1.0
    annualized_return = float(equity[-1] ** (252.0 / len(returns)) - 1.0)
    annualized_volatility = float(np.std(returns, ddof=1) * math.sqrt(252.0))
    annualized_sharpe = (
        float(np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(252.0))
        if np.std(returns, ddof=1) > np.finfo(float).tiny
        else np.nan
    )
    return {
        "cumulative_net_return": float(equity[-1] - 1.0),
        "annualized_net_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "annualized_sharpe": annualized_sharpe,
        "max_drawdown": float(np.min(drawdown)),
        "total_turnover": float(daily["overlay_turnover"].sum()),
        "total_cost": float(daily["overlay_cost"].sum()),
        "average_abs_overlay": float(daily["overlay_target"].abs().mean()),
    }


def build_timing_sensitivity(
    price_data: pd.DataFrame,
    results_by_window: dict[int, pd.DataFrame],
    cycle_config: CycleConfig,
    baseline_config: TimingConfig,
    *,
    progress: ProgressCallback | None = None,
) -> pd.DataFrame:
    variants = {
        "baseline": baseline_config,
        "agreement_10pct": replace(baseline_config, agreement_tolerance=0.10),
        "agreement_20pct": replace(baseline_config, agreement_tolerance=0.20),
        "phase_tight": replace(
            baseline_config,
            bottom_arm_level=-0.70,
            bottom_trigger_level=-0.50,
            top_arm_level=0.70,
            top_trigger_level=0.50,
        ),
        "phase_loose": replace(
            baseline_config,
            bottom_arm_level=-0.50,
            bottom_trigger_level=-0.30,
            top_arm_level=0.50,
            top_trigger_level=0.30,
        ),
        "harmonic_r2_15pct": replace(baseline_config, harmonic_min_r2=0.15),
        "stability_13_of_15": replace(baseline_config, stability_min_hits=13),
    }
    records: list[dict] = []
    date_span_years = None
    for variant_name, config in variants.items():
        if progress:
            progress(f"稳健性对照：{variant_name}")
        indicators = build_indicators_from_ma_results(
            price_data,
            results_by_window,
            cycle_config,
            config,
        )
        timing = apply_timing_state_machine(indicators, config)
        _, study = build_event_study(timing, config)
        performance = _overlay_performance(timing)
        if date_span_years is None:
            date_span_years = max(
                (timing["date"].max() - timing["date"].min()).days / 365.25,
                1.0,
            )

        def study_value(event_type: str, horizon: int, column: str) -> float:
            row = study.loc[
                (study["event_type"] == event_type)
                & (study["horizon_days"] == horizon),
                column,
            ]
            return float(row.iloc[0]) if not row.empty else np.nan

        phase_events = int((timing["phase_event"] != "").sum())
        records.append(
            {
                "variant": variant_name,
                "agreement_tolerance": config.agreement_tolerance,
                "stability_min_hits": config.stability_min_hits,
                "harmonic_min_r2": config.harmonic_min_r2,
                "bottom_arm_level": config.bottom_arm_level,
                "bottom_trigger_level": config.bottom_trigger_level,
                "top_arm_level": config.top_arm_level,
                "top_trigger_level": config.top_trigger_level,
                "cycle_ready_days": int(timing["cycle_ready"].sum()),
                "phase_events": phase_events,
                "phase_events_per_year": float(phase_events / date_span_years),
                "bottom_turns": int((timing["phase_event"] == "bottom_turn").sum()),
                "top_turns": int((timing["phase_event"] == "top_turn").sum()),
                "timing_actions": int((timing["timing_action"] != "").sum()),
                "bottom_20d_directional_edge_pct": study_value(
                    "bottom_turn", 20, "mean_directional_edge_pct"
                ),
                "top_20d_directional_edge_pct": study_value(
                    "top_turn", 20, "mean_directional_edge_pct"
                ),
                "overlay_cumulative_net_return_pct": 100.0
                * performance.get("cumulative_net_return", np.nan),
                "overlay_annualized_sharpe": performance.get(
                    "annualized_sharpe", np.nan
                ),
                "overlay_max_drawdown_pct": 100.0
                * performance.get("max_drawdown", np.nan),
            }
        )
    return pd.DataFrame.from_records(records)


def _plot_timing_research(
    daily: pd.DataFrame,
    timing_config: TimingConfig,
    output_path: str | Path,
) -> None:
    _configure_plot_style()
    fig, axes = plt.subplots(
        4,
        1,
        figsize=(15, 13),
        sharex=True,
        gridspec_kw={"height_ratios": [1.15, 1.0, 1.0, 0.9], "hspace": 0.10},
    )
    price_axis, period_axis, phase_axis, overlay_axis = axes
    dates = pd.to_datetime(daily["date"])
    bottom = daily["phase_event"] == "bottom_turn"
    top = daily["phase_event"] == "top_turn"

    price_axis.plot(dates, daily["close"], color="#333333", linewidth=1.0, label="收盘价")
    price_axis.plot(dates, daily["trend_ma120"], color="#d62728", linewidth=1.2, label="MA120")
    price_axis.scatter(dates[bottom], daily.loc[bottom, "close"], marker="^", s=45, color="#2ca02c", label="周期底部转向")
    price_axis.scatter(dates[top], daily.loc[top, "close"], marker="v", s=45, color="#d62728", label="周期顶部转向")
    price_axis.set_title("中证全指：三均线共识周期择时 V1", loc="left", fontsize=14)
    price_axis.set_ylabel("指数点位")
    price_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    price_axis.legend(loc="upper left", frameon=False, ncol=4, fontsize=9)

    colors = ("#9ecae1", "#3182bd", "#08519c")
    for window, color in zip(timing_config.ma_windows, colors):
        period_axis.plot(
            dates,
            daily[f"ma{window}_tracked_period_days"],
            color=color,
            linewidth=0.65,
            alpha=0.48,
            label=f"MA{window}",
        )
    period_axis.plot(dates, daily["consensus_period_days"], color="#111111", linewidth=1.25, label="共识周期")
    ready = daily["cycle_ready"].to_numpy(dtype=bool)
    period_axis.scatter(dates[ready], daily.loc[ready, "consensus_period_days"], color="#111111", s=7, alpha=0.7, label="周期可用")
    period_axis.axhline(20.0, color="#999999", linewidth=0.7, linestyle="--")
    period_axis.axhline(60.0, color="#999999", linewidth=0.7, linestyle="--")
    period_axis.set_ylim(17.0, 63.0)
    period_axis.set_ylabel("周期（交易日）")
    period_axis.set_title("MA34/55/89 与共识周期", loc="left", fontsize=12)
    period_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    period_axis.legend(loc="upper left", frameon=False, ncol=5, fontsize=9)

    phase_axis.plot(dates, daily["cycle_score"], color="#7b2cbf", linewidth=0.95, label="周期位置")
    for level in (
        timing_config.bottom_arm_level,
        timing_config.bottom_trigger_level,
        timing_config.top_trigger_level,
        timing_config.top_arm_level,
    ):
        phase_axis.axhline(level, color="#aaaaaa", linewidth=0.6, linestyle="--")
    phase_axis.scatter(dates[bottom], daily.loc[bottom, "cycle_score"], marker="^", s=42, color="#2ca02c")
    phase_axis.scatter(dates[top], daily.loc[top, "cycle_score"], marker="v", s=42, color="#d62728")
    phase_axis.set_ylim(-1.08, 1.08)
    phase_axis.set_ylabel("周期位置 [-1, 1]")
    phase_axis.set_title("原始价格谐波相位与不定期转向事件", loc="left", fontsize=12)
    phase_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    phase_axis.legend(loc="upper left", frameon=False)

    overlay_axis.fill_between(dates, 0.0, 100.0 * daily["overlay_target"], color="#1f77b4", alpha=0.28, label="相对基础仓位调整")
    overlay_axis.axhline(0.0, color="#666666", linewidth=0.7)
    overlay_axis.set_ylabel("仓位调整（百分点）")
    overlay_axis.set_title("趋势过滤后的仓位叠加（最大 ±15%，按周期置信度缩放）", loc="left", fontsize=12)
    overlay_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    cumulative_axis = overlay_axis.twinx()
    cumulative_axis.plot(dates, 100.0 * daily["overlay_cumulative_net"], color="#ff7f0e", linewidth=1.0, label="叠加收益（含5bp成本）")
    cumulative_axis.set_ylabel("累计增量收益（%）")
    lines, labels = overlay_axis.get_legend_handles_labels()
    lines2, labels2 = cumulative_axis.get_legend_handles_labels()
    overlay_axis.legend(lines + lines2, labels + labels2, loc="upper left", frameon=False, ncol=2, fontsize=9)
    overlay_axis.xaxis.set_major_locator(mdates.YearLocator())
    overlay_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    overlay_axis.set_xlabel("交易日期")

    fig.subplots_adjust(left=0.075, right=0.93, top=0.96, bottom=0.06)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def run_cycle_timing_research(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    timing_config: TimingConfig | None = None,
    cycle_config: CycleConfig | None = None,
    force_rebuild_red_noise: bool = False,
    progress: ProgressCallback | None = print,
) -> dict:
    started = time.perf_counter()
    active_timing = timing_config or TimingConfig()
    active_timing.validate()
    active_cycle = cycle_config or short_ma_config()
    active_cycle.validate()
    input_file = Path(input_path).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(f"读取并校验数据：{input_file}")
    price_data = load_price_data(input_file)
    cache_path = destination / "timing_red_noise_thresholds.npz"
    references = build_or_load_red_noise_references(
        active_cycle,
        cache_path,
        force_rebuild=force_rebuild_red_noise,
        progress=progress,
    )
    indicators, ma_results = build_timing_indicators(
        price_data,
        active_cycle,
        references[active_cycle.bands[0].name],
        active_timing,
        progress=progress,
    )
    daily = apply_timing_state_machine(indicators, active_timing)
    events, event_study = build_event_study(daily, active_timing)
    action_study = build_action_study(events, active_timing)
    sensitivity = build_timing_sensitivity(
        price_data,
        ma_results,
        active_cycle,
        active_timing,
        progress=progress,
    )
    if progress:
        progress("计算 MA34、MA55、MA89 的独立周期相位。")
    daily = add_individual_ma_phase_scores(daily, price_data, active_timing)

    daily_path = destination / "cycle_timing_daily.csv"
    events_path = destination / "cycle_timing_events.csv"
    event_study_path = destination / "cycle_timing_event_study.csv"
    action_study_path = destination / "cycle_timing_action_study.csv"
    sensitivity_path = destination / "cycle_timing_sensitivity.csv"
    figure_path = destination / "cycle_timing_overview.png"
    summary_path = destination / "cycle_timing_summary.json"
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    events.to_csv(events_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    event_study.to_csv(event_study_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    action_study.to_csv(action_study_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    sensitivity.to_csv(sensitivity_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    for window, frame in ma_results.items():
        frame.to_csv(
            destination / f"ma{window}_short_cycle_daily.csv",
            index=False,
            encoding="utf-8-sig",
            float_format="%.10g",
        )
    _plot_timing_research(daily, active_timing, figure_path)

    latest = daily.iloc[-1]
    phase_event_count = int((daily["phase_event"] != "").sum())
    action_count = int((daily["timing_action"] != "").sum())
    years = max((daily["date"].max() - daily["date"].min()).days / 365.25, 1.0)
    summary = {
        "input": {
            "path": str(input_file),
            "sha256": _file_sha256(input_file),
            "rows": int(len(price_data)),
            "date_min": price_data["date"].min().strftime("%Y-%m-%d"),
            "date_max": price_data["date"].max().strftime("%Y-%m-%d"),
            "code": str(price_data["code"].iloc[0]),
            "index_name": str(price_data["index_name"].iloc[0]),
        },
        "timing_config": asdict(active_timing),
        "cycle_config": {
            "band": asdict(active_cycle.bands[0]),
            "zero_pad_factor": active_cycle.zero_pad_factor,
            "red_noise_surrogates": active_cycle.red_noise_surrogates,
            "random_seed": active_cycle.random_seed,
        },
        "result": {
            "rows": int(len(daily)),
            "cycle_ready_days": int(daily["cycle_ready"].sum()),
            "cycle_ready_rate": float(daily["cycle_ready"].mean()),
            "phase_events": phase_event_count,
            "phase_events_per_year": float(phase_event_count / years),
            "timing_actions": action_count,
            "timing_actions_per_year": float(action_count / years),
            "bottom_turns": int((daily["phase_event"] == "bottom_turn").sum()),
            "top_turns": int((daily["phase_event"] == "top_turn").sum()),
            "latest": {
                "date": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
                "consensus_period_days": _finite_or_none(latest["consensus_period_days"]),
                "ma_agreement_count": int(latest["ma_agreement_count"]),
                "cycle_ready": bool(latest["cycle_ready"]),
                "cycle_score": _finite_or_none(latest["cycle_score"]),
                "cycle_direction_score": _finite_or_none(latest["cycle_direction_score"]),
                "cycle_confidence": _finite_or_none(latest["cycle_confidence"]),
                "trend_state": str(latest["trend_state"]),
                "phase_event": str(latest["phase_event"]),
                "timing_action": str(latest["timing_action"]),
                "overlay_target": float(latest["overlay_target"]),
            },
        },
        "overlay_performance": _overlay_performance(daily),
        "event_study": event_study.to_dict(orient="records"),
        "action_study": action_study.to_dict(orient="records"),
        "sensitivity": sensitivity.to_dict(orient="records"),
        "outputs": {
            "daily": str(daily_path),
            "events": str(events_path),
            "event_study": str(event_study_path),
            "action_study": str(action_study_path),
            "sensitivity": str(sensitivity_path),
            "overview": str(figure_path),
            "summary": str(summary_path),
            "red_noise_cache": str(cache_path),
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "runtime_seconds": time.perf_counter() - started,
        "research_boundary": (
            "信号参数在本轮真实数据运行前固定；事件研究中的未来收益只用于评价，"
            "不进入当日指标或信号。仓位结果是相对基础仓位的研究性叠加，不是完整投资组合。"
        ),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if progress:
        progress(f"已生成周期择时 V1：{destination}")
    return {
        "daily": daily,
        "events": events,
        "event_study": event_study,
        "action_study": action_study,
        "sensitivity": sensitivity,
        "summary": summary,
        "ma_results": ma_results,
    }
