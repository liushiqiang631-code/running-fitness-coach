# STRIDE 跑步教练产品端

这是《跑步健身教练 Agent 产品功能、UI 与数据分析完整方案》的可运行 MVP。它不会重建 `rag/`，只通过同源代理复用已有的 FastAPI SSE 问答服务。

## 启动

产品端没有第三方运行依赖，不需要执行 `npm install` 或 `pnpm install`。

最简单的方式是双击 [`start_product.bat`](./start_product.bat)。浏览器会在服务就绪后打开 `http://127.0.0.1:3000`，关闭命令窗口即可停止产品服务。

也可以在终端运行：

```powershell
cd E:\跑步健身教练\Agent
node server.js
```

AI 教练需要另行双击 `E:\跑步健身教练\rag\start_server.bat`。RAG 未启动时，其余计划、训练、反馈、复盘、分析和档案功能仍可使用。

可选环境变量：

- `PORT`：产品端口，默认 `3000`
- `HOST`：监听地址，默认 `127.0.0.1`
- `RAG_BASE_URL`：已有 RAG 地址，默认 `http://127.0.0.1:8008`

## 已实现

- 登录与五步 Onboarding
- Dashboard、周/月/周期计划
- 训练详情、训练中、反馈、AI 复盘与计划回写
- 跑量、强度、效率、比赛预测分析与自然语言解释
- RAG 流式 AI 教练与离线降级提示
- 比赛项目、比赛清单、运动员档案
- 疼痛评分达到 4 时的安全模式
- 桌面侧栏与 390px 手机底部导航

演示状态保存在浏览器 `localStorage` 的 `stride-coach-v1` 键中，可在计划页面恢复演示数据。

## 验证

```powershell
node --test tests\*.test.js
node --check server.js
node --check public\app.js
node --check public\core.js
node --check public\data.js
```
