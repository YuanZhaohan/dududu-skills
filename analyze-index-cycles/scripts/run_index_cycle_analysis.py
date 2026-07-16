"""Run the complete public-data index cycle analysis workflow."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from .cycle_model import CycleConfig, load_price_data, run_cycle_research
    from .cycle_timing import TimingConfig, run_cycle_timing_research
    from .fetch_csindex_close import fetch_close_series, write_close_series
    from .interactive_report import build_interactive_report
    from .lppls_history import compute_lppls_history
    from .ma_short_cycle import short_ma_config
except ImportError:
    from cycle_model import CycleConfig, load_price_data, run_cycle_research
    from cycle_timing import TimingConfig, run_cycle_timing_research
    from fetch_csindex_close import fetch_close_series, write_close_series
    from interactive_report import build_interactive_report
    from lppls_history import compute_lppls_history
    from ma_short_cycle import short_ma_config


SECTION_CHOICES = ("spectral", "timing", "lppls")


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    return value


def _yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def _pct(value: object, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number * 100:.{digits}f}%"


def _number(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number:.{digits}f}"


def _build_report(summary: dict) -> str:
    data = summary["data"]
    lines = [
        f"# {data['index_code']}.CSI {data['index_name']}周期分析",
        "",
        f"- 最新交易日：{data['date_end']}",
        f"- 样本：{data['date_start']} 至 {data['date_end']}，共 {data['rows']:,} 个交易日",
        f"- 收盘价数据源：{data['source']}",
        f"- 来源地址：{data['source_url']}",
        "- 口径：交易日序列，不补周末或节假日；所有实时指标只使用当日及以前数据。",
        "",
    ]

    spectral = summary.get("spectral")
    if spectral:
        lines.extend(
            [
                "## 滚动频谱周期",
                "",
                "| 频段 | 当前周期 | 峰谷幅度 | 红噪声 p 值 | 稳定有效 |",
                "|---|---:|---:|---:|:---:|",
            ]
        )
        for band in ("short", "medium", "long"):
            item = spectral["band_summary"][band]
            latest = item["latest"]
            lines.append(
                f"| {item['label']} | {_number(latest['period_days'], 1)} 日 | "
                f"{_number(latest['peak_to_trough_pct'], 2)}% | "
                f"{_number(latest['pvalue'], 4)} | {_yes_no(latest['stable_valid'])} |"
            )
        lines.extend(
            [
                "",
                "这里的 p 值是单日、单频段最高峰相对 AR(1) 红噪声的局部经验检验，"
                "不能解释为全历史多重检验后的显著性。",
                "",
            ]
        )

    timing = summary.get("timing")
    if timing:
        latest = timing["result"]["latest"]
        lines.extend(
            [
                "## MA34/55/89 共识周期状态",
                "",
                f"- 共识周期：{_number(latest['consensus_period_days'], 2)} 个交易日",
                f"- 达成一致的均线数：{int(latest['ma_agreement_count'])}",
                f"- 周期结构可用：{_yes_no(latest['cycle_ready'])}",
                f"- 周期位置：{_number(latest['cycle_score'], 3)}（约 -1 为谷、+1 为峰）",
                f"- 趋势环境：{latest['trend_state']}",
                f"- 当日相位事件：{latest['phase_event'] or '无'}",
                f"- 研究性仓位叠加：{_pct(latest['overlay_target'])}",
                "",
                "周期位置只有在多均线周期一致、稳定性、频谱强度与谐波拟合质量共同通过时才可用。",
                "",
            ]
        )

    lppls = summary.get("lppls")
    if lppls:
        short = lppls["latest"].get("short")
        macro = lppls["latest"].get("macro")
        lines.extend(["## LPPLS 泡沫置信状态", ""])
        if short:
            lines.extend(
                [
                    "### 40–160 日锚点层",
                    "",
                    f"- 正泡沫置信度：{_pct(short['positive_confidence'])}；"
                    f"强风险预警：{_yes_no(short['positive_strong'])}",
                    f"- 负泡沫置信度：{_pct(short['negative_confidence'])}；"
                    f"强底部观察：{_yes_no(short['negative_strong'])}",
                    f"- 负泡沫稳健确认：{_yes_no(short['negative_robust_confirmation'])}",
                    "",
                ]
            )
        if macro:
            lines.extend(
                [
                    "### 120–500 日大周期层",
                    "",
                    f"- 正泡沫风险温度：{_pct(macro['positive_confidence'])}；"
                    f"当日观察：{_yes_no(macro['macro_positive_risk_watch'])}",
                    f"- 负泡沫底部温度：{_pct(macro['negative_confidence'])}；"
                    f"当日观察：{_yes_no(macro['macro_negative_bottom_watch'])}",
                    f"- 连续 3 日大底确认：{_yes_no(lppls['macro_three_day_bottom_confirmation'])}",
                    f"- 连续 3 日顶部风险监测：{_yes_no(lppls['macro_three_day_top_monitor'])}",
                    "",
                ]
            )
        lines.extend(
            [
                "LPPLS 的 `tc` 不是精确反转日期。正泡沫侧只作风险预警；负泡沫侧也必须结合"
                "趋势止跌、市场宽度、成交量、流动性和可交易工具确认。",
                "",
            ]
        )

    lines.extend(
        [
            "## 使用边界",
            "",
            "- 频谱、相位和 LPPLS 是不同证据层，不应简单投票合成单一买卖信号。",
            "- 所有事件研究阈值来自现有历史样本，样本量有限，不能视为样本外保证。",
            "- 指数点位不可直接交易；实际执行还需选择 ETF、期货或其他工具并单独验证成本与滑点。",
            "- 本报告用于研究，不构成投资建议。",
            "",
        ]
    )
    return "\n".join(lines)


def run_workflow(
    *,
    index_code: str,
    start_date: str,
    end_date: str,
    input_path: Path | None,
    output_dir: Path,
    sections: tuple[str, ...],
    red_noise_surrogates: int,
    random_seed: int,
    force_rebuild_red_noise: bool,
    run_sensitivity: bool,
    lppls_confirmation_days: int,
    lppls_chunk_size: int,
    n_jobs: int,
) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if input_path is None:
        frame, metadata = fetch_close_series(index_code, start_date, end_date)
        price_path = output_dir / "data" / f"{index_code}_daily.csv"
        data_result = write_close_series(frame, metadata, price_path)
    else:
        price_path = input_path.resolve()
        price_data = load_price_data(price_path)
        data_result = {
            "index_code": str(price_data["code"].iloc[0]).split(".")[0],
            "index_name": str(price_data["index_name"].iloc[0]),
            "source": "用户提供的本地文件",
            "source_url": str(price_path),
            "rows": int(len(price_data)),
            "date_start": price_data["date"].min().strftime("%Y-%m-%d"),
            "date_end": price_data["date"].max().strftime("%Y-%m-%d"),
            "latest_close": float(price_data.sort_values("date")["close"].iloc[-1]),
            "output": str(price_path),
        }

    summary: dict[str, Any] = {"data": data_result, "sections": list(sections)}
    outputs: dict[str, str] = {"price_data": str(price_path)}

    if "spectral" in sections:
        config = replace(
            CycleConfig(),
            red_noise_surrogates=red_noise_surrogates,
            random_seed=random_seed,
        )
        result = run_cycle_research(
            price_path,
            output_dir / "spectral",
            config,
            force_rebuild_red_noise=force_rebuild_red_noise,
            run_sensitivity=run_sensitivity,
            progress=print,
        )
        summary["spectral"] = result["summary"]
        outputs["spectral_summary"] = result["summary"]["outputs"]["summary"]

    if "timing" in sections:
        config = short_ma_config(
            red_noise_surrogates=red_noise_surrogates,
            random_seed=random_seed,
        )
        result = run_cycle_timing_research(
            price_path,
            output_dir / "timing",
            timing_config=TimingConfig(),
            cycle_config=config,
            force_rebuild_red_noise=force_rebuild_red_noise,
            progress=print,
        )
        summary["timing"] = result["summary"]
        outputs["timing_summary"] = result["summary"]["outputs"]["summary"]

    if "lppls" in sections:
        result = compute_lppls_history(
            price_path,
            output_dir / "lppls",
            profiles=("short", "macro"),
            confirmation_days=lppls_confirmation_days,
            chunk_size=lppls_chunk_size,
            n_jobs=n_jobs,
            progress=print,
        )
        summary["lppls"] = result["summary"]
        outputs["lppls_summary"] = result["summary"]["outputs"]["summary_json"]

    summary["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    report_path = output_dir / "index_cycle_report.md"
    dashboard_path = output_dir / "index_cycle_dashboard.html"
    summary_path = output_dir / "analysis_summary.json"
    outputs.update(
        {
            "dashboard": str(dashboard_path),
            "report": str(report_path),
            "summary": str(summary_path),
        }
    )
    summary["outputs"] = outputs
    summary = _native(summary)
    build_interactive_report(summary, dashboard_path)
    report_path.write_text(_build_report(summary), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公开数据驱动的指数周期与 LPPLS 分层分析。")
    parser.add_argument("--index-code", default="000985")
    parser.add_argument("--start-date", default="2004-12-31")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("index_cycle_outputs"))
    parser.add_argument(
        "--sections", nargs="+", choices=SECTION_CHOICES, default=list(SECTION_CHOICES)
    )
    parser.add_argument("--red-noise-surrogates", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--force-rebuild-red-noise", action="store_true")
    parser.add_argument("--skip-sensitivity", action="store_true")
    parser.add_argument("--lppls-confirmation-days", type=int, default=3)
    parser.add_argument("--lppls-chunk-size", type=int, default=250)
    parser.add_argument("--n-jobs", type=int, default=-1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_workflow(
        index_code=args.index_code,
        start_date=args.start_date,
        end_date=args.end_date,
        input_path=args.input,
        output_dir=args.output_dir,
        sections=tuple(dict.fromkeys(args.sections)),
        red_noise_surrogates=args.red_noise_surrogates,
        random_seed=args.seed,
        force_rebuild_red_noise=args.force_rebuild_red_noise,
        run_sensitivity=not args.skip_sensitivity,
        lppls_confirmation_days=args.lppls_confirmation_days,
        lppls_chunk_size=args.lppls_chunk_size,
        n_jobs=args.n_jobs,
    )
    print(json.dumps(result["outputs"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
