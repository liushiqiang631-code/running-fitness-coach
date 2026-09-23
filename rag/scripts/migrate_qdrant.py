# -*- coding: utf-8 -*-
"""一次性迁移:把 running_coach 从共享 qdrant(6333)整体搬到健身教练专属 qdrant(6334)。

保留向量/payload/point id;集合参数与 payload 索引与源完全一致。
不重新调用嵌入 API,纯本地搬运。
用法: python rag/scripts/migrate_qdrant.py
"""
import hashlib

from qdrant_client import QdrantClient, models

SRC_URL = "http://127.0.0.1:6333"  # 农业知识库 qdrant(共享实例,只读)
DST_URL = "http://127.0.0.1:6334"  # 健身教练专属 qdrant
COLL = "running_coach"
BATCH = 500

src = QdrantClient(url=SRC_URL, trust_env=False, timeout=120)
dst = QdrantClient(url=DST_URL, trust_env=False, timeout=120)

# 1) 源集合信息
info = src.get_collection(COLL)
v = info.config.params.vectors
print(f"源集合 {COLL}: {info.points_count} 点, 向量 {v.size} 维 / {v.distance}")

# 2) 重建目标集合(参数与源一致)
if dst.collection_exists(COLL):
    dst.delete_collection(COLL)
dst.create_collection(
    collection_name=COLL,
    vectors_config=models.VectorParams(size=v.size, distance=v.distance),
    on_disk_payload=True,
)
for field in ["source", "chapter", "page", "book_id"]:
    dst.create_payload_index(collection_name=COLL, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)
print("目标集合已重建(1024/Cosine, on_disk_payload, payload 索引)")

# 3) scroll + upsert 全量搬运
offset = None
total = 0
while True:
    points, offset = src.scroll(
        collection_name=COLL, limit=BATCH, with_payload=True, with_vectors=True, offset=offset
    )
    if not points:
        break
    pts = [models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points]
    dst.upsert(collection_name=COLL, points=pts)
    total += len(pts)
    print(f"  已搬运 {total}", flush=True)
    if offset is None:
        break

# 4) 校验数量
src_cnt = src.count(collection_name=COLL, exact=True).count
dst_cnt = dst.count(collection_name=COLL, exact=True).count
print(f"数量校验: 源 {src_cnt} 点 / 目标 {dst_cnt} 点 -> {'一致' if src_cnt == dst_cnt else '不一致!'}")

# 5) 按同一点 id 抽样比对向量指纹
p1, _ = src.scroll(collection_name=COLL, limit=1, with_payload=False, with_vectors=True)
if p1:
    pid = p1[0].id
    p2 = dst.retrieve(collection_name=COLL, ids=[pid], with_vectors=True)
    if p2:
        h1 = hashlib.sha256(repr(p1[0].vector).encode()).hexdigest()[:16]
        h2 = hashlib.sha256(repr(p2[0].vector).encode()).hexdigest()[:16]
        print(f"抽样点 {pid}: 向量指纹 源={h1} 目标={h2} -> {'一致' if h1 == h2 else '不一致!'}")
    else:
        print(f"[警告] 目标库查不到抽样点 {pid}")
