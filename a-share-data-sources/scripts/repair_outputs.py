from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import DATA_DIR, clean_json_value, emit_json  # noqa: E402


MARKERS = ("NaN", "Infinity", "-Infinity")


def _has_markers(text: str) -> bool:
    return any(marker in text for marker in MARKERS)


def _open_text(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def repair_json(path: Path) -> bool:
    with _open_text(path, "rt") as f:
        text = f.read()
    if not _has_markers(text):
        return False
    payload = json.loads(text)
    with _open_text(path, "wt") as f:
        json.dump(clean_json_value(payload), f, ensure_ascii=False, allow_nan=False)
    return True


def repair_jsonl(path: Path) -> bool:
    with _open_text(path, "rt") as f:
        text = f.read()
    if not _has_markers(text):
        return False
    rows: list[Any] = [json.loads(line) for line in text.splitlines() if line.strip()]
    with _open_text(path, "wt") as f:
        for row in rows:
            f.write(json.dumps(clean_json_value(row), ensure_ascii=False, allow_nan=False) + "\n")
    return True


def repair_outputs(root: Path = DATA_DIR) -> dict[str, Any]:
    repaired: list[str] = []
    errors: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if str(path).endswith(".json.gz") and repair_json(path):
                repaired.append(str(path))
            elif (str(path).endswith(".jsonl") or str(path).endswith(".jsonl.gz")) and repair_jsonl(path):
                repaired.append(str(path))
        except Exception as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return {"ok": not errors, "repaired_count": len(repaired), "repaired": repaired, "errors": errors}


if __name__ == "__main__":
    emit_json(repair_outputs())
