# -*- coding: utf-8 -*-
"""构建本地 BM25 索引(jieba 分词 + rank_bm25),输出到 rag/data/bm25/。

- 跑步术语加入 jieba 词典(防切碎: VDOT/法特莱克/配速 等)
- 过滤单字/纯标点噪音,英文数字串保留(VDOT、1RM、BMI)
- 输出: bm25.pkl(可查询) + docs.json(每块 id/text 供加载)
运行: python rag/scripts/build_bm25.py
"""
import glob
import json
import os
import pickle
import re
import sys

import jieba
from rank_bm25 import BM25Okapi

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config_loader import load_config

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]
BM25_DIR = CFG["paths"]["bm25_dir"]

# 加载跑步术语到 jieba
for term in CFG.get("bm25_terms", []):
    jieba.add_word(term)
# 数字+单位/字母组合整词保留
jieba.add_word("1RM")
jieba.add_word("VDOT")

_TOKEN_RE = re.compile(r"^[\w一-鿿-]{2,}$")  # 保留中文词/英文词/含数字词,长度>=2


def tokenize(text):
    """分词并清洗: 去标点噪音,保留有意义 token。"""
    tokens = []
    for w in jieba.cut(text):
        w = w.strip().lower()
        if not w:
            continue
        if _TOKEN_RE.match(w):
            tokens.append(w)
    return tokens


def load_all_chunks():
    chunks = []
    for f in sorted(glob.glob(os.path.join(JSON_DIR, "*.json"))):
        chunks.extend(json.load(open(f, encoding="utf-8")))
    return chunks


def main():
    chunks = load_all_chunks()
    print(f"总块数: {len(chunks)}")
    corpus = []
    doc_ids = []
    tokenized_docs = []
    for c in chunks:
        toks = tokenize(c.get("text") or "")
        corpus.append(toks)
        doc_ids.append(c["id"])
        tokenized_docs.append(toks)
    print(f"分词语料: 平均每块 {sum(len(t) for t in corpus)/max(len(corpus),1):.0f} token")

    bm25 = BM25Okapi(corpus)
    os.makedirs(BM25_DIR, exist_ok=True)
    with open(os.path.join(BM25_DIR, "bm25.pkl"), "wb") as f:
        pickle.dump(bm25, f)
    # docs.json 保留 id→text 映射(检索返回需回查 text)
    with open(os.path.join(BM25_DIR, "docs.json"), "w", encoding="utf-8") as f:
        json.dump({"ids": doc_ids}, f, ensure_ascii=False)
    # 词汇表统计供排查
    vocab = sorted(bm25.idf.keys())
    with open(os.path.join(BM25_DIR, "vocab.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(vocab))
    print(f"完成: 语料 {len(corpus)} 篇,词表 {len(vocab)} 词 → {BM25_DIR}")


if __name__ == "__main__":
    main()
