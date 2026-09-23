# -*- coding: utf-8 -*-
"""跑步健身教练 RAG 核心: 多路召回(语义 dense + BM25 + 强关键词过滤 + 主题词软加分)
+ RRF 融合 + rerank 重排 + 同文档扩展。供 FastAPI 与命令行/评估脚本共用。

关键点:
- Qdrant(localhost) 用 trust_env=False 直连(绕本机代理)
- bge-m3 嵌入 / bge-reranker-v2-m3 重排走 SiliconFlow API(进程级 httpx 连接池复用)
- Qdrant 不可用时自动降级为仅 BM25
- 强关键词(书名/作者)命中 → dense 元数据硬过滤; 主题词(损伤/营养/周期化等)
  → 作为第三路 RRF 通道加分,不做硬过滤(泛化问题不会被锁进单本书)
"""
import glob
import json
import os
import pickle
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

# 查询嵌入 LRU 缓存(重复问题跳过 embed API 调用,~0.9s/次)
import functools

import httpx
import jieba
import tiktoken
from rank_bm25 import BM25Okapi

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from config_loader import load_config, get_qdrant, get_api_key

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]
BM25_DIR = CFG["paths"]["bm25_dir"]
IMAGES_DIR = CFG["paths"]["images_dir"]
EMBED = CFG["models"]["embed"]
RERANK = CFG["models"]["rerank"]
RET = CFG["retrieval"]
ENC = tiktoken.get_encoding("cl100k_base")

# 跑步术语进 jieba
for _t in CFG.get("bm25_terms", []):
    jieba.add_word(_t)
jieba.add_word("1RM")
jieba.add_word("VDOT")

_TOKEN_RE = re.compile(r"^[\w一-鿿-]{2,}$")

_dense_warned = False  # dense 通道降级只在进程内提示一次(避免刷屏)
_bm25_warned = False  # BM25 通道缺失同理


# 书名/作者等专有名词 → book_id 元数据过滤(强信号: 命中才过滤)
BOOK_KEYWORD_STRONG = {
    "丹尼尔斯": ["b006"],
    "卡诺瓦": ["b008", "d001", "d002", "d003"],
    "挪威": ["b004"],
    "magness": ["b005"],
    "科学跑步": ["b005"],
    "刷新pb": ["b007"],
    "希格登": ["b007"],
    "邦帕": ["b010"],
    "nsca": ["b002", "b003", "b012", "b013", "b014"],
    "美国国家体能": ["b002", "b003", "b012", "b013", "b014"],
    "uphill": ["b019"],   # Training for the Uphill Athlete(书名英文词)
}
# 主题词(弱信号): 营养/损伤/力量训练 等词全库多本书都会讨论。若也参与硬过滤,
# 泛化问题(如「跑后怎么补充营养」)会被人为压到单本书的召回面,故不用于过滤。
# 保留此表供后续做「按书加分」类软排序使用。
BOOK_KEYWORD_TOPIC = {
    "hiit": ["b001"],
    "高强度间歇": ["b001"],
    "周期化": ["b009", "b010"],
    "运动学": ["b011"],
    "力量训练": ["b012"],
    "营养": ["b013"],
    "速度训练": ["b014"],
    "田径": ["b015"],
    "损伤": ["b016"],
    "康复": ["b016"],
    "生理学": ["b017"],
    "解剖": ["b018"],
    "越野": ["b019"],     # 越野/山地跑主题主要在 uphill athlete
    "上坡": ["b019"],
    "山地跑": ["b019"],
}
# 注意: 多个强关键词命中时取并集(should/OR); 互斥关键词(卡诺瓦 vs 丹尼尔斯)各自生效

_chunk_index = None
_bm25_obj = None
_bm25_ids = None
_qdrant = None


def tokenize(text):
    out = []
    for w in jieba.cut(text):
        w = w.strip().lower()
        if w and _TOKEN_RE.match(w):
            out.append(w)
    return out


def load_chunks():
    """惰性加载全部块 id→chunk 索引。"""
    global _chunk_index
    if _chunk_index is None:
        idx = {}
        for f in glob.glob(os.path.join(JSON_DIR, "*.json")):
            for c in json.load(open(f, encoding="utf-8")):
                idx[c["id"]] = c
        _chunk_index = idx
    return _chunk_index


def load_bm25():
    global _bm25_obj, _bm25_ids
    if _bm25_obj is None:
        with open(os.path.join(BM25_DIR, "bm25.pkl"), "rb") as f:
            _bm25_obj = pickle.load(f)
        _bm25_ids = json.load(open(os.path.join(BM25_DIR, "docs.json"), encoding="utf-8"))["ids"]
    return _bm25_obj, _bm25_ids


def get_qdrant_client():
    global _qdrant
    if _qdrant is None:
        _qdrant = get_qdrant()
    return _qdrant


# ---------------- 嵌入 ----------------
# 进程级 httpx 连接池: 每次查询都要打 SiliconFlow(embed + rerank 两次),
# 复用 keep-alive 连接省掉每次查询的 TCP+TLS 握手(~0.2-0.5s);线程安全。
_http: httpx.Client = None


def get_http_client():
    global _http
    if _http is None:
        _http = httpx.Client(trust_env=True, timeout=120)
    return _http


def embed_texts(texts, base=None, model=None):
    """调用 bge-m3 批量嵌入,失败指数退避。返回向量列表。"""
    base = base or EMBED["base"]
    model = model or EMBED["name"]
    key = get_api_key("siliconflow")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": texts}
    delay = 2
    last_err = None
    client = get_http_client()
    for _ in range(5):
        try:
            r = client.post(base + "/v1/embeddings", headers=headers, json=payload, timeout=120)
            if r.status_code == 200:
                data = r.json()["data"]
                data.sort(key=lambda x: x["index"])
                return [d["embedding"] for d in data]
            last_err = f"HTTP {r.status_code}: {r.text[:150]}"
        except httpx.HTTPError as e:
            last_err = str(e)
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"嵌入失败: {last_err}")


@functools.lru_cache(maxsize=RET.get("query_cache_size", 512))
def _embed_query_cached(query):
    return embed_texts([query])[0]


def embed_query(query):
    """查询嵌入,带 LRU 缓存(query_cache_size 由 retrieve 控制)。"""
    return _embed_query_cached(query)


# ---------------- 通道 A: dense ----------------
def dense_retrieve(query_vec, top_k, book_filter=None):
    """Qdrant dense 检索。book_filter: list[book_id] 或 None。"""
    global _dense_warned
    try:
        client = get_qdrant_client()
    except Exception as e:
        if not _dense_warned:
            print(f"[dense 通道不可用,降级为 BM25] {e}", file=sys.stderr)
            _dense_warned = True
        return []
    query_filter = None
    if book_filter:
        # 多书关键词(卡诺瓦/周期化/NSCA)→ 用 should(OR): must(AND)会让一个 point 无法同时匹配多个书 id → dense 全空
        query_filter = {"should": [{"key": "book_id", "match": {"value": b}} for b in book_filter]}
    try:
        # qdrant-client >=1.15 用 query_points(旧 search 已移除)
        res = client.query_points(
            collection_name=client.collection,
            query=query_vec,
            limit=top_k,
            query_filter=query_filter,
        )
        # point id 是 UUID,真实 chunk id 在 payload.chunk_id
        return [(p.payload.get("chunk_id") or p.id, p.score) for p in res.points]
    except Exception as e:
        if not _dense_warned:
            print(f"[dense 通道不可用,降级为 BM25] {e}", file=sys.stderr)
            _dense_warned = True
        return []


# ---------------- 通道 B: BM25 ----------------
def bm25_retrieve(query, top_k):
    global _bm25_warned
    try:
        bm25, ids = load_bm25()
    except Exception as e:
        if not _bm25_warned:
            print(f"[BM25 通道不可用,降级为仅 dense] {e}", file=sys.stderr)
            _bm25_warned = True
        return []
    scores = bm25.get_scores(tokenize(query))
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    out = []
    for i in order:
        if scores[i] <= 0:
            break
        out.append((ids[i], scores[i]))
        if len(out) >= top_k:
            break
    return out


# ---------------- 通道 C: 元数据过滤 ----------------
def detect_book_filter(query):
    """识别 query 中提到的书名/作者等强关键词,返回 book_id 列表(可能多本)。"""
    q = query.lower()
    matched = []
    for kw, books in BOOK_KEYWORD_STRONG.items():
        if kw in q:
            matched.extend(books)
    return list(dict.fromkeys(matched)) if matched else None


def detect_topic_books(query):
    """识别主题词(弱信号: 损伤/营养/周期化…),返回 book_id 列表。
    主题词不做硬过滤(泛化问题会被锁进单本书),而是作为第三路召回通道加分。"""
    q = query.lower()
    matched = []
    for kw, books in BOOK_KEYWORD_TOPIC.items():
        if kw in q:
            matched.extend(books)
    return list(dict.fromkeys(matched)) if matched else None


# ---------------- 融合: RRF ----------------
def rrf_merge(channel_results, k=60):
    """channel_results: list of lists of (id, score)。按 RRF 融合去重。"""
    fused = {}
    for ch in channel_results:
        for rank, (cid, _score) in enumerate(ch, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: -x[1])


# ---------------- 重排 ----------------
def truncate_doc(text, max_tokens):
    toks = ENC.encode(text)
    if len(toks) <= max_tokens:
        return text
    return ENC.decode(toks[:max_tokens])


def rerank_scores(query, candidate_ids, timeout=None):
    """bge-reranker-v2-m3 对全部候选打分,返回 {id: relevance_score}。失败/超时返回空(dict)。
    timeout: 硬超时(秒)。超时降级 → 调用方退回 RRF 排序,保证延迟上界。"""
    if not candidate_ids:
        return {}
    timeout = timeout or RET.get("rerank_timeout", 2.5)
    chunks = load_chunks()
    # 幽灵 id(缓存/库与块索引不一致时)直接跳过,避免 KeyError
    valid = [c for c in candidate_ids if c in chunks]
    if not valid:
        return {}
    docs = [truncate_doc(chunks[c].get("text") or "", RERANK["max_tokens"]) for c in valid]
    key = get_api_key("siliconflow")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": RERANK["name"], "query": query, "documents": docs}
    try:
        r = get_http_client().post(RERANK["base"] + "/v1/rerank", headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            res = r.json()["results"]
            out = {}
            for item in res:
                idx = item.get("index")
                # 供应商返回的 index 越界/缺失时跳过该条,不崩溃
                if isinstance(idx, int) and 0 <= idx < len(valid):
                    out[valid[idx]] = item["relevance_score"]
            return out
        print(f"[rerank HTTP {r.status_code}] {r.text[:150]}", file=sys.stderr)
    except httpx.TimeoutException:
        print(f"[rerank 超时({timeout}s),降级为 RRF]", file=sys.stderr)
    except httpx.HTTPError as e:
        print(f"[rerank 网络错误] {e}", file=sys.stderr)
    return {}


# ---------------- 同文档扩展 ----------------
def _neighbor_ids(cid, window):
    """返回同书相邻块 id(±window)。id 形如 <book>-<seq:04d>,超出范围/不存在则跳过。"""
    try:
        book, seq_s = cid.rsplit("-", 1)
        seq = int(seq_s)
    except (ValueError, IndexError):
        return []
    chunks = load_chunks()
    out = []
    for s in range(max(1, seq - window), seq + window + 1):
        if s == seq:
            continue
        nid = f"{book}-{s:04d}"
        if nid in chunks:
            out.append(nid)
    return out


def expand_same_doc(cids, window=None, max_add=None):
    """对主结果做同文档扩展: 主块保持原顺序在前,邻块追加在尾部(不挤占主块位置)。

    避免 800 token 切块把同一表格/段落的前后文拆散——数据类问题(配速表/VDOT 表)
    的答案页往往就落在邻块里。窗口与上限由 config 控制,邻居不参与 rerank。
    """
    window = RET.get("same_doc_window", 2) if window is None else window
    max_add = RET.get("same_doc_max", 8) if max_add is None else max_add
    if not window or not cids:
        return list(cids)
    out = list(cids)  # 主块保持原位置
    added = set()
    owner_set = set(cids)
    for cid in cids:
        if len(added) >= max_add:
            break
        for nid in _neighbor_ids(cid, window):
            if nid in owner_set or nid in added:
                continue
            added.add(nid)
            out.append(nid)
            if len(added) >= max_add:
                break
    return out


# ---------------- 总入口 ----------------
@functools.lru_cache(maxsize=RET.get("query_cache_size", 512))
def _retrieve_cached(query):
    """完整检索管线(缓存核心): 返回 ((cid, score), ...) 有序元组。
    缓存键 = query: 完全相同的问题直接命中,即时返回(管线内部与 top_n 无关,
    切片由 retrieve() 做,避免同题不同 top_n 存重复缓存)。"""
    dense_k = RET["dense_top_k"]
    bm25_k = RET["bm25_top_k"]

    book_filter = detect_book_filter(query) if RET.get("metadata_filter", True) else None
    topic_books = detect_topic_books(query) if RET.get("metadata_filter", True) else None

    # 通道 B: BM25(本地;索引缺失时降级为只用 dense)
    bm25_hits = bm25_retrieve(query, bm25_k)

    # 通道 A: dense
    dense_hits = []
    try:
        qvec = embed_query(query)
        dense_hits = dense_retrieve(qvec, dense_k, book_filter)
    except Exception as e:
        print(f"[dense 通道不可用] {e}", file=sys.stderr)
        qvec = None

    # 通道 C: 主题词软加分(如「损伤/营养」)→ 对主题书再查一次 dense。
    # 候选在 RRF 中双计 → 主题书被加强; 泛化问题无主题词 → 与原管线完全一致。
    topic_hits = []
    if topic_books and qvec is not None:
        try:
            topic_hits = dense_retrieve(qvec, dense_k, topic_books)
        except Exception as e:
            print(f"[topic 通道不可用] {e}", file=sys.stderr)

    if not dense_hits and not bm25_hits and not topic_hits:
        return ()

    # RRF 融合
    fused = rrf_merge([dense_hits, bm25_hits, topic_hits], k=RET["rrf_k"])
    fused_ids = [cid for cid, _ in fused[: RET["rerank_input"]]]

    # 重排打分(只对进了 rerank_input 窗口的候选打分; 超时降级为 RRF 排序)
    rscores = rerank_scores(query, fused_ids)

    # 最终排序 = w * rerank归一 + (1-w) * rrf归一
    w = RET.get("rerank_weight", 0.5)
    max_r = max(rscores.values()) if rscores else 1.0
    max_r = max_r if max_r > 0 else 1.0  # 全 0 分时避免 0.0/0.0 除零
    rrf_vals = dict(fused)
    max_rrf = max((rrf_vals[c] for c in fused_ids), default=1.0)
    scored = []
    for cid in fused_ids:
        rrf_norm = rrf_vals[cid] / max_rrf if max_rrf else 0.0
        r_norm = (rscores.get(cid, 0.0) / max_r) if rscores else 0.0
        scored.append((cid, w * r_norm + (1 - w) * rrf_norm))
    scored.sort(key=lambda x: -x[1])
    return tuple((cid, round(float(sc), 4)) for cid, sc in scored)


def retrieve(query, top_n=None, verbose=False):
    """完整检索管线,返回 [{id, score, chunk}] 按重排后顺序(带结果缓存)。
    降级: 任一通道失败不影响整体(Qdrant 挂 → 只走 BM25)。
    """
    if top_n is None:
        top_n = RET["rerank_top_n"]
    ordered = _retrieve_cached(query)
    # 主块 = 重排后的前 top_n; 同文档扩展只对主块做: 邻块追加其后,不挤占主块位置
    primaries = ordered[:top_n]
    ids = [cid for cid, _ in primaries]
    if RET.get("same_doc_expand", True):
        ids = expand_same_doc(ids)
    score_of = dict(primaries)
    chunks = load_chunks()
    results = []
    for cid in ids:
        chunk = chunks.get(cid)
        if not chunk:
            continue
        results.append({"id": cid, "score": score_of.get(cid, 0.0), "chunk": chunk})
    return results


if __name__ == "__main__":
    import time

    for q in sys.argv[1:]:
        t0 = time.time()
        print(f"\n=== Q: {q} ===")
        res = retrieve(q, top_n=6, verbose=True)
        for r in res:
            c = r["chunk"]
            print(f"  {r['id']} s={r['score']} p{c.get('page')} {c.get('source','')[:22]}")
            print(f"    {(c.get('text') or '')[:90]}".replace("\n", " "))
        print(f"  耗时 {time.time()-t0:.1f}s")
