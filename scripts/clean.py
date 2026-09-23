# -*- coding: utf-8 -*-
"""清洗 MinerU 输出的 markdown: 去水印/控制字符/页码噪音/折叠空行。

对齐 跑步健身教练Agent_工作总结与方案 §2.3 / §4:
- z-library / xgv5 / Doc2X / Pdg2Pic 等水印(正则整段过滤)
- ResearchGate DRM 乱码等控制字符、页码、页眉页脚、空标题、论文页眉噪音
- 折叠多余空行、规范化空白

用法: python scripts/clean.py <in.md> <out.md>
"""
import re
import sys

# 已知水印/版本标记(整行或连续行匹配即整段删)
WATERMARKS = [
    r"z-lib\.sk",
    r"z-lib",
    r"z-access",
    r"go-to-library",
    r"z-library",
    r"1lib\.sk",
    r"libgen",
    r"Doc2X",
    r"Pdg2Pic",
    r"xgv5",
    r"book see .*?libgen",
    r"researchgate",
    r"www\.[a-z0-9-]+\.(ir|com|net)\s*$",
    r"Librarian",
]
# 需替换/删除的不可见与控制字符
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f﻿]")
# 纯页码行(独立成行, 1-4 位数字, 可带前后空格)
PAGENUM_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$")
# 页眉页脚常见残留: 书籍名 + 章节名 + 页码 的行
HEADFOOT_LINE_RE = re.compile(r"^\s*(training for the uphill athlete|contents|index)\s*[·•|]\s*\d{1,4}\s*$", re.I)


def clean_md(text):
    lines = text.split("\n")
    out = []
    for ln in lines:
        raw = ln
        ln = CTRL_RE.sub("", ln)
        if not ln.strip():
            out.append("")
            continue
        if PAGENUM_LINE_RE.match(ln):
            continue  # 独立页码行删除
        if HEADFOOT_LINE_RE.match(ln):
            continue
        if any(re.search(p, ln, re.I) for p in WATERMARKS):
            continue  # 整行水印删除
        out.append(ln.rstrip())
    # 折叠连续空行 → 单个空行,首尾去空行
    collapsed = []
    blank = 0
    for ln in out:
        if not ln:
            blank += 1
            if blank <= 1:
                collapsed.append("")
        else:
            blank = 0
            collapsed.append(ln)
    while collapsed and not collapsed[0]:
        collapsed.pop(0)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    text = open(src, encoding="utf-8").read()
    cleaned = clean_md(text)
    open(dst, "w", encoding="utf-8").write(cleaned)
    print(f"clean: {len(text)} → {len(cleaned)} 字符, 输出 {dst}")
