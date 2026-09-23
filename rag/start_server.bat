@echo off
rem 独立窗口启动 RAG 服务(http://127.0.0.1:8008,关闭本窗口即停止)
chcp 936 >nul
cd /d "%~dp0"
title 跑步健身教练 - RAG 服务 (:8008)
python scripts\start_server.py
pause
