"""LPPLS calibration and positive/negative bubble confidence indicators.

The implementation follows the stable Filimonov--Sornette re-parameterisation:

    log p(t) = A + B f + C1 f cos(w log(t_c-t))
                         + C2 f sin(w log(t_c-t)),
    f = (t_c-t)^m.

For fixed nonlinear parameters (t_c, m, w), the four remaining parameters are
estimated together by NumPy least squares.  Confidence is the fraction of
shrinking windows that pass the selected LPPLS filters.  B < 0 identifies a
positive bubble and B > 0 a negative bubble.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import f as f_distribution


@dataclass(frozen=True)
class FitConfig:
    """Numerical search and fit-filter settings."""

    m_bounds: tuple[float, float] = (0.01, 0.99)
    omega_bounds: tuple[float, float] = (2.0, 25.0)
    search_m_bounds: tuple[float, float] | None = None
    search_omega_bounds: tuple[float, float] | None = None
    search_tc_fraction: float = 1.0 / 3.0
    filter_tc_fraction: float = 1.0 / 5.0
    min_oscillations: float = 2.5
    max_relative_error: float = 0.15
    min_damping: float = 1.0
    n_starts: int = 3
    maxiter: int = 140
    start_design: str = "legacy"
    basin_objective_tolerance: float = 0.05
    basin_tc_tolerance: float = 0.03
    basin_m_tolerance: float = 0.10
    basin_omega_tolerance: float = 1.50
    min_basin_agreement: float = 0.60
    boundary_margin_fraction: float = 0.02
    filter_mode: str = "core"
    significance: float = 0.10
    enforce_damping_in_search: bool = True

    def __post_init__(self) -> None:
        if self.filter_mode not in {"core", "strict"}:
            raise ValueError("filter_mode must be 'core' or 'strict'")
        if self.n_starts < 1:
            raise ValueError("n_starts must be at least 1")
        if self.start_design not in {"legacy", "latin"}:
            raise ValueError("start_design must be 'legacy' or 'latin'")
        if not 0.0 < self.min_basin_agreement <= 1.0:
            raise ValueError("min_basin_agreement must be in (0, 1]")
        if not 0.0 <= self.boundary_margin_fraction < 0.5:
            raise ValueError("boundary_margin_fraction must be in [0, 0.5)")

    @property
    def effective_search_m_bounds(self) -> tuple[float, float]:
        return self.search_m_bounds or self.m_bounds

    @property
    def effective_search_omega_bounds(self) -> tuple[float, float]:
        return self.search_omega_bounds or self.omega_bounds


@dataclass
class FitResult:
    endpoint_index: int
    window: int
    success: bool
    valid: bool
    bubble: str
    tc_delta: float = np.nan
    tc_index: float = np.nan
    m: float = np.nan
    omega: float = np.nan
    A: float = np.nan
    B: float = np.nan
    C1: float = np.nan
    C2: float = np.nan
    rmse_log: float = np.nan
    max_relative_error: float = np.nan
    oscillations: float = np.nan
    damping: float = np.nan
    adf_pvalue: float = np.nan
    pp_pvalue: float = np.nan
    lomb_fap: float = np.nan
    objective: float = np.nan
    starts_attempted: int = 0
    starts_converged: int = 0
    near_best_starts: int = 0
    agreeing_starts: int = 0
    basin_agreement: float = np.nan
    tc_delta_iqr: float = np.nan
    m_iqr: float = np.nan
    omega_iqr: float = np.nan
    boundary_distance: float = np.nan
    parameter_interior: bool = False
    stable_solution: bool = False
    robust_valid: bool = False
    message: str = ""

    def as_record(self) -> dict:
        return asdict(self)


def _design_matrix(t: np.ndarray, tc: float, m: float, omega: float) -> np.ndarray | None:
    tau = tc - t
    if np.any(tau <= 0.0) or not np.all(np.isfinite(tau)):
        return None
    power = np.power(tau, m)
    phase = omega * np.log(tau)
    return np.column_stack(
        (np.ones_like(t), power, power * np.cos(phase), power * np.sin(phase))
    )


def _linear_fit(
    t: np.ndarray, y: np.ndarray, tc: float, m: float, omega: float
) -> tuple[np.ndarray, np.ndarray, float] | None:
    matrix = _design_matrix(t, tc, m, omega)
    if matrix is None:
        return None
    try:
        coefficients, _, rank, _ = np.linalg.lstsq(matrix, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if rank < 4 or not np.all(np.isfinite(coefficients)):
        return None
    fitted = matrix @ coefficients
    mse = float(np.mean(np.square(y - fitted)))
    if not np.isfinite(mse):
        return None
    return coefficients, fitted, mse


def _damping(m: float, omega: float, B: float, C1: float, C2: float) -> float:
    amplitude = float(np.hypot(C1, C2))
    if amplitude <= np.finfo(float).eps:
        return np.inf
    return float(m * abs(B) / (omega * amplitude))


def _starting_points(
    span: float,
    n_starts: int,
    seed: int,
    m_bounds: tuple[float, float],
    omega_bounds: tuple[float, float],
    design: str = "legacy",
) -> list[np.ndarray]:
    if design == "latin":
        rng = np.random.default_rng(seed)
        strata = (np.arange(n_starts, dtype=float) + 0.5) / n_starts
        tc_unit = rng.permutation(strata)
        m_unit = rng.permutation(strata)
        omega_unit = rng.permutation(strata)
        tc_low = max(0.1, 0.02 * span)
        tc_high = max(tc_low + 1e-6, 0.32 * span)
        return [
            np.array(
                [
                    tc_low + tc_unit[index] * (tc_high - tc_low),
                    m_bounds[0] + m_unit[index] * (m_bounds[1] - m_bounds[0]),
                    omega_bounds[0]
                    + omega_unit[index] * (omega_bounds[1] - omega_bounds[0]),
                ],
                dtype=float,
            )
            for index in range(n_starts)
        ]

    templates = (
        (0.08, 0.30, 6.0),
        (0.18, 0.50, 9.0),
        (0.30, 0.70, 13.0),
        (0.12, 0.85, 18.0),
    )
    points = [np.array([max(0.1, span * d), m, w], dtype=float) for d, m, w in templates]
    if n_starts > len(points):
        rng = np.random.default_rng(seed)
        for _ in range(n_starts - len(points)):
            points.append(
                np.array(
                    [
                        rng.uniform(0.02 * span, 0.32 * span),
                        rng.uniform(*m_bounds),
                        rng.uniform(*omega_bounds),
                    ]
                )
            )
    return points[:n_starts]


def _lomb_false_alarm_probability(
    log_tau: np.ndarray, detrended: np.ndarray, omega: float, omega_bounds: tuple[float, float]
) -> float:
    """Approximate Lomb false-alarm probability via a sinusoidal F test.

    The multiple-frequency adjustment uses the approximate number of independent
    Fourier frequencies in the searched log-time interval.  This is transparent
    and deterministic, but it should not be treated as bit-for-bit identical to
    the proprietary DS LPPLS implementation.
    """

    n = detrended.size
    if n < 12 or np.std(detrended) <= np.finfo(float).eps:
        return 1.0
    reduced = np.column_stack((np.ones(n),))
    full = np.column_stack(
        (np.ones(n), np.cos(omega * log_tau), np.sin(omega * log_tau))
    )
    beta0, *_ = np.linalg.lstsq(reduced, detrended, rcond=None)
    beta1, *_ = np.linalg.lstsq(full, detrended, rcond=None)
    sse0 = float(np.sum(np.square(detrended - reduced @ beta0)))
    sse1 = float(np.sum(np.square(detrended - full @ beta1)))
    if sse1 <= np.finfo(float).eps or sse0 <= sse1:
        return 1.0 if sse0 <= sse1 else 0.0
    statistic = ((sse0 - sse1) / 2.0) / (sse1 / (n - 3))
    single_frequency_p = float(f_distribution.sf(statistic, 2, n - 3))
    searched_cycles = (
        (omega_bounds[1] - omega_bounds[0])
        * float(np.ptp(log_tau))
        / (2.0 * np.pi)
    )
    independent_frequencies = max(1, int(np.ceil(searched_cycles)))
    return float(min(1.0, single_frequency_p * independent_frequencies))


def _strict_residual_tests(
    residual: np.ndarray,
    tau: np.ndarray,
    y: np.ndarray,
    coefficients: np.ndarray,
    m: float,
    omega: float,
    omega_bounds: tuple[float, float],
) -> tuple[float, float, float]:
    from statsmodels.tsa.stattools import adfuller

    try:
        adf_pvalue = float(adfuller(residual, regression="c", autolag="AIC")[1])
    except (ValueError, np.linalg.LinAlgError):
        adf_pvalue = 1.0

    try:
        from arch.unitroot import PhillipsPerron

        pp_pvalue = float(PhillipsPerron(residual, trend="c").pvalue)
    except ImportError as exc:
        raise RuntimeError(
            "strict mode requires the optional 'arch' package for the Phillips-Perron test"
        ) from exc
    except (ValueError, np.linalg.LinAlgError):
        pp_pvalue = 1.0

    A, B, _, _ = coefficients
    detrended = np.power(tau, -m) * (y - A - B * np.power(tau, m))
    lomb_fap = _lomb_false_alarm_probability(
        np.log(tau), detrended, omega, omega_bounds
    )
    return adf_pvalue, pp_pvalue, lomb_fap


def fit_lppls_window(
    log_price: Sequence[float],
    *,
    endpoint_index: int = -1,
    config: FitConfig | None = None,
    seed: int = 0,
) -> FitResult:
    """Fit and filter one log-price window."""

    cfg = config or FitConfig()
    y = np.asarray(log_price, dtype=float)
    n = y.size
    empty = FitResult(endpoint_index, n, False, False, "none")
    if n < 12 or not np.all(np.isfinite(y)):
        empty.message = "window is too short or contains non-finite values"
        return empty

    t = np.arange(n, dtype=float)
    span = float(n - 1)
    min_delta = 0.1
    max_delta = max(min_delta + 1e-6, cfg.search_tc_fraction * span)
    bounds = (
        (min_delta, max_delta),
        cfg.effective_search_m_bounds,
        cfg.effective_search_omega_bounds,
    )

    def objective(params: np.ndarray) -> float:
        delta, m, omega = map(float, params)
        tc = span + delta
        solved = _linear_fit(t, y, tc, m, omega)
        if solved is None:
            return 1e12
        coefficients, _, mse = solved
        if cfg.enforce_damping_in_search:
            damping = _damping(m, omega, coefficients[1], coefficients[2], coefficients[3])
            if damping < cfg.min_damping:
                gap = cfg.min_damping - damping
                mse = mse * (1.0 + 50.0 * gap * gap) + 1e-8 * gap * gap
        return mse

    candidates: list[tuple[object, np.ndarray, float]] = []
    for initial in _starting_points(
        span,
        cfg.n_starts,
        seed,
        cfg.effective_search_m_bounds,
        cfg.effective_search_omega_bounds,
        cfg.start_design,
    ):
        initial = np.array(
            [
                np.clip(initial[0], bounds[0][0], bounds[0][1]),
                np.clip(initial[1], bounds[1][0], bounds[1][1]),
                np.clip(initial[2], bounds[2][0], bounds[2][1]),
            ]
        )
        result = minimize(
            objective,
            initial,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": cfg.maxiter, "ftol": 1e-12, "gtol": 1e-7, "maxls": 30},
        )
        if np.isfinite(result.fun) and np.all(np.isfinite(result.x)):
            candidates.append((result, np.asarray(result.x, dtype=float), float(result.fun)))

    if not candidates:
        empty.message = "all nonlinear searches failed"
        return empty

    converged_candidates = [item for item in candidates if bool(item[0].success)]
    selection_pool = converged_candidates or candidates
    best, best_params, best_value = min(selection_pool, key=lambda item: item[2])

    objective_limit = best_value * (1.0 + cfg.basin_objective_tolerance) + 1e-12
    near_best = [item for item in converged_candidates if item[2] <= objective_limit]
    agreeing = [
        item
        for item in near_best
        if abs(item[1][0] - best_params[0]) <= cfg.basin_tc_tolerance * span
        and abs(item[1][1] - best_params[1]) <= cfg.basin_m_tolerance
        and abs(item[1][2] - best_params[2]) <= cfg.basin_omega_tolerance
    ]
    denominator = max(1, cfg.n_starts)
    basin_agreement = len(agreeing) / denominator
    stable_solution = bool(
        len(converged_candidates) >= int(np.ceil(cfg.min_basin_agreement * cfg.n_starts))
        and basin_agreement >= cfg.min_basin_agreement
    )
    dispersion_pool = near_best or converged_candidates or candidates
    dispersion_params = np.vstack([item[1] for item in dispersion_pool])
    parameter_iqr = np.subtract(*np.percentile(dispersion_params, [75, 25], axis=0))

    delta, m, omega = map(float, best_params)
    tc = span + delta
    solved = _linear_fit(t, y, tc, m, omega)
    if solved is None:
        empty.message = "linear parameter solve failed"
        return empty
    coefficients, fitted, mse = solved
    A, B, C1, C2 = map(float, coefficients)
    residual = y - fitted
    tau = tc - t
    oscillations = float((omega / np.pi) * np.log(tau[0] / tau[-1]))
    damping = _damping(m, omega, B, C1, C2)
    relative_error = np.abs(np.expm1(fitted - y))
    max_relative_error = float(np.max(relative_error))

    core_valid = bool(
        cfg.m_bounds[0] <= m <= cfg.m_bounds[1]
        and cfg.omega_bounds[0] <= omega <= cfg.omega_bounds[1]
        and 0.0 < delta <= cfg.filter_tc_fraction * span
        and oscillations >= cfg.min_oscillations
        and damping >= cfg.min_damping
        and max_relative_error <= cfg.max_relative_error
    )

    m_range = cfg.m_bounds[1] - cfg.m_bounds[0]
    omega_range = cfg.omega_bounds[1] - cfg.omega_bounds[0]
    normalized_distances = (
        (m - cfg.m_bounds[0]) / m_range,
        (cfg.m_bounds[1] - m) / m_range,
        (omega - cfg.omega_bounds[0]) / omega_range,
        (cfg.omega_bounds[1] - omega) / omega_range,
    )
    boundary_distance = float(min(normalized_distances))
    parameter_interior = bool(boundary_distance >= cfg.boundary_margin_fraction)

    adf_pvalue = pp_pvalue = lomb_fap = np.nan
    valid = core_valid
    if cfg.filter_mode == "strict" and core_valid:
        adf_pvalue, pp_pvalue, lomb_fap = _strict_residual_tests(
            residual, tau, y, coefficients, m, omega, cfg.omega_bounds
        )
        valid = bool(
            adf_pvalue <= cfg.significance
            and pp_pvalue <= cfg.significance
            and lomb_fap <= cfg.significance
        )

    bubble = "positive" if B < 0.0 else "negative" if B > 0.0 else "none"
    return FitResult(
        endpoint_index=endpoint_index,
        window=n,
        success=bool(best.success),
        valid=valid,
        bubble=bubble,
        tc_delta=delta,
        tc_index=float(endpoint_index + delta) if endpoint_index >= 0 else tc,
        m=m,
        omega=omega,
        A=A,
        B=B,
        C1=C1,
        C2=C2,
        rmse_log=float(np.sqrt(mse)),
        max_relative_error=max_relative_error,
        oscillations=oscillations,
        damping=damping,
        adf_pvalue=adf_pvalue,
        pp_pvalue=pp_pvalue,
        lomb_fap=lomb_fap,
        objective=float(best_value),
        starts_attempted=cfg.n_starts,
        starts_converged=len(converged_candidates),
        near_best_starts=len(near_best),
        agreeing_starts=len(agreeing),
        basin_agreement=basin_agreement,
        tc_delta_iqr=float(parameter_iqr[0]),
        m_iqr=float(parameter_iqr[1]),
        omega_iqr=float(parameter_iqr[2]),
        boundary_distance=boundary_distance,
        parameter_interior=parameter_interior,
        stable_solution=stable_solution,
        robust_valid=bool(valid and stable_solution),
        message=str(best.message),
    )


def _fit_endpoint(
    endpoint: int,
    log_prices: np.ndarray,
    windows: tuple[int, ...],
    config: FitConfig,
) -> tuple[dict, list[dict]]:
    records: list[dict] = []
    for window in windows:
        start = endpoint - window + 1
        fit = fit_lppls_window(
            log_prices[start : endpoint + 1],
            endpoint_index=endpoint,
            config=config,
            seed=(endpoint * 1009 + window * 9176) % (2**32 - 1),
        )
        records.append(fit.as_record())
    valid = [item for item in records if item["valid"]]
    robust = [item for item in records if item["robust_valid"]]
    interior = [item for item in valid if item["parameter_interior"]]
    robust_interior = [item for item in robust if item["parameter_interior"]]
    positive_count = sum(item["bubble"] == "positive" for item in valid)
    negative_count = sum(item["bubble"] == "negative" for item in valid)
    robust_positive_count = sum(item["bubble"] == "positive" for item in robust)
    robust_negative_count = sum(item["bubble"] == "negative" for item in robust)
    interior_positive_count = sum(item["bubble"] == "positive" for item in interior)
    interior_negative_count = sum(item["bubble"] == "negative" for item in interior)
    robust_interior_positive_count = sum(
        item["bubble"] == "positive" for item in robust_interior
    )
    robust_interior_negative_count = sum(
        item["bubble"] == "negative" for item in robust_interior
    )
    denominator = len(records)
    indicator = {
        "endpoint_index": endpoint,
        "windows_tested": denominator,
        "valid_fits": len(valid),
        "positive_fits": positive_count,
        "negative_fits": negative_count,
        "positive_indicator": positive_count / denominator,
        "negative_indicator": negative_count / denominator,
        "robust_positive_fits": robust_positive_count,
        "robust_negative_fits": robust_negative_count,
        "robust_positive_indicator": robust_positive_count / denominator,
        "robust_negative_indicator": robust_negative_count / denominator,
        "interior_positive_fits": interior_positive_count,
        "interior_negative_fits": interior_negative_count,
        "robust_interior_positive_fits": robust_interior_positive_count,
        "robust_interior_negative_fits": robust_interior_negative_count,
    }
    return indicator, records


def compute_confidence_indicators(
    prices: Sequence[float],
    dates: Sequence,
    *,
    windows: Iterable[int],
    endpoint_step: int = 5,
    n_jobs: int = 1,
    config: FitConfig | None = None,
    require_all_windows: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute causal positive and negative LPPLS confidence indicators."""

    from joblib import Parallel, delayed

    cfg = config or FitConfig()
    price_array = np.asarray(prices, dtype=float)
    date_array = pd.to_datetime(np.asarray(dates))
    if price_array.ndim != 1 or price_array.size != date_array.size:
        raise ValueError("prices and dates must be one-dimensional arrays with equal length")
    if np.any(~np.isfinite(price_array)) or np.any(price_array <= 0.0):
        raise ValueError("prices must be finite and strictly positive")
    clean_windows = tuple(sorted({int(value) for value in windows if int(value) >= 12}))
    if not clean_windows:
        raise ValueError("at least one window of length >= 12 is required")
    if max(clean_windows) > price_array.size:
        raise ValueError("the largest window exceeds the number of observations")
    if endpoint_step < 1:
        raise ValueError("endpoint_step must be at least 1")

    first_endpoint = (max(clean_windows) if require_all_windows else min(clean_windows)) - 1
    endpoint_list = list(range(first_endpoint, price_array.size, endpoint_step))
    last_endpoint = price_array.size - 1
    if endpoint_list[-1] != last_endpoint:
        endpoint_list.append(last_endpoint)
    endpoints = tuple(endpoint_list)
    log_prices = np.log(price_array)
    computed = Parallel(n_jobs=n_jobs, prefer="processes", verbose=0)(
        delayed(_fit_endpoint)(endpoint, log_prices, clean_windows, cfg)
        for endpoint in endpoints
    )

    indicator_records: list[dict] = []
    fit_records: list[dict] = []
    for indicator, fits in computed:
        endpoint = int(indicator["endpoint_index"])
        indicator.update(
            {
                "date": date_array[endpoint],
                "price": price_array[endpoint],
                "log_price": log_prices[endpoint],
            }
        )
        indicator_records.append(indicator)
        for record in fits:
            record["date"] = date_array[endpoint]
        fit_records.extend(fits)

    indicators = pd.DataFrame(indicator_records).sort_values("date").reset_index(drop=True)
    fits = pd.DataFrame(fit_records).sort_values(["date", "window"]).reset_index(drop=True)
    return indicators, fits


def plot_confidence_indicators(
    dates: Sequence,
    prices: Sequence[float],
    indicators: pd.DataFrame,
    output_path,
    *,
    title: str | None = None,
) -> None:
    """Create a two-panel chart matching the supplied reference figure."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    date_array = pd.to_datetime(np.asarray(dates))
    log_price = np.log(np.asarray(prices, dtype=float))
    indicator_dates = pd.to_datetime(indicators["date"])

    fig, axes = plt.subplots(2, 1, figsize=(18, 10), sharex=True, constrained_layout=True)
    colors = (("#ff7070", "positive_indicator", "bubble indicator (pos)"),
              ("#69b96f", "negative_indicator", "bubble indicator (neg)"))
    for axis, (color, column, label) in zip(axes, colors):
        axis.plot(date_array, log_price, color="black", linewidth=1.0, zorder=2)
        axis.set_ylabel("ln(p)")
        axis.grid(True, linestyle="--", alpha=0.55)
        twin = axis.twinx()
        twin.plot(indicator_dates, indicators[column], color=color, linewidth=1.4, label=label)
        twin.set_ylabel(label)
        twin.set_ylim(bottom=-0.01)
        twin.legend(loc="upper left")
    if title:
        fig.suptitle(title)
    axes[-1].set_xlabel("date")
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
