# 跑步健身教练 Agent —— RAG 应用

基于 22 本跑步/体能训练文献(15,199 块)的检索增强问答 Web 应用。

## 架构

```
用户问题 → 多路召回(dense bge-m3 + BM25 + 元数据过滤) → RRF 融合
        → bge-reranker-v2-m3 重排 → top-5~8 送 LLM 作答(带来源+页码引用)
        同文档扩展(主块 ±2 邻块,不挤占主块位置)
```

- 向量库: Qdrant(本机 docker,collection `running_coach`,dense 1024 + Cosine)
- 嵌入/重排/问答: 硅基流动 API(`BAAI/bge-m3` / `BAAI/bge-reranker-v2-m3` / `Qwen3-30B-A3B-Instruct-2507`)
- 后端: FastAPI(SSE 流式); 前端: 单 HTML 页

## 一键运行

```bash
# 推荐: 双击 rag/start_server.bat(独立窗口,不随会话退出)
# 或在独立终端:
cd E:/跑步健身教练/rag
python scripts/start_server.py        # 启动 FastAPI,浏览器打开 http://127.0.0.1:8008
```
> 注意: 在 Agent 会话内用后台方式起的服务进程会随会话退出被终止,请用上面两种方式(独立终端/bat)运行以获得持久服务。

## 分步流程

| 步骤 | 命令 | 产出 |
|---|---|---|
| 数据体检 | `python scripts/audit_pipeline.py` | 语料/切分/清洗/缓存/索引一致性审计 |
| 全量嵌入 | `python scripts/embed_chunks.py` | `data/cache/embeddings.npy` + `meta.json`(断点续跑) |
| 建库入库 | `python scripts/build_collection.py [--recreate] [--prune]` | Qdrant `running_coach` |
| BM25 索引 | `python scripts/build_bm25.py` | `data/bm25/bm25.pkl` |
| 检索验证 | `python app/rag_core.py "问题"` | 多路召回+重排 top-6 |
| 问答验证 | `python app/chat.py "问题"` | LLM 回答+引用 |
| 单元测试 | `python -m pytest tests/` | 纯逻辑回归(rrf/过滤/分块/页码重叠…) |
| 完整评估 | `python scripts/eval_retrieval.py` | Recall/MRR/nDCG 报告 → `data/eval/report.txt` |

## 关键配置(`config/config.yaml`)

- `qdrant.trust_env: false` —— 本机 docker Qdrant 必须绕本机代理直连
  (httpx 默认 `trust_env=true` 会把 localhost 也走代理 → 502,踩过的坑)
- `models.chat.name` —— 硅基流动 `Qwen/Qwen3-30B-A3B`(旧 ID)已禁用,
  用 `Qwen/Qwen3-30B-A3B-Instruct-2507`
- API key 从环境变量读: `SILICONFLOW_API_KEY`、`DEEPSEEK_API_KEY`

## 数据/接口

- `POST /chat` —— SSE 流式问答,body `{query, history}`
- `POST /search` —— 检索调试,返回命中的块(不调 LLM)
- `GET /health` —— 健康检查(Qdrant 状态 + collection 计数)
- `GET /images/<book_id>/<文件名>` —— 书页插图(由 `images` 字段相对路径拼成)

## 评估结果

全量验收(2026-08-12,50 题 golden 检索 + 50 题回答):

| 指标 | 结果 | 验收线(方案 5.10) |
|---|---|---|
| Recall@10 | **98.0%**(49/50) | ≥85% ✅ |
| Recall@20 | 98.0% | — |
| MRR | 0.757 | — |
| nDCG@10 | 0.645 | — |
| **引用页码正确率** | **99.2%**(238/240) | ≥90% ✅ |
| **流式输出成功率** | **100%**(50/50) | 目标 100% ✅ |

回归复评(2026-08-31,b019 入库与检索管线升级后,报告在 `data/eval/`):
Recall@10 保持 **98.0%**(49/50),MRR 0.731 / nDCG@10 0.483;
8 题回答快评引用正确率 **100%**(23/23)、流式 100%。

分主题 Recall@10:数据查询/训练方法/伤病康复/周期化/术语解释 全 **100%**,多流派对照 83%(仅 1 题未命中)。

关键调优(见 git log):
1. **golden 标注修正** —— 多题「未命中」实为标注页码过窄(如 R 训练目的在 p74、力量耐力定义在 b009),修正后从 90%→98%
2. **RRF×rerank 加权混合排序**(`rerank_weight: 0.5`)—— 纯 rerank 会把数据题正确表页压出 top10,混合后 Recall@10 稳定
3. **同文档扩展**(`same_doc_expand: on`)—— 主块 ±2 邻块补全被 800 token 切块拆散的表格/段落(邻块不参与 rerank、不挤占主块)

## 已知限制

- **OCR 数据表反查**(如「马拉松 3:30 → VDOT」):扁平化 OCR 表很难被语义检索/rerank 匹配到正确行,
  问答可能给出错误推算。方案既定对策:数据类问题保留 `images` 引用,交给多模态模型读图复核(当前 chat 为纯文本模型)。
- **跨语言比较型 query**(如「低跑量 vs 高跑量」,golden 在英文 b004/b005):单条语料没进任一路 top-50,属真实召回缺口。
- 语料有 4 个历史遗留超长块(b006-0458 等,1.3k~1.6k token,audit_pipeline 记 WARN):在 bge-m3 8k 上限内,
  嵌入/重排/LLM 各环节均按上限截断,功能无影响;彻底修复需整书重切+重嵌,会改变全块 id,暂保留。
- audit 的「正文含独立数字行」警告多为表格数据(Borg RPE 等级表、丹尼尔斯训练表周次、作息时间表),
  并非页码噪音,不可自动清除;已人工确认保留。印刷页码与 PDF 页码存在偏移,自动区分不可靠。
- 首查延迟受外部 API 波动影响(硅基流动 embed 实测 0.9~12s 波动)。已优化: 重复问题全量结果缓存 **0.00s**、
  rerank 输入 50→20、rerank 硬超时 1.8s(超时降级 RRF)、服务启动预热。API 正常时首查 ~2-3s,负载高时 5-15s。
  严格 <2s 需去掉 embed/rerank 任一外部调用(召回会降),暂未采用。

## 容错降级

- Qdrant 不可用 → 自动只走 BM25(应用仍可问答,召回略降)
- 嵌入/重排 API 失败 → 指数退避重试; 重排失败 → 用 RRF 原序
- 检索无结果 → 明确提示「文献中未找到」,不编造
- 多轮对话 → 先 query 改写补全指代,历史保留最近几轮
