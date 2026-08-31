# -*- coding: utf-8 -*-
"""rag_core 纯逻辑单元测试: RRF 融合 / 关键词分级过滤 / 同文档扩展。"""
import rag_core
from rag_core import detect_book_filter, detect_topic_books, expand_same_doc, rrf_merge, tokenize


def test_rrf_merge_dedup_and_order():
    a = [("a", 0.9), ("b", 0.8)]
    b = [("b", 0.85), ("c", 0.7)]
    fused = dict(rrf_merge([a, b], k=60))
    # 双通道都命中的 b 应排在只命中单通道的 a/c 之上
    assert fused["b"] > fused["a"]
    assert fused["a"] > fused["c"]


def test_rrf_merge_empty_channels():
    assert rrf_merge([]) == []
    assert rrf_merge([[], []]) == []


def test_detect_book_filter_strong_only():
    # 专有名词(强信号)命中 → 过滤
    assert detect_book_filter("卡诺瓦的训练法怎么样") == ["b008", "d001", "d002", "d003"]
    assert detect_book_filter("丹尼尔斯 VDOT 怎么查") == ["b006"]
    # 互斥关键词各自生效(并集)
    got = detect_book_filter("卡诺瓦和丹尼尔斯都提到配速")
    assert "b006" in got and "b008" in got


def test_detect_book_filter_topic_words_do_not_filter():
    # 主题词弱信号不再硬过滤(曾把这类问题锁进单本书)
    assert detect_book_filter("跑后怎么补充营养") is None
    assert detect_book_filter("力量训练对跑步有帮助吗") is None
    assert detect_book_filter("普通问题没有关键词") is None


def test_detect_topic_books_soft_bonus():
    # 主题词 → 软加分书列表(用于第三路召回通道,不做硬过滤)
    assert detect_topic_books("跑后怎么补充营养") == ["b013"]
    assert detect_topic_books("髂胫束和康复训练") == ["b016"]
    assert detect_topic_books("什么是周期化训练") == ["b009", "b010"]
    # 无主题词 → None(第三路通道不启用)
    assert detect_topic_books("VDOT 42 配速") is None
    # 主题词与强关键词都命中时互不干扰(强关键词仍然硬过滤)
    assert detect_book_filter("丹尼尔斯书里的营养建议") == ["b006"]


def test_tokenize_keeps_terms():
    toks = tokenize("VDOT 41 的 马拉松 配速")
    assert "vdot" in toks
    assert "马拉松" in toks
    # 单字/标点会被过滤
    assert all(len(t) >= 2 for t in toks)


def _fake_chunks():
    return {f"b006-{s:04d}": {"id": f"b006-{s:04d}"} for s in range(1, 11)}


def test_expand_same_doc_adds_neighbors(monkeypatch):
    monkeypatch.setattr(rag_core, "load_chunks", _fake_chunks)
    got = expand_same_doc(["b006-0003", "b006-0009"], window=2, max_add=8)
    # 主块在前且顺序不变
    assert got[:2] == ["b006-0003", "b006-0009"]
    # 邻块被追加: 0003 的邻块(0001/0002/0004/0005)存在; 边界 0009 无 0010+ 越界
    assert "b006-0001" in got and "b006-0004" in got and "b006-0005" in got
    # 全程不出现越界/不属于该书的 id
    assert all(i.startswith("b006-") and 1 <= int(i[-3:]) <= 10 for i in got)


def test_expand_same_doc_dedup_and_cap(monkeypatch):
    monkeypatch.setattr(rag_core, "load_chunks", _fake_chunks)
    got = expand_same_doc(["b006-0005"], window=2, max_add=2)
    assert got[0] == "b006-0005"
    assert len(got) == 1 + 2  # 只追加 2 个(上限)
    assert len(set(got)) == len(got)


def test_expand_same_doc_disabled_window():
    assert expand_same_doc(["b006-0003"], window=0) == ["b006-0003"]
    assert expand_same_doc(["b006-0003"], max_add=0) == ["b006-0003"]
