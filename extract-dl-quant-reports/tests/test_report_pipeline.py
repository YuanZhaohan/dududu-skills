import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("report_pipeline", ROOT / "scripts" / "report_pipeline.py")
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pipeline)


def evidence(page=1):
    return [{
        "pdf_page": page,
        "printed_page": str(page),
        "section": "方法",
        "figure_table": None,
        "quote": "本文使用过去二十日特征预测未来五日超额收益。",
        "ocr_quality": "high",
    }]


def claim(identifier, label="字段", value="值", page=1):
    return {
        "id": identifier,
        "label": label,
        "value": value,
        "details": "测试细节",
        "confidence": "high",
        "value_origin": "reported",
        "evidence": evidence(page),
    }


def valid_document():
    template = json.loads((ROOT / "assets" / "report-template.json").read_text(encoding="utf-8"))
    template["document_id"] = "sample-a1b2c3d4"
    template["metadata"].update({
        "title": "深度学习选股测试",
        "authors": ["张三"],
        "institution": "测试机构",
        "publication_date": "2026-01-01",
        "source_file": "C:/reports/sample.pdf",
        "source_sha256": "a" * 64,
        "pdf_page_count": 3,
        "processed_at": "2026-01-01T00:00:00+08:00",
    })
    template["executive_summary"] = [claim("summary-001", "预测目标 y", "未来五日超额收益")]
    for section in pipeline.MANDATORY_SECTIONS:
        if section == "validity_audit":
            template["sections"][section] = [
                claim(f"audit-{index:03d}", f"审计项 {index}", "已说明") for index in range(1, 7)
            ]
        else:
            template["sections"][section] = [claim(f"{section}-001")]
    template["topics"] = ["LSTM", "A股", "超额收益预测"]
    template["review_notes"] = ["核对公式截图"]
    return template


class PipelineTests(unittest.TestCase):
    def test_extracts_both_markdown_field_variants_and_images(self):
        payload = {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"content": "第一页", "images": {"fig.png": "aGVsbG8="}}},
                    {"markdown": {"text": "第二页"}},
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            markdown, pages, assets = pipeline.extract_markdown_and_assets(payload, Path(tmp))
            self.assertIn("<!-- PDF_PAGE: 1 -->", markdown)
            self.assertIn("第一页", markdown)
            self.assertIn("第二页", markdown)
            self.assertEqual(len(pages), 2)
            self.assertEqual(assets, ["fig.png"])
            self.assertEqual((Path(tmp) / "fig.png").read_bytes(), b"hello")

    def test_chunking_keeps_page_markers(self):
        markdown = "<!-- PDF_PAGE: 1 -->\n\n" + "甲" * 80 + "\n\n<!-- PDF_PAGE: 2 -->\n\n" + "乙" * 80
        chunks = pipeline.chunk_markdown(markdown, 120)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["pdf_pages"], [1])
        self.assertEqual(chunks[1]["pdf_pages"], [2])

    def test_valid_document_passes(self):
        errors, warnings = pipeline.validate_document(valid_document())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_claim_requires_paired_origin(self):
        data = valid_document()
        data["sections"]["losses"][0]["confidence"] = "missing"
        errors, _ = pipeline.validate_document(data)
        self.assertTrue(any("missing/not_stated" in error for error in errors))

    def test_chart_estimate_must_be_labeled(self):
        data = valid_document()
        data["sections"]["backtest_performance"][0]["value_origin"] = "chart_estimate"
        errors, _ = pipeline.validate_document(data)
        self.assertTrue(any("图表估算" in error for error in errors))

    def test_build_wiki(self):
        data = valid_document()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            extraction = output / "_intermediate" / data["document_id"] / "extraction.json"
            pipeline.write_json(extraction, data)
            args = type("Args", (), {"output": str(output), "document": None, "allow_invalid": False})()
            result = pipeline.command_build(args)
            self.assertEqual(result, 0)
            report = output / "reports" / f"{data['document_id']}.md"
            self.assertTrue(report.exists())
            self.assertIn("预测目标 y", report.read_text(encoding="utf-8"))
            self.assertTrue((output / "topics" / "lstm.md").exists())
            self.assertTrue((output / "review-queue.md").exists())

    def test_missing_token_is_not_treated_as_ocr_failure(self):
        source = Path(__file__).resolve()
        args = type("Args", (), {
            "force": True,
            "request_format": "auto",
            "retries": 0,
            "timeout": 1,
            "no_fallback": False,
            "chunk_chars": 100,
            "mode": "thorough",
            "force_template": False,
            "copy_sources": False,
        })()
        old = pipeline.os.environ.pop("PADDLEOCR_ACCESS_TOKEN", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(pipeline.PipelineError, "PADDLEOCR_ACCESS_TOKEN"):
                    pipeline.prepare_document(source, Path(tmp), args, {"documents": {}})
        finally:
            if old is not None:
                pipeline.os.environ["PADDLEOCR_ACCESS_TOKEN"] = old

    def test_prepare_creates_prompt_packets_without_persisting_token(self):
        args = type("Args", (), {
            "force": True,
            "request_format": "auto",
            "retries": 0,
            "timeout": 1,
            "no_fallback": False,
            "chunk_chars": 120,
            "mode": "thorough",
            "force_template": False,
            "copy_sources": False,
        })()
        payload = {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"content": "# 方法\n\n使用 LSTM 预测未来五日收益。"}},
                    {"markdown": {"text": "# 回测\n\n回测窗口为 2020 至 2025 年。"}},
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            pdf.write_bytes(b"%PDF-mock")
            output = root / "wiki"
            with mock.patch.dict(pipeline.os.environ, {"PADDLEOCR_ACCESS_TOKEN": "unit-test-secret"}, clear=False):
                with mock.patch.object(pipeline, "call_paddleocr", return_value=(payload, "multipart")):
                    result = pipeline.prepare_document(pdf, output, args, {"documents": {}})
            self.assertEqual(result, "paddleocr")
            document_dirs = list((output / "_intermediate").iterdir())
            self.assertEqual(len(document_dirs), 1)
            job_dir = document_dirs[0]
            self.assertTrue((job_dir / "prompt-packets" / "phase-0.request.md").exists())
            self.assertTrue((job_dir / "prompt-packets" / "phase-4.request.md").exists())
            self.assertTrue(list((job_dir / "prompt-packets").glob("phase-1-*.request.md")))
            all_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in output.rglob("*") if path.is_file()
            )
            self.assertNotIn("unit-test-secret", all_text)


if __name__ == "__main__":
    unittest.main()
