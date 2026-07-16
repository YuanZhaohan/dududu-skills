"""Compute the researched short- and macro-horizon LPPLS state near the latest date."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

try:
    from .cycle_model import load_price_data
    from .lppls_indicator import FitConfig, fit_lppls_window
except ImportError:
    from cycle_model import load_price_data
    from lppls_indicator import FitConfig, fit_lppls_window


PROFILE_SETTINGS = {
    "short": {
        "windows": tuple(range(40, 161, 5)),
        "n_starts": 5,
        "maxiter": 180,
        "start_design": "latin",
        "damping_positive": 0.50,
        "damping_negative": 0.50,
        "oscillations_positive": 2.50,
        "oscillations_negative": 2.50,
    },
    "macro": {
        "windows": tuple(range(120, 501, 10)),
        "n_starts": 4,
        "maxiter": 180,
        "start_design": "legacy",
        "damping_positive": 0.20,
        "damping_negative": 0.40,
        "oscillations_positive": 3.00,
        "oscillations_negative": 2.50,
    },
}


def _native(value):
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _fit_one(
    log_prices: np.ndarray,
    endpoint: int,
    window: int,
    config: FitConfig,
    profile: str,
) -> dict:
    start = endpoint - window + 1
    fit = fit_lppls_window(
        log_prices[start : endpoint + 1],
        endpoint_index=endpoint,
        config=config,
        seed=(endpoint * 1009 + window * 9176) % (2**32 - 1),
    )
    record = fit.as_record()
    record["profile"] = profile
    return record


def _profile_config(profile: str) -> FitConfig:
    settings = PROFILE_SETTINGS[profile]
    return FitConfig(
        m_bounds=(0.10, 0.90),
        omega_bounds=(4.0, 25.0),
        search_m_bounds=(0.10, 0.90),
        search_omega_bounds=(4.0, 25.0),
        search_tc_fraction=1.0 / 3.0,
        filter_tc_fraction=1.0 / 5.0,
        min_oscillations=2.5,
        max_relative_error=0.15,
        min_damping=0.0,
        n_starts=int(settings["n_starts"]),
        maxiter=int(settings["maxiter"]),
        start_design=str(settings["start_design"]),
        enforce_damping_in_search=False,
        filter_mode="core",
    )


def _summarize_endpoint(
    fits: pd.DataFrame,
    *,
    profile: str,
    endpoint: int,
    endpoint_date: pd.Timestamp,
) -> dict:
    settings = PROFILE_SETTINGS[profile]
    total = int(len(settings["windows"]))
    valid = fits["valid"].astype(bool)
    if profile == "macro":
        valid &= fits["tc_delta"].le(
            np.minimum(0.20 * (fits["window"] - 1.0), 60.0)
        )
    positive = (
        valid
        & fits["bubble"].eq("positive")
        & fits["damping"].ge(float(settings["damping_positive"]))
        & fits["oscillations"].ge(float(settings["oscillations_positive"]))
    )
    negative = (
        valid
        & fits["bubble"].eq("negative")
        & fits["damping"].ge(float(settings["damping_negative"]))
        & fits["oscillations"].ge(float(settings["oscillations_negative"]))
    )
    if profile == "macro":
        boundary = (
            np.isclose(fits["m"], 0.10, atol=1e-5)
            | np.isclose(fits["m"], 0.90, atol=1e-5)
            | np.isclose(fits["omega"], 4.0, atol=1e-5)
            | np.isclose(fits["omega"], 25.0, atol=1e-5)
        )
        interior = ~boundary
    else:
        interior = fits["parameter_interior"].astype(bool)
    stable = fits["stable_solution"].astype(bool)

    positive_count = int(positive.sum())
    negative_count = int(negative.sum())
    interior_positive = int((positive & interior).sum())
    interior_negative = int((negative & interior).sum())
    stable_interior_positive = int((positive & interior & stable).sum())
    stable_interior_negative = int((negative & interior & stable).sum())
    positive_confidence = positive_count / total
    negative_confidence = negative_count / total

    record = {
        "date": endpoint_date.strftime("%Y-%m-%d"),
        "endpoint_index": int(endpoint),
        "profile": profile,
        "windows_tested": total,
        "positive_fits": positive_count,
        "negative_fits": negative_count,
        "positive_confidence": positive_confidence,
        "negative_confidence": negative_confidence,
        "interior_positive_fits": interior_positive,
        "interior_negative_fits": interior_negative,
        "stable_interior_positive_fits": stable_interior_positive,
        "stable_interior_negative_fits": stable_interior_negative,
        "positive_observation": positive_confidence >= 0.15,
        "negative_observation": negative_confidence >= 0.15,
        "positive_strong": positive_confidence >= 0.20 and interior_positive >= 1,
        "negative_strong": negative_confidence >= 0.20 and interior_negative >= 1,
        "negative_robust_confirmation": (
            negative_confidence >= 0.20 and stable_interior_negative >= 1
        ),
        "macro_positive_risk_watch": False,
        "macro_negative_bottom_watch": False,
    }
    if profile == "macro":
        record["macro_positive_risk_watch"] = (
            positive_confidence >= 0.30 and interior_positive >= 1
        )
        record["macro_negative_bottom_watch"] = (
            negative_confidence >= 0.25 and interior_negative >= 2
        )
    return record


def compute_lppls_snapshot(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    profiles: tuple[str, ...] = ("short", "macro"),
    confirmation_days: int = 3,
    n_jobs: int = -1,
    progress=print,
) -> dict:
    if confirmation_days < 1:
        raise ValueError("confirmation_days must be at least 1")
    unknown = sorted(set(profiles).difference(PROFILE_SETTINGS))
    if unknown:
        raise ValueError(f"unknown LPPLS profiles: {unknown}")

    started = perf_counter()
    price_data = load_price_data(input_path)
    log_prices = np.log(price_data["close"].to_numpy(dtype=float))
    dates = pd.to_datetime(price_data["date"]).reset_index(drop=True)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    summary_records: list[dict] = []
    fit_records: list[dict] = []
    for profile in profiles:
        settings = PROFILE_SETTINGS[profile]
        max_window = max(settings["windows"])
        if len(price_data) < max_window + confirmation_days - 1:
            raise ValueError(
                f"{profile} LPPLS needs at least {max_window + confirmation_days - 1} rows"
            )
        config = _profile_config(profile)
        endpoints = tuple(range(len(price_data) - confirmation_days, len(price_data)))
        if progress:
            progress(
                f"LPPLS {profile}: {len(settings['windows'])} windows x "
                f"{len(endpoints)} endpoints"
            )
        for endpoint in endpoints:
            records = Parallel(n_jobs=n_jobs, prefer="processes")(
                delayed(_fit_one)(log_prices, endpoint, window, config, profile)
                for window in settings["windows"]
            )
            fits = pd.DataFrame.from_records(records)
            fits["date"] = dates.iloc[endpoint].strftime("%Y-%m-%d")
            fit_records.extend(fits.to_dict(orient="records"))
            summary_records.append(
                _summarize_endpoint(
                    fits,
                    profile=profile,
                    endpoint=endpoint,
                    endpoint_date=dates.iloc[endpoint],
                )
            )

    summary_frame = pd.DataFrame.from_records(summary_records).sort_values(
        ["date", "profile"]
    )
    fits_frame = pd.DataFrame.from_records(fit_records).sort_values(
        ["date", "profile", "window"]
    )
    summary_csv = output_path / "lppls_snapshot.csv"
    fits_csv = output_path / "lppls_fit_details.csv"
    summary_frame.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    fits_frame.to_csv(fits_csv, index=False, encoding="utf-8-sig")

    latest: dict[str, dict] = {}
    for profile in profiles:
        subset = summary_frame.loc[summary_frame["profile"].eq(profile)].sort_values("date")
        latest[profile] = subset.iloc[-1].to_dict()

    macro = summary_frame.loc[summary_frame["profile"].eq("macro")].sort_values("date")
    macro_three_day_bottom = bool(
        len(macro) >= 3 and macro.tail(3)["macro_negative_bottom_watch"].astype(bool).all()
    )
    macro_three_day_top = bool(
        len(macro) >= 3 and macro.tail(3)["macro_positive_risk_watch"].astype(bool).all()
    )
    payload = {
        "input": str(Path(input_path).resolve()),
        "rows": int(len(price_data)),
        "date_start": dates.iloc[0].strftime("%Y-%m-%d"),
        "date_end": dates.iloc[-1].strftime("%Y-%m-%d"),
        "profiles": list(profiles),
        "confirmation_days_computed": int(confirmation_days),
        "settings": {
            name: {
                **{key: value for key, value in PROFILE_SETTINGS[name].items() if key != "windows"},
                "windows": list(PROFILE_SETTINGS[name]["windows"]),
                "fit_config": asdict(_profile_config(name)),
            }
            for name in profiles
        },
        "latest": latest,
        "macro_three_day_bottom_confirmation": macro_three_day_bottom,
        "macro_three_day_top_monitor": macro_three_day_top,
        "runtime_seconds": perf_counter() - started,
        "outputs": {"summary_csv": str(summary_csv), "fits_csv": str(fits_csv)},
        "interpretation_boundary": (
            "LPPLS is a causal structural warning indicator, not a precise reversal date or "
            "a standalone trading rule. Positive bubbles are risk warnings; negative bubbles "
            "need trend, breadth, liquidity, and execution confirmation."
        ),
    }
    payload = _native(payload)
    json_path = output_path / "lppls_snapshot.json"
    payload["outputs"]["summary_json"] = str(json_path)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": payload, "daily": summary_frame, "fits": fits_frame}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算最新短周期与大周期 LPPLS 状态。")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--profiles", nargs="+", choices=tuple(PROFILE_SETTINGS), default=["short", "macro"]
    )
    parser.add_argument("--confirmation-days", type=int, default=3)
    parser.add_argument("--n-jobs", type=int, default=-1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = compute_lppls_snapshot(
        args.input,
        args.output_dir,
        profiles=tuple(args.profiles),
        confirmation_days=args.confirmation_days,
        n_jobs=args.n_jobs,
        progress=print,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
