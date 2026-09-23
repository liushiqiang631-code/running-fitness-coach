# -*- coding: utf-8 -*-
"""全量嵌入 14,777 块 → bge-m3 → 缓存到 rag/data/cache/。

特性:
- 批量 128 块/次调用 SiliconFlow /v1/embeddings(单次调用固定延迟 ~4-5s,大 batch 摊薄)
- 长块按 bge-m3 上下文上限(8000 token)截断
- 断点续跑:已嵌入 id 从缓存跳过,中断后重跑同命令即可续
- 缓存每 save_every 批落盘一次(避免每批重写大 npy 的开销)
- API 失败指数退避重试

运行: python rag/scripts/embed_chunks.py [--batch N] [--limit N](调试用,只嵌前 N 块)
"""
import argparse
import glob
import json
import os
import sys
import time

import httpx
import numpy as np
import tiktoken

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config_loader import load_config, get_api_key

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]
CACHE_DIR = CFG["paths"]["cache_dir"]
EMBED = CFG["models"]["embed"]
MAX_TOKENS = EMBED["max_tokens"]

CACHE_NPY = os.path.join(CACHE_DIR, "embeddings.npy")
CACHE_META = os.path.join(CACHE_DIR, "meta.json")

ENC = tiktoken.get_encoding("cl100k_base")


def load_all_chunks():
    chunks = []
    for f in sorted(glob.glob(os.path.join(JSON_DIR, "*.json"))):
        data = json.load(open(f, encoding="utf-8"))
        chunks.extend(data)
    return chunks


def truncate_text(text, max_tokens=MAX_TOKENS):
    """超长文本截断到 max_tokens,返回 (截断后文本, 是否截断)。"""
    tokens = ENC.encode(text)
    if len(tokens) <= max_tokens:
        return text, False
    return ENC.decode(tokens[:max_tokens]), True


def load_cache():
    """读已有缓存: (ids列表, vectors npy)。"""
    if not (os.path.exists(CACHE_NPY) and os.path.exists(CACHE_META)):
        return [], np.zeros((0, EMBED["dim"]), dtype=np.float32)
    meta = json.load(open(CACHE_META, encoding="utf-8"))
    vecs = np.load(CACHE_NPY)
    return meta["ids"], vecs


def save_cache(ids, vectors):
    os.makedirs(CACHE_DIR, exist_ok=True)
    # 注意: np.save 会为不以 .npy 结尾的文件名追加 .npy,故 tmp 名用 _tmp 前缀而非 .tmp 后缀
    tmp_npy = os.path.join(CACHE_DIR, "embeddings_tmp.npy")
    tmp_meta = os.path.join(CACHE_DIR, "meta_tmp.json")
    np.save(tmp_npy, vectors)
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump({"ids": ids, "dim": EMBED["dim"]}, f)
    os.replace(tmp_npy, CACHE_NPY)
    os.replace(tmp_meta, CACHE_META)


def embed_batch(client, texts):
    """调用 bge-m3,返回向量列表。失败重试(指数退避,最多 5 次)。"""
    key = get_api_key("siliconflow")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": EMBED["name"], "input": texts}
    delay = 2
    for attempt in range(5):
        try:
            r = client.post(EMBED["base"] + "/v1/embeddings", headers=headers, json=payload, timeout=120)
            if r.status_code == 200:
                data = r.json()["data"]
                data.sort(key=lambda x: x["index"])
                return [d["embedding"] for d in data]
            # 限流/5xx → 退避重试
            print(f"  HTTP {r.status_code}: {r.text[:200]} 等待 {delay}s 重试", flush=True)
        except httpx.HTTPError as e:
            print(f"  网络错误: {e} 等待 {delay}s 重试", flush=True)
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"嵌入失败: 批内文本前 80 字符 = {texts[0][:80]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=EMBED.get("batch_size", 128))
    ap.add_argument("--save-every", type=int, default=10, help="每 N 批落盘一次缓存")
    ap.add_argument("--limit", type=int, default=0, help="调试:只嵌前 N 块")
    args = ap.parse_args()

    chunks = load_all_chunks()
    if args.limit:
        chunks = chunks[: args.limit]
    print(f"总块数: {len(chunks)}")

    existing_ids, vectors = load_cache()
    have = set(existing_ids)
    pending = [c for c in chunks if c["id"] not in have]
    print(f"缓存已有 {len(existing_ids)},待嵌入 {len(pending)}")

    if not pending:
        print("全部已嵌入,无需处理。")
        return

    # 索引: id -> chunk,便于新批按序拼接
    new_ids = []
    new_vecs = []

    with httpx.Client(trust_env=True, timeout=120) as client:
        for i in range(0, len(pending), args.batch):
            batch = pending[i : i + args.batch]
            texts = []
            truncated_flags = []
            for c in batch:
                t, flag = truncate_text(c.get("text") or "")
                texts.append(t)
                truncated_flags.append(flag)
            vecs = embed_batch(client, texts)
            assert len(vecs) == len(batch), f"返回数量 {len(vecs)} != 请求 {len(batch)}"
            for c, v, flag in zip(batch, vecs, truncated_flags):
                new_ids.append(c["id"])
                new_vecs.append(v)
                if flag:
                    print(f"  截断: {c['id']} ({c.get('token_count')} token)", flush=True)
            done = len(existing_ids) + len(new_ids)
            # 每 save_every 批落盘一次(减少大 npy 重写开销),断点续跑粒度 = save_every 批
            if len(new_ids) >= args.save_every * args.batch:
                ids_all = existing_ids + new_ids
                vecs_all = np.vstack([vectors, np.asarray(new_vecs, dtype=np.float32)])
                save_cache(ids_all, vecs_all)
                print(f"  进度 {done}/{len(chunks)} ({done/len(chunks)*100:.1f}%) [已落盘]", flush=True)
            else:
                print(f"  进度 {done}/{len(chunks)} ({done/len(chunks)*100:.1f}%)", flush=True)

    # 收尾落盘
    if new_ids:
        ids_all = existing_ids + new_ids
        vecs_all = np.vstack([vectors, np.asarray(new_vecs, dtype=np.float32)])
        save_cache(ids_all, vecs_all)

    print(f"完成: 缓存 {len(existing_ids) + len(new_ids)} 个向量 → {CACHE_NPY}")


if __name__ == "__main__":
    main()
