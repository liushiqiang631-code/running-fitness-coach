# -*- coding: utf-8 -*-
"""一次性迁移存量分块 JSON 到新约定(幂等,可重复执行):

1. token_count / char_count: str → int(b019 由旧版 chunker 写成 str)
2. images: 绝对路径(如 E:\\..\\images\\b001\\x.jpg)→ 相对 images 根目录
   的 "<book_id>/<文件名>",项目整体移动/换机不再失效

不改动块 id/text,向量缓存与 BM25 索引无需重建;Qdrant payload 需重建
(build_collection.py 从缓存全量 upsert,不调 API)。
运行: python scripts/migrate_chunk_fields.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag", "scripts"))
from config_loader import load_config

CFG = load_config()
JSON_DIR = CFG["paths"]["json_dir"]
IMAGES_DIR = CFG["paths"]["images_dir"]


def migrate_chunk(c, book_id):
    changed = False
    for field in ("token_count", "char_count"):
        v = c.get(field)
        if isinstance(v, str):
            try:
                c[field] = int(v)
            except ValueError:
                c[field] = 0
            changed = True
    imgs = c.get("images")
    if imgs:
        new_imgs = []
        for img in imgs:
            if os.path.isabs(img):
                new_imgs.append(f"{book_id}/{os.path.basename(img)}")
                changed = True
            else:
                new_imgs.append(img)
        c["images"] = new_imgs
    return changed


def main():
    files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    print(f"待迁移文件: {len(files)} 个 (images 根目录: {IMAGES_DIR})")
    n_files = n_chunks = n_imgs = 0
    for f in files:
        bid = os.path.basename(f)[:4]
        chunks = json.load(open(f, encoding="utf-8"))
        touched = 0
        img_cnt = 0
        for c in chunks:
            if migrate_chunk(c, bid):
                touched += 1
                img_cnt += len(c.get("images") or [])
        if not touched:
            continue
        # 原子写: tmp + replace,中断不留半个文件
        tmp = f + ".migrating"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(chunks, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, f)
        n_files += 1
        n_chunks += touched
        n_imgs += img_cnt
        print(f"  {bid}: 迁移 {touched} 块 / {img_cnt} 条图片引用")
    print(f"完成: {n_files} 个文件, {n_chunks} 块, {n_imgs} 条图片引用改为相对路径")


if __name__ == "__main__":
    main()
