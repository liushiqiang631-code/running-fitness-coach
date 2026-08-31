# -*- coding: utf-8 -*-
"""检索评估函数与 chat 回答层测试: page_overlap / 空检索非流式返回。"""
from chat import _answer, build_sources
from eval_retrieval import page_overlap


# ---------------- 页码重叠 ----------------
def test_page_overlap_exact():
    assert page_overlap(["91"], "91")
    assert page_overlap(["91"], "92-93") is False


def test_page_overlap_range():
    assert page_overlap(["95"], "95-96")
    assert page_overlap(["34-35"], "34-36")  # 区间有交集
    assert page_overlap(["34-35"], "36") is False


def test_page_overlap_empty_golden():
    assert page_overlap([], "99")  # 未指定页码 = 按书即可
    assert page_overlap([], None)


# ---------------- 空检索回答(修复回归) ----------------
def test_answer_empty_chunks_nostream_still_returns_message():
    # 曾: 生成器里 return 值被 join 丢弃 → 返回空串
    out = "".join(_answer("问题", [], stream=False))
    assert "文献中未找到相关内容" in out


def test_answer_empty_chunks_stream_yields_message():
    segs = list(_answer("问题", [], stream=True))
    assert segs and "文献中未找到相关内容" in segs[0]


# ---------------- 来源卡片 ----------------
def test_build_sources_dedup_when_title_page_int_vs_str():
    chunks = [
        {"chunk": {"source": "丹尼尔斯经典跑步训练法(杰克·丹尼尔斯)", "page": 91}},
        {"chunk": {"source": "丹尼尔斯经典跑步训练法(杰克·丹尼尔斯)", "page": "91"}},
        {"chunk": {"source": "卡诺瓦.pdf", "page": 42}},
        {"chunk": {"source": "没有页码的书", "page": None}},
        {"chunk": {"source": "", "page": 8}},
    ]
    assert build_sources(chunks) == [
        {"title": "丹尼尔斯经典跑步训练法", "page": "91"},
        {"title": "卡诺瓦.pdf", "page": "42"},
    ]
