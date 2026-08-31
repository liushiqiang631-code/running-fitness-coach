from app.chat import build_sources


def test_build_sources_keeps_first_unique_title_and_page_only():
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
