# -*- coding: utf-8 -*-
"""MinerU 精准解析流水线: 新书 PDF → 分块语料。

流程(对齐 跑步健身教练Agent_工作总结与方案 §4.1 的踩坑经验):
1. 单任务 ≤200 页 → 本地按 SEGMENT_PAGES 拆分(默认 190)
2. POST /api/v4/file-urls/batch 拿 PUT-only 签名 URL(上传后不要再 GET 验证,会 403)
3. 读入内存 PUT 上传(传 file 对象走 chunked,OSS 不认,文件损坏)
4. 上传完成后系统自动提交解析任务(不要再调 extract/task/batch,会报文件损坏)
5. 轮询 GET /api/v4/extract-results/batch/{batch_id},字段是 data.extract_result
6. 各段下载 full_zip_url → 解压 → 合并 full.md + content_list_v2(page_idx 按段偏移)

断点续跑: 状态记在 <work>/state.json,中断后重跑同命令即可续。

用法:
  python scripts/parse_book.py --pdf <路径> --book-id b019 \
      --title "Training for the Uphill Athlete" [--seg 190]
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile

import requests

BASE = "https://mineru.net/api/v4"
TOKEN = os.environ.get("MINERU_TOKEN")
if not TOKEN:
    _cfg = os.path.expanduser("~/.mineru/config.yaml")
    if os.path.exists(_cfg):
        m = re.search(r"token:\s*(\S+)", open(_cfg, encoding="utf-8").read())
        if m:
            TOKEN = m.group(1)
assert TOKEN, "需要 MINERU_TOKEN 环境变量或 ~/.mineru/config.yaml"

HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
POLL_INTERVAL = 20   # 秒
POLL_TIMEOUT = 90 * 60  # 单段最长等待 90 分钟


def split_pdf(pdf_path, seg_pages, work_dir):
    """PDF 按页拆分为 N 段文件,返回段文件路径列表(每段从第 1 页重新编号)。"""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path)
    n = len(reader.pages)
    segs = []
    start = 1
    while start <= n:
        end = min(start + seg_pages - 1, n)
        name = f"seg{len(segs)+1}_p{start}-{end}.pdf"
        out = os.path.join(work_dir, name)
        if not os.path.exists(out):
            w = PdfWriter()
            for i in range(start - 1, end):
                w.add_page(reader.pages[i])
            with open(out, "wb") as f:
                w.write(f)
        segs.append({"file": out, "start_page": start, "end_page": end})
        start = end + 1
    print(f"拆分: {n} 页 → {len(segs)} 段, 每段上限 {seg_pages} 页")
    return segs


def request_batch_upload(segments, book_id):
    """POST /file-urls/batch,返回 (batch_id, [ (file, url) ])。"""
    files = [
        {"name": os.path.basename(s["file"]), "data_id": f"{book_id}-{i+1}"}
        for i, s in enumerate(segments)
    ]
    body = {
        "files": files,
        "model_version": "pipeline",   # 文本层 PDF;扫描版用 vlm(含 OCR)
        "language": "en",
        "enable_formula": False,        # 本书无公式
        "enable_table": True,
    }
    r = requests.post(f"{BASE}/file-urls/batch", json=body, headers=HDR, timeout=60)
    r.raise_for_status()
    data = r.json().get("data", {})
    batch_id = data["batch_id"]
    urls = data["file_urls"]
    assert len(urls) == len(segments), f"URL 数({len(urls)})!=段数({len(segments)})"
    print(f"batch_id={batch_id} 已拿到 {len(urls)} 个上传 URL")
    return batch_id, [(s, urls[i]) for i, s in enumerate(segments)]


def put_upload(seg, url):
    """读入内存 PUT 上传。

    踩坑: 不能带任何 Content-Type 头 —— OSS 预签名把 Content-Type 计入签名,
    显式设置会 403 SignatureDoesNotMatch;裸 data=bytes 无 Content-Type 才 200。
    也勿用 files= (会走 chunked,OSS 不认,文件损坏)。同时禁用系统代理(默认 session 虽也可,
    显式绕过更稳)。
    """
    with open(seg["file"], "rb") as f:
        data = f.read()
    r = requests.put(url, data=data, timeout=300, proxies={"http": None, "https": None})
    if r.status_code != 200:
        raise RuntimeError(f"PUT 失败 {seg['file']}: {r.status_code} {r.text[:200]}")
    print(f"上传完成 {os.path.basename(seg['file'])} ({len(data)/1024/1024:.1f} MB)")


def poll_batch(batch_id):
    """轮询直到全部段 done/failed,返回 {file_name: {state, full_zip_url}}。"""
    done = {}
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(f"{BASE}/extract-results/batch/{batch_id}", headers=HDR, timeout=60)
        r.raise_for_status()
        results = r.json().get("data", {}).get("extract_result", [])
        for item in results:
            done[item["file_name"]] = item
        finished = [v.get("state") for v in done.values() if v.get("state")]
        pending = [v for v in done.values() if v.get("state") not in ("done", "failed")]
        print(f"  [{batch_id}] done={finished.count('done')} failed={finished.count('failed')} pending={len(pending)}")
        if pending:
            time.sleep(POLL_INTERVAL)
            continue
        failed = [v for v in done.values() if v.get("state") == "failed"]
        if failed:
            for v in failed:
                print(f"  段失败: {v.get('file_name')} err={v.get('err_msg','')}")
            raise RuntimeError("存在解析失败的段")
        return done
    raise TimeoutError(f"batch {batch_id} 轮询超时")


def download_and_extract(result, seg, work_dir, seg_idx):
    """下载 zip 并解压到 work_dir/seg{seg_idx}/,重命名 content_list 为 content_list_v2.json。"""
    seg_dir = os.path.join(work_dir, f"seg{seg_idx}")
    if os.path.exists(os.path.join(seg_dir, "content_list_v2.json")):
        return seg_dir
    zip_url = result.get("full_zip_url")
    assert zip_url, f"段 {seg['file']} 无 full_zip_url: {result}"
    zip_path = os.path.join(work_dir, f"seg{seg_idx}.zip")
    if not os.path.exists(zip_path):
        r = requests.get(zip_url, timeout=300)
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(r.content)
        print(f"下载 zip seg{seg_idx}: {len(r.content)/1024/1024:.1f} MB")
    os.makedirs(seg_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():
            # content_list 文件名形如 <taskid>_content_list.json
            if m.endswith("_content_list.json"):
                dst = os.path.join(seg_dir, "content_list_v2.json")
            else:
                dst = os.path.join(seg_dir, os.path.basename(m))
            if not os.path.exists(dst):
                with open(dst, "wb") as out:
                    out.write(z.read(m))
    return seg_dir


def load_content_list(seg_dir):
    """读取一段 content_list_v2.json,返回 (blocks, full_md)。"""
    blocks = json.load(open(os.path.join(seg_dir, "content_list_v2.json"), encoding="utf-8"))
    md_path = os.path.join(seg_dir, "full.md")
    full_md = ""
    if os.path.exists(md_path):
        full_md = open(md_path, encoding="utf-8").read()
    return blocks, full_md


def merge_segments(work_dir, segments, seg_dirs):
    """合并各段 full.md + content_list_v2,page_idx 按段起始页偏移,img_path 加 segN/ 前缀防冲突。"""
    merged_blocks = []
    md_parts = []
    for i, (seg, seg_dir) in enumerate(zip(segments, seg_dirs), start=1):
        offset = seg["start_page"] - 1
        blocks, full_md = load_content_list(seg_dir)
        for b in blocks:
            b = dict(b)
            if "page_idx" in b:
                b["page_idx"] = b["page_idx"] + offset
            if "img_path" in b and b["img_path"]:
                b["img_path"] = f"seg{i}/{b['img_path']}"
            merged_blocks.append(b)
        md_parts.append(full_md)
        print(f"  段 {i}: {len(blocks)} blocks, md {len(full_md)} 字符, 偏移 {offset}")
    out_dir = os.path.join(work_dir, "merged")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "content_list_v2.json"), "w", encoding="utf-8") as f:
        json.dump(merged_blocks, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "full.md"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(md_parts))
    print(f"合并完成: {len(merged_blocks)} blocks, full.md {sum(len(m) for m in md_parts)} 字符")
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--book-id", required=True, help="如 b019")
    ap.add_argument("--title", required=True, help="书名,用于 source 字段")
    ap.add_argument("--seg", type=int, default=190)
    args = ap.parse_args()

    work_dir = os.path.join(os.path.dirname(__file__), "..", "_work", args.book_id)
    os.makedirs(work_dir, exist_ok=True)
    state_path = os.path.join(work_dir, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}

    segments = split_pdf(args.pdf, args.seg, work_dir)

    if state.get("batch_id"):
        print(f"恢复: 已存在 batch_id={state['batch_id']}")
        results = poll_batch(state["batch_id"])
        batch_id = state["batch_id"]
    else:
        batch_id, uploads = request_batch_upload(segments, args.book_id)
        for seg, url in uploads:
            put_upload(seg, url)
        state["batch_id"] = batch_id
        json.dump(state, open(state_path, "w", encoding="utf-8"))
        results = poll_batch(batch_id)

    seg_dirs = []
    for i, seg in enumerate(segments, start=1):
        fname = os.path.basename(seg["file"])
        result = next((v for v in results.values() if v.get("file_name") == fname), None)
        assert result, f"batch 结果里找不到 {fname}"
        seg_dir = download_and_extract(result, seg, work_dir, i)
        seg_dirs.append(seg_dir)

    merged = merge_segments(work_dir, segments, seg_dirs)
    print(f"\n解析完成 → {merged}")


if __name__ == "__main__":
    main()
