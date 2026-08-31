# -*- coding: utf-8 -*-
"""跑步健身教练 Agent —— FastAPI 服务。

- GET  /              前端页面(rag/app/static/index.html)
- GET  /health        健康检查
- POST /chat          SSE 流式问答
- POST /search        检索调试(返回命中的块)
启动: uvicorn app.main:app --host 127.0.0.1 --port 8000  (在 rag/ 下运行)
"""
import json
import os
import sys
import time as _t
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rag_core import retrieve
from chat import build_sources, chat_stream_from_chunks, retrieve_for_chat
from config_loader import load_config

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
IMAGE_DIR = load_config()["paths"]["images_dir"]


def warm_start():
    """预热: 预加载块索引 / BM25 / jieba / Qdrant 连接,避免首次请求冷启动慢。"""
    t0 = _t.time()
    try:
        from rag_core import load_chunks, load_bm25, get_qdrant_client
        load_chunks()
        load_bm25()
        client = get_qdrant_client()
        client.count(collection_name=client.collection)  # 建立 Qdrant 连接,首次请求不用付连接成本
        print(f"[预热] 块索引 + BM25 + Qdrant 连接就绪 ({_t.time()-t0:.1f}s)", flush=True)
    except Exception as e:
        print(f"[预热] 跳过: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_start()
    yield


app = FastAPI(title="跑步健身教练 Agent", version="0.1.0", lifespan=lifespan)
# 书页插图: chunk.images 存相对 images 根目录的路径(如 b001/xxx.jpg),经此路由展示
if os.path.isdir(IMAGE_DIR):
    app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


class ChatRequest(BaseModel):
    query: str
    history: list = []  # [{"role":"user"/"assistant","content":...}]


class SearchRequest(BaseModel):
    query: str
    top_n: int = 8


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    try:
        from rag_core import get_qdrant_client
        coll = get_qdrant_client().collection
        cnt = get_qdrant_client().count(collection_name=coll)
        qdrant = {"ok": True, "collection": coll, "points": cnt.count}
    except Exception as e:
        qdrant = {"ok": False, "error": str(e)[:120]}
    return JSONResponse({"status": "ok", "qdrant": qdrant})


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.query.strip():
        return JSONResponse({"error": "query 不能为空"}, status_code=400)

    # 注意: 必须用同步生成器交给 StreamingResponse(Starlette 会在线程池里迭代,
    # 事件循环不被同步的检索/模型调用阻塞),这样才能逐块输出。
    # 若用 async 生成器去 for 循环一个同步生成器,会把整条流攒到最后一次性吐出来(非流式)。
    def gen():
        try:
            query, chunks = retrieve_for_chat(req.query, history=req.history)
            for delta in chat_stream_from_chunks(query, chunks, history=req.history):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            sources = build_sources(chunks)
            if sources:
                yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        except Exception as e:
            # 详细错误只进服务端日志; SSE 只给通用提示(保留 [出错] 前缀,前端 isRagErrorText 依赖它识别),
            # 避免把内部地址/异常细节透给用户
            print(f"[chat 出错] {e}", file=sys.stderr)
            yield f"data: {json.dumps({'delta': '[出错] 回答服务暂时出错,请稍后重试。'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    """检索调试: 返回命中的块(不调用 LLM)。"""
    res = retrieve(req.query, top_n=req.top_n)
    out = []
    for r in res:
        c = r["chunk"]
        out.append(
            {
                "id": r["id"],
                "score": r["score"],
                "source": c.get("source"),
                "page": c.get("page"),
                "chapter": c.get("chapter"),
                "text": (c.get("text") or "")[:400],
                "images": c.get("images", []),
            }
        )
    return JSONResponse({"query": req.query, "hits": out})


if __name__ == "__main__":
    import uvicorn

    # 端口与 scripts/start_server.py、agent-rag.bat、Agent 代理的默认 RAG_BASE_URL 保持一致
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("RAG_PORT", 8008)), reload=False)
