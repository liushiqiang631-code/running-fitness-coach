# -*- coding: utf-8 -*-
"""标注辅助: 对测试集每题跑 BM25 top-5,打印书+页码+片段,用于核对 golden。
运行: python rag/scripts/annotate.py > rag/data/eval/annotate_dump.txt
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
from rag_core import bm25_retrieve, load_chunks

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data", "eval"))
from testset import TESTSET

chunks = load_chunks()

for item in TESTSET:
    q = item["query"]
    hits = bm25_retrieve(q, 5)
    print(f"\n### {item['topic']} | {q}")
    print(f"    期望书: {item['books']}")
    for cid, score in hits:
        c = chunks.get(cid, {})
        page = c.get("page", "?")
        book = c.get("source", "")[:24]
        print(f"    {cid} s={score:.1f} p{page} [{book}]")
        print(f"      {(c.get('text') or '')[:80]}".replace("\n", " "))
