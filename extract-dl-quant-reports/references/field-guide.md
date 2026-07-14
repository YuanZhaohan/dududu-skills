# Field guide

## Contents

1. Claim object and allowed values
2. Mandatory sections and labels
3. Evidence rules
4. Figures

Use this vocabulary to make reports comparable. Every entry in `executive_summary` and every section is a claim object:

```json
{
  "id": "target-001",
  "label": "预测目标 y",
  "value": "未来 5 个交易日个股超额收益",
  "details": "相对中证 500；按截面去极值后训练",
  "confidence": "high",
  "value_origin": "reported",
  "evidence": [
    {
      "pdf_page": 12,
      "printed_page": "10",
      "section": "3.2 标签构造",
      "figure_table": null,
      "quote": "...",
      "ocr_quality": "high"
    }
  ]
}
```

Allowed confidence values: `high`, `medium`, `low`, `missing`.

Allowed `value_origin` values:

- `reported`: directly stated in text/table/formula.
- `derived`: mechanically derived from explicit source values; explain the calculation.
- `chart_estimate`: visually estimated from a chart; include `图表估算` in `details`.
- `not_stated`: absent from the report; use `confidence: missing` and no evidence.

## Mandatory sections and labels

### Research design

- `problem_definition`: research question, prediction task, economic rationale.
- `core_method`: main contribution, pipeline, learning paradigm.
- `model_architecture`: model family, layers/modules, inputs/outputs, aggregation, hyperparameters.
- `datasets`: source, market, asset universe, date coverage, sample construction, frequency.
- `features`: feature name/group, lookback window, construction time, availability timing.
- `targets`: exact `y`, horizon, benchmark adjustment, cross-sectional/time-series construction, label timing.
- `losses`: exact formula, optimization direction, weighting, regularization, auxiliary objectives.
- `standardization`: winsorization, missing values, normalization axis, fit window, leakage protection.
- `training_settings`: train/validation/test windows, rolling/expanding scheme, optimizer, learning rate, batch size, epochs, early stopping, seeds, retraining frequency.
- `experimental_conclusions`: conclusions supported by stated results.
- `limitations`: author-stated and evidence-backed limitations. Clearly distinguish analyst inference.

### Comparisons

- `baselines`: baseline definitions and matching evaluation window.
- `ablations`: removed component and measured effect.
- `model_comparisons`: model-to-model comparison under the same data, window, and metric.

### Backtest

- `backtest_design`: backtest start/end, rolling windows, universe, benchmark, rebalance frequency, portfolio construction, weighting, long/short rules, constraints, transaction costs, slippage, suspension/limit handling.
- `backtest_performance`: annualized return, excess return, Sharpe, maximum drawdown, volatility, win rate, IC, RankIC, ICIR, turnover, group returns, long-short spread, subperiod and robustness results. Preserve units and exact evaluation window.

### Validity audit

Create one claim for each item, using value `已说明`, `未说明`, or `存疑`:

1. Feature/target/trade-time alignment and future leakage.
2. Standardization fitted only with contemporaneously available data.
3. Survivorship bias and point-in-time universe/data handling.
4. Train/validation/test/backtest overlap.
5. Transaction costs, slippage, suspension, and price-limit rules.
6. Repeated tuning, multiple testing, and overfitting risk.

Do not accuse the authors of an error without direct evidence. Explain why an item is `存疑`.

## Evidence rules

- `pdf_page` is 1-based and refers to the physical PDF page.
- `printed_page` is the page number printed in the report; use `null` when unavailable.
- Keep quotes short but sufficient to support the claim.
- Use multiple evidence entries when a fact depends on a definition and a result on different pages.
- For formulas that OCR cannot reconstruct, add a figure entry pointing to the extracted formula image and mark the claim `low` until reviewed.

## Figures

Each figure entry uses:

```json
{
  "id": "figure-001",
  "kind": "architecture|formula|table|backtest_curve|other",
  "title": "",
  "asset_path": "assets/<document-id>/<file>",
  "pdf_page": 1,
  "printed_page": null,
  "figure_table": "图 3",
  "source_caption": "",
  "interpretation": "",
  "confidence": "medium"
}
```

Only reference files that exist. An interpretation must be clearly separate from the source caption.
