---
name: extract-dl-quant-reports
description: Extract deep-learning quantitative-finance research reports from PDF into evidence-backed structured JSON and an Obsidian-compatible Markdown wiki. Use for Chinese or English reports about neural networks, financial prediction, factor modeling, portfolio construction, or backtests when the user needs OCR-first parsing, model/data/feature/target/loss/training extraction, backtest windows and performance, page-level citations, figures, validity checks, batch incremental processing, or wiki generation.
---

# Extract Deep-Learning Quant Reports

Turn PDF reports into a reviewable, traceable knowledge base. Keep semantic reasoning host-agnostic: the current agent performs extraction; Python scripts handle OCR, caching, chunking, validation, and deterministic Wiki rendering.

## Non-negotiable rules

- Ask for the input PDF file(s) or directory and the Wiki output directory before writing anything.
- Warn that PDFs are uploaded to the configured PaddleOCR service. Upload is allowed by default because the user chose this behavior.
- Read `PADDLEOCR_ACCESS_TOKEN` from the environment. Never request that the user paste a token into chat, print it, or store it in the skill/output.
- Use OCR first. If OCR fails after retries, allow local text fallback only for text-extractable PDFs and label the result `local_text_fallback`.
- Never invent missing facts. Write `未说明` / `Not stated`, set confidence to `missing`, and leave evidence empty.
- Attach evidence to every substantive claim. Prefer both physical PDF page and printed report page, plus section and figure/table number when available.
- Preserve evidence quotes in the source language. Summarize English reports in Chinese while retaining original technical names; keep Chinese reports in Chinese.
- Treat chart-derived numbers as estimates and label them `图表估算`. Never mix them with reported values.
- Generate drafts first. Do not mark a report `reviewed` without explicit human confirmation.

## Workflow

### 1. Confirm the job

Ask one concise question if either input or output is unknown. Confirm:

1. PDF path(s) or a directory.
2. Wiki output directory.
3. Mode: `thorough` by default; `fast` only for preview.
4. Whether to copy source PDFs into `sources/`; default is no.

Resolve the directory containing this `SKILL.md` as the skill directory. In OpenClaw, `{baseDir}` may be used. Do not hard-code a Codex or OpenClaw installation path.

### 2. Prepare OCR and prompt packets

Run:

```text
python <skill-dir>/scripts/report_pipeline.py prepare --input <pdf-or-directory> --output <wiki-dir> --mode thorough
```

Add `--copy-sources` only when requested. The command hashes PDFs, skips unchanged files, calls PaddleOCR, preserves raw JSON/Markdown/images, creates page-aware chunks, and writes an extraction template plus prompt packets under `_intermediate/<document-id>/`.

If the default endpoint changes, set `PADDLEOCR_DOC_PARSING_API_URL`. The built-in default is the user-approved AIStudio layout-parsing endpoint. Set `PADDLEOCR_REQUEST_FORMAT=multipart` or `json-base64` only when auto-detection is unsuitable.

### 3. Extract in bounded passes

Read [prompts.md](references/prompts.md) completely before semantic extraction. For `thorough` mode, follow its phases in order; do not collapse them into a single free-form summary:

1. Inspect OCR quality and build a page/section map.
2. Extract claims chunk by chunk into JSONL.
3. Merge duplicates and preserve conflicts.
4. Run the quantitative-validity audit.
5. Populate `extraction.json` using [report-template.json](assets/report-template.json) and the formal contract in [report.schema.json](references/report.schema.json).

Read [field-guide.md](references/field-guide.md) when populating field labels or deciding whether a field is missing. Work on one chunk and one section at a time when the host model has limited context or weaker reasoning.

For `fast` mode, use the fast prompt in `prompts.md`, set `extraction_mode` to `fast`, and keep the review status `draft`.

### 4. Validate before rendering

Run:

```text
python <skill-dir>/scripts/report_pipeline.py validate --output <wiki-dir>
```

Fix schema errors, missing mandatory sections, evidence/page inconsistencies, and unmarked chart estimates. Low-confidence or missing facts may remain, but must appear in the review queue.

### 5. Build the Wiki

Run:

```text
python <skill-dir>/scripts/report_pipeline.py build --output <wiki-dir>
```

The renderer creates `index.md`, `reports/`, `topics/`, `assets/`, and `review-queue.md`. Do not hand-edit generated topic/index pages; update `extraction.json` and rebuild.

### 6. Report completion honestly

State which PDFs were processed, skipped, degraded, or failed; list validation issues and files requiring review. A successful script exit does not imply that semantic extraction is correct.

## Portability

Read [portability.md](references/portability.md) only when installing, moving, or debugging the skill across Codex, OpenClaw, Windows, macOS, or Linux.
