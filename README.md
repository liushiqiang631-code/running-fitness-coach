# STRIDE · 跑步健身教练 Agent

一个将训练计划、训练记录、反馈复盘与检索增强问答整合在一起的本地 MVP。产品端使用原生 JavaScript 与 Node.js，知识服务使用 Python / FastAPI；检索链路包括 Qdrant 向量召回、BM25、RRF 融合、BGE 重排、来源页码展示与 SSE 流式回答。

**本仓库是经过清理的公开源码快照。** 不含完整书库、书籍 PDF / Word / 原文转换文件、书页插图、私有向量索引、真实运动员数据、聊天日志或任何 API 密钥。`examples/` 仅包含为公开演示重新编写的短说明；界面中的人物、训练记录和指标均为虚构演示数据。

## 项目能力

- 产品流程：引导建档、周/月/周期计划、训练详情、完成反馈、复盘、计划调整、统计分析与比赛目标。
- 状态管理：浏览器 `localStorage` 持久化；反馈后的计划回写、重复提交保护，以及疼痛评分触发的演示安全模式。
- RAG：中文分词、向量与 BM25 混合召回、书目关键词过滤、主题软加分、RRF、重排超时降级、查询缓存与同文档相邻块补充。
- 对话：多轮问题改写、有限历史窗口、上下文预算、来源去重与流式文本/来源事件。
- 工程：同源 Node.js 代理、前端安全响应头、请求体限制、纯逻辑单元测试、嵌入缓存和批量入库脚本。

```text
浏览器产品界面 → Node.js 同源代理 → FastAPI /chat（SSE）
                                      ↓
用户问题 → 多轮改写 → dense + BM25 + 元数据召回 → RRF → 重排
                                                       ↓
                               有限检索上下文 + 来源页码 → 模型回答
```

## 快速运行：不需要模型密钥的产品演示

需要 Node.js 20 或更高版本。产品端无第三方运行依赖，无需安装 npm 包。在仓库根目录运行：

```sh
node Agent/server.js
```

打开 `http://127.0.0.1:3000`。计划、记录、反馈与分析界面可以独立体验；未连接 RAG 时，AI 对话显示连接提示。引导建档只是演示界面，**没有服务端账户认证**。不要输入真实密码或敏感健康资料。

## 启动知识服务

需要 Python 3.11+。以下命令在仓库根目录执行：

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
python -m pip install -r rag/requirements.txt
python rag/scripts/build_bm25.py
python rag/scripts/start_server.py
```

默认配置只索引 `examples/chunks/` 的原创演示说明，生成的 BM25 文件会被 Git 忽略。浏览器可访问 `http://127.0.0.1:8008`；`POST /search` 可用于检查本地检索。

**外部模型调用默认关闭**，即便系统里已有模型密钥也不会自动使用。要启用 AI 回答，请先确认所提交的问题与检索资料允许发送给模型服务商，再设置 `RUNNING_COACH_ENABLE_EXTERNAL_API=1` 和自己的 `SILICONFLOW_API_KEY`。检查 `rag/config/config.yaml` 中的嵌入、重排和聊天模型地址/型号是否仍可用；调用可能产生费用。`.env.example` 仅作配置说明，本项目不会自动加载 `.env` 文件。

完整向量检索另需自行安装并启动 Qdrant，在本机 `127.0.0.1:6333` 提供服务。启用外部模型且确认费用后，手动运行：

```sh
python rag/scripts/embed_chunks.py
python rag/scripts/build_collection.py
```

默认 collection 为 `running_coach_demo`，避免与已有私人 collection 重名。`--recreate` 会删除选定 collection，`--prune` 会删除孤立数据，不要对生产数据使用这些选项。无需向量服务时，可使用 BM25 检索；生成自然语言答案仍需要配置模型。

## 使用自己的合法资料

`scripts/chunker.py` 接收已获得合法使用权的 MinerU 结构化文档列表，按标题与 token 上限分块。它不提供图书下载或解析服务。现有分块格式见 `examples/chunks/b999_demo.json`；示例的 `token_count=0` 表示未预计算，正式数据应由分块脚本生成。

通过 `RUNNING_COACH_JSON_DIR` 指向自己的分块目录，`RUNNING_COACH_IMAGES_DIR` 指向可选的合法插图目录；相对路径以当前配置文件目录为基准，推荐使用环境变量配置绝对路径。不要把私有语料放入公开 `examples/`。

原项目的书目关键词映射保留在 `rag/app/rag_core.py`，只包含名称与元数据 ID，不含书籍内容；使用自己的资料时应替换这些映射。更换语料后重新构建 BM25/向量索引，并重启服务以清理进程内缓存。

| 环境变量 | 用途 |
|---|---|
| `RUNNING_COACH_CONFIG` | 替换 YAML 配置文件 |
| `RUNNING_COACH_JSON_DIR` / `RUNNING_COACH_IMAGES_DIR` | 本地授权语料 / 可选插图 |
| `RUNNING_COACH_CACHE_DIR` / `RUNNING_COACH_BM25_DIR` / `RUNNING_COACH_EVAL_DIR` | 本地生成数据与评估目录 |
| `RUNNING_COACH_ENABLE_EXTERNAL_API` | `1` 时才允许外部模型调用，默认 `0` |
| `SILICONFLOW_API_KEY` | 当前默认模型服务的密钥，仅从环境读取 |
| `QDRANT_URL` / `QDRANT_COLLECTION` | 向量服务地址 / 集合名称 |
| `HOST` / `PORT` | 产品端地址 / 端口，默认 `127.0.0.1:3000` |
| `RAG_BASE_URL` / `RAG_PORT` | 产品端代理目标 / RAG 监听端口，默认 `8008` |

## 验证

```sh
node --test Agent/tests/*.test.js
python -m pytest rag/tests/ -q
```

公开示例只有一个原创演示题，用来说明评估输入格式，不代表真实准确率。`rag/scripts/eval_retrieval.py` 提供命中、MRR 等分析代码；历史输出中 `Recall` 的名称实际对应题级 Hit Rate，页码匹配与 nDCG 实现也存在简化，不应直接用于对外发布严格基准结论。此仓库不附带原书库上的历史评估成绩。

## 边界与隐私

- 这是用于作品展示和本地运行的 MVP，未完成生产认证、权限隔离或多用户部署加固。保持默认本机监听，不要直接暴露到公网。
- 训练、恢复分数、比赛预测和疼痛规则是产品演示逻辑；输出不能代替专业教练判断或医疗建议。
- 用户状态保存在当前浏览器，清除站点数据可删除；不要发布浏览器导出、截图、会话、日志或模型响应。
- 只加载自己构建且可信的 BM25 pickle 索引，不加载他人提供的 pickle 文件。
- 第三方依赖与模型服务各自遵循其条款。本次公开快照未另行添加开源许可证，也未对第三方代码或资料授予新的许可。

目录说明：`Agent/` 产品端；`rag/app/` 知识问答服务；`rag/scripts/` 索引与评估工具；`rag/tests/` 单元测试；`scripts/chunker.py` 结构化资料分块；`examples/` 原创演示资料。
