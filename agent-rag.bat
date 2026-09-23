@echo off
setlocal EnableExtensions
chcp 936 >nul
cd /d "%~dp0"
title 跑步健身教练 - 一键启动

echo ================================================
echo    跑步健身教练 - 一键启动 (Docker + RAG + 产品端)
echo ================================================
echo.

rem ---------- 0. 依赖检查 ----------
where node >nul 2>nul || (echo [错误] 未找到 Node.js,请先安装 & pause & exit /b 1)
where python >nul 2>nul || (echo [错误] 未找到 Python,请先安装 & pause & exit /b 1)
where docker >nul 2>nul || (echo [错误] 未找到 docker,请先安装 Docker Desktop & pause & exit /b 1)
where curl >nul 2>nul || (echo [错误] 未找到 curl ^(Windows 10+ 自带^) & pause & exit /b 1)

set "QD_OK=0"

rem ---------- 1. Docker Desktop ----------
docker info >nul 2>nul
if not errorlevel 1 goto docker_running

echo [1/5] Docker 未运行,正在启动 Docker Desktop ...
if exist "%ProgramW6432%\Docker\Docker\Docker Desktop.exe" (
  start "" "%ProgramW6432%\Docker\Docker\Docker Desktop.exe"
) else if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
  start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
) else (
  echo [错误] 找不到 Docker Desktop.exe,请手动打开 Docker Desktop 后重试。
  pause & exit /b 1
)
set /a n=0
:wait_docker
ping -n 2 127.0.0.1 >nul
docker info >nul 2>nul
if not errorlevel 1 goto docker_ok
set /a n+=1
if %n% GEQ 90 (
  echo [警告] Docker 90 秒未就绪,继续启动 ^(可能走 BM25 降级^)
  goto step2
)
goto wait_docker

:docker_ok
echo [1/5] Docker 已就绪
goto step2

:docker_running
echo [1/5] Docker 已在运行
goto step2

rem ---------- 2. Qdrant 容器 ----------
:step2
set /a n=0
:try_create
docker start qdrant >nul 2>nul
if errorlevel 1 goto qdrant_create
echo [2/5] qdrant 容器已启动
goto qdrant_wait
:qdrant_create
echo [2/5] 未找到 qdrant 容器,正在创建...
docker run -d --name qdrant -p 6334:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
if errorlevel 1 echo [错误] docker run 失败,请检查 Docker 引擎/镜像/网络
:qdrant_wait
ping -n 2 127.0.0.1 >nul
set /a n+=1
curl -s --noproxy "*" -o nul http://127.0.0.1:6334/collections
if not errorlevel 1 goto qdrant_up
if %n% GEQ 30 (
  echo [警告] Qdrant 未能在 :6334 就绪,将以 BM25 降级模式继续
  goto step3
)
goto try_create
:qdrant_up
set "QD_OK=1"
echo [2/5] Qdrant 就绪 ^(http://127.0.0.1:6334^)
goto step3

rem ---------- 3. 检查/重建向量集合 ----------
:step3
if "%QD_OK%"=="0" (
  echo [3/5] 跳过向量集合检查 ^(Qdrant 不可用^)
  goto step4
)
python -c "import sys;sys.path.insert(0,'rag/scripts');from config_loader import load_config,get_qdrant;c=get_qdrant();sys.exit(0 if c.count(collection_name=c.collection).count>0 else 1)" >nul 2>nul
if not errorlevel 1 goto coll_ok
echo [3/5] 检测到向量集合缺失或为空,重建中 ...
python rag/scripts/build_collection.py --recreate
goto step4
:coll_ok
echo [3/5] 向量集合已就绪 ^(running_coach^)
goto step4

rem ---------- 4. 启动 RAG + 产品端 ----------
:step4
curl -s --noproxy "*" -o nul -m 2 http://127.0.0.1:8008/health
if not errorlevel 1 goto rag_running
echo [4/5] 启动 RAG 服务 ^(:8008^) ...
start "" /b cmd /c "cd /d %~dp0rag && python scripts\start_server.py"
goto agent_step

:rag_running
echo [4/5] RAG 服务已在运行 ^(:8008^)
:agent_step
curl -s --noproxy "*" -o nul -m 2 http://127.0.0.1:3000/api/health
if not errorlevel 1 goto agent_running
echo [4/5] 启动产品端 ^(:3000^) ...
start "" /b cmd /c "cd /d %~dp0Agent && node server.js"
goto step5

:agent_running
echo [4/5] 产品端已在运行 ^(:3000^)

rem ---------- 5. 就绪后打开浏览器 ----------
:step5
rem Wait for Agent then open browser (bypass system proxy)
for /L %%i in (1,1,90) do (
  curl -s --noproxy "*" -o nul -m 2 http://127.0.0.1:3000/api/health
  if not errorlevel 1 (
    start "" "http://127.0.0.1:3000"
    goto opened
  )
  ping -n 2 127.0.0.1 >nul
)
:opened
echo.
echo [5/5] 产品端  http://127.0.0.1:3000  就绪后自动打开浏览器
echo        RAG 调试页 http://127.0.0.1:8008 ^(不自动打开^)
echo        关闭本窗口即停止 RAG 与产品端服务 ^(Docker Desktop 不受影响^)。
echo.
pause
