# -*- coding: utf-8 -*-
"""从 MinerU 各段解压目录把图片收集到 跑步健身教练images/<book_id>/。

- 遍历 merged content_list_v2.json 里的 img_path(形如 segN/images/xxx.jpg)
- 复制到最终图片目录(绝对路径),重名跳过(幂等)
- 报告: 总数/去重/零字节坏图
用法: python scripts/extract_images.py --book-id b019 --content-list <merged.json> --work-dir <_work/b019> --images-out <images_dir>
"""
import argparse
import json
import os
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--content-list", required=True)
    ap.add_argument("--work-dir", required=True, help="如 源码/_work/b019")
    ap.add_argument("--images-out", required=True, help="最终图片目录,如 E:/跑步健身教练/跑步健身教练images/b019")
    args = ap.parse_args()

    blocks = json.load(open(args.content_list, encoding="utf-8"))
    paths = sorted({b["img_path"] for b in blocks if isinstance(b, dict) and b.get("img_path")})
    print(f"content_list 引用 {len(paths)} 个图片路径")

    out_dir = args.images_out
    os.makedirs(out_dir, exist_ok=True)
    copied = 0
    zero_bad = []
    missing = []
    for rel in paths:
        # 兼容两种解压布局: work_dir/segN/images/xxx.jpg(保留子目录) 与 work_dir/segN/xxx.jpg(展平)
        candidates = [os.path.join(args.work_dir, rel)]
        if rel.count("/") == 2:      # segN/images/xxx.jpg → 也试展平位置
            parts = rel.split("/")
            candidates.append(os.path.join(args.work_dir, parts[0], parts[-1]))
        src = next((c for c in candidates if os.path.exists(c)), None)
        if not src:
            missing.append(rel)
            continue
        dst = os.path.join(out_dir, os.path.basename(rel))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        if os.path.getsize(dst) == 0:
            zero_bad.append(dst)
        copied += 1

    n = len(os.listdir(out_dir))
    print(f"可用图片: {copied}/{len(paths)}, 目标目录共 {n} 张")
    if missing:
        print(f"!! 缺失 {len(missing)} 张: {missing[:5]}")
    if zero_bad:
        print(f"!! 零字节坏图 {len(zero_bad)} 张: {zero_bad[:5]}")


if __name__ == "__main__":
    main()
