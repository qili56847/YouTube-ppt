"""FastAPI 应用入口，注册路由、静态文件服务、生命周期管理。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.database import init_db
from app.routers import video, sse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化数据库，关闭时清理资源。"""
    logger.info("启动 YouTube Slides 服务...")

    # 确保数据目录存在
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.jobs_path.mkdir(parents=True, exist_ok=True)

    # 初始化数据库
    init_db()
    logger.info(f"数据库初始化完成: {settings.db_path}")
    logger.info(f"服务启动成功，访问 http://{settings.app_host}:{settings.app_port}")

    yield

    logger.info("服务关闭中...")


app = FastAPI(
    title="YouTube Slides",
    description="将 YouTube 影片转换为静态阅读版投影片",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS（开发模式允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(video.router)
app.include_router(sse.router)

# 挂载静态文件（前端）
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# 前端路由
@app.get("/")
async def index():
    """首页：YouTube URL 输入表单。"""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/job/{job_id}")
async def job_page(job_id: int):
    """任务进度页。"""
    return FileResponse(str(static_dir / "job.html"))


@app.get("/viewer/{job_id}")
async def viewer_page(job_id: int):
    """从服务器提供投影片查看器页。"""
    return FileResponse(str(static_dir / "viewer.html"))


@app.get("/health")
async def health() -> dict:
    """健康检查端点。"""
    return {"status": "ok", "version": "1.0.0"}
