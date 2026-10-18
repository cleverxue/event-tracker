@echo off
rem event-tracker 常驻启动脚本（Windows 任务计划程序调用）
rem 由任务计划在用户登录时触发；uvicorn 常驻，崩溃由任务设置的
rem RestartCount 兜底重试。日志写 logs\scheduler.log（append）。
cd /d C:\Users\admin\WorkBuddy\2026-10-17-13-58-00\event-tracker
"C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 >> logs\scheduler.log 2>&1
