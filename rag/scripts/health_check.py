# -*- coding: utf-8 -*-
"""嵌入前数据体检:统计 14,777 块的 token 分布、碎片块占比、图片/OCR 情况。
运行: python rag/scripts/health_check.py
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config_loader import load_config

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]


def main():
    files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    total = 0
    tok_hist = Counter()
    book_stats = defaultdict(lambda: {"chunks": 0, "frag": 0, "img": 0, "ocr": 0, "page": 0})
    frag_chunks = []  # <30 token 的样本
    all_pages = 0

    for f in files:
        book_id = os.path.basename(f)[:4]
        chunks = json.load(open(f, encoding="utf-8"))
        for c in chunks:
            total += 1
            tok = c.get("token_count", 0)
            hist_key = (
                "<30" if tok < 30 else
                "30-99" if tok < 100 else
                "100-399" if tok < 400 else
                "400-799" if tok < 800 else
                ">=800"
            )
            tok_hist[hist_key] += 1
            s = book_stats[book_id]
            s["chunks"] += 1
            if tok < 30:
                s["frag"] += 1
                if len(frag_chunks) < 12:
                    frag_chunks.append((book_id, c["id"], tok, (c.get("text") or "")[:60].replace("\n", " ")))
            if c.get("images"):
                s["img"] += 1
            if c.get("ocr_source") or "[表" in (c.get("text") or ""):
                s["ocr"] += 1
            if c.get("page"):
                s["page"] += 1
                all_pages += 1

    print(f"总块数: {total}")
    print(f"token 分布(按块): {dict(sorted(tok_hist.items()))}")
    print(f"碎片块(<30 token): {tok_hist.get('<30', 0)} 占比 {tok_hist.get('<30',0)/total*100:.1f}%")
    print(f"带图片块: {sum(s['img'] for s in book_stats.values())}")
    print(f"带页码块: {all_pages} 占比 {all_pages/total*100:.1f}%")
    print()
    print("各书统计(碎片占比排序):")
    print(f"{'书':<6}{'块数':>6}{'碎片':>6}{'碎片%':>7}{'图片':>6}{'页%':>7}")
    for bid in sorted(book_stats, key=lambda b: -book_stats[b]["frag"] / max(book_stats[b]["chunks"], 1)):
        s = book_stats[bid]
        print(f"{bid:<6}{s['chunks']:>6}{s['frag']:>6}{s['frag']/s['chunks']*100:>6.1f}%{s['img']:>6}{s['page']/s['chunks']*100:>6.1f}%")

    print("\n碎片块样本(<30 token):")
    for book_id, cid, tok, text in frag_chunks:
        print(f"  {cid} tok={tok} | {text}")


if __name__ == "__main__":
    main()
