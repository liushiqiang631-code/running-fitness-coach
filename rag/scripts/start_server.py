# -*- coding: utf-8 -*-
"""启动 FastAPI 服务(前台)。浏览器访问 http://127.0.0.1:8008
运行: python scripts/start_server.py  (在 rag/ 目录下)
"""
import os
import sys
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if __name__ == "__main__":
    port = int(os.environ.get("RAG_PORT", 8008))
    print(f"跑步健身教练 Agent → http://127.0.0.1:{port}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)
