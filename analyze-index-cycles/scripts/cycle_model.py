from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from matplotlib.ticker import FuncFormatter
from scipy.signal import find_peaks


ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class BandConfig:
    name: str
    label: str
    min_period_days: float
    max_period_days: float
    window_days: int
    color: str


def _default_bands() -> tuple[BandConfig, ...]:
    return (
        BandConfig("short", "短周期 20–60日", 20.0, 60.0, 252, "#1f77b4"),
        BandConfig("medium", "中周期 60–120日", 60.0, 120.0, 504, "#ff7f0e"),
        BandConfig("long", "长周期 120–252日", 120.0, 252.0, 756, "#7b2cbf"),
    )


@dataclass(frozen=True)
class CycleConfig:
    bands: tuple[BandConfig, ...] = field(default_factory=_default_bands)
    hp_cutoff_days: float = 252.0
    sensitivity_cutoffs: tuple[float, ...] = (180.0, 252.0, 360.0)
    hp_target_cycle_gain: float = 0.5
    zero_pad_factor: int = 8
    top_peaks: int = 3
    track_tolerance: float = 0.15
    stability_window: int = 15
    stability_min_hits: int = 12
    amplitude_cycles: float = 2.5
    red_noise_confidence: float = 0.95
    red_noise_surrogates: int = 1000
    red_noise_phi_min: float = -0.20
    red_noise_phi_max: float = 0.98
    red_noise_phi_step: float = 0.02
    random_seed: int = 20260715
    fft_batch_size: int = 256
    simulation_batch_size: int = 250

    def validate(self) -> None:
        if self.zero_pad_factor < 1:
            raise ValueError("zero_pad_factor 必须至少为 1。")
        if not 0 < self.track_tolerance < 1:
            raise ValueError("track_tolerance 必须位于 (0, 1)。")
        if not 0 < self.stability_min_hits <= self.stability_window:
            raise ValueError("稳定命中数必须位于 1 与稳定窗口之间。")
        if self.red_noise_surrogates < 100:
            raise ValueError("红噪声模拟次数至少为 100。")
        for band in self.bands:
            if band.min_period_days <= 0 or band.max_period_days <= band.min_period_days:
                raise ValueError(f"频段 {band.name} 的周期边界无效。")
            if band.window_days < math.ceil(self.amplitude_cycles * band.max_period_days):
                raise ValueError(
                    f"频段 {band.name} 的窗口不足以覆盖 {self.amplitude_cycles} 个最长周期。"
                )


@dataclass(frozen=True)
class RedNoiseReference:
    band_name: str
    phi_grid: np.ndarray
    sorted_max_power: np.ndarray
    confidence: float

    def _interpolated_distribution(self, phi: float) -> np.ndarray:
        clipped = float(np.clip(phi, self.phi_grid[0], self.phi_grid[-1]))
        right = int(np.searchsorted(self.phi_grid, clipped, side="right"))
        if right <= 0:
            return self.sorted_max_power[0]
        if right >= len(self.phi_grid):
            return self.sorted_max_power[-1]
        left = right - 1
        span = self.phi_grid[right] - self.phi_grid[left]
        weight = 0.0 if span == 0 else (clipped - self.phi_grid[left]) / span
        return (1.0 - weight) * self.sorted_max_power[left] + weight * self.sorted_max_power[right]

    def threshold(self, phi: float) -> float:
        dist = self._interpolated_distribution(phi)
        return float(np.quantile(dist, self.confidence))

    def pvalue(self, phi: float, observed_power: float) -> float:
        dist = self._interpolated_distribution(phi)
        exceed = int(dist.size - np.searchsorted(dist, observed_power, side="left"))
        return float((exceed + 1) / (dist.size + 1))


def load_price_data(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"找不到输入文件：{input_path}")

    data = pd.read_csv(input_path, encoding="utf-8-sig")
    required = ["日期", "代码", "指数名称", "收盘价"]
    missing_columns = [column for column in required if column not in data.columns]
    if missing_columns:
        raise ValueError(f"输入缺少必要列：{missing_columns}")

    result = data[required].copy()
    result.columns = ["date", "code", "index_name", "close"]
    parsed_dates = pd.to_datetime(result["date"], errors="coerce")
    if parsed_dates.isna().any():
        bad_rows = result.index[parsed_dates.isna()].tolist()[:10]
        raise ValueError(f"日期无法解析，示例行号：{bad_rows}")
    result["date"] = parsed_dates

    if result["date"].duplicated().any():
        duplicated = result.loc[result["date"].duplicated(keep=False), "date"]
        examples = duplicated.dt.strftime("%Y-%m-%d").unique().tolist()[:10]
        raise ValueError(f"日期存在重复：{examples}")

    numeric_close = pd.to_numeric(result["close"], errors="coerce")
    if numeric_close.isna().any():
        bad_dates = result.loc[numeric_close.isna(), "date"].dt.strftime("%Y-%m-%d").tolist()[:10]
        raise ValueError(f"收盘价存在缺失或非数值，示例日期：{bad_dates}")
    if (numeric_close <= 0).any():
        bad_dates = result.loc[numeric_close <= 0, "date"].dt.strftime("%Y-%m-%d").tolist()[:10]
        raise ValueError(f"收盘价必须为正数，示例日期：{bad_dates}")
    result["close"] = numeric_close.astype(float)

    if result["code"].isna().any() or result["index_name"].isna().any():
        raise ValueError("代码或指数名称存在缺失。")
    unique_codes = result["code"].astype(str).unique()
    unique_names = result["index_name"].astype(str).unique()
    if len(unique_codes) != 1 or len(unique_names) != 1:
        raise ValueError(
            "第一版只支持单一指数；"
            f"检测到代码 {unique_codes.tolist()}、名称 {unique_names.tolist()}。"
        )

    result["code"] = result["code"].astype(str)
    result["index_name"] = result["index_name"].astype(str)
    return result.sort_values("date").reset_index(drop=True)


def causal_hp_filter(log_price: np.ndarray, lamb: float) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(log_price, dtype=float)
    if values.ndim != 1 or values.size < 3:
        raise ValueError("单边 HP 至少需要 3 个观测值。")
    if not np.isfinite(values).all():
        raise ValueError("单边 HP 输入包含非有限值。")
    if lamb <= 0 or not np.isfinite(lamb):
        raise ValueError("HP lambda 必须为有限正数。")

    transition = np.array([[1.0, 1.0], [0.0, 1.0]], dtype=float)
    observation = np.array([1.0, 0.0], dtype=float)
    process_cov = np.array([[0.0, 0.0], [0.0, 1.0 / lamb]], dtype=float)
    observation_var = 1.0
    identity = np.eye(2)

    state = np.array([values[0], 0.0], dtype=float)
    covariance = np.eye(2) * 1.0e6
    trend = np.empty_like(values)

    for index, observed in enumerate(values):
        if index > 0:
            state = transition @ state
            covariance = transition @ covariance @ transition.T + process_cov

        innovation = observed - float(observation @ state)
        innovation_var = float(observation @ covariance @ observation + observation_var)
        gain = (covariance @ observation) / innovation_var
        state = state + gain * innovation

        update = identity - np.outer(gain, observation)
        covariance = update @ covariance @ update.T + np.outer(gain, gain) * observation_var
        covariance = 0.5 * (covariance + covariance.T)
        trend[index] = state[0]

    return trend, values - trend


def _cycle_gain(lamb: float, period_days: float) -> float:
    sample_size = max(2048, int(math.ceil(period_days * 20.0)))
    time_index = np.arange(sample_size, dtype=float)
    signal = np.sin(2.0 * np.pi * time_index / period_days)
    trend, cycle = causal_hp_filter(signal, lamb)
    del trend
    burn_in = min(sample_size // 2, int(math.ceil(period_days * 6.0)))
    tail_time = time_index[burn_in:]
    tail_cycle = cycle[burn_in:]
    design = np.column_stack(
        [
            np.sin(2.0 * np.pi * tail_time / period_days),
            np.cos(2.0 * np.pi * tail_time / period_days),
        ]
    )
    coefficients, *_ = np.linalg.lstsq(design, tail_cycle, rcond=None)
    return float(np.hypot(coefficients[0], coefficients[1]))


@lru_cache(maxsize=32)
def calibrate_hp_lambda(period_days: float, target_cycle_gain: float = 0.5) -> float:
    if period_days <= 2:
        raise ValueError("HP 截止周期必须大于 2 个交易日。")
    if not 0 < target_cycle_gain < 1:
        raise ValueError("目标周期增益必须位于 (0, 1)。")

    scale_anchor = period_days**4 / 4500.0
    low_log10 = math.log10(scale_anchor) - 2.0
    high_log10 = math.log10(scale_anchor) + 2.0
    low_gain = _cycle_gain(10.0**low_log10, period_days)
    high_gain = _cycle_gain(10.0**high_log10, period_days)
    if not low_gain < target_cycle_gain < high_gain:
        raise RuntimeError(
            "无法在预设范围内校准 HP lambda："
            f"low_gain={low_gain:.6f}, high_gain={high_gain:.6f}。"
        )

    for _ in range(36):
        middle = 0.5 * (low_log10 + high_log10)
        gain = _cycle_gain(10.0**middle, period_days)
        if gain < target_cycle_gain:
            low_log10 = middle
        else:
            high_log10 = middle
    return float(10.0 ** (0.5 * (low_log10 + high_log10)))


def _next_power_of_two(value: int) -> int:
    return 1 << max(0, int(math.ceil(math.log2(value))))


def _nfft(window_days: int, zero_pad_factor: int) -> int:
    return _next_power_of_two(window_days) * zero_pad_factor


def _normalized_periodogram_batch(
    windows: np.ndarray,
    hann: np.ndarray,
    nfft: int,
) -> np.ndarray:
    centered = windows - windows.mean(axis=1, keepdims=True)
    variance = np.mean(centered * centered, axis=1)
    variance = np.maximum(variance, np.finfo(float).tiny)
    transformed = np.fft.rfft(centered * hann[None, :], n=nfft, axis=1)
    denominator = variance[:, None] * float(np.sum(hann * hann))
    return (np.abs(transformed) ** 2) / denominator


def _estimate_ar1_batch(windows: np.ndarray) -> np.ndarray:
    centered = windows - windows.mean(axis=1, keepdims=True)
    numerator = np.sum(centered[:, :-1] * centered[:, 1:], axis=1)
    denominator = np.sum(centered[:, :-1] ** 2, axis=1)
    phi = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > np.finfo(float).tiny,
    )
    return np.clip(phi, -0.999, 0.999)


def _band_mask(frequencies: np.ndarray, band: BandConfig) -> np.ndarray:
    return (frequencies >= 1.0 / band.max_period_days) & (
        frequencies <= 1.0 / band.min_period_days
    )


def _max_band_power_batch(
    windows: np.ndarray,
    hann: np.ndarray,
    nfft: int,
    mask: np.ndarray,
) -> np.ndarray:
    power = _normalized_periodogram_batch(windows, hann, nfft)
    return np.max(power[:, mask], axis=1)


def _simulate_ar1(
    phi: float,
    sample_size: int,
    n_simulations: int,
    rng: np.random.Generator,
) -> np.ndarray:
    simulations = np.empty((n_simulations, sample_size), dtype=float)
    simulations[:, 0] = rng.standard_normal(n_simulations)
    innovation_scale = math.sqrt(max(1.0 - phi * phi, 1.0e-12))
    for column in range(1, sample_size):
        simulations[:, column] = (
            phi * simulations[:, column - 1]
            + innovation_scale * rng.standard_normal(n_simulations)
        )
    return simulations


def build_red_noise_reference(
    band: BandConfig,
    config: CycleConfig,
    progress: ProgressCallback | None = None,
) -> RedNoiseReference:
    phi_grid = np.arange(
        config.red_noise_phi_min,
        config.red_noise_phi_max + config.red_noise_phi_step * 0.5,
        config.red_noise_phi_step,
    )
    hann = np.hanning(band.window_days)
    nfft = _nfft(band.window_days, config.zero_pad_factor)
    frequencies = np.fft.rfftfreq(nfft, d=1.0)
    mask = _band_mask(frequencies, band)
    if not mask.any():
        raise RuntimeError(f"频段 {band.name} 在 FFT 网格中没有有效频率。")

    seed_offset = sum((index + 1) * ord(char) for index, char in enumerate(band.name))
    rng = np.random.default_rng(config.random_seed + seed_offset)
    distributions = np.empty((phi_grid.size, config.red_noise_surrogates), dtype=np.float32)

    for grid_index, phi in enumerate(phi_grid):
        cursor = 0
        while cursor < config.red_noise_surrogates:
            batch_size = min(
                config.simulation_batch_size,
                config.red_noise_surrogates - cursor,
            )
            simulated = _simulate_ar1(phi, band.window_days, batch_size, rng)
            distributions[grid_index, cursor : cursor + batch_size] = _max_band_power_batch(
                simulated,
                hann,
                nfft,
                mask,
            ).astype(np.float32)
            cursor += batch_size
        distributions[grid_index].sort()
        if progress and (grid_index == 0 or (grid_index + 1) % 15 == 0):
            progress(
                f"红噪声阈值 {band.label}: {grid_index + 1}/{len(phi_grid)} 个 AR(1) 网格"
            )

    return RedNoiseReference(
        band_name=band.name,
        phi_grid=phi_grid.astype(float),
        sorted_max_power=distributions.astype(float),
        confidence=config.red_noise_confidence,
    )


def _red_noise_cache_metadata(config: CycleConfig) -> dict:
    return {
        "bands": [asdict(band) for band in config.bands],
        "zero_pad_factor": config.zero_pad_factor,
        "surrogates": config.red_noise_surrogates,
        "phi_min": config.red_noise_phi_min,
        "phi_max": config.red_noise_phi_max,
        "phi_step": config.red_noise_phi_step,
        "confidence": config.red_noise_confidence,
        "seed": config.random_seed,
    }


def build_or_load_red_noise_references(
    config: CycleConfig,
    cache_path: str | Path,
    force_rebuild: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, RedNoiseReference]:
    cache = Path(cache_path)
    expected_metadata = _red_noise_cache_metadata(config)
    if cache.exists() and not force_rebuild:
        try:
            with np.load(cache, allow_pickle=False) as stored:
                metadata = json.loads(str(stored["metadata"].item()))
                if metadata == expected_metadata:
                    references = {}
                    phi_grid = stored["phi_grid"].astype(float)
                    for band in config.bands:
                        references[band.name] = RedNoiseReference(
                            band.name,
                            phi_grid,
                            stored[f"distribution_{band.name}"].astype(float),
                            config.red_noise_confidence,
                        )
                    if progress:
                        progress(f"复用红噪声阈值缓存：{cache}")
                    return references
        except (KeyError, ValueError, json.JSONDecodeError):
            if progress:
                progress("红噪声缓存不兼容，重新生成。")

    references = {
        band.name: build_red_noise_reference(band, config, progress=progress)
        for band in config.bands
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, np.ndarray] = {
        "metadata": np.array(json.dumps(expected_metadata, ensure_ascii=False, sort_keys=True)),
        "phi_grid": next(iter(references.values())).phi_grid,
    }
    for band in config.bands:
        payload[f"distribution_{band.name}"] = references[band.name].sorted_max_power.astype(
            np.float32
        )
    np.savez_compressed(cache, **payload)
    if progress:
        progress(f"已保存红噪声阈值缓存：{cache}")
    return references


def _quadratic_peak_frequency(
    power: np.ndarray,
    frequencies: np.ndarray,
    index: int,
) -> float:
    if index <= 0 or index >= len(power) - 1:
        return float(frequencies[index])
    left, center, right = np.log(np.maximum(power[index - 1 : index + 2], 1.0e-30))
    denominator = left - 2.0 * center + right
    if abs(denominator) <= 1.0e-15:
        return float(frequencies[index])
    offset = float(np.clip(0.5 * (left - right) / denominator, -1.0, 1.0))
    spacing = float(frequencies[index + 1] - frequencies[index])
    return float(frequencies[index] + offset * spacing)


def _refine_frequencies_with_local_regression(
    windows: np.ndarray,
    initial_frequencies: np.ndarray,
    band: BandConfig,
    batch_size: int,
) -> np.ndarray:
    """Refine FFT peaks using a vectorized local harmonic-regression grid."""
    sample_size = windows.shape[1]
    time_index = np.arange(sample_size, dtype=float)
    centered_time = time_index - time_index.mean()
    time_norm = float(centered_time @ centered_time)
    frequency_offsets = np.linspace(-0.55, 0.55, 9) / sample_size
    step = float(frequency_offsets[1] - frequency_offsets[0])
    refined = np.asarray(initial_frequencies, dtype=float).copy()

    for batch_start in range(0, len(windows), batch_size):
        batch_end = min(len(windows), batch_start + batch_size)
        values = np.asarray(windows[batch_start:batch_end], dtype=float)
        centered = values - values.mean(axis=1, keepdims=True)
        slopes = (centered @ centered_time) / time_norm
        residual = centered - slopes[:, None] * centered_time[None, :]

        initial = initial_frequencies[batch_start:batch_end]
        grids = initial[:, None] + frequency_offsets[None, :]
        grids = np.clip(grids, 1.0 / band.max_period_days, 1.0 / band.min_period_days)
        angles = 2.0 * np.pi * grids[:, :, None] * time_index[None, None, :]
        cosine = np.cos(angles)
        sine = np.sin(angles)
        cosine_rhs = np.einsum("bgw,bw->bg", cosine, residual, optimize=True)
        sine_rhs = np.einsum("bgw,bw->bg", sine, residual, optimize=True)
        cosine_norm = np.einsum("bgw,bgw->bg", cosine, cosine, optimize=True)
        sine_norm = np.einsum("bgw,bgw->bg", sine, sine, optimize=True)
        cross = np.einsum("bgw,bgw->bg", cosine, sine, optimize=True)
        determinant = np.maximum(cosine_norm * sine_norm - cross * cross, 1.0e-18)
        explained = (
            sine_norm * cosine_rhs * cosine_rhs
            - 2.0 * cross * cosine_rhs * sine_rhs
            + cosine_norm * sine_rhs * sine_rhs
        ) / determinant

        best = np.argmax(explained, axis=1)
        rows = np.arange(batch_end - batch_start)
        selected = grids[rows, best]
        interior = (best > 0) & (best < explained.shape[1] - 1)
        if interior.any():
            interior_rows = rows[interior]
            interior_best = best[interior]
            left = explained[interior_rows, interior_best - 1]
            center = explained[interior_rows, interior_best]
            right = explained[interior_rows, interior_best + 1]
            denominator = left - 2.0 * center + right
            offset = np.divide(
                0.5 * (left - right),
                denominator,
                out=np.zeros_like(center),
                where=np.abs(denominator) > 1.0e-18,
            )
            selected[interior] += np.clip(offset, -1.0, 1.0) * step
        refined[batch_start:batch_end] = np.clip(
            selected,
            1.0 / band.max_period_days,
            1.0 / band.min_period_days,
        )
    return refined


def _period_candidates(
    values: np.ndarray,
    band_power: np.ndarray,
    band_frequencies: np.ndarray,
    band: BandConfig,
    top_peaks: int,
) -> list[dict[str, float]]:
    local_peaks, _ = find_peaks(band_power)
    strongest_index = int(np.argmax(band_power))
    candidate_indices = np.unique(np.append(local_peaks, strongest_index))
    order = candidate_indices[np.argsort(band_power[candidate_indices])[::-1]][:top_peaks]

    candidates: list[dict[str, float]] = []
    for local_index in order:
        refined_frequency = _quadratic_peak_frequency(
            band_power,
            band_frequencies,
            int(local_index),
        )
        period = 1.0 / refined_frequency
        candidates.append(
            {
                "period": float(np.clip(period, band.min_period_days, band.max_period_days)),
                "frequency": refined_frequency,
                "peak_power": float(band_power[local_index]),
            }
        )
    return candidates


def estimate_local_amplitude(
    log_price: np.ndarray,
    end_index: int,
    period_days: float,
    amplitude_cycles: float,
) -> dict[str, float]:
    if not np.isfinite(period_days) or period_days <= 2:
        return {
            "amplitude_log": np.nan,
            "amplitude_up_pct": np.nan,
            "amplitude_down_pct": np.nan,
            "peak_to_trough_pct": np.nan,
            "cycle_component_pct": np.nan,
            "harmonic_r2": np.nan,
        }
    lookback = int(math.ceil(amplitude_cycles * period_days))
    start_index = end_index - lookback + 1
    if start_index < 0:
        return {
            "amplitude_log": np.nan,
            "amplitude_up_pct": np.nan,
            "amplitude_down_pct": np.nan,
            "peak_to_trough_pct": np.nan,
            "cycle_component_pct": np.nan,
            "harmonic_r2": np.nan,
        }

    values = np.asarray(log_price[start_index : end_index + 1], dtype=float)
    time_index = np.arange(lookback, dtype=float)
    scaled_time = (time_index - time_index.mean()) / lookback
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
    harmonic_r2 = float(np.clip(harmonic_r2, 0.0, 1.0))

    cosine_coefficient = float(coefficients[2])
    sine_coefficient = float(coefficients[3])
    amplitude_log = float(np.hypot(cosine_coefficient, sine_coefficient))
    current_component = float(
        cosine_coefficient * np.cos(angle[-1]) + sine_coefficient * np.sin(angle[-1])
    )
    return {
        "amplitude_log": amplitude_log,
        "amplitude_up_pct": 100.0 * math.expm1(amplitude_log),
        "amplitude_down_pct": 100.0 * (1.0 - math.exp(-amplitude_log)),
        "peak_to_trough_pct": 100.0 * math.expm1(2.0 * amplitude_log),
        "cycle_component_pct": 100.0 * math.expm1(current_component),
        "harmonic_r2": harmonic_r2,
    }


def analyze_band(
    price_data: pd.DataFrame,
    log_price: np.ndarray,
    hp_trend: np.ndarray,
    hp_cycle: np.ndarray,
    hp_cutoff_days: float,
    band: BandConfig,
    config: CycleConfig,
    red_noise_reference: RedNoiseReference,
    estimate_amplitude: bool = True,
    progress: ProgressCallback | None = None,
) -> pd.DataFrame:
    window = band.window_days
    if len(hp_cycle) < window:
        return pd.DataFrame()

    windows = np.lib.stride_tricks.sliding_window_view(hp_cycle, window)
    ar1_phi = _estimate_ar1_batch(windows)
    nfft = _nfft(window, config.zero_pad_factor)
    frequencies = np.fft.rfftfreq(nfft, d=1.0)
    mask = _band_mask(frequencies, band)
    band_frequencies = frequencies[mask]
    hann = np.hanning(window)
    observation_count = windows.shape[0]

    raw_periods = np.full(observation_count, np.nan)
    tracked_periods = np.full(observation_count, np.nan)
    peak_powers = np.full(observation_count, np.nan)
    thresholds = np.full(observation_count, np.nan)
    pvalues = np.full(observation_count, np.nan)
    significant = np.zeros(observation_count, dtype=bool)
    track_ids = np.zeros(observation_count, dtype=int)
    track_breaks = np.zeros(observation_count, dtype=bool)
    selected_frequencies = np.full(observation_count, np.nan)

    previous_period = np.nan
    current_track_id = 0
    for batch_start in range(0, observation_count, config.fft_batch_size):
        batch_end = min(observation_count, batch_start + config.fft_batch_size)
        batch_windows = windows[batch_start:batch_end]
        batch_power = _normalized_periodogram_batch(batch_windows, hann, nfft)[:, mask]

        for offset in range(batch_end - batch_start):
            row_index = batch_start + offset
            candidates = _period_candidates(
                batch_windows[offset],
                batch_power[offset],
                band_frequencies,
                band,
                config.top_peaks,
            )
            raw = candidates[0]
            raw_periods[row_index] = raw["period"]

            selected = raw
            if np.isfinite(previous_period):
                continuous = [
                    candidate
                    for candidate in candidates
                    if abs(candidate["period"] / previous_period - 1.0)
                    <= config.track_tolerance
                ]
                if continuous:
                    selected = max(continuous, key=lambda candidate: candidate["peak_power"])
                else:
                    current_track_id += 1
                    track_breaks[row_index] = True
            previous_period = selected["period"]
            tracked_periods[row_index] = selected["period"]
            selected_frequencies[row_index] = selected["frequency"]
            peak_powers[row_index] = selected["peak_power"]
            track_ids[row_index] = current_track_id

            thresholds[row_index] = red_noise_reference.threshold(ar1_phi[row_index])
            pvalues[row_index] = red_noise_reference.pvalue(
                ar1_phi[row_index],
                peak_powers[row_index],
            )
            significant[row_index] = pvalues[row_index] <= 1.0 - config.red_noise_confidence

        if progress:
            progress(
                f"{band.label} 滚动频谱：{batch_end}/{observation_count} 个交易日"
            )

    refined_frequencies = _refine_frequencies_with_local_regression(
        windows,
        selected_frequencies,
        band,
        config.fft_batch_size,
    )
    tracked_periods = 1.0 / refined_frequencies

    stability_hits = np.zeros(observation_count, dtype=int)
    period_dispersion = np.full(observation_count, np.nan)
    stable_valid = np.zeros(observation_count, dtype=bool)
    for row_index in range(observation_count):
        start = max(0, row_index - config.stability_window + 1)
        current_track = track_ids[row_index]
        same_track = track_ids[start : row_index + 1] == current_track
        recent_periods = tracked_periods[start : row_index + 1]
        finite_same_track = same_track & np.isfinite(recent_periods)
        if not finite_same_track.any():
            continue
        median_period = float(np.median(recent_periods[finite_same_track]))
        relative_deviation = np.abs(recent_periods / median_period - 1.0)
        hits = (
            finite_same_track
            & significant[start : row_index + 1]
            & (relative_deviation <= config.track_tolerance)
        )
        stability_hits[row_index] = int(hits.sum())
        period_dispersion[row_index] = float(
            100.0 * np.median(relative_deviation[finite_same_track])
        )
        stable_valid[row_index] = bool(
            significant[row_index]
            and stability_hits[row_index] >= config.stability_min_hits
        )

    end_indices = np.arange(window - 1, len(price_data))
    amplitude_columns = {
        "amplitude_log": np.full(observation_count, np.nan),
        "amplitude_up_pct": np.full(observation_count, np.nan),
        "amplitude_down_pct": np.full(observation_count, np.nan),
        "peak_to_trough_pct": np.full(observation_count, np.nan),
        "cycle_component_pct": np.full(observation_count, np.nan),
        "harmonic_r2": np.full(observation_count, np.nan),
    }
    if estimate_amplitude:
        for row_index, end_index in enumerate(end_indices):
            amplitude = estimate_local_amplitude(
                log_price,
                int(end_index),
                tracked_periods[row_index],
                config.amplitude_cycles,
            )
            for column, value in amplitude.items():
                amplitude_columns[column][row_index] = value

    slice_data = price_data.iloc[end_indices].reset_index(drop=True)
    result = pd.DataFrame(
        {
            "date": slice_data["date"],
            "code": slice_data["code"],
            "index_name": slice_data["index_name"],
            "band": band.name,
            "band_label": band.label,
            "window_days": band.window_days,
            "min_period_days": band.min_period_days,
            "max_period_days": band.max_period_days,
            "hp_cutoff_days": hp_cutoff_days,
            "close": slice_data["close"].to_numpy(dtype=float),
            "causal_hp_trend": np.exp(hp_trend[end_indices]),
            "hp_cycle_pct": 100.0 * np.expm1(hp_cycle[end_indices]),
            "raw_period_days": raw_periods,
            "tracked_period_days": tracked_periods,
            "ar1_phi": ar1_phi,
            "peak_power": peak_powers,
            "red_noise_threshold_95": thresholds,
            "red_noise_pvalue": pvalues,
            "significant_95": significant,
            "track_id": track_ids,
            "track_break": track_breaks,
            "stability_hits_15": stability_hits,
            "period_dispersion_pct_15": period_dispersion,
            "stable_valid": stable_valid,
            **amplitude_columns,
        }
    )
    return result


def _configure_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 120,
        }
    )


def plot_cycle_overview(
    price_data: pd.DataFrame,
    hp_trend: np.ndarray,
    daily_results: pd.DataFrame,
    config: CycleConfig,
    output_path: str | Path,
) -> None:
    _configure_plot_style()
    fig, (price_axis, cycle_axis) = plt.subplots(
        2,
        1,
        figsize=(16, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [1.1, 1.0], "hspace": 0.08},
    )
    dates = pd.to_datetime(price_data["date"])
    price_axis.plot(dates, price_data["close"], color="#2f2f2f", linewidth=1.0, label="收盘价")
    price_axis.plot(dates, np.exp(hp_trend), color="#d62728", linewidth=1.4, label="单边 HP 趋势")
    price_axis.set_title("中证全指：收盘价与严格单边 HP 趋势", loc="left", fontsize=14)
    price_axis.set_ylabel("指数点位")
    price_axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    price_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    price_axis.legend(loc="upper left", frameon=False, ncol=2)

    for band in config.bands:
        subset = daily_results.loc[daily_results["band"] == band.name].sort_values("date")
        if subset.empty:
            continue
        subset_dates = pd.to_datetime(subset["date"])
        component = subset["cycle_component_pct"].to_numpy(dtype=float)
        valid_component = np.where(subset["stable_valid"].to_numpy(dtype=bool), component, np.nan)
        cycle_axis.plot(
            subset_dates,
            component,
            color=band.color,
            linewidth=0.8,
            alpha=0.22,
        )
        latest = subset.iloc[-1]
        label = (
            f"{band.label}｜最新 {latest['tracked_period_days']:.1f}日｜"
            f"峰谷 {latest['peak_to_trough_pct']:.1f}%"
        )
        cycle_axis.plot(
            subset_dates,
            valid_component,
            color=band.color,
            linewidth=1.6,
            alpha=0.95,
            label=label,
        )

    cycle_axis.axhline(0.0, color="#666666", linewidth=0.8)
    cycle_axis.set_title("三个主周期的实时端点重构（淡线为未通过稳定性检验）", loc="left", fontsize=13)
    cycle_axis.set_ylabel("相对局部趋势偏离（%）")
    cycle_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    cycle_axis.legend(loc="upper left", frameon=False, fontsize=9)
    cycle_axis.xaxis.set_major_locator(mdates.YearLocator())
    cycle_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    cycle_axis.set_xlabel("交易日期")

    fig.suptitle(
        "周期曲线均为当日可得结果；后续数据不会改写历史值",
        x=0.5,
        y=0.995,
        fontsize=10,
        color="#555555",
    )
    fig.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_cycle_diagnostics(
    daily_results: pd.DataFrame,
    config: CycleConfig,
    output_path: str | Path,
) -> None:
    _configure_plot_style()
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(16, 12),
        sharex=True,
        gridspec_kw={"hspace": 0.10},
    )
    period_axis, amplitude_axis, pvalue_axis = axes

    for band in config.bands:
        subset = daily_results.loc[daily_results["band"] == band.name].sort_values("date")
        if subset.empty:
            continue
        dates = pd.to_datetime(subset["date"])
        valid = subset["stable_valid"].to_numpy(dtype=bool)
        period = subset["tracked_period_days"].to_numpy(dtype=float)
        amplitude = subset["peak_to_trough_pct"].to_numpy(dtype=float)
        pvalue = subset["red_noise_pvalue"].to_numpy(dtype=float)

        period_axis.plot(dates, period, color=band.color, linewidth=0.9, alpha=0.65, label=band.label)
        period_axis.scatter(dates[valid], period[valid], color=band.color, s=7, alpha=0.9)
        amplitude_axis.plot(dates, amplitude, color=band.color, linewidth=0.9, alpha=0.70, label=band.label)
        pvalue_axis.plot(dates, pvalue, color=band.color, linewidth=0.8, alpha=0.65, label=band.label)
        pvalue_axis.scatter(dates[valid], pvalue[valid], color=band.color, s=7, alpha=0.9)

    period_axis.set_title("滚动主周期长度（圆点为稳定有效）", loc="left", fontsize=13)
    period_axis.set_ylabel("交易日")
    period_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    period_axis.legend(loc="upper left", frameon=False, ncol=3, fontsize=9)

    amplitude_axis.set_title("局部谐波回归估计的峰谷幅度", loc="left", fontsize=13)
    amplitude_axis.set_ylabel("峰谷幅度（%）")
    amplitude_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)

    pvalue_axis.axhline(0.05, color="#d62728", linewidth=1.0, linestyle="--", label="95% 阈值")
    pvalue_axis.set_title("AR(1) 红噪声频段最高峰经验 p 值", loc="left", fontsize=13)
    pvalue_axis.set_ylabel("p 值")
    pvalue_axis.set_ylim(-0.01, 1.01)
    pvalue_axis.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.6)
    pvalue_axis.xaxis.set_major_locator(mdates.YearLocator())
    pvalue_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    pvalue_axis.set_xlabel("交易日期")

    fig.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _band_summary(daily_results: pd.DataFrame, config: CycleConfig) -> dict:
    summary: dict[str, dict] = {}
    for band in config.bands:
        subset = daily_results.loc[daily_results["band"] == band.name].sort_values("date")
        stable = subset.loc[subset["stable_valid"]]
        latest = subset.iloc[-1] if not subset.empty else None
        summary[band.name] = {
            "label": band.label,
            "rows": int(len(subset)),
            "significant_days": int(subset["significant_95"].sum()) if not subset.empty else 0,
            "stable_days": int(subset["stable_valid"].sum()) if not subset.empty else 0,
            "stable_rate": float(subset["stable_valid"].mean()) if not subset.empty else None,
            "median_stable_period_days": (
                float(stable["tracked_period_days"].median()) if not stable.empty else None
            ),
            "latest": (
                {
                    "date": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
                    "period_days": float(latest["tracked_period_days"]),
                    "peak_to_trough_pct": float(latest["peak_to_trough_pct"]),
                    "pvalue": float(latest["red_noise_pvalue"]),
                    "stable_valid": bool(latest["stable_valid"]),
                }
                if latest is not None
                else None
            ),
        }
    return summary


def _sensitivity_summary(
    results_by_cutoff: dict[float, pd.DataFrame],
    main_cutoff: float,
    config: CycleConfig,
) -> pd.DataFrame:
    main = results_by_cutoff[main_cutoff]
    records: list[dict] = []
    for cutoff, results in results_by_cutoff.items():
        for band in config.bands:
            subset = results.loc[results["band"] == band.name].copy()
            main_subset = main.loc[main["band"] == band.name, ["date", "tracked_period_days", "stable_valid"]]
            comparison = subset[["date", "tracked_period_days", "stable_valid"]].merge(
                main_subset,
                on="date",
                suffixes=("", "_main"),
                how="inner",
            )
            both_finite = np.isfinite(comparison["tracked_period_days"]) & np.isfinite(
                comparison["tracked_period_days_main"]
            )
            period_difference = np.abs(
                comparison.loc[both_finite, "tracked_period_days"]
                / comparison.loc[both_finite, "tracked_period_days_main"]
                - 1.0
            )
            union_valid = comparison["stable_valid"] | comparison["stable_valid_main"]
            overlap = comparison["stable_valid"] & comparison["stable_valid_main"]
            records.append(
                {
                    "hp_cutoff_days": cutoff,
                    "band": band.name,
                    "band_label": band.label,
                    "rows": len(subset),
                    "significant_days": int(subset["significant_95"].sum()),
                    "stable_days": int(subset["stable_valid"].sum()),
                    "stable_rate": float(subset["stable_valid"].mean()),
                    "median_period_days": float(subset["tracked_period_days"].median()),
                    "median_abs_period_diff_vs_main_pct": (
                        float(100.0 * period_difference.median())
                        if not period_difference.empty
                        else np.nan
                    ),
                    "stable_jaccard_vs_main": (
                        float(overlap.sum() / union_valid.sum()) if union_valid.any() else np.nan
                    ),
                }
            )
    return pd.DataFrame.from_records(records)


def run_cycle_research(
    input_path: str | Path,
    output_dir: str | Path,
    config: CycleConfig | None = None,
    *,
    force_rebuild_red_noise: bool = False,
    run_sensitivity: bool = True,
    progress: ProgressCallback | None = print,
) -> dict:
    started = time.perf_counter()
    active_config = config or CycleConfig()
    active_config.validate()
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(f"读取并校验数据：{input_path}")
    price_data = load_price_data(input_path)
    log_price = np.log(price_data["close"].to_numpy(dtype=float))

    cache_path = output_dir / "red_noise_thresholds.npz"
    red_noise_references = build_or_load_red_noise_references(
        active_config,
        cache_path,
        force_rebuild=force_rebuild_red_noise,
        progress=progress,
    )

    cutoffs: Iterable[float]
    if run_sensitivity:
        cutoffs = active_config.sensitivity_cutoffs
    else:
        cutoffs = (active_config.hp_cutoff_days,)
    unique_cutoffs = tuple(dict.fromkeys(float(value) for value in cutoffs))
    if active_config.hp_cutoff_days not in unique_cutoffs:
        unique_cutoffs += (active_config.hp_cutoff_days,)

    calibrated_lambdas = {
        cutoff: calibrate_hp_lambda(cutoff, active_config.hp_target_cycle_gain)
        for cutoff in unique_cutoffs
    }
    results_by_cutoff: dict[float, pd.DataFrame] = {}
    main_trend: np.ndarray | None = None

    for cutoff in unique_cutoffs:
        if progress:
            progress(f"计算 {cutoff:.0f} 日截止周期的单边 HP 与滚动频谱。")
        trend, cycle = causal_hp_filter(log_price, calibrated_lambdas[cutoff])
        frames = []
        for band in active_config.bands:
            frames.append(
                analyze_band(
                    price_data,
                    log_price,
                    trend,
                    cycle,
                    cutoff,
                    band,
                    active_config,
                    red_noise_references[band.name],
                    estimate_amplitude=cutoff == active_config.hp_cutoff_days,
                    progress=progress,
                )
            )
        results = pd.concat(frames, ignore_index=True)
        band_order = {band.name: index for index, band in enumerate(active_config.bands)}
        results["_band_order"] = results["band"].map(band_order)
        results = results.sort_values(["date", "_band_order"]).drop(columns="_band_order")
        results_by_cutoff[cutoff] = results.reset_index(drop=True)
        if cutoff == active_config.hp_cutoff_days:
            main_trend = trend

    if main_trend is None:
        raise RuntimeError("未生成主截止周期结果。")
    main_results = results_by_cutoff[active_config.hp_cutoff_days]
    daily_path = output_dir / "all_a_cycle_daily.csv"
    main_results.to_csv(daily_path, index=False, encoding="utf-8-sig", float_format="%.10g")

    sensitivity = _sensitivity_summary(
        results_by_cutoff,
        active_config.hp_cutoff_days,
        active_config,
    )
    sensitivity_path = output_dir / "all_a_cycle_sensitivity.csv"
    sensitivity.to_csv(sensitivity_path, index=False, encoding="utf-8-sig", float_format="%.10g")

    overview_path = output_dir / "all_a_cycle_overview.png"
    diagnostics_path = output_dir / "all_a_cycle_diagnostics.png"
    plot_cycle_overview(price_data, main_trend, main_results, active_config, overview_path)
    plot_cycle_diagnostics(main_results, active_config, diagnostics_path)

    summary = {
        "input": {
            "path": str(input_path),
            "sha256": _file_sha256(input_path),
            "rows": int(len(price_data)),
            "date_min": price_data["date"].min().strftime("%Y-%m-%d"),
            "date_max": price_data["date"].max().strftime("%Y-%m-%d"),
            "code": str(price_data["code"].iloc[0]),
            "index_name": str(price_data["index_name"].iloc[0]),
        },
        "config": {
            **asdict(active_config),
            "bands": [asdict(band) for band in active_config.bands],
        },
        "calibrated_hp_lambda": {str(cutoff): value for cutoff, value in calibrated_lambdas.items()},
        "band_summary": _band_summary(main_results, active_config),
        "outputs": {
            "daily": str(daily_path),
            "sensitivity": str(sensitivity_path),
            "overview": str(overview_path),
            "diagnostics": str(diagnostics_path),
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
        "method_boundary": (
            "95% 显著性是当日、频段内最高峰的局部检验；"
            "本阶段不生成相位标签、交易信号或回测。"
        ),
    }
    summary_path = output_dir / "run_summary.json"
    summary["outputs"]["summary"] = str(summary_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(f"研究结果已写入：{output_dir}")
    return {"daily": main_results, "sensitivity": sensitivity, "summary": summary}
