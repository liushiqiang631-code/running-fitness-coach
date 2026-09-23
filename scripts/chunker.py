# -*- coding: utf-8 -*-
"""按 MinerU content_list_v2 分块 → 跑步健身教练json集合/<book_id>_<书名>.json。

对齐 跑步健身教练Agent_工作总结与方案 §2.5 / §4.2:
- content_list_v2 为权威数据源(页码+标题层级),比解析 md 可靠
- 按标题树切块,超长段落按 token 切分(上限 800 token)
- 噪音块(page_header/footer/footnote/number)直接丢弃
- 图像/图表块保留图片引用但不塞进文本,避免污染 embedding
- 表格块(table_body 是 HTML)转纯文本保留 + 原图引用(数据表双保险)
- 字段: id/text/source/source_file/chapter/section/heading_path/page/images/token_count/char_count
- images 存相对 images 根目录的路径(如 "b019/xxx.jpg"),项目整体移动/换机不用改数据

实测 b019 的 schema:
  type ∈ {text,image,table,header,chart,list,page_footnote,footer,code}
  标题: type=text 且带 text_level('1'/'2')
  page_idx: 0-based 字符串 → 输出 +1

用法: python scripts/chunker.py --book-id b019 --title "..." --source-file <pdf名> \
      --content-list <merged.json> --json-out <out.json> [--token-max 800]
"""
import argparse
import html as _html
import json
import os
import re
import sys

import tiktoken

ENC = tiktoken.get_encoding("cl100k_base")

NOISE_TYPES = {"header", "page_header", "footer", "page_footer", "page_footnote", "page_number"}
TEXT_TYPES = {"text", "list", "code"}          # 无 text_level 的正文
# 书尾附属章节: MinerU 常把它们判成 level-2 挂到最后一章,提升为 level-1(独立章节)
BACK_MATTER = {"glossary", "index", "appendix", "appendices", "acknowledgments",
               "acknowledgements", "references", "bibliography", "about the author"}
IMAGE_TYPES = {"image", "chart"}               # 图/图/示意图: 只留图片引用
TABLE_TYPE = "table"                           # 表格: 文本(HTML)+图片引用双保险


def table_text(body):
    """table_body 是 HTML 字符串 → 纯文本(单元格用 | 分隔,保持表格可读)。"""
    if not body or not str(body).strip():
        return ""
    s = str(body)
    s = s.replace("</td>", " | ").replace("</tr>", "\n").replace("</th>", " | ")
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\| ?", " | ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def hard_split(text, token_max):
    """单个超长段落按 token 上限硬切(在最近的空格处断行)。"""
    tokens = ENC.encode(text)
    if len(tokens) <= token_max:
        return [text]
    parts, i = [], 0
    while i < len(tokens):
        j = min(i + token_max, len(tokens))
        if j < len(tokens):
            k = j
            # decode 只收 token 列表(单个 int 会 TypeError),空格边界回溯
            while k > i and ENC.decode([tokens[k]]) != " ":
                k -= 1
            if k > i:
                j = k
        parts.append(ENC.decode(tokens[i:j]))
        i = j
    return parts


def page_str(pages):
    pages = sorted(set(pages))
    if not pages:
        return ""
    if len(pages) == 1:
        return str(pages[0])
    return f"{pages[0]}-{pages[-1]}"


def build_chunks(blocks, book_id, source, source_file, token_max, page_base=1):
    chunks = []
    pending_text = []
    pending_imgs = []
    pages = []
    heading_stack = []          # [(level, text)]
    seq = 0

    def flush():
        """把本节的 pending_text(可能已被 split_long 切成多段)逐段各出一个 chunk。"""
        nonlocal seq
        items = [t.strip() for t in pending_text if t.strip()]
        imgs = sorted(set(pending_imgs))
        if not items and not imgs:
            pending_text.clear(); pending_imgs.clear(); pages.clear()
            return
        heading_path = [h[1] for h in heading_stack]
        chapter = heading_stack[0][1] if heading_stack else ""
        section = next((t for lv, t in heading_stack if lv == 2), chapter)
        pstr = page_str([p + page_base for p in pages])
        if not items:
            # 纯图占位(如纯图表格): 单 chunk,空文本+图引用
            seq += 1
            chunks.append({
                "id": f"{book_id}-{seq:04d}", "text": "", "source": source,
                "source_file": source_file, "chapter": chapter, "section": section,
                "heading_path": heading_path, "page": pstr, "images": imgs,
                "token_count": 0, "char_count": 0,
            })
        else:
            for i, t in enumerate(items):
                seq += 1
                chunks.append({
                    "id": f"{book_id}-{seq:04d}", "text": t, "source": source,
                    "source_file": source_file, "chapter": chapter, "section": section,
                    "heading_path": heading_path, "page": pstr,
                    "images": imgs if i == 0 else [],   # 图引用挂在片段首
                    "token_count": len(ENC.encode(t)),
                    "char_count": len(t),
                })
        pending_text.clear(); pending_imgs.clear(); pages.clear()

    def split_long():
        text = "\n\n".join(t.strip() for t in pending_text if t.strip())
        pending_text.clear()
        if not text:
            return
        if len(ENC.encode(text)) <= token_max:
            pending_text.append(text)
            return
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        cur, cur_tok = [], 0
        for p in parts:
            tok = len(ENC.encode(p))
            if tok > token_max and not cur:
                # 单段超限: 硬切(按 token 数在空白处切),避免留下超长段
                for sub in hard_split(p, token_max):
                    pending_text.append(sub)
                continue
            if cur_tok + tok > token_max and cur:
                pending_text.append("\n\n".join(cur))
                cur, cur_tok = [], 0
            cur.append(p)
            cur_tok += tok
        if cur:
            pending_text.append("\n\n".join(cur))

    for b in blocks:
        if not isinstance(b, dict):
            continue
        typ = b.get("type", "")
        if typ in NOISE_TYPES:
            continue
        page = b.get("page_idx")
        page = int(page) if str(page).isdigit() else None
        is_title = b.get("text_level") is not None
        text = (b.get("text") or "").strip()
        img = (b.get("img_path") or "").strip()

        if is_title:
            split_long()
            flush()
            level = int(b.get("text_level") or 1)
            title = text or "(空标题)"
            if title.strip().lower() in BACK_MATTER:
                level = 1   # 书尾章节提升为独立章
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            if page is not None:
                pages.append(page)
            continue

        if typ == TABLE_TYPE:
            body = table_text(b.get("table_body"))
            if body:
                pending_text.append(body)
            if img:
                pending_imgs.append(f"{book_id}/{os.path.basename(img)}")
            if page is not None:
                pages.append(page)
            continue

        if typ in IMAGE_TYPES:
            if img:
                pending_imgs.append(f"{book_id}/{os.path.basename(img)}")
            if page is not None:
                pages.append(page)
            continue

        if typ in TEXT_TYPES and text:
            pending_text.append(text)
            if page is not None:
                pages.append(page)

    split_long()
    flush()
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--title", required=True, help="书名(source 字段)")
    ap.add_argument("--source-file", required=True, help="原 PDF 文件名(source_file 字段)")
    ap.add_argument("--content-list", required=True, help="merged content_list_v2.json 路径")
    ap.add_argument("--json-out", required=True, help="输出 json 路径")
    ap.add_argument("--token-max", type=int, default=800)
    ap.add_argument("--page-base", type=int, default=1, help="page_idx 0-based → 输出 +1")
    ap.add_argument("--inspect", action="store_true", help="打印 schema 摘要后退出")
    args = ap.parse_args()

    blocks = json.load(open(args.content_list, encoding="utf-8"))
    if args.inspect:
        import collections
        types = collections.Counter(b.get("type") for b in blocks if isinstance(b, dict))
        print(f"总块数 {len(blocks)}; type 分布: {dict(types)}")
        return

    chunks = build_chunks(blocks, args.book_id, args.title, args.source_file,
                          args.token_max, args.page_base)
    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=1)
    with_img = sum(1 for c in chunks if c["images"])
    no_page = sum(1 for c in chunks if not c["page"])
    table_chunks = sum(1 for c in chunks if " | " in c["text"] or "<table>" in c["text"])
    print(f"分块完成: {len(chunks)} 块, 带图 {with_img}, 无页码 {no_page}, 含表 {table_chunks}, 输出 {args.json_out}")


if __name__ == "__main__":
    main()
