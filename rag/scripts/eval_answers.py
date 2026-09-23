# -*- coding: utf-8 -*-
"""回答质量评估: 引用页码正确率 + 流式输出验证。

对测试集每道题:
- 用「SSE 流式路径」生成回答(与 Web 应用同一代码路径 _answer/chat_stream)
- 校验每个 [n] 引用:
    1) 引用编号越界(> 本次检索结果数)→ 幻觉引用
    2) 引用对应块的页码 vs 回答中标注的页码 → 页码正确率
- 记录流式段数(验证流式输出是否正常)

指标:
- 引用正确率 = 正确引用 / 总引用(达标线 ≥90%)
- 题级引用通过率 = 全部引用都正确的题 / 总题
- 流式成功率 = 流式输出产生 ≥1 段 / 总题(目标 100%)
- 无引用回答(检索为空→"未找到")不计入引用错误

运行: python rag/scripts/eval_answers.py [--maxq N]
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
from config_loader import load_config
from chat import retrieve_for_chat, _answer
from eval_retrieval import page_overlap

CFG = load_config()
EVAL_DIR = CFG["paths"]["eval_dir"]
TESTSET_PATH = os.path.join(EVAL_DIR, "testset.json")

CITE_RE = re.compile(r"\[(\d{1,3})\]")
# 只认 p123 / p 123 / p92-93 / 第123页; 必须避开书名里的「第2版/第4版」
PAGE_IN_ANSWER_RE = re.compile(r"(?:p\s*\.?\s*)(\d+)(?:\s*[-–~]\s*(\d+))?|第\s*(\d+)\s*页")
# 页表来自块: chunk.page 形如 "91" 或 "92-93"


def citations_in(text):
    """提取回答中的 (编号, 编号后60字符) 列表。"""
    out = []
    for m in CITE_RE.finditer(text):
        idx = int(m.group(1))
        ctx = text[m.start(): m.start() + 60]
        out.append((idx, ctx))
    return out


def find_answer_page(ctx):
    """从引用上下文提取页码, 如 p125 / p92-93 / 第125页(避开书名里的第N版)。"""
    m = PAGE_IN_ANSWER_RE.search(ctx)
    if not m:
        return None
    if m.group(2):  # p92-93
        return f"{m.group(1)}-{m.group(2)}"
    if m.group(3):  # 第123页
        return m.group(3)
    return m.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maxq", type=int, default=0, help="调试: 只跑前 N 题")
    args = ap.parse_args()

    ts = json.load(open(TESTSET_PATH, encoding="utf-8"))
    qs = ts["questions"]
    if args.maxq:
        qs = qs[: args.maxq]

    total_q = 0
    q_with_cite = 0
    q_cite_ok = 0          # 题内所有引用都正确
    q_stream_ok = 0        # 流式输出正常(≥1 段)
    total_cite = 0
    cite_ok = 0
    oob_cites = 0          # 越界引用(幻觉)
    page_mismatch = 0
    no_source = 0          # 未找到答案(无引用)
    failures = []

    t_start = time.time()
    for item in qs:
        q = item["query"]
        total_q += 1
        try:
            fq, chunks = retrieve_for_chat(q)
        except Exception as e:
            print(f"[检索失败] {item['id']} {q}: {e}", file=sys.stderr)
            failures.append({"id": item["id"], "query": q, "err": str(e)})
            continue

        # 用流式路径生成回答(同 Web 应用)
        segs = 0
        ans = ""
        try:
            for seg in _answer(fq, chunks, stream=True):
                ans += seg
                segs += 1
        except Exception as e:
            print(f"[生成失败] {item['id']} {q}: {e}", file=sys.stderr)
            failures.append({"id": item["id"], "query": q, "err": str(e)})
            continue

        if segs >= 1:
            q_stream_ok += 1
        if not chunks:
            # 检索为空 → 诚实"未找到",无引用可校验
            no_source += 1
            print(f"[未找到] {item['id']} | {q}")
            continue

        # 页码校验: 回答中出现的所有页码,必须存在于本次检索块集合内
        # (兼容 [n] 引用 与 自由文本「来源:书名 pXX」两种格式; 防幻觉引用)
        q_ok = True
        cites = citations_in(ans)
        oob = 0
        for idx, _ctx in cites:
            if idx < 1 or idx > len(chunks):
                oob += 1
                q_ok = False
        # 收集回答里的页码
        answer_pages = [p for m in PAGE_IN_ANSWER_RE.finditer(ans) if (p := (m.group(2) and f"{m.group(1)}-{m.group(2)}") or (m.group(3) or m.group(1)))]
        chunk_pages = [c["chunk"].get("page") for c in chunks]
        bad_pages = [p for p in answer_pages if not any(page_overlap([p], cp) for cp in chunk_pages)]
        total_cite += len(answer_pages)
        cite_ok += len(answer_pages) - len(bad_pages)
        page_mismatch += len(bad_pages)
        oob_cites += oob
        q_with_cite += 1 if (cites or answer_pages) else 0
        if not (cites or answer_pages):
            failures.append({"id": item["id"], "query": q, "reason": "有检索结果但回答无任何引用/页码", "answer": ans[:200]})
            print(f"[无引用] {item['id']} | {q}")
        elif q_ok and not bad_pages:
            q_cite_ok += 1
            print(f"[OK ] {item['id']} cites={len(cites)} pages={len(answer_pages)} segs={segs} {q}")
        else:
            failures.append({"id": item["id"], "query": q, "oob": oob,
                             "bad_pages": bad_pages, "chunk_pages": chunk_pages})
            print(f"[X  ] {item['id']} cites={len(cites)} pages={len(answer_pages)} oob={oob} bad={bad_pages} {q}")

    # ---- 报告 ----
    lines = []
    def out(s=""):
        print(s); lines.append(s)

    out("=" * 66)
    out(f"回答质量评估 {total_q} 题 · 耗时 {time.time()-t_start:.0f}s")
    out(f"引用总条数: {total_cite}  引用正确: {cite_ok}  "
        f"越界(幻觉): {oob_cites}  页码不符: {page_mismatch}")
    if total_cite:
        out(f"引用页码正确率 = {cite_ok/total_cite*100:.1f}%   (验收线 ≥90%)")
    out(f"题级引用通过率 = {q_cite_ok}/{q_with_cite} = {q_cite_ok/q_with_cite*100:.1f}%")
    out(f"流式输出成功率 = {q_stream_ok}/{total_q} = {q_stream_ok/total_q*100:.1f}%")
    out(f"检索为空→诚实未找到: {no_source} 题")
    out("=" * 66)

    report_path = os.path.join(EVAL_DIR, "answer_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n== FAILURES ==\n" + json.dumps(failures, ensure_ascii=False, indent=1))
    out(f"报告: {report_path}")


if __name__ == "__main__":
    main()
