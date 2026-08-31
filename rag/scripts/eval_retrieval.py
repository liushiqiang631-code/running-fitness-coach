# -*- coding: utf-8 -*-
"""检索评估: 对测试集 50 题跑完整检索,输出 Recall@10/20、MRR、nDCG@10。

- 命中判定: 结果块的书id ∈ golden 书 且 页码重叠(或 golden 未指定页码=按书即可)
- 输出: 终端报告 + rag/data/eval/report.txt + failures 明细
运行: python rag/scripts/eval_retrieval.py [--topn 20] [--maxq N]
"""
import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
from config_loader import load_config
from rag_core import retrieve

CFG = load_config()
EVAL_DIR = CFG["paths"]["eval_dir"]
TESTSET_PATH = os.path.join(EVAL_DIR, "testset.json")


def page_overlap(golden_pages, chunk_page):
    """页码重叠判定: "95-96" 命中 golden "95" 或 "96"。"""
    if not golden_pages:
        return True
    if not chunk_page:
        return False
    cp = str(chunk_page).replace(" ", "")
    for gp in golden_pages:
        g = str(gp).replace(" ", "")
        if g in cp or cp in g:
            return True
        # 区间重叠: "34-35" vs "35" 已覆盖; "34-35" vs "34-36" 需逐页
        def expand(s):
            if "-" in s:
                a, b = s.split("-")
                try:
                    return set(range(int(a), int(b) + 1))
                except ValueError:
                    return set()
            try:
                return {int(s)}
            except ValueError:
                return set()
        ga, ca = expand(g), expand(cp)
        if ga & ca:
            return True
    return False


def is_hit(chunk, golden):
    """判断某 chunk 是否命中某条 golden。"""
    book_id = chunk.get("book_id") or (chunk.get("id") or "")[:4]
    for g in golden:
        if book_id == g["book"] and page_overlap(g.get("pages", []), chunk.get("page")):
            return True
    return False


def dcg_at_k(rels, k):
    dcg = 0.0
    for i in range(min(k, len(rels))):
        if rels[i]:
            dcg += 1.0 / math.log2(i + 2)
    return dcg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=20, help="检索返回候选数(评估 Recall@10/20)")
    ap.add_argument("--maxq", type=int, default=0, help="调试: 只跑前 N 题")
    args = ap.parse_args()

    ts = json.load(open(TESTSET_PATH, encoding="utf-8"))
    qs = ts["questions"]
    if args.maxq:
        qs = qs[: args.maxq]

    topic_stats = defaultdict(lambda: {"n": 0, "hit10": 0, "hit20": 0, "mrr_sum": 0.0})
    failures = []
    all_n, all_h10, all_h20 = 0, 0, 0
    mrr_sum = 0.0
    ndcg10_sum = 0.0

    t_start = time.time()
    for item in qs:
        q = item["query"]
        golden = item["golden"]
        t0 = time.time()
        res = retrieve(q, top_n=args.topn)
        dt = time.time() - t0

        ids = [r["id"] for r in res]
        hits = [is_hit(r["chunk"], golden) for r in res]
        first_hit_rank = next((i + 1 for i, h in enumerate(hits) if h), None)

        hit10 = any(hits[:10])
        hit20 = any(hits)
        mrr = 1.0 / first_hit_rank if first_hit_rank else 0.0
        ndcg10 = dcg_at_k(hits, 10) / dcg_at_k([True] * min(10, sum(hits)), 10) if any(hits) else 0.0

        st = topic_stats[item["topic"]]
        st["n"] += 1
        st["hit10"] += 1 if hit10 else 0
        st["hit20"] += 1 if hit20 else 0
        st["mrr_sum"] += mrr
        all_n += 1
        all_h10 += 1 if hit10 else 0
        all_h20 += 1 if hit20 else 0
        mrr_sum += mrr
        ndcg10_sum += ndcg10

        status = "OK " if hit20 else "MISS"
        print(f"[{status}] {item['id']} R10={int(hit10)} {q}  rank={first_hit_rank} {dt:.1f}s")
        if not hit20:
            failures.append(
                {"id": item["id"], "topic": item["topic"], "query": q, "golden": golden, "top3": [
                    {"id": i, "book": (r["chunk"].get("id") or "")[:4], "page": r["chunk"].get("page"),
                     "text": (r["chunk"].get("text") or "")[:80]} for i, r in enumerate(res[:3])
                ]}
            )
        if args.maxq and all_n >= args.maxq:
            break

    # ---- 报告 ----
    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 70)
    out(f"测试集 {all_n} 题 · 检索候选 {args.topn} · 耗时 {time.time()-t_start:.0f}s")
    out(f"总体 Recall@10 = {all_h10}/{all_n} = {all_h10/all_n*100:.1f}%")
    out(f"总体 Recall@20 = {all_h20}/{all_n} = {all_h20/all_n*100:.1f}%")
    out(f"总体 MRR = {mrr_sum/all_n:.3f}")
    out(f"总体 nDCG@10 = {ndcg10_sum/all_n:.3f}")
    out("-" * 70)
    out("分主题 Recall@10:")
    for topic, s in sorted(topic_stats.items()):
        out(f"  {topic}: {s['hit10']}/{s['n']} = {s['hit10']/s['n']*100:5.1f}%  (MRR {s['mrr_sum']/s['n']:.3f})")
    out("=" * 70)

    report_path = os.path.join(EVAL_DIR, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n== FAILURES ==\n" + json.dumps(failures, ensure_ascii=False, indent=1))
    if failures:
        out(f"\n未命中 {len(failures)} 题,明细见 {report_path}")
    out(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()
