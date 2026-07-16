"""计算并增量缓存短周期与大周期 LPPLS 全历史指数。"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

try:
    from .cycle_model import load_price_data
    from .lppls_indicator import FitConfig, fit_lppls_window
    from .lppls_snapshot import PROFILE_SETTINGS, _native, _profile_config
except ImportError:
    from cycle_model import load_price_data
    from lppls_indicator import FitConfig, fit_lppls_window
    from lppls_snapshot import PROFILE_SETTINGS, _native, _profile_config


def _prefix_hash(price_data: pd.DataFrame, endpoint: int) -> str:
    prefix = price_data.iloc[: endpoint + 1]
    digest = hashlib.sha256()
    digest.update(
        np.ascontiguousarray(
            pd.to_datetime(prefix["date"]).astype("int64").to_numpy(dtype=np.int64)
        ).tobytes()
    )
    digest.update(
        np.ascontiguousarray(prefix["close"].to_numpy(dtype=np.float64)).tobytes()
    )
    return digest.hexdigest()


def _config_payload(profile: str, config: FitConfig) -> dict[str, Any]:
    settings = PROFILE_SETTINGS[profile]
    return _native(
        {
            "profile": profile,
            "windows": list(settings["windows"]),
            "damping_positive": settings["damping_positive"],
            "damping_negative": settings["damping_negative"],
            "oscillations_positive": settings["oscillations_positive"],
            "oscillations_negative": settings["oscillations_negative"],
            "fit_config": asdict(config),
        }
    )


def _fit_profile_endpoint(
    log_prices: np.ndarray,
    endpoint: int,
    windows: tuple[int, ...],
    config: FitConfig,
    profile: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for window in windows:
        start = endpoint - window + 1
        fit = fit_lppls_window(
            log_prices[start : endpoint + 1],
            endpoint_index=endpoint,
            config=config,
            seed=(endpoint * 1009 + window * 9176) % (2**32 - 1),
        )
        record = fit.as_record()
        record["profile"] = profile
        records.append(record)
    return records


def _summarize_history_fits(
    fits: pd.DataFrame,
    *,
    profile: str,
    dates: pd.Series,
    closes: np.ndarray,
) -> pd.DataFrame:
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

    flags = pd.DataFrame(
        {
            "endpoint_index": fits["endpoint_index"].to_numpy(dtype=int),
            "positive_fits": positive.to_numpy(dtype=int),
            "negative_fits": negative.to_numpy(dtype=int),
            "interior_positive_fits": (positive & interior).to_numpy(dtype=int),
            "interior_negative_fits": (negative & interior).to_numpy(dtype=int),
            "stable_interior_positive_fits": (
                positive & interior & stable
            ).to_numpy(dtype=int),
            "stable_interior_negative_fits": (
                negative & interior & stable
            ).to_numpy(dtype=int),
        }
    )
    counts = flags.groupby("endpoint_index", sort=True, as_index=False).sum()
    endpoints = counts["endpoint_index"].to_numpy(dtype=int)
    endpoint_dates = (
        pd.to_datetime(dates.iloc[endpoints]).dt.strftime("%Y-%m-%d").to_numpy()
    )
    counts.insert(0, "date", endpoint_dates)
    counts.insert(1, "profile", profile)
    counts.insert(2, "close", closes[endpoints])
    counts.insert(4, "windows_tested", total)
    counts["positive_confidence"] = counts["positive_fits"] / total
    counts["negative_confidence"] = counts["negative_fits"] / total
    counts["positive_observation"] = counts["positive_confidence"].ge(0.15)
    counts["negative_observation"] = counts["negative_confidence"].ge(0.15)
    counts["positive_strong"] = counts["positive_confidence"].ge(0.20) & counts[
        "interior_positive_fits"
    ].ge(1)
    counts["negative_strong"] = counts["negative_confidence"].ge(0.20) & counts[
        "interior_negative_fits"
    ].ge(1)
    counts["negative_robust_confirmation"] = counts["negative_confidence"].ge(
        0.20
    ) & counts["stable_interior_negative_fits"].ge(1)
    counts["macro_positive_risk_watch"] = False
    counts["macro_negative_bottom_watch"] = False
    if profile == "macro":
        counts["macro_positive_risk_watch"] = counts["positive_confidence"].ge(
            0.30
        ) & counts["interior_positive_fits"].ge(1)
        counts["macro_negative_bottom_watch"] = counts["negative_confidence"].ge(
            0.25
        ) & counts["interior_negative_fits"].ge(2)
    return counts


def _cache_is_valid(
    metadata: dict[str, Any],
    *,
    profile: str,
    config_payload: dict[str, Any],
    price_data: pd.DataFrame,
) -> bool:
    try:
        endpoint = int(metadata["last_endpoint"])
        return bool(
            metadata.get("profile") == profile
            and metadata.get("config") == config_payload
            and 0 <= endpoint < len(price_data)
            and metadata.get("last_date")
            == pd.Timestamp(price_data["date"].iloc[endpoint]).strftime("%Y-%m-%d")
            and metadata.get("prefix_hash") == _prefix_hash(price_data, endpoint)
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _compute_profile_history(
    price_data: pd.DataFrame,
    output_path: Path,
    *,
    profile: str,
    n_jobs: int,
    chunk_size: int,
    progress,
) -> tuple[pd.DataFrame, dict[str, str]]:
    settings = PROFILE_SETTINGS[profile]
    windows = tuple(int(value) for value in settings["windows"])
    first_endpoint = max(windows) - 1
    config = _profile_config(profile)
    config_payload = _config_payload(profile, config)
    summary_path = output_path / f"lppls_history_{profile}.csv"
    fits_path = output_path / f"lppls_history_fit_details_{profile}.csv"
    metadata_path = output_path / f"lppls_history_cache_{profile}.json"

    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    valid_cache = (
        summary_path.exists()
        and fits_path.exists()
        and _cache_is_valid(
            metadata,
            profile=profile,
            config_payload=config_payload,
            price_data=price_data,
        )
    )
    if valid_cache:
        history = pd.read_csv(summary_path, encoding="utf-8-sig")
        last_endpoint = int(metadata["last_endpoint"])
    else:
        history = pd.DataFrame()
        last_endpoint = first_endpoint - 1
        for path in (summary_path, fits_path, metadata_path):
            path.unlink(missing_ok=True)

    endpoints = list(range(max(first_endpoint, last_endpoint + 1), len(price_data)))
    if not endpoints:
        if progress:
            progress(f"LPPLS {profile} 全历史：复用 {len(history):,} 个逐日端点")
        return history, {
            "history_csv": str(summary_path),
            "fits_csv": str(fits_path),
            "cache_metadata": str(metadata_path),
        }

    log_prices = np.log(price_data["close"].to_numpy(dtype=float))
    closes = price_data["close"].to_numpy(dtype=float)
    dates = pd.to_datetime(price_data["date"]).reset_index(drop=True)
    for start in range(0, len(endpoints), chunk_size):
        chunk = endpoints[start : start + chunk_size]
        computed = Parallel(n_jobs=n_jobs, prefer="processes", verbose=0)(
            delayed(_fit_profile_endpoint)(
                log_prices, endpoint, windows, config, profile
            )
            for endpoint in chunk
        )
        records = [record for endpoint_records in computed for record in endpoint_records]
        fits = pd.DataFrame.from_records(records)
        fits["date"] = pd.to_datetime(dates.iloc[fits["endpoint_index"].to_numpy(dtype=int)]).to_numpy()
        chunk_history = _summarize_history_fits(
            fits,
            profile=profile,
            dates=dates,
            closes=closes,
        )
        fits.to_csv(
            fits_path,
            mode="a" if fits_path.exists() else "w",
            header=not fits_path.exists(),
            index=False,
            encoding="utf-8-sig",
        )
        history = pd.concat([history, chunk_history], ignore_index=True)
        history = history.drop_duplicates(
            subset=["profile", "endpoint_index"], keep="last"
        ).sort_values("endpoint_index")
        history.to_csv(summary_path, index=False, encoding="utf-8-sig")
        last = int(chunk[-1])
        metadata = {
            "profile": profile,
            "config": config_payload,
            "last_endpoint": last,
            "last_date": dates.iloc[last].strftime("%Y-%m-%d"),
            "prefix_hash": _prefix_hash(price_data, last),
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if progress:
            progress(
                f"LPPLS {profile} 全历史：{start + len(chunk):,}/{len(endpoints):,} "
                f"个新增端点"
            )
    return history, {
        "history_csv": str(summary_path),
        "fits_csv": str(fits_path),
        "cache_metadata": str(metadata_path),
    }


def compute_lppls_history(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    profiles: tuple[str, ...] = ("short", "macro"),
    confirmation_days: int = 3,
    n_jobs: int = -1,
    chunk_size: int = 250,
    progress=print,
) -> dict[str, Any]:
    if confirmation_days < 1:
        raise ValueError("confirmation_days must be at least 1")
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    unknown = sorted(set(profiles).difference(PROFILE_SETTINGS))
    if unknown:
        raise ValueError(f"unknown LPPLS profiles: {unknown}")

    started = perf_counter()
    price_data = load_price_data(input_path)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    profile_outputs: dict[str, dict[str, str]] = {}
    histories: list[pd.DataFrame] = []
    for profile in profiles:
        history, paths = _compute_profile_history(
            price_data,
            output_path,
            profile=profile,
            n_jobs=n_jobs,
            chunk_size=chunk_size,
            progress=progress,
        )
        histories.append(history)
        profile_outputs[profile] = paths

    history_frame = pd.concat(histories, ignore_index=True).sort_values(
        ["date", "profile"]
    )
    history_csv = output_path / "lppls_history.csv"
    history_frame.to_csv(history_csv, index=False, encoding="utf-8-sig")
    latest = {
        profile: history_frame.loc[history_frame["profile"].eq(profile)]
        .sort_values("date")
        .iloc[-1]
        .to_dict()
        for profile in profiles
    }
    macro = history_frame.loc[history_frame["profile"].eq("macro")].sort_values("date")
    macro_three_day_bottom = bool(
        len(macro) >= confirmation_days
        and macro.tail(confirmation_days)["macro_negative_bottom_watch"]
        .astype(bool)
        .all()
    )
    macro_three_day_top = bool(
        len(macro) >= confirmation_days
        and macro.tail(confirmation_days)["macro_positive_risk_watch"]
        .astype(bool)
        .all()
    )
    dates = pd.to_datetime(price_data["date"]).reset_index(drop=True)
    payload = _native(
        {
            "input": str(Path(input_path).resolve()),
            "rows": int(len(price_data)),
            "date_start": dates.iloc[0].strftime("%Y-%m-%d"),
            "date_end": dates.iloc[-1].strftime("%Y-%m-%d"),
            "profiles": list(profiles),
            "history_rows": int(len(history_frame)),
            "confirmation_days_computed": int(confirmation_days),
            "settings": {
                profile: _config_payload(profile, _profile_config(profile))
                for profile in profiles
            },
            "latest": latest,
            "macro_three_day_bottom_confirmation": macro_three_day_bottom,
            "macro_three_day_top_monitor": macro_three_day_top,
            "runtime_seconds": perf_counter() - started,
            "outputs": {
                "summary_csv": str(history_csv),
                "history_csv": str(history_csv),
                **{
                    f"{profile}_{name}": path
                    for profile, paths in profile_outputs.items()
                    for name, path in paths.items()
                },
            },
            "interpretation_boundary": (
                "LPPLS is a causal structural warning indicator, not a precise reversal "
                "date or a standalone trading rule."
            ),
        }
    )
    json_path = output_path / "lppls_history.json"
    payload["outputs"]["summary_json"] = str(json_path)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"summary": payload, "history": history_frame}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算并缓存 LPPLS 全历史正负泡沫指数。")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--profiles", nargs="+", choices=tuple(PROFILE_SETTINGS), default=["short", "macro"]
    )
    parser.add_argument("--confirmation-days", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=250)
    parser.add_argument("--n-jobs", type=int, default=-1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = compute_lppls_history(
        args.input,
        args.output_dir,
        profiles=tuple(args.profiles),
        confirmation_days=args.confirmation_days,
        n_jobs=args.n_jobs,
        chunk_size=args.chunk_size,
        progress=print,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
