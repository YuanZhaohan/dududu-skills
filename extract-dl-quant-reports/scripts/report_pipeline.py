#!/usr/bin/env python3
"""Portable OCR preparation, validation, and Wiki rendering pipeline.

Semantic extraction is intentionally performed by the current host agent. This
script never calls an LLM and never persists the PaddleOCR access token.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_API_URL = "https://sc3bjbw1u4d4paw0.aistudio-app.com/layout-parsing"
SCHEMA_VERSION = "1.0"
CONFIDENCE_VALUES = {"high", "medium", "low", "missing"}
ORIGIN_VALUES = {"reported", "derived", "chart_estimate", "not_stated"}
MANDATORY_SECTIONS = [
    "problem_definition",
    "core_method",
    "model_architecture",
    "datasets",
    "features",
    "targets",
    "losses",
    "standardization",
    "training_settings",
    "experimental_conclusions",
    "limitations",
    "baselines",
    "ablations",
    "model_comparisons",
    "backtest_design",
    "backtest_performance",
    "validity_audit",
]
SECTION_TITLES = {
    "problem_definition": "问题定义",
    "core_method": "核心方法",
    "model_architecture": "模型架构",
    "datasets": "数据集",
    "features": "特征",
    "targets": "预测目标 y",
    "losses": "Loss 设计",
    "standardization": "标准化与预处理",
    "training_settings": "训练设置",
    "experimental_conclusions": "实验结论",
    "limitations": "局限性",
    "baselines": "基线模型",
    "ablations": "消融实验",
    "model_comparisons": "模型对比",
    "backtest_design": "回测设计",
    "backtest_performance": "回测表现",
    "validity_audit": "量化研究有效性审计",
}


class PipelineError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def slugify(value: str, limit: int = 64) -> str:
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-_").lower()
    return (value or "report")[:limit].rstrip("-_")


def discover_pdfs(inputs: Iterable[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise PipelineError(f"Input does not exist: {path}")
        candidates = [path] if path.is_file() else sorted(path.rglob("*.pdf"))
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                found[str(candidate).casefold()] = candidate
    if not found:
        raise PipelineError("No PDF files found in the supplied input paths.")
    return sorted(found.values(), key=lambda item: str(item).casefold())


def build_multipart(pdf_path: Path) -> tuple[bytes, str]:
    boundary = f"----dlquant{uuid.uuid4().hex}"
    filename = pdf_path.name.replace('"', "_")
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
        b"Content-Type: application/pdf\r\n\r\n",
        pdf_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def build_json_base64(pdf_path: Path) -> tuple[bytes, str]:
    payload = {
        "file": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
        "fileType": 0,
        "visualize": True,
    }
    return json.dumps(payload).encode("utf-8"), "application/json"


def request_once(url: str, token: str, pdf_path: Path, request_format: str, timeout: int) -> dict[str, Any]:
    if request_format == "multipart":
        body, content_type = build_multipart(pdf_path)
    elif request_format == "json-base64":
        body, content_type = build_json_base64(pdf_path)
    else:
        raise PipelineError(f"Unsupported request format: {request_format}")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "extract-dl-quant-reports/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError("PaddleOCR returned a non-JSON response.") from exc
    if not isinstance(parsed, dict):
        raise PipelineError("PaddleOCR returned an unexpected JSON root.")
    return parsed


def call_paddleocr(
    pdf_path: Path,
    url: str,
    token: str,
    request_format: str,
    retries: int,
    timeout: int,
) -> tuple[dict[str, Any], str]:
    formats = [request_format] if request_format != "auto" else ["multipart", "json-base64"]
    failures: list[str] = []
    for fmt in formats:
        for attempt in range(retries + 1):
            try:
                return request_once(url, token, pdf_path, fmt, timeout), fmt
            except urllib.error.HTTPError as exc:
                safe_reason = f"HTTP {exc.code}"
                failures.append(f"{fmt} attempt {attempt + 1}: {safe_reason}")
                if exc.code in {400, 404, 405, 413, 415, 422}:
                    break
            except (urllib.error.URLError, TimeoutError, PipelineError, OSError) as exc:
                failures.append(f"{fmt} attempt {attempt + 1}: {type(exc).__name__}: {exc}")
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    raise PipelineError("PaddleOCR failed. " + " | ".join(failures[-6:]))


def decode_image(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        return base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        return None


def safe_asset_name(raw: str, fallback: str) -> str:
    name = Path(str(raw).replace("\\", "/")).name
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    return name or fallback


def extract_markdown_and_assets(payload: dict[str, Any], asset_dir: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    root = payload.get("result", payload)
    if not isinstance(root, dict):
        raise PipelineError("PaddleOCR response has no result object.")
    results = root.get("layoutParsingResults")
    if not isinstance(results, list) or not results:
        raise PipelineError("PaddleOCR response has no layoutParsingResults pages.")
    pages: list[dict[str, Any]] = []
    assets: list[str] = []
    asset_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(results, start=1):
        item = item if isinstance(item, dict) else {}
        markdown = item.get("markdown") if isinstance(item.get("markdown"), dict) else {}
        content = markdown.get("content") or markdown.get("text") or item.get("markdownContent") or ""
        if not isinstance(content, str):
            content = str(content)
        image_maps = []
        for candidate in (markdown.get("images"), item.get("markdownImages"), item.get("outputImages")):
            if isinstance(candidate, dict):
                image_maps.append(candidate)
        page_assets: list[str] = []
        image_number = 0
        for image_map in image_maps:
            for raw_name, encoded in image_map.items():
                image_number += 1
                data = decode_image(encoded)
                if data is None:
                    continue
                fallback = f"page-{index:04d}-image-{image_number:03d}.png"
                filename = safe_asset_name(str(raw_name), fallback)
                if "." not in filename:
                    filename += mimetypes.guess_extension("image/png") or ".png"
                target = asset_dir / filename
                if target.exists() and target.read_bytes() != data:
                    target = asset_dir / f"page-{index:04d}-{image_number:03d}-{filename}"
                target.write_bytes(data)
                relative = target.name
                if relative not in assets:
                    assets.append(relative)
                page_assets.append(relative)
        pages.append({"pdf_page": index, "markdown": content.strip(), "assets": page_assets})
    combined = []
    for page in pages:
        combined.append(f"<!-- PDF_PAGE: {page['pdf_page']} -->\n\n{page['markdown']}".rstrip())
    return "\n\n".join(combined).strip() + "\n", pages, assets


def local_pdf_text(pdf_path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise PipelineError("Local fallback needs the optional 'pypdf' package.") from exc
    reader = PdfReader(str(pdf_path))
    pages = []
    blocks = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append({"pdf_page": index, "markdown": text, "assets": []})
        blocks.append(f"<!-- PDF_PAGE: {index} -->\n\n{text}".rstrip())
    combined = "\n\n".join(blocks).strip()
    if len(re.sub(r"\s+", "", combined)) < max(200, len(reader.pages) * 20):
        raise PipelineError("PDF has too little extractable text for safe local fallback.")
    return combined + "\n", pages


PAGE_MARKER = re.compile(r"(?=<!-- PDF_PAGE: \d+ -->)")
PAGE_NUMBER = re.compile(r"<!-- PDF_PAGE: (\d+) -->")


def split_large_block(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]
    marker_match = PAGE_NUMBER.search(block)
    marker = marker_match.group(0) if marker_match else ""
    body = block[marker_match.end() :] if marker_match else block
    paragraphs = re.split(r"\n\s*\n", body)
    parts: list[str] = []
    current = marker
    for paragraph in paragraphs:
        candidate = (current + "\n\n" + paragraph).strip()
        if len(candidate) > max_chars and current.strip() != marker:
            parts.append(current.strip())
            current = (marker + "\n\n" + paragraph).strip()
        elif len(candidate) > max_chars:
            for start in range(0, len(paragraph), max_chars - len(marker) - 4):
                parts.append((marker + "\n\n" + paragraph[start : start + max_chars]).strip())
            current = marker
        else:
            current = candidate
    if current.strip() and current.strip() != marker:
        parts.append(current.strip())
    return parts


def chunk_markdown(markdown: str, max_chars: int) -> list[dict[str, Any]]:
    blocks = [item.strip() for item in PAGE_MARKER.split(markdown) if item.strip()]
    expanded = [part for block in blocks for part in split_large_block(block, max_chars)]
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    for block in expanded:
        if current and current_len + len(block) + 2 > max_chars:
            text = "\n\n".join(current)
            pages = [int(value) for value in PAGE_NUMBER.findall(text)]
            chunks.append({"text": text + "\n", "pdf_pages": sorted(set(pages))})
            current, current_len = [], 0
        current.append(block)
        current_len += len(block) + 2
    if current:
        text = "\n\n".join(current)
        pages = [int(value) for value in PAGE_NUMBER.findall(text)]
        chunks.append({"text": text + "\n", "pdf_pages": sorted(set(pages))})
    return chunks


def load_manifest(output: Path) -> dict[str, Any]:
    path = output / "_pipeline" / "manifest.json"
    if path.exists():
        value = read_json(path)
        if isinstance(value, dict):
            value.setdefault("documents", {})
            return value
    return {"schema_version": SCHEMA_VERSION, "updated_at": now_iso(), "documents": {}}


def prepare_document(pdf_path: Path, output: Path, args: argparse.Namespace, manifest: dict[str, Any]) -> str:
    digest = sha256_file(pdf_path)
    document_id = f"{slugify(pdf_path.stem, 48)}-{digest[:8]}"
    intermediate = output / "_intermediate" / document_id
    existing = manifest["documents"].get(document_id)
    if existing and existing.get("source_sha256") == digest and (intermediate / "ocr.md").exists() and not args.force:
        print(f"SKIP unchanged: {pdf_path.name} -> {document_id}")
        return "skipped"

    output.mkdir(parents=True, exist_ok=True)
    asset_dir = output / "assets" / document_id
    token = os.environ.get("PADDLEOCR_ACCESS_TOKEN", "").strip()
    api_url = os.environ.get("PADDLEOCR_DOC_PARSING_API_URL", DEFAULT_API_URL).strip()
    request_format = os.environ.get("PADDLEOCR_REQUEST_FORMAT", args.request_format).strip()
    if not token:
        raise PipelineError("PADDLEOCR_ACCESS_TOKEN is not set; configure it before processing PDFs.")
    ocr_mode = "paddleocr"
    raw_payload: dict[str, Any] | None = None
    used_format: str | None = None
    try:
        raw_payload, used_format = call_paddleocr(
            pdf_path, api_url, token, request_format, args.retries, args.timeout
        )
        markdown, pages, assets = extract_markdown_and_assets(raw_payload, asset_dir)
    except PipelineError as exc:
        if args.no_fallback:
            raise
        print(f"WARN OCR failed for {pdf_path.name}: {exc}", file=sys.stderr)
        markdown, pages = local_pdf_text(pdf_path)
        assets = []
        ocr_mode = "local_text_fallback"

    intermediate.mkdir(parents=True, exist_ok=True)
    if raw_payload is not None:
        write_json(intermediate / "ocr.raw.json", raw_payload)
    write_text(intermediate / "ocr.md", markdown)
    write_json(intermediate / "ocr.pages.json", pages)
    chunks = chunk_markdown(markdown, args.chunk_chars)
    chunk_manifest = []
    chunk_dir = intermediate / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for old in chunk_dir.glob("chunk-*.md"):
        old.unlink()
    for index, chunk in enumerate(chunks, start=1):
        filename = f"chunk-{index:04d}.md"
        write_text(chunk_dir / filename, chunk["text"])
        chunk_manifest.append({
            "chunk_id": f"chunk-{index:04d}",
            "path": f"chunks/{filename}",
            "pdf_pages": chunk["pdf_pages"],
            "characters": len(chunk["text"]),
        })

    packet_dir = intermediate / "prompt-packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    for old in packet_dir.glob("*.request.md"):
        old.unlink()
    page_overview = "\n".join(
        f"- {item['chunk_id']}: PDF pages {item['pdf_pages']} ({item['characters']} chars)"
        for item in chunk_manifest
    )
    write_text(
        packet_dir / "phase-0.request.md",
        "# Phase 0 request\n\n"
        "Follow `../PROMPTS.md`, section `Phase 0: OCR and page-map inspection`, including all shared rules.\n\n"
        f"- Document id: `{document_id}`\n"
        f"- OCR mode: `{ocr_mode}`\n"
        f"- Observed pages: `{len(pages)}`\n\n"
        "Chunk overview:\n\n"
        f"{page_overview}\n\n"
        "Read chunk headings and page markers as needed. Save the JSON result as `../work/page-map.json`.\n",
    )
    for item in chunk_manifest:
        chunk_text = (intermediate / item["path"]).read_text(encoding="utf-8")
        request_text = (
            "# Phase 1 request\n\n"
            "Follow `../PROMPTS.md`, section `Phase 1: chunk extraction`, including all shared rules. "
            "Inspect all allowed sections, but emit only claims directly supported by this chunk.\n\n"
            f"- Document id: `{document_id}`\n"
            f"- Chunk id: `{item['chunk_id']}`\n"
            f"- Physical PDF pages: `{item['pdf_pages']}`\n"
            "- Target sections: all allowed sections relevant to this chunk\n\n"
            "Save JSONL as `../work/claims-" + item["chunk_id"] + ".jsonl`.\n\n"
            "## OCR chunk\n\n"
            + chunk_text
        )
        write_text(packet_dir / f"phase-1-{item['chunk_id']}.request.md", request_text)
    write_text(
        packet_dir / "phase-2.request.md",
        "# Phase 2 request\n\n"
        "Follow `../PROMPTS.md`, section `Phase 2: merge and conflict resolution`, including all shared rules. "
        "Read every `../work/claims-*.jsonl` file and `../work/page-map.json`. "
        "Save the result as `../work/merged-sections.json`.\n",
    )
    write_text(
        packet_dir / "phase-3.request.md",
        "# Phase 3 request\n\n"
        "Follow `../PROMPTS.md`, section `Phase 3: validity audit`, including all shared rules. "
        "Read `../work/merged-sections.json` and reopen the cited OCR chunks. "
        "Save the six audit claims as `../work/validity-audit.json`.\n",
    )
    write_text(
        packet_dir / "phase-4.request.md",
        "# Phase 4 request\n\n"
        "Follow `../PROMPTS.md`, section `Phase 4: final JSON assembly`, including all shared rules. "
        "Use `../extraction.template.json`, `../job.json`, `../work/page-map.json`, "
        "`../work/merged-sections.json`, and `../work/validity-audit.json`. "
        "Write the complete result to `../extraction.json`, then run the validator.\n",
    )
    (intermediate / "work").mkdir(parents=True, exist_ok=True)

    template = read_json(SKILL_DIR / "assets" / "report-template.json")
    template["document_id"] = document_id
    template["metadata"].update({
        "source_file": str(pdf_path),
        "source_sha256": digest,
        "pdf_page_count": len(pages),
        "ocr_mode": ocr_mode,
        "extraction_mode": args.mode,
        "processed_at": now_iso(),
    })
    write_json(intermediate / "extraction.template.json", template)
    if not (intermediate / "extraction.json").exists() or args.force_template:
        write_json(intermediate / "extraction.json", copy.deepcopy(template))
    shutil.copy2(SKILL_DIR / "references" / "prompts.md", intermediate / "PROMPTS.md")

    source_meta = {
        "document_id": document_id,
        "source_file": str(pdf_path),
        "source_sha256": digest,
        "source_size": pdf_path.stat().st_size,
        "pdf_page_count": len(pages),
        "ocr_mode": ocr_mode,
        "ocr_request_format": used_format,
        "mode": args.mode,
        "chunks": chunk_manifest,
        "prompt_packets": [
            "prompt-packets/phase-0.request.md",
            *[f"prompt-packets/phase-1-{item['chunk_id']}.request.md" for item in chunk_manifest],
            "prompt-packets/phase-2.request.md",
            "prompt-packets/phase-3.request.md",
            "prompt-packets/phase-4.request.md"
        ],
        "assets": [f"assets/{document_id}/{name}" for name in assets],
        "prepared_at": now_iso(),
    }
    write_json(intermediate / "job.json", source_meta)
    if args.copy_sources:
        target = output / "sources" / f"{document_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, target)

    manifest["documents"][document_id] = source_meta
    print(f"PREPARED: {pdf_path.name} -> {document_id} ({len(pages)} pages, {len(chunks)} chunks, {ocr_mode})")
    return ocr_mode


def command_prepare(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    pdfs = discover_pdfs(args.input)
    manifest = load_manifest(output)
    failures = []
    for pdf in pdfs:
        try:
            prepare_document(pdf, output, args, manifest)
        except Exception as exc:  # continue batch while recording exact file failure
            failures.append({"source_file": str(pdf), "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED: {pdf.name}: {exc}", file=sys.stderr)
    manifest["updated_at"] = now_iso()
    manifest["failures"] = failures
    write_json(output / "_pipeline" / "manifest.json", manifest)
    print(f"Manifest: {output / '_pipeline' / 'manifest.json'}")
    return 1 if failures else 0


def validate_evidence(evidence: Any, path: str, page_count: int | None, errors: list[str]) -> None:
    if not isinstance(evidence, list):
        errors.append(f"{path} must be an array")
        return
    for index, item in enumerate(evidence):
        ep = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{ep} must be an object")
            continue
        page = item.get("pdf_page")
        if not isinstance(page, int) or page < 1:
            errors.append(f"{ep}.pdf_page must be a positive integer")
        elif page_count and page > page_count:
            errors.append(f"{ep}.pdf_page {page} exceeds page count {page_count}")
        if not isinstance(item.get("quote"), str) or not item["quote"].strip():
            errors.append(f"{ep}.quote must be non-empty")


def validate_claim(claim: Any, path: str, page_count: int | None, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(claim, dict):
        errors.append(f"{path} must be an object")
        return
    for key in ("id", "label", "value", "details", "confidence", "value_origin", "evidence"):
        if key not in claim:
            errors.append(f"{path}.{key} is required")
    confidence = claim.get("confidence")
    origin = claim.get("value_origin")
    evidence = claim.get("evidence")
    if confidence not in CONFIDENCE_VALUES:
        errors.append(f"{path}.confidence must be one of {sorted(CONFIDENCE_VALUES)}")
    if origin not in ORIGIN_VALUES:
        errors.append(f"{path}.value_origin must be one of {sorted(ORIGIN_VALUES)}")
    if confidence == "missing" or origin == "not_stated":
        if confidence != "missing" or origin != "not_stated":
            errors.append(f"{path} missing/not_stated must be used together")
        if evidence not in ([], None):
            errors.append(f"{path}.evidence must be empty for not_stated facts")
        warnings.append(f"{path} requires review because it is missing")
    else:
        validate_evidence(evidence, f"{path}.evidence", page_count, errors)
        if isinstance(evidence, list) and not evidence:
            errors.append(f"{path}.evidence cannot be empty for a stated fact")
    if confidence == "low":
        warnings.append(f"{path} requires review because confidence is low")
    if origin == "chart_estimate" and "图表估算" not in str(claim.get("details", "")):
        errors.append(f"{path}.details must contain '图表估算' for chart estimates")


def validate_document(data: Any, output: Path | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["document root must be an object"], warnings
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("document_id", "metadata", "executive_summary", "sections", "figures", "topics", "review_notes"):
        if key not in data:
            errors.append(f"{key} is required")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for key in ("title", "source_file", "source_sha256", "ocr_mode", "extraction_mode", "review_status"):
        if not metadata.get(key):
            errors.append(f"metadata.{key} is required")
    page_count = metadata.get("pdf_page_count")
    if page_count is not None and (not isinstance(page_count, int) or page_count < 1):
        errors.append("metadata.pdf_page_count must be null or a positive integer")
        page_count = None
    summary = data.get("executive_summary")
    if not isinstance(summary, list) or not summary:
        errors.append("executive_summary must contain at least one claim")
    else:
        for index, claim in enumerate(summary):
            validate_claim(claim, f"executive_summary[{index}]", page_count, errors, warnings)
    sections = data.get("sections")
    if not isinstance(sections, dict):
        errors.append("sections must be an object")
    else:
        for section in MANDATORY_SECTIONS:
            claims = sections.get(section)
            if not isinstance(claims, list) or not claims:
                errors.append(f"sections.{section} must contain at least one stated or not_stated claim")
                continue
            for index, claim in enumerate(claims):
                validate_claim(claim, f"sections.{section}[{index}]", page_count, errors, warnings)
        audit = sections.get("validity_audit")
        if isinstance(audit, list) and len(audit) != 6:
            errors.append("sections.validity_audit must contain exactly six audit claims")
    figures = data.get("figures")
    if not isinstance(figures, list):
        errors.append("figures must be an array")
    else:
        for index, figure in enumerate(figures):
            path = f"figures[{index}]"
            if not isinstance(figure, dict):
                errors.append(f"{path} must be an object")
                continue
            asset_path = figure.get("asset_path")
            if not isinstance(asset_path, str) or not asset_path:
                errors.append(f"{path}.asset_path is required")
            elif output is not None:
                candidate = (output / asset_path).resolve()
                try:
                    candidate.relative_to(output.resolve())
                except ValueError:
                    errors.append(f"{path}.asset_path escapes the Wiki root")
                else:
                    if not candidate.is_file():
                        errors.append(f"{path}.asset_path does not exist: {asset_path}")
    if not isinstance(data.get("topics"), list):
        errors.append("topics must be an array")
    if metadata.get("review_status") not in {"draft", "reviewed"}:
        errors.append("metadata.review_status must be draft or reviewed")
    return errors, warnings


def extraction_paths(output: Path, document: str | None = None) -> list[Path]:
    base = output / "_intermediate"
    if document:
        path = base / document / "extraction.json"
        return [path] if path.exists() else []
    return sorted(base.glob("*/extraction.json")) if base.exists() else []


def command_validate(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    paths = extraction_paths(output, args.document)
    if not paths:
        print("No extraction.json files found.", file=sys.stderr)
        return 2
    total_errors = 0
    result = {"validated_at": now_iso(), "documents": {}}
    for path in paths:
        try:
            data = read_json(path)
            errors, warnings = validate_document(data, output)
        except Exception as exc:
            errors, warnings = [f"invalid JSON: {exc}"], []
        document_id = path.parent.name
        result["documents"][document_id] = {"errors": errors, "warnings": warnings}
        total_errors += len(errors)
        print(f"{document_id}: {len(errors)} errors, {len(warnings)} warnings")
        for message in errors:
            print(f"  ERROR {message}")
        for message in warnings:
            print(f"  WARN  {message}")
    write_json(output / "_pipeline" / "validation.json", result)
    return 1 if total_errors else 0


def md_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>")


def evidence_label(item: dict[str, Any]) -> str:
    parts = [f"PDF p.{item.get('pdf_page', '?')}"]
    if item.get("printed_page") not in (None, ""):
        parts.append(f"报告 p.{item['printed_page']}")
    if item.get("section"):
        parts.append(str(item["section"]))
    if item.get("figure_table"):
        parts.append(str(item["figure_table"]))
    return " / ".join(parts)


def claim_source(claim: dict[str, Any]) -> str:
    evidence = claim.get("evidence") or []
    return "; ".join(evidence_label(item) for item in evidence if isinstance(item, dict)) or "—"


def render_claims(claims: list[dict[str, Any]]) -> str:
    lines = ["| 字段 | 内容 | 置信度 | 来源 |", "|---|---|---|---|"]
    for claim in claims:
        value = md_escape(claim.get("value", ""))
        details = md_escape(claim.get("details", ""))
        combined = value + (f"<br><small>{details}</small>" if details else "")
        lines.append(
            f"| {md_escape(claim.get('label', ''))} | {combined} | {md_escape(claim.get('confidence', ''))} | {md_escape(claim_source(claim))} |"
        )
    evidence_lines = []
    for claim in claims:
        for item in claim.get("evidence") or []:
            quote = str(item.get("quote", "")).strip().replace("\n", " ")
            if quote:
                evidence_lines.append(f"> **{md_escape(claim.get('label', ''))} — {evidence_label(item)}**：{quote}")
    return "\n".join(lines + (["", "证据：", ""] + evidence_lines if evidence_lines else []))


def yaml_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_report(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    title = metadata.get("title") or data["document_id"]
    topics = [str(item) for item in data.get("topics", []) if str(item).strip()]
    lines = [
        "---",
        f"title: {yaml_scalar(title)}",
        f"document_id: {yaml_scalar(data['document_id'])}",
        f"review_status: {yaml_scalar(metadata.get('review_status'))}",
        f"source_language: {yaml_scalar(metadata.get('source_language'))}",
        f"publication_date: {yaml_scalar(metadata.get('publication_date'))}",
        f"source_sha256: {yaml_scalar(metadata.get('source_sha256'))}",
        f"tags: {json.dumps(topics, ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        "## 一页式速览",
        "",
        render_claims(data.get("executive_summary", [])),
        "",
        "## 元数据",
        "",
        f"- 作者：{', '.join(metadata.get('authors') or []) or '未说明'}",
        f"- 机构：{metadata.get('institution') or '未说明'}",
        f"- 发布日期：{metadata.get('publication_date') or '未说明'}",
        f"- 报告版本：{metadata.get('report_version') or '未说明'}",
        f"- 来源文件：`{metadata.get('source_file') or ''}`",
        f"- OCR 模式：`{metadata.get('ocr_mode')}`",
        f"- 提取模式：`{metadata.get('extraction_mode')}`",
        f"- 审核状态：`{metadata.get('review_status')}`",
    ]
    if topics:
        topic_links = [f"[[../topics/{slugify(topic)}|{topic}]]" for topic in topics]
        lines.extend([f"- 主题：{' · '.join(topic_links)}"])
    for section in MANDATORY_SECTIONS:
        lines.extend(["", f"## {SECTION_TITLES[section]}", "", render_claims(data["sections"].get(section, []))])
    figures = data.get("figures") or []
    if figures:
        lines.extend(["", "## 关键图表与公式", ""])
        for figure in figures:
            asset_path = str(figure.get("asset_path", ""))
            relative = "../" + asset_path.replace("\\", "/")
            lines.extend([
                f"### {figure.get('title') or figure.get('figure_table') or figure.get('id')}",
                "",
                f"![{figure.get('title') or figure.get('id')}]({relative})",
                "",
                f"- 来源：PDF p.{figure.get('pdf_page', '?')} / {figure.get('figure_table') or '未编号'}",
                f"- 原文说明：{figure.get('source_caption') or '未说明'}",
                f"- 谨慎解读：{figure.get('interpretation') or '未说明'}",
                f"- 置信度：`{figure.get('confidence') or 'missing'}`",
                "",
            ])
    review_notes = data.get("review_notes") or []
    lines.extend(["", "## 人工复核", ""])
    lines.extend([f"- {item}" for item in review_notes] or ["- 暂无额外复核备注；仍需按审核状态确认全文。"])
    return "\n".join(lines).rstrip() + "\n"


def command_build(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    paths = extraction_paths(output, args.document)
    if not paths:
        print("No extraction.json files found.", file=sys.stderr)
        return 2
    documents = []
    validation_failures = []
    review_rows = []
    topics: dict[str, list[tuple[str, str]]] = {}
    for path in paths:
        data = read_json(path)
        errors, warnings = validate_document(data, output)
        if errors:
            validation_failures.append((data.get("document_id", path.parent.name), errors))
            continue
        documents.append(data)
        document_id = data["document_id"]
        title = data["metadata"].get("title") or document_id
        write_text(output / "reports" / f"{document_id}.md", render_report(data))
        for topic in data.get("topics", []):
            name = str(topic).strip()
            if name:
                topics.setdefault(name, []).append((document_id, title))
        for warning in warnings:
            review_rows.append((document_id, title, warning))
        for note in data.get("review_notes") or []:
            review_rows.append((document_id, title, str(note)))
    if validation_failures and not args.allow_invalid:
        for document_id, errors in validation_failures:
            print(f"Cannot build {document_id}; validation errors:", file=sys.stderr)
            for message in errors:
                print(f"  {message}", file=sys.stderr)
        return 1

    index_lines = ["# 深度学习量化研报 Wiki", "", f"更新时间：{now_iso()}", "", "| 研报 | 日期 | 机构 | 模式 | 状态 |", "|---|---|---|---|---|"]
    for data in sorted(documents, key=lambda item: str(item["metadata"].get("publication_date", "")), reverse=True):
        meta = data["metadata"]
        index_lines.append(
            f"| [[reports/{data['document_id']}|{md_escape(meta.get('title') or data['document_id'])}]] | {md_escape(meta.get('publication_date'))} | {md_escape(meta.get('institution'))} | {md_escape(meta.get('extraction_mode'))} | {md_escape(meta.get('review_status'))} |"
        )
    write_text(output / "index.md", "\n".join(index_lines) + "\n")

    topic_dir = output / "topics"
    topic_dir.mkdir(parents=True, exist_ok=True)
    expected_topic_files = set()
    for topic, refs in sorted(topics.items(), key=lambda item: item[0].casefold()):
        filename = f"{slugify(topic)}.md"
        expected_topic_files.add(filename)
        lines = [f"# {topic}", ""]
        lines.extend(f"- [[../reports/{document_id}|{title}]]" for document_id, title in refs)
        write_text(topic_dir / filename, "\n".join(lines) + "\n")
    for old in topic_dir.glob("*.md"):
        if old.name not in expected_topic_files:
            old.unlink()

    review_lines = ["# 人工复核队列", "", "| 研报 | 原因 |", "|---|---|"]
    review_lines.extend(f"| [[reports/{doc_id}|{md_escape(title)}]] | {md_escape(reason)} |" for doc_id, title, reason in review_rows)
    if not review_rows:
        review_lines.append("| — | 当前无自动标记项 |")
    write_text(output / "review-queue.md", "\n".join(review_lines) + "\n")
    print(f"BUILT: {len(documents)} reports, {len(topics)} topics -> {output}")
    if validation_failures:
        print(f"SKIPPED invalid reports: {len(validation_failures)}", file=sys.stderr)
    return 0


def command_status(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    manifest = load_manifest(output)
    validations = output / "_pipeline" / "validation.json"
    print(json.dumps({
        "output": str(output),
        "documents": manifest.get("documents", {}),
        "failures": manifest.get("failures", []),
        "validation": read_json(validations) if validations.exists() else None,
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and build a traceable deep-learning quant-report Wiki.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="OCR PDFs, cache results, and create extraction jobs.")
    prepare.add_argument("--input", nargs="+", required=True, help="PDF files or directories.")
    prepare.add_argument("--output", required=True, help="Wiki output directory.")
    prepare.add_argument("--mode", choices=("thorough", "fast"), default="thorough")
    prepare.add_argument("--copy-sources", action="store_true")
    prepare.add_argument("--force", action="store_true", help="Repeat OCR even when the source hash is unchanged.")
    prepare.add_argument("--force-template", action="store_true", help="Replace extraction.json with a blank template.")
    prepare.add_argument("--no-fallback", action="store_true", help="Do not try local text extraction after OCR failure.")
    prepare.add_argument("--request-format", choices=("auto", "multipart", "json-base64"), default="auto")
    prepare.add_argument("--retries", type=int, default=2)
    prepare.add_argument("--timeout", type=int, default=300)
    prepare.add_argument("--chunk-chars", type=int, default=12000)
    prepare.set_defaults(func=command_prepare)

    validate = subparsers.add_parser("validate", help="Validate extraction JSON files.")
    validate.add_argument("--output", required=True)
    validate.add_argument("--document")
    validate.set_defaults(func=command_validate)

    build = subparsers.add_parser("build", help="Render validated JSON into a Markdown Wiki.")
    build.add_argument("--output", required=True)
    build.add_argument("--document")
    build.add_argument("--allow-invalid", action="store_true", help="Build valid reports while skipping invalid reports.")
    build.set_defaults(func=command_build)

    status = subparsers.add_parser("status", help="Print manifest and validation status as JSON.")
    status.add_argument("--output", required=True)
    status.set_defaults(func=command_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
