@echo off
rem 独立窗口启动产品端(http://127.0.0.1:3000,关闭本窗口即停止)
rem AI 教练需另行启动 RAG: 双击 ..\rag\start_server.bat
chcp 936 >nul
cd /d "%~dp0"
title 跑步健身教练 - 产品端 (:3000)
node server.js
pause
