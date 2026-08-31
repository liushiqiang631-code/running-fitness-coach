# -*- coding: utf-8 -*-
"""统一的配置加载 + Qdrant 连接助手。
所有 rag 脚本/应用都通过这里读 config.yaml。
关键点: 本机 docker Qdrant 必须 trust_env=False 直连,
否则 httpx 默认捡到本机代理(localhost 被代理拦截返回 502)。
"""
import os
import yaml
import httpx
from qdrant_client import QdrantClient

CONFIG_PATH = os.environ.get("RUNNING_COACH_CONFIG") or os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
CONFIG_DIR = os.path.dirname(os.path.abspath(CONFIG_PATH))


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("json_dir", "images_dir", "cache_dir", "bm25_dir", "eval_dir"):
        override = os.environ.get("RUNNING_COACH_" + key.upper())
        if override:
            cfg.setdefault("paths", {})[key] = override
    if os.environ.get("QDRANT_URL"):
        cfg["qdrant"]["url"] = os.environ["QDRANT_URL"]
    if os.environ.get("QDRANT_COLLECTION"):
        cfg["qdrant"]["collection"] = os.environ["QDRANT_COLLECTION"]
    # Resolve relative paths against the selected config file.
    for key, val in cfg.get("paths", {}).items():
        if val and not os.path.isabs(val):
            cfg["paths"][key] = os.path.normpath(os.path.join(CONFIG_DIR, val))
    return cfg


def get_qdrant(collection=None):
    """返回已连接的 QdrantClient(trust_env=False 绕代理)。
    同时把默认 collection 挂到 client 上,方便统一使用。
    """
    cfg = load_config()
    q = cfg["qdrant"]
    client = QdrantClient(
        url=q["url"],
        trust_env=q.get("trust_env", False),
        timeout=60,
    )
    client.collection = collection or q["collection"]
    return client


def get_api_key(provider="siliconflow"):
    if provider == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "")
    return os.environ.get("SILICONFLOW_API_KEY", "")


def external_api_enabled():
    """External model calls require explicit opt-in, even when a key is inherited."""
    return os.environ.get("RUNNING_COACH_ENABLE_EXTERNAL_API", "0") == "1"


def require_external_api():
    if not external_api_enabled():
        raise RuntimeError("External model calls are disabled. See README for explicit opt-in.")
