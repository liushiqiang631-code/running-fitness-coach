# -*- coding: utf-8 -*-
"""新书入库质量检查(对齐 §2.8):
- 字段齐全/无重复 id/无控制字符/无超长块/token_count 为 int
- 页码在书本页数内;images 引用文件全部存在、非零字节(相对路径按 images 根目录解析)
- 水印/噪音残留扫描
用法: python scripts/check.py --json <新书 json> --book-pages 460 [--md <清洗后 md>]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag", "scripts"))
from config_loader import load_config

CFG = load_config()
IMAGES_DIR = CFG["paths"]["images_dir"]

REQUIRED = ["id", "text", "source", "source_file", "chapter", "section",
            "heading_path", "page", "images", "token_count", "char_count"]
# 与 scripts/clean.py 的水印表对齐(不含真实出版商/作者网址)
WATERMARKS = ["z-lib", "z-access", "go-to-library", "xgv5", "doc2x", "pdg2pic", "1lib", "libgen", "researchgate", "librarian"]
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def resolve_image(img):
    """图片引用已改相对 images 根目录(如 b019/xxx.jpg);旧绝对路径兼容解析。"""
    return img if os.path.isabs(img) else os.path.join(IMAGES_DIR, img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--book-pages", type=int, default=0, help="书本总页数,页码校验用")
    ap.add_argument("--md", default="", help="清洗后的 md 路径,水印残留扫描用")
    args = ap.parse_args()

    chunks = json.load(open(args.json, encoding="utf-8"))
    problems = []
    ids = set()
    for c in chunks:
        for k in REQUIRED:
            if k not in c:
                problems.append(f"缺字段 {k}: {c.get('id','?')}")
        cid = c.get("id")
        if cid in ids:
            problems.append(f"重复 id: {cid}")
        ids.add(cid)
        if not isinstance(c.get("token_count"), int):
            problems.append(f"token_count 非 int: {cid}")
        if not isinstance(c.get("char_count"), int):
            problems.append(f"char_count 非 int: {cid}")
        txt = c.get("text", "")
        if CTRL_RE.search(txt):
            problems.append(f"控制字符: {cid}")
        if len(txt) > 20000:
            problems.append(f"超长块({len(txt)}字符): {cid}")
        if args.book_pages:
            for part in str(c.get("page", "")).split("-"):
                part = part.strip()
                if part.isdigit() and int(part) > args.book_pages:
                    problems.append(f"页码越界 {part}: {cid}")
        for img in c.get("images", []):
            path = resolve_image(img)
            if not os.path.exists(path):
                problems.append(f"图片缺失 {path}: {cid}")
            elif os.path.getsize(path) == 0:
                problems.append(f"图片零字节 {path}: {cid}")

    # 只扫正文 text(元数据 source_file 可能含 z-library 下载文件名,属误报)
    for w in WATERMARKS:
        for c in chunks:
            if re.search(w, (c.get("text") or ""), re.I):
                problems.append(f"水印残留: {w} ({c.get('id','?')})")
                break
    if args.md and os.path.exists(args.md):
        md = open(args.md, encoding="utf-8").read()
        for w in WATERMARKS:
            if re.search(w, md, re.I):
                problems.append(f"md 水印残留: {w}")

    no_page = [c["id"] for c in chunks if not c.get("page")]
    if no_page:
        problems.append(f"{len(no_page)} 块无页码: {no_page[:5]}")

    print(f"chunks={len(chunks)} 唯一id={len(ids)} 无页码={len(no_page)}")
    if problems:
        print(f"!! {len(problems)} 个问题:")
        for p in problems[:40]:
            print("  -", p)
    else:
        print("✅ 全部通过")


if __name__ == "__main__":
    main()
