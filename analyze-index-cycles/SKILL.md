---
name: analyze-index-cycles
description: 获取公开指数日收盘价并执行严格无前视的指数周期分析，包括单边 HP 去趋势、滚动 Hann FFT、AR(1) 红噪声检验、MA34/55/89 独立周期与共识相位、JLS/LPPLS 全历史正负泡沫指数和大周期拐点观察。用于分析 000985 中证全指或其他中证指数的当前周期长度、振幅、稳定性、相位状态与泡沫风险，并生成可拖动范围的中文网页、可审计数据和中文研究报告。
---

# 指数周期分析

把频谱周期、相位择时和 LPPLS 视为三个独立证据层。保持严格无前视，不把研究指标包装成确定的买卖结论。

## 执行流程

1. 执行公开数据抓取前阅读 [数据源说明](references/data-source.md)。默认从中证指数有限公司官网公开接口获取日线；保留来源地址、抓取时间和原始交易日。
2. 运行完整分析前确认 Python 环境具备 `numpy`、`pandas`、`scipy`、`matplotlib`、`joblib` 和 `plotly`。依赖清单位于 `scripts/requirements.txt`。
3. 默认运行完整流程：

```powershell
D:\anaconda\python.exe scripts/run_index_cycle_analysis.py --index-code 000985 --start-date 2004-12-31 --output-dir <输出目录>
```

4. 只分析用户提供的数据时传入 `--input`。输入必须包含 `日期/代码/指数名称/收盘价`，只允许单一指数，收盘价必须为正，日期不得重复：

```powershell
D:\anaconda\python.exe scripts/run_index_cycle_analysis.py --input <CSV路径> --output-dir <输出目录>
```

5. 只运行部分证据层时使用 `--sections spectral`、`--sections timing` 或 `--sections lppls`；允许同时指定多个值。
6. 解释参数、阈值或统计边界前阅读 [方法与口径](references/methodology.md)。不要根据最新结果临时调整阈值。

## 分步入口

只下载并校验官方收盘价：

```powershell
D:\anaconda\python.exe scripts/fetch_csindex_close.py --index-code 000985 --start-date 2004-12-31 --output <CSV路径>
```

只计算并缓存 LPPLS 短周期与大周期全历史指数：

```powershell
D:\anaconda\python.exe scripts/lppls_history.py --input <CSV路径> --output-dir <输出目录> --confirmation-days 3 --n-jobs -1
```

首次频谱运行默认使用每个 AR(1) 系数网格 1,000 个替代样本并生成缓存。只有调试时才降低 `--red-noise-surrogates`；正式报告恢复为 1,000。不要删除缓存来解决普通重跑问题。

LPPLS 首次全历史运行会逐日拟合大量窗口，耗时明显长于只算最新状态。程序按批次保存短周期与大周期缓存；后续新增交易日只续算新端点。不要手工拼接不同参数或不同指数的历史缓存。

## 检查输出

优先打开 `<输出目录>/index_cycle_dashboard.html`。网页主图只展示与收盘价直接对照的最终信号，并提供范围按钮、鼠标缩放和底部拖动范围条；p 值、稳定命中、边界拟合等稳健性结果不单独绘图，统一在网页最后用中文解释。再按需检查：

- `index_cycle_dashboard.html`：自包含交互网页，含频谱周期分量、MA 共识相位和 LPPLS 风险温度；拖动时间范围后，收盘价坐标轴按可见区间动态缩放并保留最小跨度保护。
- `index_cycle_report.md`：中文文本研究报告。
- `data/*_daily.csv` 与同名 `.metadata.json`：标准化日线和来源元数据。
- `spectral/all_a_cycle_daily.csv`：短、中、长三个频段的逐日周期、振幅、红噪声 p 值和稳定性。
- `spectral/all_a_cycle_sensitivity.csv`：180/252/360 日 HP 截止周期敏感性。
- `timing/cycle_timing_daily.csv`：MA34/55/89 独立识别周期与独立相位、共识周期与共识相位、趋势和事件。
- `lppls/lppls_history.csv`：短周期与大周期逐日正泡沫、负泡沫指数。
- `lppls/lppls_history_fit_details_*.csv`：每个历史端点、每个窗口的参数、边界和数值稳定性诊断。
- `analysis_summary.json`：全流程机器可读摘要。

检查以下条件后再汇报：

- 数据末日与用户期望一致；若当日尚未收盘，明确最新可用交易日。
- 日期升序、无重复、收盘价为正，周末和节假日未被填补。
- 频谱结论同时报告周期、峰谷幅度、p 值和 `stable_valid`，不能只报最强峰。
- MA34/55/89 未形成共识时，识别周期仍在概览图中分别画出；周期相位必须拆成四张互不叠加的图：可用共识相位、MA34 独立相位、MA55 独立相位、MA89 独立相位。只有在 `cycle_ready=True` 时才在汇总图中解释最终共识相位事件。
- LPPLS 原始置信度使用全部窗口作分母；边界拟合和稳定盆地只作为独立诊断层。
- LPPLS 网页必须使用全历史逐日指数，不得用最近三个端点代替全历史曲线；短周期层和大周期层分成两张独立图，正负泡沫指数只画连续线，不画采样点，并用高透明度面积阴影辅助辨识。
- 大周期底部观察必须满足连续 3 日确认；顶部侧只称为风险温度或风险预警。

## 汇报规则

先写数据来源、样本区间和最新交易日，再依次写频谱周期、MA 共识相位和 LPPLS 状态。明确区分“当前指标”“历史事件研究”“实际交易规则”。

必须保留以下边界：

- 周期单位是交易日，不是自然日。
- 红噪声 p 值是单日、单频段最高峰的局部经验检验，不是全历史多重检验。
- LPPLS 的临界时间 `tc` 不是精确反转日期。
- 正泡沫只作风险预警；负泡沫也要结合趋势、市场宽度、成交量、流动性与执行工具确认。
- 指数点位不可直接交易，研究报告不构成投资建议。
