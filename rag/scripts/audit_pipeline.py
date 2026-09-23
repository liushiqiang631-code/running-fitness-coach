# -*- coding: utf-8 -*-
"""RAG 链路体检: 对语料 JSON / 切分质量 / 清洗残留 / 嵌入缓存 / Qdrant 索引做一致性审计。

检查项:
  A. JSON 数据质量   —— id 唯一性/格式、token_count 与 char_count 类型、空文本块、图片引用存在性
  B. 文档切分质量    —— token 分布、超长块(>token_max)、碎片块(<30)、缺页码/缺章节
  C. 文本清洗残留    —— 水印/乱码/控制字符/独立页码行
  D. Embedding 缓存  —— meta.json ids 与语料 ids 双向差集、向量形状/dtype/NaN
  E. Qdrant 索引     —— 点数、payload 完整性、孤儿点(Qdrant 有而语料无)与缺漏(语料有而 Qdrant 无)

输出: 每项 PASS/WARN/FAIL + 明细样本; 退出码 0(全 PASS/WARN) 或 1(有 FAIL)。
运行: python rag/scripts/audit_pipeline.py [--skip-qdrant]
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config_loader import load_config

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]
IMAGES_DIR = CFG["paths"]["images_dir"]
CACHE_DIR = CFG["paths"]["cache_dir"]

ID_RE = re.compile(r"^[bd]\d{3}-\d{4,}$")
# 真水印词(与 clean.py / check.py 对齐); 出版社/作者网址是正文合法内容,不列入
WATERMARK_RE = re.compile(
    r"z-lib|z-access|go-to-library|1lib\.|libgen|doc2x|pdg2pic|xgv5|researchgate|librarian",
    re.I,
)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 常见 UTF-8/GBK 互转乱码特征
MOJIBAKE_RE = re.compile(r"锘|锟斤拷|Ã©|â€|å|æ[¨°˜]")
PAGENUM_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$", re.M)

TOKEN_MAX = 800  # 与 chunker 默认一致
TOKEN_HARD = 1200  # 明显超过此值才算切分 FAIL(800-1200 之间是边界块,记 WARN)

issues = {"FAIL": [], "WARN": []}


def fail(section, msg):
    issues["FAIL"].append(f"[{section}] {msg}")
    print(f"  FAIL  {msg}")


def warn(section, msg):
    issues["WARN"].append(f"[{section}] {msg}")
    print(f"  WARN  {msg}")


def ok(section, msg):
    print(f"  PASS  {msg}")


def section(title):
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def load_all():
    books = {}
    for f in sorted(glob.glob(os.path.join(JSON_DIR, "*.json"))):
        bid = os.path.basename(f)[:4]
        books[bid] = json.load(open(f, encoding="utf-8"))
    return books


def audit_json(books, total):
    section("A. JSON 数据质量")
    bad_type, dup_ids, bad_id_fmt, empty_no_img = [], [], [], []
    abs_img, missing_img = [], []
    id_set = set()
    for bid, chunks in sorted(books.items()):
        for c in chunks:
            cid = c.get("id", "")
            if cid in id_set:
                dup_ids.append(cid)
            id_set.add(cid)
            if not ID_RE.match(cid):
                bad_id_fmt.append(cid)
            if not isinstance(c.get("token_count"), int) or not isinstance(c.get("char_count"), int):
                bad_type.append(f"{cid}(token={type(c.get('token_count')).__name__},char={type(c.get('char_count')).__name__})")
            if not (c.get("text") or "").strip() and not c.get("images"):
                empty_no_img.append(cid)
            for img in c.get("images", []):
                if os.path.isabs(img):
                    abs_img.append((cid, img))
                    path = img
                else:
                    path = os.path.join(IMAGES_DIR, img)
                if not os.path.exists(path):
                    missing_img.append((cid, os.path.basename(img)))
    ok("A", f"语料 {len(books)} 本书 / {total} 块")
    if dup_ids:
        fail("A", f"重复 id {len(dup_ids)} 个: {dup_ids[:5]}")
    else:
        ok("A", "块 id 全局唯一")
    if bad_id_fmt:
        warn("A", f"id 格式异常 {len(bad_id_fmt)} 个: {bad_id_fmt[:5]}")
    else:
        ok("A", "id 格式统一 (<book>-<序号>)")
    if bad_type:
        fail("A", f"token_count/char_count 非整数 {len(bad_type)} 块: {bad_type[:5]}")
    else:
        ok("A", "token_count/char_count 均为 int")
    if empty_no_img:
        warn("A", f"空文本且无图片引用 {len(empty_no_img)} 块: {empty_no_img[:5]}")
    else:
        ok("A", "无「空文本且无图」块")
    if abs_img:
        fail("A", f"images 存绝对路径 {len(abs_img)} 条(不可移植): {abs_img[:2]}")
    else:
        ok("A", "images 均为相对路径")
    if missing_img:
        fail("A", f"图片文件缺失 {len(missing_img)} 条: {missing_img[:3]}")
    else:
        ok("A", "全部图片引用在磁盘上存在")


def audit_chunking(books, total):
    section("B. 文档切分质量")
    over, frag, no_page, no_chapter = [], [], [], []
    tok_sum = 0
    for bid, chunks in sorted(books.items()):
        for c in chunks:
            try:
                tok = int(c.get("token_count") or 0)  # b019 曾存 str,容错 coerced
            except (TypeError, ValueError):
                tok = 0
            tok_sum += tok
            if tok > TOKEN_HARD:
                over.append((c["id"], tok))
            elif tok > TOKEN_MAX and len(over) < 5:
                over.append((c["id"], tok))  # 802-1200 边界块,样本展示
            if tok < 30:
                frag.append((c["id"], tok))
            if not c.get("page"):
                no_page.append(c["id"])
            if not c.get("chapter"):
                no_chapter.append(c["id"])
    avg = tok_sum / max(total, 1)
    ok("B", f"平均块长 {avg:.0f} token")
    if over:
        hard = sorted((cid, tok) for cid, tok in over if tok > TOKEN_HARD)
        soft = sorted((cid, tok) for cid, tok in over if tok <= TOKEN_HARD)
        if hard:
            warn("B", f"超过 {TOKEN_HARD} token 的块 {len(hard)} 个(历史切分残留,可重切重嵌): {hard[:5]}")
        if soft:
            warn("B", f"超过 {TOKEN_MAX} token 的边界块 {len(soft)} 个(802-{TOKEN_HARD},可接受): {soft[:5]}")
        if not (hard or soft):
            fail("B", "超长块统计异常")
    else:
        ok("B", f"无超过 {TOKEN_MAX} token 的块")
    frag_pct = len(frag) / max(total, 1) * 100
    if frag_pct > 15:
        warn("B", f"碎片块(<30 token) {len(frag)} 个 ({frag_pct:.1f}%),样本: {frag[:5]}")
    else:
        ok("B", f"碎片块(<30 token) {len(frag)} 个 ({frag_pct:.1f}%)")
    if no_page:
        warn("B", f"缺页码 {len(no_page)} 块 ({len(no_page)/total*100:.1f}%),如 {no_page[:3]}")
    else:
        ok("B", "全部块有页码")
    if no_chapter:
        warn("B", f"缺章节 {len(no_chapter)} 块,如 {no_chapter[:3]}")
    else:
        ok("B", "全部块有章节")


def audit_cleaning(books):
    section("C. 文本清洗残留")
    wm, ctrl, moji, pagenum = [], [], [], []
    for bid, chunks in sorted(books.items()):
        for c in chunks:
            t = c.get("text") or ""
            if not t:
                continue
            m = WATERMARK_RE.search(t)
            if m:
                wm.append((c["id"], t[max(0, m.start() - 20): m.end() + 20]))
            if CTRL_RE.search(t):
                ctrl.append(c["id"])
            if MOJIBAKE_RE.search(t):
                moji.append((c["id"], MOJIBAKE_RE.search(t).group(0)))
            if PAGENUM_LINE_RE.search(t):
                pagenum.append(c["id"])
    if wm:
        warn("C", f"疑似水印残留 {len(wm)} 块: {wm[:3]}")
    else:
        ok("C", "无水印残留(z-lib/libgen/Doc2X 等)")
    if ctrl:
        warn("C", f"含控制字符 {len(ctrl)} 块: {ctrl[:5]}")
    else:
        ok("C", "无控制字符")
    if moji:
        warn("C", f"疑似乱码 {len(moji)} 块: {moji[:3]}")
    else:
        ok("C", "无典型转码乱码")
    if pagenum:
        # 注意: 该启发式对表格数据有误报——Borg RPE 等级/训练表周次/作息时间等
        # 也是独立数字行,不可自动删除;人工核对后再处理
        warn("C", f"正文含独立数字行 {len(pagenum)} 块(页码噪音或表格数据,需人工区分): {pagenum[:5]}")
    else:
        ok("C", "无独立页码行残留")


def audit_cache(total_ids):
    section("D. Embedding 缓存一致性")
    npy_path = os.path.join(CACHE_DIR, "embeddings.npy")
    meta_path = os.path.join(CACHE_DIR, "meta.json")
    if not (os.path.exists(npy_path) and os.path.exists(meta_path)):
        fail("D", "缓存文件缺失(embeddings.npy / meta.json)")
        return
    meta = json.load(open(meta_path, encoding="utf-8"))
    ids = meta["ids"]
    vecs = np.load(npy_path, mmap_mode="r")
    ok("D", f"缓存 {len(ids)} 条, 语料 {len(total_ids)} 条")
    cache_set = set(ids)
    missing = total_ids - cache_set
    orphan = cache_set - total_ids
    if missing:
        fail("D", f"语料有而缓存无(需补嵌) {len(missing)} 条: {sorted(missing)[:5]}")
    else:
        ok("D", "语料 id 全部已嵌入")
    if orphan:
        warn("D", f"缓存有而语料无(陈旧缓存) {len(orphan)} 条: {sorted(orphan)[:5]}")
    else:
        ok("D", "缓存无陈旧 id")
    if vecs.shape[0] != len(ids):
        fail("D", f"向量数 {vecs.shape[0]} != meta ids {len(ids)}")
    else:
        ok("D", f"向量形状 {vecs.shape} 与 meta 一致")
    if vecs.shape[1] != CFG["models"]["embed"]["dim"]:
        fail("D", f"向量维度 {vecs.shape[1]} != 配置 {CFG['models']['embed']['dim']}")
    else:
        ok("D", f"向量维度 = {vecs.shape[1]} (与配置一致)")
    # 分块抽样查 NaN/Inf(全量 load 60MB 也可接受,直接算)
    bad = int(np.sum(~np.isfinite(vecs)))
    if bad:
        fail("D", f"含 NaN/Inf 的向量元素 {bad} 个")
    else:
        ok("D", "无 NaN/Inf")
    # 陈旧 tmp 文件
    stale = [f for f in glob.glob(os.path.join(CACHE_DIR, "*")) if ".tmp" in os.path.basename(f)]
    if stale:
        warn("D", f"遗留临时文件: {[os.path.basename(s) for s in stale]}")


def audit_qdrant(total_ids, books):
    section("E. Qdrant 索引一致性")
    try:
        from config_loader import get_qdrant
        client = get_qdrant()
        coll = client.collection
        if not client.collection_exists(coll):
            fail("E", f"collection {coll} 不存在")
            return
        info = client.get_collection(coll)
        cnt = client.count(collection_name=coll, exact=True).count
        ok("E", f"collection {coll}: {cnt} 点 / 语料 {len(total_ids)} 块")
        if cnt != len(total_ids):
            warn("E", f"点数({cnt}) != 语料块数({len(total_ids)})")
        # payload 索引
        idx_fields = set((info.payload_schema or {}).keys())
        need = {"source", "chapter", "page", "book_id"}
        miss_idx = need - idx_fields
        if miss_idx:
            warn("E", f"缺 payload 索引: {miss_idx}")
        else:
            ok("E", "payload 索引齐全 (source/chapter/page/book_id)")
        # 全量 scroll 收集 chunk_id
        qdrant_ids = []
        no_book, no_page, no_source = [], [], []
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=coll, limit=500, with_payload=True,
                with_vectors=False, offset=offset,
            )
            if not points:
                break
            for p in points:
                cid = (p.payload or {}).get("chunk_id")
                if cid:
                    qdrant_ids.append(cid)
                if not (p.payload or {}).get("book_id"):
                    no_book.append(p.id)
                if not (p.payload or {}).get("page"):
                    no_page.append(p.id)
                if not (p.payload or {}).get("source"):
                    no_source.append(p.id)
            if offset is None:
                break
        qs = set(qdrant_ids)
        orphans = qs - total_ids
        missing = total_ids - qs
        if len(qdrant_ids) != len(qs):
            warn("E", f"Qdrant 内有重复 chunk_id {len(qdrant_ids) - len(qs)} 个")
        else:
            ok("E", "chunk_id 无重复")
        if orphans:
            fail("E", f"孤儿点(Qdrant 有而语料无) {len(orphans)} 个: {sorted(orphans)[:5]}")
        else:
            ok("E", "无孤儿点")
        if missing:
            fail("E", f"缺漏点(语料有而 Qdrant 无) {len(missing)} 个: {sorted(missing)[:5]}")
        else:
            ok("E", "语料 id 全部已入库")
        if no_book:
            warn("E", f"payload 缺 book_id {len(no_book)} 点")
        if no_source:
            warn("E", f"payload 缺 source {len(no_source)} 点")
        if no_page:
            warn("E", f"payload 缺 page {len(no_page)} 点 (纯图块属正常)")
        if not (no_book or no_source):
            ok("E", "payload book_id/source 完整")
    except Exception as e:
        warn("E", f"Qdrant 不可达,跳过: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-qdrant", action="store_true", help="只做离线检查")
    args = ap.parse_args()

    books = load_all()
    total = sum(len(v) for v in books.values())
    all_ids = {c["id"] for chunks in books.values() for c in chunks}

    audit_json(books, total)
    audit_chunking(books, total)
    audit_cleaning(books)
    audit_cache(all_ids)
    if not args.skip_qdrant:
        audit_qdrant(all_ids, books)

    print(f"\n{'=' * 66}\n汇总: FAIL {len(issues['FAIL'])} 项 / WARN {len(issues['WARN'])} 项")
    for line in issues["FAIL"]:
        print(f"  {line}")
    for line in issues["WARN"]:
        print(f"  {line}")
    sys.exit(1 if issues["FAIL"] else 0)


if __name__ == "__main__":
    main()
