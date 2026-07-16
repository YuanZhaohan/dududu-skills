"""生成只展示最终信号、并把稳健性写成文字的交互式 HTML。"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

try:
    from plotly.io._html import get_plotlyjs
except ImportError:  # pragma: no cover
    from plotly.offline import get_plotlyjs


FONT_FAMILY = "Microsoft YaHei, PingFang SC, Segoe UI, Arial, sans-serif"
COLORS = {
    "blue": "#2563EB",
    "purple": "#7B2CBF",
    "orange": "#F59E0B",
    "red": "#D62728",
    "green": "#169B62",
    "teal": "#0F766E",
    "slate": "#667085",
    "black": "#202020",
}
BAND_COLORS = {"short": COLORS["blue"], "medium": COLORS["orange"], "long": COLORS["purple"]}

DYNAMIC_PRICE_SCRIPT = r"""
(function () {
  function axisLayoutKey(axisId, prefix) {
    if (!axisId || axisId === prefix) return prefix + "axis";
    return prefix + "axis" + axisId.slice(1);
  }

  function asTime(value) {
    if (typeof value === "number") return value;
    var parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : NaN;
  }

  function attachDynamicPriceScale(graph) {
    if (!graph || typeof graph.on !== "function" || graph.__dynamicPriceScale) return;
    graph.__dynamicPriceScale = true;
    var applying = false;
    var queued = false;

    function rescaleVisiblePrice() {
      queued = false;
      if (applying || !graph._fullLayout || !graph._fullData) return;
      var groups = {};
      graph._fullData.forEach(function (trace) {
        if (!trace.meta || trace.meta.dynamicPrice !== true) return;
        var xKey = axisLayoutKey(trace.xaxis, "x");
        var yKey = axisLayoutKey(trace.yaxis, "y");
        var xAxis = graph._fullLayout[xKey];
        if (!xAxis || !xAxis.range || xAxis.range.length !== 2) return;
        var start = asTime(xAxis.range[0]);
        var end = asTime(xAxis.range[1]);
        if (!Number.isFinite(start) || !Number.isFinite(end)) return;
        var lower = Math.min(start, end);
        var upper = Math.max(start, end);
        var bucket = groups[yKey] || (groups[yKey] = []);
        var xValues = trace.x || [];
        var yValues = trace.y || [];
        for (var index = 0; index < yValues.length; index += 1) {
          var timestamp = asTime(xValues[index]);
          var price = Number(yValues[index]);
          if (timestamp >= lower && timestamp <= upper && Number.isFinite(price)) {
            bucket.push(price);
          }
        }
      });

      var update = {};
      Object.keys(groups).forEach(function (yKey) {
        var prices = groups[yKey];
        if (!prices.length) return;
        var minimum = Math.min.apply(null, prices);
        var maximum = Math.max.apply(null, prices);
        var center = (minimum + maximum) / 2;
        var rawSpan = maximum - minimum;
        var protectedSpan = Math.max(rawSpan * 1.16, Math.abs(center) * 0.05, 1);
        update[yKey + ".autorange"] = false;
        update[yKey + ".range"] = [center - protectedSpan / 2, center + protectedSpan / 2];
      });
      if (!Object.keys(update).length) return;
      applying = true;
      Plotly.relayout(graph, update).finally(function () { applying = false; });
    }

    function queueRescale() {
      if (applying || queued) return;
      queued = true;
      window.requestAnimationFrame(rescaleVisiblePrice);
    }

    graph.on("plotly_relayout", queueRescale);
    window.setTimeout(queueRescale, 0);
  }

  document.querySelectorAll(".plotly-graph-div").forEach(attachDynamicPriceScale);
})();
"""


def _read_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def _fmt(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not np.isfinite(number) else f"{number:.{digits}f}"


def _pct(value: object, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not np.isfinite(number) else f"{number * 100:.{digits}f}%"


def _yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def _pill(text: str, kind: str) -> str:
    return f"<span class='pill {kind}'>{escape(text)}</span>"


def _range_selector() -> dict[str, Any]:
    return {
        "buttons": [
            {"count": 1, "label": "1月", "step": "month", "stepmode": "backward"},
            {"count": 3, "label": "3月", "step": "month", "stepmode": "backward"},
            {"count": 6, "label": "6月", "step": "month", "stepmode": "backward"},
            {"count": 1, "label": "今年", "step": "year", "stepmode": "todate"},
            {"count": 1, "label": "1年", "step": "year", "stepmode": "backward"},
            {"count": 3, "label": "3年", "step": "year", "stepmode": "backward"},
            {"step": "all", "label": "全部"},
        ],
        "x": 0,
        "y": 1.12,
        "xanchor": "left",
        "yanchor": "bottom",
        "bgcolor": "rgba(248,250,252,0.96)",
        "activecolor": "#DBEAFE",
        "bordercolor": "#CBD5E1",
        "borderwidth": 1,
        "font": {"size": 11, "color": "#334155"},
    }


def _style_date_figure(
    fig: go.Figure,
    dates: pd.Series,
    *,
    rows: int,
    height: int,
    default_visible_days: int = 756,
) -> go.Figure:
    clean_dates = pd.to_datetime(dates, errors="coerce").dropna().sort_values()
    if clean_dates.empty:
        return fig
    visible_start = clean_dates.iloc[max(0, len(clean_dates) - default_visible_days)]
    visible_end = clean_dates.iloc[-1]
    fig.update_layout(
        height=height,
        margin={"l": 82, "r": 82, "t": 96, "b": 76},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"family": FONT_FAMILY, "size": 12, "color": "#334155"},
        hoverlabel={"font": {"family": FONT_FAMILY, "size": 12}},
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.01,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 11},
            "bgcolor": "rgba(255,255,255,0.84)",
        },
        dragmode="zoom",
        uirevision="index-cycle-dashboard",
    )
    fig.update_xaxes(
        type="date",
        range=[visible_start, visible_end],
        showgrid=True,
        gridcolor="#EEF2F6",
        zeroline=False,
        showline=True,
        linecolor="#D0D5DD",
        tickformat="%Y-%m",
        hoverformat="%Y-%m-%d",
        nticks=9,
        tickfont={"size": 11, "color": "#667085"},
        title_font={"size": 12, "color": "#475467"},
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#EEF2F6",
        zerolinecolor="#CBD5E1",
        showline=True,
        linecolor="#D0D5DD",
        tickfont={"size": 11, "color": "#667085"},
        title_font={"size": 12, "color": "#344054"},
        automargin=True,
    )
    fig.update_xaxes(rangeselector=_range_selector(), row=1, col=1)
    for row in range(1, rows):
        fig.update_xaxes(rangeslider={"visible": False}, row=row, col=1)
    fig.update_xaxes(
        rangeslider={
            "visible": True,
            "thickness": 0.08,
            "bgcolor": "#F8FAFC",
            "bordercolor": "#CBD5E1",
            "borderwidth": 1,
        },
        row=rows,
        col=1,
    )
    return fig


def _fig_html(fig: go.Figure) -> str:
    return pio.to_html(
        fig,
        full_html=False,
        include_plotlyjs=False,
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )


def _spectral_chart(summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    daily = _read_csv(summary["spectral"]["outputs"]["daily"])
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"])
    if daily.empty:
        return "<div class='chart-error'>频谱结果为空。</div>", {}

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.095,
        row_heights=[0.52, 0.48],
        specs=[[{}], [{"secondary_y": True}]],
    )
    base = daily.loc[daily["band"].eq("short")].sort_values("date")
    fig.add_trace(
        go.Scatter(
            x=base["date"],
            y=base["close"],
            name="收盘指数",
            mode="lines",
            line={"color": COLORS["black"], "width": 1.1},
            meta={"dynamicPrice": True},
            hovertemplate="%{x|%Y-%m-%d}<br>收盘=%{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=base["date"],
            y=base["causal_hp_trend"],
            name="HP 趋势",
            mode="lines",
            line={"color": COLORS["red"], "width": 1.6},
            hovertemplate="%{x|%Y-%m-%d}<br>趋势=%{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=base["date"],
            y=base["close"],
            name="收盘指数（右轴）",
            mode="lines",
            line={"color": "#8491A5", "width": 1.45},
            opacity=0.72,
            meta={"dynamicPrice": True},
            hovertemplate="%{x|%Y-%m-%d}<br>收盘=%{y:.2f}<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=True,
    )

    signal_latest: dict[str, Any] = {}
    for band in ("short", "medium", "long"):
        part = daily.loc[daily["band"].eq(band)].sort_values("date")
        if part.empty:
            continue
        label = str(part["band_label"].iloc[-1])
        legend_label = {
            "short": "短周期 20–60日",
            "medium": "中周期 60–120日",
            "long": "长周期 120–252日",
        }[band]
        fig.add_trace(
            go.Scatter(
                x=part["date"],
                y=part["cycle_component_pct"],
                name=legend_label,
                mode="lines",
                line={"color": BAND_COLORS[band], "width": 1.45},
                hovertemplate="%{x|%Y-%m-%d}<br>周期分量=%{y:.2f}%<extra></extra>",
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
        latest = part.iloc[-1]
        signal_latest[band] = {
            "label": label,
            "component_pct": latest.get("cycle_component_pct"),
            "period_days": latest.get("tracked_period_days"),
        }

    fig.add_hline(y=0, line={"color": "#98A2B3", "width": 1, "dash": "dot"}, row=2, col=1)
    fig.update_yaxes(title_text="指数点位", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(
        title_text="周期偏离（%）",
        title_font={"color": COLORS["purple"]},
        tickfont={"color": COLORS["purple"]},
        tickformat=".1f",
        row=2,
        col=1,
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="收盘指数（灰）",
        title_font={"color": COLORS["slate"]},
        tickfont={"color": COLORS["slate"]},
        tickformat=",.0f",
        row=2,
        col=1,
        secondary_y=True,
        showgrid=False,
    )
    fig.update_xaxes(title_text="交易日期", row=2, col=1)
    _style_date_figure(fig, base["date"], rows=2, height=760)
    return _fig_html(fig), signal_latest


def _load_timing_daily(summary: dict[str, Any]) -> pd.DataFrame:
    daily = _read_csv(summary["timing"]["outputs"]["daily"])
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    return daily.dropna(subset=["date"]).sort_values("date")


def _timing_chart(summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    daily = _load_timing_daily(summary)
    if daily.empty:
        return "<div class='chart-error'>相位结果为空。</div>", {}

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.10,
        row_heights=[0.56, 0.44],
        specs=[[{}], [{}]],
    )
    fig.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["close"],
            name="收盘指数",
            mode="lines",
            line={"color": COLORS["black"], "width": 1.1},
            meta={"dynamicPrice": True},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=daily["date"], y=daily["trend_ma120"], name="MA120 趋势", mode="lines", line={"color": COLORS["red"], "width": 1.55}),
        row=1,
        col=1,
    )
    for event_name, label, symbol, color in (
        ("bottom_turn", "底部转向", "triangle-up", COLORS["green"]),
        ("top_turn", "顶部转向", "triangle-down", COLORS["red"]),
    ):
        events = daily.loc[daily["phase_event"].eq(event_name)]
        if not events.empty:
            fig.add_trace(
                go.Scatter(
                    x=events["date"],
                    y=events["close"],
                    name=label,
                    mode="markers",
                    marker={"symbol": symbol, "size": 9, "color": color, "line": {"color": "white", "width": 0.7}},
                ),
                row=1,
                col=1,
            )

    for column, name, color in (
        ("ma34_tracked_period_days", "MA34 识别周期", COLORS["blue"]),
        ("ma55_tracked_period_days", "MA55 识别周期", COLORS["orange"]),
        ("ma89_tracked_period_days", "MA89 识别周期", COLORS["teal"]),
    ):
        fig.add_trace(
            go.Scatter(
                x=daily["date"],
                y=daily[column],
                name=name,
                mode="lines",
                line={"color": color, "width": 1.25},
                opacity=0.86,
                hovertemplate="%{x|%Y-%m-%d}<br>识别周期=%{y:.1f}日<extra></extra>",
            ),
            row=2,
            col=1,
        )
    consensus_period = daily["consensus_period_days"].where(
        daily["ma_agreement_count"].ge(2)
    )
    fig.add_trace(
        go.Scatter(
            x=daily["date"],
            y=consensus_period,
            name="共识周期（至少两条）",
            mode="lines",
            connectgaps=False,
            line={"color": COLORS["purple"], "width": 2.2},
            hovertemplate="%{x|%Y-%m-%d}<br>共识周期=%{y:.1f}日<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.update_yaxes(title_text="指数点位", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(
        title_text="识别周期（交易日）",
        range=[18, 62],
        tickvals=[20, 30, 40, 50, 60],
        row=2,
        col=1,
    )
    fig.update_xaxes(title_text="交易日期", row=2, col=1)
    _style_date_figure(fig, daily["date"], rows=2, height=720)
    fig.update_layout(margin={"l": 82, "r": 82, "t": 134, "b": 76})
    timing_selector = _range_selector()
    timing_selector["y"] = 1.17
    fig.update_xaxes(rangeselector=timing_selector, row=1, col=1)

    latest = daily.iloc[-1]
    diagnostics = {
        "boundary": bool(latest.get("consensus_boundary_peak", False)),
        "stable_hits": int(latest.get("consensus_stability_hits_15", 0)),
        "strength": float(latest.get("consensus_strength_ratio", np.nan)),
        "harmonic_r2": float(latest.get("harmonic_r2", np.nan)),
    }
    return _fig_html(fig), diagnostics


def _timing_phase_chart(
    daily: pd.DataFrame,
    *,
    phase_column: str,
    trace_name: str,
    color: str,
    ready_only: bool = False,
) -> str:
    if phase_column not in daily.columns:
        return "<div class='chart-error'>该层级的周期相位数据不存在。</div>"
    phase = daily[phase_column]
    if ready_only:
        phase = phase.where(_as_bool(daily["cycle_ready"]))

    fig = make_subplots(
        rows=1,
        cols=1,
        specs=[[{"secondary_y": True}]],
    )
    fig.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["close"],
            name="收盘指数（右轴）",
            mode="lines",
            line={"color": "#8491A5", "width": 1.45},
            opacity=0.70,
            meta={"dynamicPrice": True},
            hovertemplate="%{x|%Y-%m-%d}<br>收盘=%{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=daily["date"],
            y=phase,
            name=trace_name,
            mode="lines",
            connectgaps=False,
            line={"color": color, "width": 2.0 if ready_only else 1.65},
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>{trace_name}=%{{y:.3f}}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_hrect(
        y0=0.4,
        y1=1.05,
        fillcolor=COLORS["red"],
        opacity=0.035,
        line_width=0,
        layer="below",
        row=1,
        col=1,
    )
    fig.add_hrect(
        y0=-1.05,
        y1=-0.4,
        fillcolor=COLORS["green"],
        opacity=0.035,
        line_width=0,
        layer="below",
        row=1,
        col=1,
    )
    for level, line_color, dash in (
        (0.6, COLORS["red"], "dot"),
        (0.4, COLORS["red"], "dash"),
        (-0.4, COLORS["green"], "dash"),
        (-0.6, COLORS["green"], "dot"),
    ):
        fig.add_hline(
            y=level,
            line={"color": line_color, "width": 1.0, "dash": dash},
            row=1,
            col=1,
        )
    fig.update_yaxes(
        title_text="周期相位",
        title_font={"color": color},
        tickfont={"color": color},
        range=[-1.05, 1.05],
        tickvals=[-1, -0.5, 0, 0.5, 1],
        ticktext=["−1 谷", "−0.5", "0", "+0.5", "+1 峰"],
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="收盘指数（灰）",
        title_font={"color": COLORS["slate"]},
        tickfont={"color": COLORS["slate"]},
        tickformat=",.0f",
        row=1,
        col=1,
        secondary_y=True,
        showgrid=False,
    )
    fig.update_xaxes(title_text="交易日期", row=1, col=1)
    _style_date_figure(fig, daily["date"], rows=1, height=500)
    fig.update_layout(margin={"l": 78, "r": 78, "t": 112, "b": 72})
    return _fig_html(fig)


def _timing_phase_charts(summary: dict[str, Any]) -> tuple[str, str, str, str]:
    daily = _load_timing_daily(summary)
    if daily.empty:
        error = "<div class='chart-error'>相位结果为空。</div>"
        return error, error, error, error
    return (
        _timing_phase_chart(
            daily,
            phase_column="cycle_score",
            trace_name="可用共识相位",
            color=COLORS["purple"],
            ready_only=True,
        ),
        _timing_phase_chart(
            daily,
            phase_column="ma34_cycle_score",
            trace_name="MA34 独立相位",
            color=COLORS["blue"],
        ),
        _timing_phase_chart(
            daily,
            phase_column="ma55_cycle_score",
            trace_name="MA55 独立相位",
            color=COLORS["orange"],
        ),
        _timing_phase_chart(
            daily,
            phase_column="ma89_cycle_score",
            trace_name="MA89 独立相位",
            color=COLORS["teal"],
        ),
    )


def _load_price(summary: dict[str, Any]) -> pd.DataFrame:
    price = _read_csv(summary["data"]["output"])
    date_column = "日期" if "日期" in price.columns else "date"
    close_column = "收盘价" if "收盘价" in price.columns else "close"
    price = price.rename(columns={date_column: "date", close_column: "close"})[["date", "close"]]
    price["date"] = pd.to_datetime(price["date"], errors="coerce")
    return price.dropna(subset=["date"]).sort_values("date")


def _lppls_profile_chart(
    daily: pd.DataFrame,
    price: pd.DataFrame,
    *,
    profile: str,
) -> str:
    part = daily.loc[daily["profile"].eq(profile)].copy()
    if part.empty:
        return "<div class='chart-error'>该层级的 LPPLS 结果为空。</div>"

    fig = make_subplots(
        rows=1,
        cols=1,
        specs=[[{"secondary_y": True}]],
    )
    fig.add_trace(
        go.Scatter(
            x=price["date"],
            y=price["close"],
            name="收盘指数（右轴）",
            mode="lines",
            line={"color": "#8491A5", "width": 1.5},
            opacity=0.76,
            meta={"dynamicPrice": True},
            hovertemplate="%{x|%Y-%m-%d}<br>收盘=%{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=True,
    )
    for column, name, color, fillcolor in (
        ("positive_confidence", "正泡沫指数", COLORS["red"], "rgba(214,39,40,0.10)"),
        ("negative_confidence", "负泡沫指数", COLORS["green"], "rgba(22,155,98,0.10)"),
    ):
        fig.add_trace(
            go.Scatter(
                x=part["date"],
                y=part[column],
                name=name,
                mode="lines",
                line={"color": color, "width": 1.65},
                fill="tozeroy",
                fillcolor=fillcolor,
                hovertemplate="%{x|%Y-%m-%d}<br>泡沫指数=%{y:.1%}<extra></extra>",
            ),
            row=1,
            col=1,
            secondary_y=False,
        )
    fig.update_yaxes(
        title_text="泡沫指数",
        title_font={"color": "#344054"},
        tickfont={"color": "#344054"},
        tickformat=".0%",
        range=[-0.02, 1.02],
        tickvals=[0, 0.25, 0.5, 0.75, 1],
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="收盘指数（灰）",
        title_font={"color": COLORS["slate"]},
        tickfont={"color": COLORS["slate"]},
        tickformat=",.0f",
        row=1,
        col=1,
        secondary_y=True,
        showgrid=False,
    )
    fig.update_xaxes(title_text="交易日期", row=1, col=1)
    _style_date_figure(
        fig,
        part["date"],
        rows=1,
        height=500,
        default_visible_days=len(part),
    )
    fig.update_layout(margin={"l": 78, "r": 78, "t": 112, "b": 72})
    return _fig_html(fig)


def _lppls_charts(summary: dict[str, Any]) -> tuple[str, str]:
    daily = _read_csv(summary["lppls"]["outputs"]["summary_csv"])
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"]).sort_values("date")
    if daily.empty:
        error = "<div class='chart-error'>LPPLS 结果为空。</div>"
        return error, error
    price = _load_price(summary)
    return (
        _lppls_profile_chart(daily, price, profile="short"),
        _lppls_profile_chart(daily, price, profile="macro"),
    )


def _spectral_signal_table(summary: dict[str, Any], signals: dict[str, Any]) -> str:
    rows = []
    for key in ("short", "medium", "long"):
        item = signals.get(key, {})
        rows.append(
            f"<tr><td>{escape(str(item.get('label', key)))}</td>"
            f"<td>{_fmt(item.get('period_days'), 1)} 日</td>"
            f"<td>{_fmt(item.get('component_pct'), 2)}%</td></tr>"
        )
    return "".join(rows)


def _spectral_robustness(summary: dict[str, Any]) -> str:
    spectral = summary.get("spectral")
    if not spectral:
        return "本次未运行频谱模块。"
    daily = _read_csv(spectral["outputs"]["daily"])
    parts = []
    for band in ("short", "medium", "long"):
        item = spectral["band_summary"][band]
        latest = item["latest"]
        band_daily = daily.loc[daily["band"].eq(band)]
        r2 = band_daily.iloc[-1].get("harmonic_r2") if not band_daily.empty else np.nan
        parts.append(
            f"{escape(item['label'])}：p={_fmt(latest['pvalue'], 4)}，"
            f"稳定有效={_yes_no(latest['stable_valid'])}，谐波 R²={_fmt(r2, 3)}"
        )
    sensitivity_text = ""
    sensitivity_path = spectral.get("outputs", {}).get("sensitivity")
    if sensitivity_path and Path(sensitivity_path).exists():
        sensitivity = _read_csv(sensitivity_path)
        long_rows = sensitivity.loc[
            sensitivity["band"].eq("long") & sensitivity["hp_cutoff_days"].isin([180.0, 360.0])
        ]
        details = []
        for row in long_rows.itertuples(index=False):
            details.append(
                f"HP {row.hp_cutoff_days:.0f} 日时周期中位偏差 "
                f"{row.median_abs_period_diff_vs_main_pct:.2f}%，稳定日 Jaccard "
                f"{row.stable_jaccard_vs_main:.2f}"
            )
        if details:
            sensitivity_text = "；敏感性检查：" + "，".join(details)
    return "；".join(parts) + sensitivity_text + "。"


def _lppls_recent_sequence(summary: dict[str, Any]) -> str:
    path = summary.get("lppls", {}).get("outputs", {}).get("summary_csv")
    if not path or not Path(path).exists():
        return "—"
    daily = _read_csv(path)
    macro = daily.loc[daily["profile"].eq("macro")].sort_values("date")
    if macro.empty:
        return "—"
    values = _as_bool(macro.tail(3)["macro_positive_risk_watch"]).tolist()
    return " / ".join("是" if value else "否" for value in values)


def _robustness_html(summary: dict[str, Any], timing_diag: dict[str, Any]) -> str:
    timing = summary.get("timing")
    lppls = summary.get("lppls")
    timing_text = "本次未运行相位模块。"
    if timing:
        latest = timing["result"]["latest"]
        timing_text = (
            f"当前周期可用={_yes_no(latest['cycle_ready'])}；共识周期是否触及频带边界="
            f"{_yes_no(timing_diag.get('boundary'))}；近 15 日稳定命中="
            f"{timing_diag.get('stable_hits', 0)}（门槛 12）；频谱强度比="
            f"{_fmt(timing_diag.get('strength'), 3)}（门槛 1）；谐波 R²="
            f"{_fmt(timing_diag.get('harmonic_r2'), 3)}（门槛 0.1）。"
        )
    lppls_text = "本次未运行 LPPLS 模块。"
    if lppls:
        short = lppls["latest"].get("short", {})
        macro = lppls["latest"].get("macro", {})
        lppls_text = (
            f"短层正/负有效拟合={int(short.get('positive_fits', 0))}/"
            f"{int(short.get('negative_fits', 0))}；大周期正泡沫有效拟合="
            f"{int(macro.get('positive_fits', 0))}/{int(macro.get('windows_tested', 0))}，"
            f"非边界={int(macro.get('interior_positive_fits', 0))}，稳定非边界="
            f"{int(macro.get('stable_interior_positive_fits', 0))}；最近端点顶部观察序列="
            f"{_lppls_recent_sequence(summary)}，连续 3 日确认="
            f"{_yes_no(lppls.get('macro_three_day_top_monitor'))}。"
        )
    return f"""
<div class="explain-item"><h3>周期分量（cycle_component_pct）指向什么</h3><p>表示某个周期频段重建后相对局部趋势的偏离：正值代表位于趋势线上方，负值代表位于趋势线下方。它是当前周期位置的描述，不是未来收益率预测。频谱稳健性结果：{_spectral_robustness(summary)}</p></div>
<div class="explain-item"><h3>周期位置（cycle_score）指向什么</h3><p>把谐波相位压缩到约 −1 至 +1：接近 −1 表示周期谷附近，接近 +1 表示周期峰附近。汇总图只画 cycle_ready 为真的可用共识相位；MA34、MA55、MA89 三张独立图保留各自可计算相位，便于查看未形成共识时的差异。相位稳健性结果：{timing_text}</p></div>
<div class="explain-item"><h3>LPPLS 风险温度指向什么</h3><p>正泡沫置信度衡量加速上涨结构在不同窗口中的覆盖比例，主要指向顶部风险升温；负泡沫置信度衡量加速下跌结构，主要指向底部观察温度。临界时间 tc 不是精确反转日。LPPLS 稳健性结果：{lppls_text}</p></div>
<div class="explain-item"><h3>如何合并理解</h3><p>三类信号回答的问题不同：频谱看“价格围绕趋势的周期摆动”，MA 相位看“可用周期当前靠近峰还是谷”，LPPLS 看“价格是否进入加速且可能不可持续的结构”。它们不能简单投票，也不直接等于买卖信号；实际执行还需要趋势、市场宽度、成交量、流动性、交易工具与成本验证。</p></div>
"""


def build_interactive_report(summary: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    data = summary["data"]
    spectral = summary.get("spectral")
    timing = summary.get("timing")
    lppls = summary.get("lppls")

    if spectral:
        spectral_html, spectral_signals = _spectral_chart(summary)
    else:
        spectral_html, spectral_signals = "<div class='chart-error'>本次未运行频谱模块。</div>", {}
    if timing:
        timing_html, timing_diag = _timing_chart(summary)
        (
            timing_consensus_html,
            timing_ma34_html,
            timing_ma55_html,
            timing_ma89_html,
        ) = _timing_phase_charts(summary)
    else:
        timing_error = "<div class='chart-error'>本次未运行相位模块。</div>"
        timing_html, timing_diag = timing_error, {}
        timing_consensus_html = timing_error
        timing_ma34_html = timing_error
        timing_ma55_html = timing_error
        timing_ma89_html = timing_error
    if lppls:
        lppls_short_html, lppls_macro_html = _lppls_charts(summary)
    else:
        lppls_error = "<div class='chart-error'>本次未运行 LPPLS 模块。</div>"
        lppls_short_html, lppls_macro_html = lppls_error, lppls_error

    generated = summary.get("generated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    spectral_table = _spectral_signal_table(summary, spectral_signals) if spectral else ""
    robustness_html = _robustness_html(summary, timing_diag)

    css = """
*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f0f2f5;color:#333;line-height:1.65}.header{background:linear-gradient(120deg,#0b132b,#1c2541 56%,#3a506b);color:#fff;padding:38px 20px;text-align:left}.header h1,.header .date{max-width:1400px;margin-left:auto;margin-right:auto}.header h1{font-size:30px;margin-bottom:8px;letter-spacing:.02em}.header .date{font-size:14px;opacity:.82}.container{max-width:1440px;margin:0 auto;padding:20px}.disclaimer{background:#fff8f0;border:1px solid #f0d8b0;border-radius:10px;padding:15px 18px;margin-bottom:20px;font-size:12.5px;color:#8b6914}.overview-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:22px}.overview-card{background:#fff;border-radius:12px;padding:18px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-top:4px solid #d0d5dd;min-height:180px;display:flex;flex-direction:column;gap:10px}.overview-card.data{border-top-color:#2563eb}.overview-card.spectral{border-top-color:#7B2CBF}.overview-card.timing{border-top-color:#0f766e}.overview-card.lppls{border-top-color:#d62728}.overview-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.overview-title h2{font-size:17px;color:#101828}.badge{border-radius:999px;padding:4px 10px;color:#fff;font-size:12px;font-weight:800;white-space:nowrap}.data .badge{background:#2563eb}.spectral .badge{background:#7B2CBF}.timing .badge{background:#0f766e}.lppls .badge{background:#d62728}.metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:9px;text-align:center}.metric b{display:block;font-size:17px;color:#1f2937}.metric span{font-size:11px;color:#667085}.note{font-size:12.5px;color:#475467;line-height:1.65;margin-top:auto}.module-tabs{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 16px;padding:10px;background:#fff;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.06)}.tab-btn{border:1px solid #d9dde7;background:#f8fafc;color:#344054;border-radius:8px;padding:9px 16px;font-size:14px;font-weight:700;cursor:pointer}.tab-btn:hover{background:#eef2f7}.tab-btn.active{background:#263b5e;border-color:#263b5e;color:#fff}.panel{display:none}.panel.active{display:block}.card{background:#fff;border-radius:12px;padding:22px;margin-bottom:22px;box-shadow:0 2px 8px rgba(0,0,0,.08)}.card h2{font-size:20px;margin-bottom:11px;padding-bottom:9px;border-bottom:1px solid #e4e7ec;color:#172b4d}.intro{font-size:13px;color:#475467;background:#f8fafc;border-left:4px solid #98a2b3;border-radius:6px;padding:10px 13px;margin-bottom:13px}.lppls-chart-block{margin-top:18px}.lppls-chart-block:first-of-type{margin-top:4px}.lppls-chart-block h3{font-size:16px;color:#243b5a;margin:0 0 3px}.phase-group{margin-top:24px;padding-top:18px;border-top:1px solid #e4e7ec}.phase-group>h3{font-size:18px;color:#172b4d;margin-bottom:3px}.phase-chart-block{margin-top:20px}.phase-chart-block h4{font-size:15px;color:#243b5a;margin-bottom:3px}.chart-note{font-size:12px;color:#667085;margin-bottom:8px}.plot-panel{border:1px solid #e4e7ec;border-radius:8px;overflow:hidden;background:#fff}.plotly-wrap{width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}th,td{padding:8px 12px;text-align:center;border-bottom:1px solid #eee}th{background:#f8f9fa;color:#556070}.pill{display:inline-block;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:700}.pill.good{background:#e8f5e9;color:#137333}.pill.warn{background:#fff3e0;color:#b45309}.pill.muted{background:#f2f4f7;color:#667085}.footnote{font-size:11.5px;color:#667085;margin-top:12px}.chart-error{padding:50px;text-align:center;color:#667085}.explain-card{border-top:5px solid #344054}.explain-card>p{font-size:13px;color:#475467;margin-bottom:15px}.explain-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.explain-item{background:#f8fafc;border:1px solid #e5e7eb;border-radius:9px;padding:14px}.explain-item h3{font-size:14px;color:#1d3557;margin-bottom:6px}.explain-item p{font-size:12.5px;color:#475467;line-height:1.78}.footer{text-align:center;color:#98a2b3;font-size:12px;padding:8px 0 28px}@media(max-width:1050px){.overview-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.explain-grid{grid-template-columns:1fr}}@media(max-width:650px){.overview-grid{grid-template-columns:1fr}.header h1{font-size:23px}.container{padding:12px}.card{padding:14px}}
"""

    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(data['index_code'])} {escape(data['index_name'])}指数周期分析</title><style>{css}</style><script>{get_plotlyjs()}</script></head><body>
<header class="header"><h1>{escape(data['index_code'])} · {escape(data['index_name'])}指数周期信号看板</h1><div class="date">样本 {escape(data['date_start'])} 至 {escape(data['date_end'])} ｜ 生成于 {escape(str(generated))}</div></header>
<main class="container"><div class="disclaimer">本页面用于研究，不构成投资建议。主图区只展示收盘价及最终信号；稳健性检验不单独画图，统一在页面最后以文字解释。</div>
<nav class="module-tabs"><button class="tab-btn active" data-target="spectral-panel">频谱周期信号</button><button class="tab-btn" data-target="timing-panel">MA 共识相位</button><button class="tab-btn" data-target="lppls-panel">LPPLS 泡沫指数</button></nav>
<section id="spectral-panel" class="panel active"><div class="card"><h2>频谱周期：短、中、长三层价格偏离</h2><p class="intro">上半区展示收盘指数与单边 HP 趋势，下半区以左轴展示周期偏离（%）、右轴以灰色展示收盘指数。拖动日期范围后，收盘价坐标会按当前可见区间自动放大，并保留适度上下留白。</p><div class="plot-panel"><div class="plotly-wrap">{spectral_html}</div></div><table><thead><tr><th>频段</th><th>当前周期</th><th>最新周期分量</th></tr></thead><tbody>{spectral_table}</tbody></table></div></section>
<section id="timing-panel" class="panel"><div class="card"><h2>MA 共识相位：周期识别与四层相位</h2><p class="intro">第一张图只保留收盘指数、MA120、转向事件以及 MA34、MA55、MA89 的识别周期。周期相位拆成四张独立图：汇总共识、MA34、MA55、MA89，不再把不同判断叠在同一个坐标区。所有图都可独立拖动日期范围，收盘价坐标会同步放大可见区间波动。</p><div class="plot-panel"><div class="plotly-wrap">{timing_html}</div></div><div class="phase-group"><h3>周期相位：汇总与三个独立层级</h3><p class="chart-note">四张图使用相同的峰谷刻度和阈值，方便纵向比较；浅红、浅绿背景只提示峰区与谷区。</p><div class="phase-chart-block"><h4>1. 汇总共识相位</h4><p class="chart-note">只显示通过稳定性和可用性门槛的最终共识相位。</p><div class="plot-panel"><div class="plotly-wrap">{timing_consensus_html}</div></div></div><div class="phase-chart-block"><h4>2. MA34 独立相位</h4><p class="chart-note">短均线层对较快节奏更敏感，使用蓝线。</p><div class="plot-panel"><div class="plotly-wrap">{timing_ma34_html}</div></div></div><div class="phase-chart-block"><h4>3. MA55 独立相位</h4><p class="chart-note">中间均线层使用橙线，单独展示其周期位置。</p><div class="plot-panel"><div class="plotly-wrap">{timing_ma55_html}</div></div></div><div class="phase-chart-block"><h4>4. MA89 独立相位</h4><p class="chart-note">较长均线层使用青线，不与另外两条独立相位叠加。</p><div class="plot-panel"><div class="plotly-wrap">{timing_ma89_html}</div></div></div></div><p class="footnote">相位事件形成于当日收盘；历史未来收益只用于事后研究，不进入当日指标。</p></div></section>
<section id="lppls-panel" class="panel"><div class="card"><h2>LPPLS：全历史正泡沫与负泡沫指数</h2><p class="intro">短周期层和大周期层分成两张独立图。红线与浅红阴影为正泡沫指数，绿线与浅绿阴影为负泡沫指数，右轴灰线为收盘指数；全部采用连续线，不显示采样点。每张图都可独立拖动范围，收盘价右轴会根据可见区间自动放大。</p><div class="lppls-chart-block"><h3>短周期层（40–160 日窗口）</h3><p class="chart-note">观察较快的泡沫升温与降温，阴影保持高透明度。</p><div class="plot-panel"><div class="plotly-wrap">{lppls_short_html}</div></div></div><div class="lppls-chart-block"><h3>大周期层（120–500 日窗口）</h3><p class="chart-note">观察更慢、更结构性的泡沫状态，时间范围与短周期图互不联动。</p><div class="plot-panel"><div class="plotly-wrap">{lppls_macro_html}</div></div></div><p class="footnote">LPPLS 的 tc 不是精确反转日期。正泡沫只作风险预警，负泡沫也需要趋势等外部证据确认。</p></div></section>
<section class="card explain-card"><h2>指标含义与稳健性说明</h2><p>以下内容专门解释每个最终指标指向什么，以及本次稳健性检验是否支持它；稳健性结果不再单独绘图。</p><div class="explain-grid">{robustness_html}</div></section>
<div class="footer">自包含离线网页 ｜ 数据源：中证指数官网公开接口 ｜ 周期单位：交易日</div></main>
<script>document.querySelectorAll('.tab-btn').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));btn.classList.add('active');document.getElementById(btn.dataset.target).classList.add('active');window.dispatchEvent(new Event('resize'));}});}});</script><script>{DYNAMIC_PRICE_SCRIPT}</script></body></html>"""
    output.write_text(html, encoding="utf-8")
    return output
