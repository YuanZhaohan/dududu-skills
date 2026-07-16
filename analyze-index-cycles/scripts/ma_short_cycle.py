from __future__ import annotations

import hashlib
import json
import platform
import time
from dataclasses import asdict, replace
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
        BandConfig,
        CycleConfig,
        RedNoiseReference,
        _configure_plot_style,
        analyze_band,
        build_or_load_red_noise_references,
        load_price_data,
    )
except ImportError:
    from cycle_model import (
        BandConfig,
        CycleConfig,
        RedNoiseReference,
        _configure_plot_style,
        analyze_band,
        build_or_load_red_noise_references,
        load_price_data,
    )


ProgressCallback = Callable[[str], None]


def short_ma_config(
    *,
    red_noise_surrogates: int = 1000,
    random_seed: int = 20260715,
) -> CycleConfig:
    short_band = BandConfig(
        "short",
        "短周期 20–60日｜MA去趋势",
        20.0,
        60.0,
        252,
        "#1f77b4",
    )
    return replace(
        CycleConfig(),
        bands=(short_band,),
        red_noise_surrogates=red_noise_surrogates,
        random_seed=random_seed,
    )


def causal_sma_cycle(
    close: np.ndarray,
    ma_window: int = 55,
) -> tuple[np.ndarray, np.ndarray]:
    """Return arithmetic-price SMA and log(close / SMA), using no future data."""
    values = np.asarray(close, dtype=float)
    if values.ndim != 1:
        raise ValueError("收盘价必须是一维数组。")
    if ma_window < 2:
        raise ValueError("均线窗口至少为 2 个交易日。")
    if values.size < ma_window:
        raise ValueError(f"样本不足以计算 MA{ma_window}。")
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("收盘价必须全部为有限正数。")

    moving_average = (
        pd.Series(values, copy=False)
        .rolling(window=ma_window, min_periods=ma_window)
        .mean()
        .to_numpy(dtype=float)
    )
    cycle = np.full(values.size, np.nan, dtype=float)
    valid = np.isfinite(moving_average)
    cycle[valid] = np.log(values[valid] / moving_average[valid])
    return moving_average, cycle


def analyze_ma_short_cycle(
    price_data: pd.DataFrame,
    config: CycleConfig,
    red_noise_reference: RedNoiseReference,
    *,
    ma_window: int = 55,
    boundary_tolerance: float = 0.02,
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, np.ndarray]:
    if len(config.bands) != 1:
        raise ValueError("MA 短周期实验必须且只能配置一个频段。")
    if not 0.0 <= boundary_tolerance < 0.25:
        raise ValueError("边界容差必须位于 [0, 0.25)。")

    band = config.bands[0]
    close = price_data["close"].to_numpy(dtype=float)
    moving_average, full_cycle = causal_sma_cycle(close, ma_window)
    valid_start = ma_window - 1
    trimmed_price = price_data.iloc[valid_start:].reset_index(drop=True)
    trimmed_log_price = np.log(trimmed_price["close"].to_numpy(dtype=float))
    trimmed_trend = np.log(moving_average[valid_start:])
    trimmed_cycle = full_cycle[valid_start:]

    result = analyze_band(
        trimmed_price,
        trimmed_log_price,
        trimmed_trend,
        trimmed_cycle,
        float(ma_window),
        band,
        config,
        red_noise_reference,
        estimate_amplitude=True,
        progress=progress,
    )
    if result.empty:
        return result, moving_average

    result = result.rename(
        columns={
            "hp_cutoff_days": "ma_window_days",
            "causal_hp_trend": "causal_ma_trend",
            "hp_cycle_pct": "ma_cycle_pct",
        }
    )
    min_boundary = band.min_period_days * (1.0 + boundary_tolerance)
    max_boundary = band.max_period_days * (1.0 - boundary_tolerance)
    raw_period = result["raw_period_days"].to_numpy(dtype=float)
    tracked_period = result["tracked_period_days"].to_numpy(dtype=float)
    result["raw_boundary_peak"] = (raw_period <= min_boundary) | (
        raw_period >= max_boundary
    )
    result["boundary_peak"] = (tracked_period <= min_boundary) | (
        tracked_period >= max_boundary
    )
    result["robust_valid"] = result["stable_valid"] & ~result["boundary_peak"]

    amplitude_log = result["amplitude_log"].to_numpy(dtype=float)
    component_pct = result["cycle_component_pct"].to_numpy(dtype=float)
    component_log = np.log1p(component_pct / 100.0)
    cycle_score = np.divide(
        component_log,
        amplitude_log,
        out=np.full_like(component_log, np.nan),
        where=amplitude_log > np.finfo(float).tiny,
    )
    result["short_cycle_score"] = np.clip(cycle_score, -1.0, 1.0)
    result["valid_cycle_score"] = result["short_cycle_score"].where(
        result["robust_valid"]
    )
    result["spectral_strength_ratio"] = np.divide(
        result["peak_power"].to_numpy(dtype=float),
        result["red_noise_threshold_95"].to_numpy(dtype=float),
        out=np.full(len(result), np.nan, dtype=float),
        where=result["red_noise_threshold_95"].to_numpy(dtype=float) > 0.0,
    )
    result.insert(4, "detrend_method", f"causal_sma_{ma_window}")
    return result, moving_average


def build_hp_comparison(
    ma_results: pd.DataFrame,
    hp_daily_path: str | Path,
    *,
    agreement_tolerance: float = 0.15,
) -> pd.DataFrame:
    hp_path = Path(hp_daily_path)
    if not hp_path.exists():
        raise FileNotFoundError(f"找不到 HP 日频结果：{hp_path}")
    hp = pd.read_csv(hp_path, encoding="utf-8-sig", parse_dates=["date"])
    hp = hp.loc[hp["band"] == "short"].copy()
    if hp.empty:
        raise ValueError("HP 日频结果中没有 short 频段。")

    ma_columns = [
        "date",
        "close",
        "tracked_period_days",
        "peak_to_trough_pct",
        "cycle_component_pct",
        "short_cycle_score",
        "red_noise_pvalue",
        "significant_95",
        "stable_valid",
        "boundary_peak",
        "robust_valid",
        "harmonic_r2",
    ]
    hp_columns = [
        "date",
        "tracked_period_days",
        "peak_to_trough_pct",
        "cycle_component_pct",
        "red_noise_pvalue",
        "significant_95",
        "stable_valid",
        "harmonic_r2",
    ]
    ma = ma_results[ma_columns].rename(
        columns={column: f"ma_{column}" for column in ma_columns if column not in {"date", "close"}}
    )
    hp = hp[hp_columns].rename(
        columns={column: f"hp_{column}" for column in hp_columns if column != "date"}
    )
    comparison = ma.merge(hp, on="date", how="inner", validate="one_to_one")
    comparison["period_abs_diff_pct"] = 100.0 * np.abs(
        comparison["ma_tracked_period_days"]
        / comparison["hp_tracked_period_days"]
        - 1.0
    )
    comparison["period_agree_15pct"] = (
        comparison["period_abs_diff_pct"] <= 100.0 * agreement_tolerance
    )
    comparison["both_significant"] = (
        comparison["ma_significant_95"] & comparison["hp_significant_95"]
    )
    comparison["both_stable"] = (
        comparison["ma_robust_valid"] & comparison["hp_stable_valid"]
    )
    return comparison.sort_values("date").reset_index(drop=True)


def _plot_ma_short_cycle(
    price_data: pd.DataFrame,
    moving_average: np.ndarray,
    ma_results: pd.DataFrame,
    comparison: pd.DataFrame | None,
    output_path: str | Path,
    ma_window: int,
) -> None:
    _configure_plot_style()
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(15, 11),
        sharex=True,
        gridspec_kw={"height_ratios": [1.2, 1.0, 1.0], "hspace": 0.10},
    )
    price_axis, period_axis, component_axis = axes
    dates = pd.to_datetime(price_data["date"])
    price_axis.plot(dates, price_data["close"], color="#333333", linewidth=1.0, label="收盘价")
    price_axis.plot(dates, moving_average, color="#d62728", linewidth=1.3, label=f"MA{ma_window}")
    price_axis.set_title(f"中证全指：MA{ma_window} 去趋势的短周期对照实验", loc="left", fontsize=14)
    price_axis.set_ylabel("指数点位")
    price_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    price_axis.legend(loc="upper left", frameon=False)

    ma_dates = pd.to_datetime(ma_results["date"])
    ma_period = ma_results["tracked_period_days"].to_numpy(dtype=float)
    ma_valid = ma_results["robust_valid"].to_numpy(dtype=bool)
    period_axis.plot(ma_dates, ma_period, color="#1f77b4", linewidth=1.0, alpha=0.75, label=f"MA{ma_window} 周期")
    period_axis.scatter(ma_dates[ma_valid], ma_period[ma_valid], color="#1f77b4", s=9, label="MA 稳定且非边界")
    if comparison is not None and not comparison.empty:
        period_axis.plot(
            pd.to_datetime(comparison["date"]),
            comparison["hp_tracked_period_days"],
            color="#ff7f0e",
            linewidth=0.9,
            alpha=0.65,
            label="HP252 周期",
        )
    period_axis.axhline(20.0, color="#888888", linewidth=0.7, linestyle="--")
    period_axis.axhline(60.0, color="#888888", linewidth=0.7, linestyle="--")
    period_axis.set_ylim(17.0, 63.0)
    period_axis.set_ylabel("周期（交易日）")
    period_axis.set_title("20–60 日滚动主周期（圆点为 MA 稳健有效）", loc="left", fontsize=12)
    period_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    period_axis.legend(loc="upper left", frameon=False, ncol=3, fontsize=9)

    ma_component = ma_results["cycle_component_pct"].to_numpy(dtype=float)
    component_axis.plot(ma_dates, ma_component, color="#1f77b4", linewidth=0.9, alpha=0.72, label=f"MA{ma_window} 识别周期")
    component_axis.scatter(ma_dates[ma_valid], ma_component[ma_valid], color="#1f77b4", s=8)
    if comparison is not None and not comparison.empty:
        component_axis.plot(
            pd.to_datetime(comparison["date"]),
            comparison["hp_cycle_component_pct"],
            color="#ff7f0e",
            linewidth=0.8,
            alpha=0.58,
            label="HP252 识别周期",
        )
    component_axis.axhline(0.0, color="#666666", linewidth=0.8)
    component_axis.set_ylabel("相对局部趋势偏离（%）")
    component_axis.set_title("原始价格上的实时端点谐波重构", loc="left", fontsize=12)
    component_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    component_axis.legend(loc="upper left", frameon=False, ncol=2, fontsize=9)
    component_axis.xaxis.set_major_locator(mdates.YearLocator())
    component_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    component_axis.set_xlabel("交易日期")

    fig.subplots_adjust(left=0.075, right=0.985, top=0.955, bottom=0.07)
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


def _latest_summary(results: pd.DataFrame) -> dict:
    latest = results.iloc[-1]
    return {
        "date": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
        "period_days": float(latest["tracked_period_days"]),
        "peak_to_trough_pct": float(latest["peak_to_trough_pct"]),
        "cycle_score": float(latest["short_cycle_score"]),
        "pvalue": float(latest["red_noise_pvalue"]),
        "significant_95": bool(latest["significant_95"]),
        "boundary_peak": bool(latest["boundary_peak"]),
        "stable_valid": bool(latest["stable_valid"]),
        "robust_valid": bool(latest["robust_valid"]),
        "harmonic_r2": float(latest["harmonic_r2"]),
    }


def run_ma_short_cycle_research(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    ma_window: int = 55,
    hp_daily_path: str | Path | None = None,
    config: CycleConfig | None = None,
    boundary_tolerance: float = 0.02,
    force_rebuild_red_noise: bool = False,
    progress: ProgressCallback | None = print,
) -> dict:
    started = time.perf_counter()
    active_config = config or short_ma_config()
    active_config.validate()
    input_file = Path(input_path).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(f"读取并校验数据：{input_file}")
    price_data = load_price_data(input_file)
    cache_path = destination / "ma_short_red_noise_thresholds.npz"
    references = build_or_load_red_noise_references(
        active_config,
        cache_path,
        force_rebuild=force_rebuild_red_noise,
        progress=progress,
    )
    if progress:
        progress(f"计算严格单边 MA{ma_window} 残差与短周期滚动频谱。")
    daily, moving_average = analyze_ma_short_cycle(
        price_data,
        active_config,
        references[active_config.bands[0].name],
        ma_window=ma_window,
        boundary_tolerance=boundary_tolerance,
        progress=progress,
    )
    if daily.empty:
        raise ValueError("样本不足以生成 MA 短周期结果。")

    daily_path = destination / f"ma{ma_window}_short_cycle_daily.csv"
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig", float_format="%.10g")

    comparison = None
    comparison_path = None
    if hp_daily_path is not None:
        comparison = build_hp_comparison(daily, hp_daily_path)
        comparison_path = destination / f"ma{ma_window}_vs_hp252_short_cycle.csv"
        comparison.to_csv(
            comparison_path,
            index=False,
            encoding="utf-8-sig",
            float_format="%.10g",
        )

    figure_path = destination / f"ma{ma_window}_short_cycle_overview.png"
    _plot_ma_short_cycle(
        price_data,
        moving_average,
        daily,
        comparison,
        figure_path,
        ma_window,
    )

    comparison_summary = None
    if comparison is not None and not comparison.empty:
        latest_comparison = comparison.iloc[-1]
        comparison_summary = {
            "rows": int(len(comparison)),
            "median_abs_period_diff_pct": float(comparison["period_abs_diff_pct"].median()),
            "period_agree_15pct_days": int(comparison["period_agree_15pct"].sum()),
            "period_agree_15pct_rate": float(comparison["period_agree_15pct"].mean()),
            "both_significant_days": int(comparison["both_significant"].sum()),
            "both_stable_days": int(comparison["both_stable"].sum()),
            "latest_hp_period_days": float(latest_comparison["hp_tracked_period_days"]),
            "latest_period_abs_diff_pct": float(latest_comparison["period_abs_diff_pct"]),
        }

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
        "method": {
            "detrend": f"arithmetic close SMA{ma_window}",
            "cycle": f"log(close / SMA{ma_window})",
            "strictly_causal": True,
            "ma_window_days": ma_window,
            "boundary_tolerance": boundary_tolerance,
            "band": asdict(active_config.bands[0]),
            "zero_pad_factor": active_config.zero_pad_factor,
            "red_noise_surrogates": active_config.red_noise_surrogates,
            "random_seed": active_config.random_seed,
        },
        "result": {
            "rows": int(len(daily)),
            "significant_days": int(daily["significant_95"].sum()),
            "stable_days": int(daily["stable_valid"].sum()),
            "boundary_peak_days": int(daily["boundary_peak"].sum()),
            "robust_valid_days": int(daily["robust_valid"].sum()),
            "median_period_days": float(daily["tracked_period_days"].median()),
            "median_robust_period_days": (
                float(daily.loc[daily["robust_valid"], "tracked_period_days"].median())
                if daily["robust_valid"].any()
                else None
            ),
            "latest": _latest_summary(daily),
        },
        "hp252_comparison": comparison_summary,
        "outputs": {
            "daily": str(daily_path),
            "comparison": str(comparison_path) if comparison_path is not None else None,
            "overview": str(figure_path),
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
    }
    summary_path = destination / f"ma{ma_window}_short_cycle_summary.json"
    summary["outputs"]["summary"] = str(summary_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if progress:
        progress(f"已生成 MA{ma_window} 短周期实验：{destination}")
    return {
        "daily": daily,
        "comparison": comparison,
        "summary": summary,
        "moving_average": moving_average,
    }

