# Prompt templates

## Contents

1. Shared rules
2. Phase 0: OCR and page-map inspection
3. Phase 1: chunk extraction
4. Phase 2: merge and conflict resolution
5. Phase 3: validity audit
6. Phase 4: final JSON assembly
7. Fast mode
8. Repair prompt

Use these prompts as task contracts. Replace bracketed variables. For weaker models, run each phase separately and never provide more than one OCR chunk at a time.

## Shared rules

Prepend this block to every semantic-extraction request:

```text
You extract facts from a deep-learning quantitative-finance report.

Hard rules:
1. Use only the supplied OCR content and supplied prior-phase artifacts.
2. Do not use background knowledge to fill missing report details.
3. Every substantive claim needs evidence with a 1-based physical PDF page.
4. Keep the evidence quote in the source language and copy it exactly from OCR except for obvious whitespace normalization.
5. If the report does not state a mandatory fact, output value "未说明", confidence "missing", value_origin "not_stated", and evidence [].
6. Keep reported values, mechanically derived values, and chart estimates separate. Mark chart estimates with value_origin "chart_estimate" and write "图表估算" in details.
7. If two passages conflict, preserve both and describe the conflict. Do not choose silently.
8. Distinguish PDF physical page from printed report page.
9. Return only the requested JSON or JSONL. Do not wrap it in Markdown fences.
10. Before answering, check every quote, page number, unit, window, and field label against the supplied content.
```

## Phase 0: OCR and page-map inspection

Use once per report. Supply the OCR manifest and page headings or all chunk headers, not necessarily the entire report.

```text
[SHARED RULES]

Task: inspect OCR coverage and produce a navigation map. Do not extract research conclusions yet.

Document id: [DOCUMENT_ID]
OCR mode: [OCR_MODE]
OCR pages/chunk headers:
[PAGE_OR_CHUNK_HEADERS]

Return one JSON object:
{
  "document_id": "...",
  "source_language": "zh|en|mixed|unknown",
  "ocr_quality": "high|medium|low",
  "page_count_observed": 0,
  "missing_or_suspect_pages": [{"pdf_page": 0, "reason": "..."}],
  "printed_page_mapping": [{"pdf_page": 0, "printed_page": "..."}],
  "section_map": [{"title": "...", "pdf_pages": [1, 2], "likely_fields": ["targets", "losses"]}],
  "formula_or_chart_pages": [{"pdf_page": 0, "kind": "formula|architecture|table|backtest_curve|other", "reason": "..."}],
  "notes": []
}

Checks:
- Cover, contents, appendix, references, duplicated headers, and blank pages.
- OCR ordering problems in multi-column pages.
- Formula corruption, broken tables, and pages represented only by images.
- Whether printed page numbers can be mapped reliably.
```

## Phase 1: chunk extraction

Run once for each chunk. If a chunk covers many unrelated sections, run it multiple times with a smaller `TARGET_SECTIONS` list.

```text
[SHARED RULES]

Task: extract atomic claims only from this OCR chunk.

Document id: [DOCUMENT_ID]
Chunk id: [CHUNK_ID]
Physical PDF pages covered: [PDF_PAGE_RANGE]
Target sections: [TARGET_SECTIONS]

Allowed section names:
problem_definition, core_method, model_architecture, datasets, features,
targets, losses, standardization, training_settings, experimental_conclusions,
limitations, baselines, ablations, model_comparisons, backtest_design,
backtest_performance.

OCR chunk:
[OCR_CHUNK]

Return JSONL, one object per line:
{
  "section": "allowed section name",
  "claim": {
    "id": "temporary-stable-id",
    "label": "specific field name",
    "value": "exact concise value with unit",
    "details": "definitions, conditions, windows, or caveats",
    "confidence": "high|medium|low|missing",
    "value_origin": "reported|derived|chart_estimate|not_stated",
    "evidence": [{
      "pdf_page": 1,
      "printed_page": null,
      "section": "source section title",
      "figure_table": null,
      "quote": "short exact OCR quote",
      "ocr_quality": "high|medium|low"
    }]
  }
}

Extraction checklist:
- For features, record construction/lookback and availability time, not just names.
- For target y, record horizon, formula, alignment, benchmark adjustment, and sampling time.
- For loss, preserve the formula and weighting/regularization.
- For standardization, record axis and fit window to assess leakage.
- For training, separate training, validation, test, and retraining windows.
- For backtests, bind every metric to its exact window, portfolio, benchmark, frequency, and cost assumption.
- Extract baselines, ablations, and model comparisons even when the result is negative.
- Do not emit a claim merely because a heading exists.
```

## Phase 2: merge and conflict resolution

Supply the Phase 1 JSONL artifacts, not the full OCR, unless checking a cited passage.

```text
[SHARED RULES]

Task: merge extracted claims without losing provenance.

Document id: [DOCUMENT_ID]
Candidate claims:
[CLAIMS_JSONL]

Return one JSON object with every mandatory section key:
{
  "sections": {
    "problem_definition": [],
    "core_method": [],
    "model_architecture": [],
    "datasets": [],
    "features": [],
    "targets": [],
    "losses": [],
    "standardization": [],
    "training_settings": [],
    "experimental_conclusions": [],
    "limitations": [],
    "baselines": [],
    "ablations": [],
    "model_comparisons": [],
    "backtest_design": [],
    "backtest_performance": []
  },
  "conflicts": [{"claim_ids": ["..."], "reason": "...", "resolution": "preserved|resolved-with-evidence"}],
  "missing_mandatory_fields": [{"section": "...", "label": "..."}]
}

Merge rules:
- Merge only when label, value, unit, definition, and applicable window agree.
- Combine evidence arrays and remove exact duplicate evidence.
- Keep results from different windows, universes, horizons, or portfolios as separate claims.
- Assign final ids as `<section-prefix>-NNN`.
- Add explicit `not_stated` claims for mandatory missing fields using the field guide.
```

## Phase 3: validity audit

Supply the merged sections and the most relevant source chunks.

```text
[SHARED RULES]

Task: audit the report's stated design. Do not claim author error without direct evidence.

Merged sections:
[MERGED_SECTIONS]

Relevant OCR excerpts:
[RELEVANT_OCR]

Return a JSON array with exactly six claim objects, one for each:
1. Feature/target/trade-time alignment and future leakage.
2. Standardization fit window and leakage protection.
3. Survivorship bias and point-in-time data/universe.
4. Train/validation/test/backtest overlap.
5. Costs, slippage, suspension, and price-limit handling.
6. Repeated tuning, multiple testing, and overfitting risk.

Each value must be `已说明`, `未说明`, or `存疑`. Use `存疑` only when evidence identifies a concrete ambiguity. Put cautious reasoning in details and retain supporting evidence.
```

## Phase 4: final JSON assembly

Use `assets/report-template.json` as the exact top-level shape.

```text
[SHARED RULES]

Task: assemble the final extraction JSON. Return one valid JSON object only.

Source provenance:
[SOURCE_METADATA]

Page map:
[PAGE_MAP_JSON]

Merged sections:
[MERGED_SECTIONS_JSON]

Validity audit:
[AUDIT_JSON]

Available extracted assets:
[ASSET_MANIFEST_JSON]

Requirements:
- Preserve schema_version, document_id, source file, SHA-256, OCR mode, extraction mode, and processing time supplied in source provenance.
- Create 5-10 executive-summary claims covering problem, target y, model, features, loss, train/backtest window, performance, and main limitation. Reuse evidence from detailed claims.
- Include every mandatory section key, even when empty is temporarily unavoidable.
- Include only figure entries whose asset_path exists in the asset manifest.
- Generate normalized topic strings for model, task, target horizon, dataset, market, asset class, frequency, and loss.
- Keep review_status `draft`.
- Add review_notes for every low/missing fact, OCR defect, conflict, formula screenshot, or chart estimate.
- Perform a final internal check, then emit JSON only.
```

## Fast mode

Use only for previews. Supply as much page-aware OCR as fits safely.

```text
[SHARED RULES]

Task: create a fast preview extraction using the exact shape in assets/report-template.json.

Source metadata:
[SOURCE_METADATA]

OCR content:
[OCR_CONTENT]

Prioritize problem, target y, model architecture, features, loss, standardization,
training windows, backtest windows, main performance, and limitations.
Keep extraction_mode `fast` and review_status `draft`.
Do not infer missing details. Add all uncertain or missing items to review_notes.
Return one JSON object only.
```

## Repair prompt

Use after the validator reports errors. Supply only the JSON and validation messages; consult OCR chunks only when fixing evidence.

```text
[SHARED RULES]

Task: repair the extraction JSON without changing supported facts.

Validation errors:
[VALIDATION_ERRORS]

Current JSON:
[CURRENT_JSON]

Relevant OCR for evidence fixes:
[OPTIONAL_OCR]

Return the complete repaired JSON only. Do not remove a fact merely to silence an evidence error; mark it missing or low confidence when the source cannot support it.
```

