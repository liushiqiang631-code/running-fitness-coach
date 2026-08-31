# -*- coding: utf-8 -*-
"""从嵌入缓存建 Qdrant collection(running_coach)并全量 upsert。

- dense 1024 + Cosine; payload 带 source/chapter/section/heading_path/page/images/token_count/book_id
- 对 source/chapter/page/book_id 建 payload 索引(支持按书/章节/页码过滤)
- 批量 500 条 upsert; --recreate 先删后建
运行: python rag/scripts/build_collection.py [--recreate]
"""
import argparse
import glob
import json
import os
import sys

import uuid

import numpy as np
from qdrant_client import models

UUID_NS = uuid.UUID("6f0e6a2e-9b1c-4f3a-9d2e-8b7a5c4d3e2f")  # 项目固定命名空间,保证 chunk_id→UUID 确定性


def chunk_uuid(cid):
    return str(uuid.uuid5(UUID_NS, cid))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config_loader import load_config, get_qdrant

CFG = load_config()
CACHE_DIR = CFG["paths"]["cache_dir"]
JSON_DIR = CFG["paths"]["json_dir"]
Q = CFG["qdrant"]


def load_chunk_index():
    idx = {}
    for f in glob.glob(os.path.join(JSON_DIR, "*.json")):
        book_id = os.path.basename(f)[:4]
        for c in json.load(open(f, encoding="utf-8")):
            idx[c["id"]] = (c, book_id)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true", help="先删除已存在 collection")
    ap.add_argument("--limit", type=int, default=0, help="调试:只写前 N 条")
    ap.add_argument("--prune", action="store_true",
                    help="删除语料中已不存在的 chunk_id(书从 JSON 目录移除时清残留点)")
    args = ap.parse_args()

    meta = json.load(open(os.path.join(CACHE_DIR, "meta.json"), encoding="utf-8"))
    ids = meta["ids"]
    vecs = np.load(os.path.join(CACHE_DIR, "embeddings.npy"))
    assert len(ids) == len(vecs), f"缓存不一致: ids={len(ids)} vecs={len(vecs)}"
    print(f"缓存向量: {len(ids)}")

    chunk_index = load_chunk_index()
    client = get_qdrant()
    coll = Q["collection"]

    if args.recreate:
        try:
            client.delete_collection(coll)
            print(f"已删除旧 collection {coll}")
        except Exception as e:
            print(f"删除失败(可能不存在): {e}")

    exists = client.collection_exists(coll)
    if not exists:
        client.create_collection(
            collection_name=coll,
            vectors_config=models.VectorParams(size=Q["vector_size"], distance=models.Distance.COSINE),
        )
        # payload 索引: 按书/章节/页码过滤
        for field in ["source", "chapter", "page", "book_id"]:
            client.create_payload_index(
                collection_name=coll, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )
        print(f"已建 collection {coll} + payload 索引(source/chapter/page/book_id)")
    else:
        print(f"collection {coll} 已存在,追加写入(不重建)")
        # 幂等校验: 旧版脚本用字符串 chunk id 作 point id,UUID 版会与之共存导致重复。
        # 抽查现有 point id 类型,若是字符串说明是旧 schema,必须 --recreate 重建。
        try:
            sample = client.scroll(collection_name=coll, limit=1, with_payload=False, with_vectors=False)
            if sample and sample[0].points:
                pid = sample[0].points[0].id
                if not str(pid).startswith("0000") or "-" not in str(pid):
                    print(
                        f"[警告] 检测到旧 schema 的字符串 point id({pid})。"
                        f"请用 --recreate 重建,否则新 UUID 点会与旧字符串点重复入库。",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"[幂等检查跳过] {e}", file=sys.stderr)

    # 分块 upsert, 500/批
    BATCH = 500
    total = len(ids) if not args.limit else min(args.limit, len(ids))
    points = []
    for i in range(total):
        cid = ids[i]
        chunk, book_id = chunk_index.get(cid, ({}, ""))
        points.append(
            models.PointStruct(
                id=chunk_uuid(cid),  # Qdrant point id 需 UUID/整数;原 id 存 payload.chunk_id
                vector=vecs[i].tolist(),
                payload={
                    "chunk_id": cid,
                    "source": chunk.get("source", ""),
                    "source_file": chunk.get("source_file", ""),
                    "chapter": chunk.get("chapter", ""),
                    "section": chunk.get("section", ""),
                    "heading_path": chunk.get("heading_path", []),
                    "page": chunk.get("page", ""),
                    "images": chunk.get("images", []),
                    "token_count": chunk.get("token_count", 0),
                    "book_id": book_id,
                },
            )
        )
        if len(points) >= BATCH:
            client.upsert(collection_name=coll, points=points)
            print(f"  upsert {i+1}/{total}", flush=True)
            points = []
    if points:
        client.upsert(collection_name=coll, points=points)
        print(f"  upsert {total}/{total}", flush=True)

    if args.prune:
        # 孤儿点清理: 语料中已不存在的 chunk_id → 删除(书被移出 json_dir 时残留)
        keep = set(ids)
        dead = []
        offset = None
        while True:
            pts, offset = client.scroll(
                collection_name=coll, limit=500, with_payload=True,
                with_vectors=False, offset=offset,
            )
            if not pts:
                break
            for p in pts:
                if (p.payload or {}).get("chunk_id") not in keep:
                    dead.append(p.id)
            if offset is None:
                break
        for i in range(0, len(dead), BATCH):
            client.delete(collection_name=coll, points_selector=dead[i : i + BATCH])
        print(f"[prune] 删除孤儿点 {len(dead)} 个")

    cnt = client.count(collection_name=coll)
    print(f"完成: collection {coll} 共 {cnt.count} 条")


if __name__ == "__main__":
    main()
