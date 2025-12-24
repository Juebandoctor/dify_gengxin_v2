"""repair_ocr_output.py

将 MinerU 版面 JSON（包含 bbox/spans/page_idx 等字段）转换为按阅读顺序的纯文本 Markdown。

- 默认：只扫描/预览，不修改文件
- 使用 --apply：备份并覆盖写回

用法示例：
  python repair_ocr_output.py --path ocr_output
  python repair_ocr_output.py --path ocr_output --apply
  python repair_ocr_output.py --file "ocr_output/xxx_ocr.md" --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


_LAYOUT_HINT_KEYS = ("\"bbox\"", "\"spans\"", "\"page_idx\"", "\"text_level\"")


def looks_like_layout_json(text: str) -> bool:
    if not text:
        return False
    head = text.lstrip()[:4000]
    if not (head.startswith("[") or head.startswith("{")):
        return False
    return ("\"bbox\"" in head) and any(k in head for k in _LAYOUT_HINT_KEYS[1:])


def safe_read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def safe_write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def bbox_center(bbox: Any) -> Tuple[float, float]:
    try:
        x0, y0, x1, y1 = bbox
        return (float(x0) + float(x1)) / 2.0, (float(y0) + float(y1)) / 2.0
    except Exception:
        return 0.0, 0.0


def bbox_sort_key(block: Dict[str, Any]) -> Tuple[float, float]:
    bbox = block.get("bbox") or []
    try:
        x0, y0, x1, y1 = bbox
        return float(y0), float(x0)
    except Exception:
        return 0.0, 0.0


def block_text(block: Any) -> str:
    if not isinstance(block, dict):
        return ""

    if "text" in block:
        text = (block.get("text") or "").strip()
        level = block.get("text_level")
        if text and isinstance(level, int) and 1 <= level <= 6:
            return ("#" * level) + " " + text
        return text

    spans = block.get("spans") or []
    parts: List[str] = []
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        content = (sp.get("content") or "").strip()
        if not content:
            continue
        if sp.get("type") == "inline_equation":
            parts.append(f"${content}$")
        else:
            parts.append(content)
    return "".join(parts).strip()


def group_blocks_by_page(blocks: Iterable[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    pages: Dict[int, List[Dict[str, Any]]] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        page = b.get("page_idx")
        if page is None:
            page = b.get("page")
        if page is None:
            page = 0
        pages.setdefault(int(page), []).append(b)
    return [pages[k] for k in sorted(pages.keys())]


def is_two_column(page_blocks: List[Dict[str, Any]]) -> bool:
    xs: List[float] = []
    min_x0: Optional[float] = None
    max_x1: Optional[float] = None

    for b in page_blocks:
        bbox = b.get("bbox")
        if not bbox:
            continue
        try:
            x0, y0, x1, y1 = bbox
            min_x0 = float(x0) if min_x0 is None else min(min_x0, float(x0))
            max_x1 = float(x1) if max_x1 is None else max(max_x1, float(x1))
            cx, _ = bbox_center(bbox)
            xs.append(cx)
        except Exception:
            continue

    if len(xs) < 20 or min_x0 is None or max_x1 is None:
        return False

    page_width = max(1.0, max_x1 - min_x0)
    xs_sorted = sorted(xs)
    mid = xs_sorted[len(xs_sorted) // 2]
    left = [x for x in xs if x < mid]
    right = [x for x in xs if x >= mid]
    if len(left) < 8 or len(right) < 8:
        return False

    gap = (sum(right) / len(right)) - (sum(left) / len(left))
    return gap > (page_width * 0.2)


def layout_json_to_text(text: str) -> str:
    try:
        data = json.loads(text)
    except Exception:
        return ""

    if isinstance(data, dict):
        for key in ("data", "result", "pages", "blocks"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break

    if not isinstance(data, list):
        return ""

    blocks = [b for b in data if isinstance(b, dict)]
    if not blocks:
        return ""

    out_lines: List[str] = []

    for page_blocks in group_blocks_by_page(blocks):
        page_blocks = [b for b in page_blocks if block_text(b)]
        if not page_blocks:
            continue

        if is_two_column(page_blocks):
            xs = [bbox_center(b.get("bbox") or [0, 0, 0, 0])[0] for b in page_blocks]
            xs_sorted = sorted(xs)
            split_x = xs_sorted[len(xs_sorted) // 2]

            left_blocks: List[Dict[str, Any]] = []
            right_blocks: List[Dict[str, Any]] = []
            for b in page_blocks:
                cx, _ = bbox_center(b.get("bbox") or [])
                (left_blocks if cx < split_x else right_blocks).append(b)

            left_blocks.sort(key=bbox_sort_key)
            right_blocks.sort(key=bbox_sort_key)
            ordered = left_blocks + right_blocks
        else:
            ordered = sorted(page_blocks, key=bbox_sort_key)

        for b in ordered:
            line = block_text(b)
            if line:
                out_lines.append(line)
        out_lines.append("")

    return "\n".join(out_lines).strip() + "\n"


@dataclass
class RepairResult:
    path: str
    changed: bool
    reason: str


def repair_file(path: str, apply: bool, backup: bool) -> RepairResult:
    try:
        raw = safe_read_text(path)
    except Exception as e:
        return RepairResult(path=path, changed=False, reason=f"read_failed: {e}")

    if not looks_like_layout_json(raw):
        # 也可能是 md 里混了 json，但开头不是 json；这里做个轻量兜底
        head = raw.lstrip()[:6000]
        if not ("\"bbox\"" in head and ("\"spans\"" in head or "\"page_idx\"" in head)):
            return RepairResult(path=path, changed=False, reason="not_layout_json")

    converted = layout_json_to_text(raw)
    if not converted.strip():
        return RepairResult(path=path, changed=False, reason="json_parse_or_convert_failed")

    if apply:
        try:
            if backup:
                bak = path + ".bak"
                if not os.path.exists(bak):
                    safe_write_text(bak, raw)
            safe_write_text(path, converted)
        except Exception as e:
            return RepairResult(path=path, changed=False, reason=f"write_failed: {e}")

    return RepairResult(path=path, changed=True, reason="converted")


def iter_target_files(root: str) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            # 只处理 OCR 输出文件，避免误伤其他 md
            if "_ocr" not in fn.lower():
                continue
            yield os.path.join(dirpath, fn)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair MinerU layout JSON to ordered Markdown")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--path", help="ocr_output 目录路径")
    g.add_argument("--file", help="单个要修复的文件")
    parser.add_argument("--apply", action="store_true", help="实际写回文件（默认仅预览）")
    parser.add_argument("--no-backup", action="store_true", help="不生成 .bak 备份")
    parser.add_argument("--limit", type=int, default=20, help="预览模式最多显示多少条将被修复的文件")

    args = parser.parse_args()

    apply = bool(args.apply)
    backup = not bool(args.no_backup)

    targets: List[str] = []
    if args.file:
        targets = [args.file]
    else:
        targets = list(iter_target_files(args.path))

    changed = 0
    scanned = 0
    preview: List[str] = []

    for p in targets:
        scanned += 1
        res = repair_file(p, apply=apply, backup=backup)
        if res.changed:
            changed += 1
            if not apply and len(preview) < args.limit:
                preview.append(p)

    if apply:
        print(f"扫描 {scanned} 个文件，已修复 {changed} 个（写回={'是' if apply else '否'}，备份={'是' if backup else '否'}）")
    else:
        print(f"扫描 {scanned} 个文件，预计可修复 {changed} 个（当前为预览模式，未写回）")
        if preview:
            print("将被修复的示例：")
            for p in preview:
                print(f"- {p}")
            if changed > len(preview):
                print(f"... 以及其他 {changed - len(preview)} 个")
        print("\n要执行实际修复，请加 --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
