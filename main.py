# -*- coding: utf-8 -*-
"""FastAPI 主入口（M3：骨架 + 调度 + API；M5 起托管前端构建产物）。

启动：uvicorn main:app --host 127.0.0.1 --port 8000
规格第八章：无认证，默认绑定 127.0.0.1，内网访问自行改绑。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import routes
from app.scheduler import jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("event-tracker")

scheduler = BackgroundScheduler(timezone=config.TIMEZONE)


def _register_jobs() -> None:
    """注册三个 CronTrigger（规格第三章 SCHEDULE）。"""
    spec = config.SCHEDULE
    scheduler.add_job(
        lambda: jobs.safe_run(jobs.job_announcement_screen),
        CronTrigger(hour=spec["announcement_screen"]["hour"],
                    minute=spec["announcement_screen"]["minute"]),
        id="announcement_screen", name="16:00 全市场初筛",
        replace_existing=True, misfire_grace_time=3600)
    scheduler.add_job(
        lambda: jobs.safe_run(jobs.job_progress_analysis),
        CronTrigger(hour=spec["progress_analysis"]["hour"],
                    minute=spec["progress_analysis"]["minute"]),
        id="progress_analysis", name="21:30 进展分析",
        replace_existing=True, misfire_grace_time=3600)
    scheduler.add_job(
        lambda: jobs.safe_run(jobs.job_tracker),
        CronTrigger(hour=spec["tracker"]["hour"],
                    minute=spec["tracker"]["minute"]),
        id="tracker", name="14:30 触发引擎",
        replace_existing=True, misfire_grace_time=3600)
    log.info("已注册定时任务: %s", [j.id for j in scheduler.get_jobs()])


@asynccontextmanager
async def lifespan(_: FastAPI):
    _register_jobs()
    scheduler.start()
    log.info("APScheduler 已启动（%s）", config.TIMEZONE)
    yield
    scheduler.shutdown(wait=False)
    log.info("APScheduler 已关闭")


app = FastAPI(title="A股公告事件跟踪系统", version="M5", lifespan=lifespan)
app.include_router(routes.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "jobs": [j.id for j in scheduler.get_jobs()]}


# 前端静态资源（M5 构建产物；未构建时保留占位页）
config.STATIC_DIR.mkdir(parents=True, exist_ok=True)
_index = config.STATIC_DIR / "index.html"
if not _index.exists():
    _index.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>A股公告事件跟踪系统</title></head><body>"
        "<h3>后端已运行</h3><p>前端未构建：进入 frontend/ 执行 "
        "npm install &amp;&amp; npm run build，产物将复制到 static/。</p>"
        "</body></html>", encoding="utf-8")
app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True),
          name="static")


@app.exception_handler(404)
async def spa_fallback(request: Request, exc: HTTPException):
    """SPA 深链回退（M5）。

    StaticFiles 挂在 "/" 会接管所有路径；React Router 的深链
    （/signals、/timeline?symbol=…）刷新时找不到同名文件而 404，
    此处对非 /api 路径统一回退 index.html，由前端路由渲染。
    /api/* 的 404 保持 JSON，不被前端页面吞掉。
    """
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.detail or "Not Found"},
                            status_code=404)
    if _index.exists():
        return FileResponse(str(_index))
    return JSONResponse({"detail": "Not Found"}, status_code=404)
