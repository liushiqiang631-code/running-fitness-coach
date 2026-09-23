# -*- coding: utf-8 -*-
"""问答 LLM 层: 上下文组装 + Prompt(5.6 设计) + 流式/非流式调用。

要点:
- 块按「来源+页码」编号,如 [1] 丹尼尔斯 p91
- 引用校验: 只允许引用本次检索返回的块编号
- 数据类问题要求给具体数字;多流派冲突并列呈现;伤病给免责声明
- 多轮对话先做 query 改写(补全指代)再检索,历史保留最近几轮摘要
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from config_loader import load_config, get_api_key

from rag_core import retrieve, load_chunks, tokenize

CFG = load_config()
CHAT = CFG["models"]["chat"]
RET = CFG["retrieval"]

# 兼容 openai sdk(>=1.0) 与自定义 base_url
from openai import OpenAI

_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI(base_url=CHAT["base"], api_key=get_api_key("siliconflow"))
    return _client


def _short_source(chunk):
    return chunk.get("source", "").split("(")[0].strip()


def build_system_prompt():
    return (
        "你是专业的跑步/体能训练教练助手,基于提供的文献内容回答用户问题。\n"
        "规则:\n"
        "1. 只引用检索到的文献内容作答,答案中出现的每个事实都应能在给出的编号块([1][2]…)中找到依据。\n"
        "2. 回答格式: 先给结论,再列依据(注明来源书名+页码,如「来源:《丹尼尔斯经典跑步训练法》p91」),最后给可执行建议。\n"
        "3. 数据类问题(配速/VDOT/1RM/体重等): 必须从检索到的表格中直接找到对应行取值,给出具体数字。严禁用插值、推算、心算方式估算数据类答案(如根据配速反推VDOT)。若检索到的内容里没有与该问题直接对应的表值,明确回答「文献中未找到该具体数据」,并说明检索到了什么相关表格。\n"
        "4. 不同流派/作者观点冲突时(如卡诺瓦 vs 丹尼尔斯 vs 挪威方法): 并列呈现各方观点并分别标注来源,不要擅自掩盖或合并。\n"
        "5. 伤病康复类问题: 只给一般性康复建议,并明确标注「非医疗诊断,严重请及时就医」。\n"
        "6. 检索内容不足以回答时,直接说「文献中未找到相关内容」,绝不编造。\n"
        "7. 每个引用编号必须在给出的块编号范围内,不得引用不存在的内容。\n"
        "8. 输出用纯文本,禁止任何 Markdown 标记(如 **加粗**、*斜体*、# 标题、--- 分隔线、> 引用)。"
        "需要强调时用引号或「重点」说明,分条用「1. 2. 3.」即可。\n"
    )


def build_context_text(chunks):
    """把检索块组装成编号上下文,预算 max_context_tokens。"""
    lines = []
    budget = RET["max_context_tokens"]
    used = 0
    for i, r in enumerate(chunks, start=1):
        c = r["chunk"]
        src = _short_source(c)
        page = c.get("page") or "无页码"
        head = f"[{i}]《{src}》p{page}"
        if c.get("chapter"):
            head += f" 章节:{c['chapter']}"
        if c.get("images"):
            head += f" [含插图:{len(c['images'])}张]"
        head += ":\n"
        text = (c.get("text") or "").strip()
        # 粗略 token 预算(中文约 1 字符≈0.7 token,此处按字符/1.5 估)
        est = len(text) // 1.5
        if used + est > budget and used > 0:
            break
        used += est
        lines.append(head + text)
    return "\n\n".join(lines)


def _rewrite_query(query, history):
    """多轮追问改写为独立 query,如「那 10K 呢」→「10K 训练配速怎么定」。"""
    if not history:
        return query
    sys_msg = (
        "你是检索查询改写器。把用户在多轮对话中的最新问题,结合上文改写成一条能独立检索的查询。"
        "只输出改写后的查询本身,不要任何解释。"
    )
    msgs = [{"role": "system", "content": sys_msg}]
    for h in history[-4:]:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": query})
    try:
        client = get_client()
        r = client.chat.completions.create(
            model=CHAT["name"], messages=msgs, temperature=0.2, max_tokens=100
        )
        q = r.choices[0].message.content.strip().strip('"').strip("「」")
        return q if q else query
    except Exception as e:
        print(f"[query 改写失败,用原文] {e}", file=sys.stderr)
        return query


def build_messages(query, chunks, history=None):
    sys_prompt = build_system_prompt()
    ctx = build_context_text(chunks)
    user_content = (
        f"【检索到的文献内容(按编号引用)】\n{ctx}\n\n"
        f"【用户问题】{query}\n\n"
        f"请根据上述文献内容回答。引用时用[编号]标注,并在依据处注明书名和页码。"
    )
    msgs = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-6:]:
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": user_content})
    return msgs


def retrieve_for_chat(query, history=None):
    """改写(多轮)→ 检索 → 返回 (final_query, chunks)。"""
    q = _rewrite_query(query, history)
    chunks = retrieve(q, top_n=RET["rerank_top_n"])
    return q, chunks


def build_sources(chunks):
    """将本次检索结果转为用于 UI 的去重来源卡片。"""
    sources = []
    seen = set()
    for result in chunks:
        chunk = result.get("chunk") or {}
        title = _short_source(chunk).strip()
        page = chunk.get("page")
        if not title or page in (None, ""):
            continue
        item = {"title": title, "page": str(page)}
        key = (item["title"], item["page"])
        if key not in seen:
            seen.add(key)
            sources.append(item)
    return sources


def _answer(query, chunks, history=None, stream=True):
    """流式/非流式回答的公共核心。chunks 为已检索结果(由调用方决定是否重新检索)。
    注意: 本函数是生成器,空检索的提示语必须 yield(生成器里的 return 值会被调用方丢弃)。"""
    if not chunks:
        yield "文献中未找到相关内容。请换种问法,或尝试询问训练方法、配速体系、伤病康复等主题。"
        return
    messages = build_messages(query, chunks, history)
    client = get_client()
    if stream:
        stream_obj = client.chat.completions.create(
            model=CHAT["name"], messages=messages, stream=True, temperature=0.4
        )
        for event in stream_obj:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content
    else:
        r = client.chat.completions.create(model=CHAT["name"], messages=messages, temperature=0.4)
        yield r.choices[0].message.content


def chat_stream(query, history=None):
    """SSE 流式回答。逐段 yield 文本增量。"""
    q, chunks = retrieve_for_chat(query, history)
    yield from chat_stream_from_chunks(q, chunks, history)


def chat_stream_from_chunks(query, chunks, history=None):
    """基于已检索的块逐段输出文本，避免端点重复检索。"""
    yield from _answer(query, chunks, history, stream=True)


def chat(query, history=None):
    """非流式(评估/测试用)。"""
    q, chunks = retrieve_for_chat(query, history)
    return "".join(_answer(q, chunks, history, stream=False))


if __name__ == "__main__":
    import time

    q = sys.argv[1] if len(sys.argv) > 1 else "VDOT 42 配速是多少"
    t0 = time.time()
    ans = chat(q)
    print(ans)
    print(f"\n--- 耗时 {time.time()-t0:.1f}s ---")
