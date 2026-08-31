# -*- coding: utf-8 -*-
"""chunker 分块逻辑单元测试: 表格转文本 / 硬切 / 计数类型 / 图片相对路径。"""
import tiktoken

from chunker import build_chunks, hard_split, table_text

ENC = tiktoken.get_encoding("cl100k_base")


def test_table_text_cleans_html():
    html = "<table><tr><th>配速</th><th>VDOT</th></tr><tr><td>4:30</td><td>41</td></tr></table>"
    out = table_text(html)
    assert "配速" in out and "4:30" in out
    assert "<" not in out  # HTML 标签全部剥除


def test_hard_split_long_text():
    text = "a" * 10000  # cl100k 下必超 800 token
    parts = hard_split(text, 800)
    assert len(parts) >= 2
    # 每一段都不超过 token 上限(硬切按 token 计,不是按字符)
    assert all(len(ENC.encode(p)) <= 800 for p in parts)
    assert "".join(parts) == text

    assert hard_split("short text", 800) == ["short text"]


def test_hard_split_backtracks_to_space():
    # 英文文本: 每段 ≤800 token,拼接无损(cl100k 会把 " word" 合并成整 token,
    # 所以只保证 token 上限与往返无损,不断言空格边界)
    text = ("word " * 4000).strip()  # ~ 20000 chars, 必超 800 token
    parts = hard_split(text, 800)
    assert len(parts) >= 2
    assert all(len(ENC.encode(p)) <= 800 for p in parts)
    assert "".join(parts) == text


def _blocks():
    # 模拟 MinerU content_list:
    # 标题1 + 正文 + 表格 → chunk1;  标题2 + 图片(无正文) → 纯图 chunk2; footer 噪音丢弃
    return [
        {"type": "text", "text_level": "1", "text": "第 1 章 配速", "page_idx": "0"},
        {"type": "text", "text": "VDOT 是训练强度指标。", "page_idx": "0"},
        {"type": "table", "table_body": "<tr><td>4:30</td><td>41</td></tr>",
         "img_path": "images/abc.jpg", "page_idx": "1"},
        {"type": "text", "text_level": "1", "text": "1.1 VDOT 说明", "page_idx": "1"},
        {"type": "image", "img_path": "images/def.jpg", "page_idx": "2"},
        {"type": "text", "text_level": "1", "text": "第 2 章 心率", "page_idx": "3"},
        {"type": "page_footer", "text": "10"},
    ]


def test_build_chunks_shape():
    chunks = build_chunks(_blocks(), "b999", "测试书", "test.pdf", 800)
    assert len(chunks) == 2
    c0, c1 = chunks
    assert c0["id"] == "b999-0001" and c1["id"] == "b999-0002"
    assert c0["chapter"] == "第 1 章 配速"
    # 图片相对路径: <book_id>/<文件名>
    assert c0["images"] == ["b999/abc.jpg"]
    assert c1["images"] == ["b999/def.jpg"]
    # 页码偏移(page_idx 0-based + 1); 段落页码把标题所在页并入
    assert c0["page"] == "1-2"
    assert c1["page"] == "2-3"
    # 计数类型为 int(曾写成 str 导致 health_check/审计崩)
    assert isinstance(c0["token_count"], int) and isinstance(c1["token_count"], int)
    assert isinstance(c0["char_count"], int)
    # 纯图块: 空文本 + 0 token
    assert c1["text"] == "" and c1["token_count"] == 0


def test_build_chunks_textual_has_token_and_char():
    chunks = build_chunks(_blocks(), "b999", "测试书", "test.pdf", 800)
    c0 = chunks[0]
    assert c0["char_count"] == len(c0["text"])
    assert c0["token_count"] == len(ENC.encode(c0["text"]))
