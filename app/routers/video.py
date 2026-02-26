"""影片处理 API 路由：创建任务、查询任务、下载结果。"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.job import Job, JobStatus
from app.schemas.job import JobCreate, JobResponse
from app.services.pipeline import Pipeline

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# 进行中的状态集合（用于并发计数）
_ACTIVE_STATUSES = {
    JobStatus.PENDING,
    JobStatus.FETCHING_METADATA,
    JobStatus.DOWNLOADING_VIDEO,
    JobStatus.DOWNLOADING_SUBTITLES,
    JobStatus.PARSING_SUBTITLES,
    JobStatus.TRANSCRIBING,
    JobStatus.TRANSLATING,
    JobStatus.EXTRACTING_FRAMES,
    JobStatus.OPTIMIZING_IMAGES,
    JobStatus.BUILDING_SLIDES,
}


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    body: JobCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> JobResponse:
    """
    创建新的 YouTube 处理任务，立即返回任务 ID。

    任务在后台异步执行，通过 SSE 端点获取实时进度。
    同一 URL 已有完成任务时直接返回缓存结果；活跃任务超限时返回 429。
    """
    # ── 缓存检测：同 URL 已完成 ──────────────────────────────
    existing = session.exec(
        select(Job)
        .where(Job.url == body.url, Job.status == JobStatus.COMPLETED)
        .order_by(Job.created_at.desc())
    ).first()
    if existing:
        return JobResponse.model_validate(existing)

    # ── 并发限制 ─────────────────────────────────────────────
    active_count = session.exec(
        select(Job).where(Job.status.in_([s.value for s in _ACTIVE_STATUSES]))
    ).all().__len__()
    if active_count >= settings.max_concurrent_jobs:
        raise HTTPException(
            status_code=429,
            detail=f"当前已有 {active_count} 个任务在处理中，最多允许 {settings.max_concurrent_jobs} 个并发任务，请稍后再试。",
        )

    job = Job(
        url=body.url,
        video_quality=body.video_quality,
        subtitle_langs=body.subtitle_langs,
        translate_target=body.translate_target,
        image_quality=body.image_quality,
        status=JobStatus.PENDING,
        progress=0,
        message="任务已创建，等待处理...",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    # 在后台启动处理流水线
    background_tasks.add_task(_run_pipeline, job.id)

    return JobResponse.model_validate(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    session: Session = Depends(get_session),
    limit: int = 20,
    offset: int = 0,
) -> list[JobResponse]:
    """获取任务列表，按创建时间降序排列。"""
    jobs = session.exec(
        select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [JobResponse.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    session: Session = Depends(get_session),
) -> JobResponse:
    """根据任务 ID 获取任务详情。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return JobResponse.model_validate(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: int,
    session: Session = Depends(get_session),
) -> None:
    """删除任务记录及其数据文件。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    # 删除任务文件
    import shutil
    from app.config import settings
    job_dir = settings.jobs_path / str(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    session.delete(job)
    session.commit()


@router.get("/{job_id}/view")
async def view_slides(
    job_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    """在浏览器中内联展示 HTML 投影片（不触发下载）。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job.status != JobStatus.COMPLETED or not job.output_path:
        raise HTTPException(status_code=400, detail="投影片尚未生成完成")

    output_path = Path(job.output_path)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="投影片文件不存在")

    # 不传 filename，浏览器收到 Content-Disposition: inline，直接渲染而非下载
    return FileResponse(path=str(output_path), media_type="text/html")


@router.get("/{job_id}/download")
async def download_slides(
    job_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    """下载已生成的 HTML 投影片文件（触发另存为）。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job.status != JobStatus.COMPLETED or not job.output_path:
        raise HTTPException(status_code=400, detail="投影片尚未生成完成")

    output_path = Path(job.output_path)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="投影片文件不存在")

    safe_title = "".join(c for c in (job.title or "slides") if c.isalnum() or c in " -_")[:50]
    return FileResponse(
        path=str(output_path),
        media_type="text/html",
        filename=f"{safe_title}.html",
    )


async def _run_pipeline(job_id: int) -> None:
    """后台任务：执行处理流水线。"""
    pipeline = Pipeline(job_id)
    await pipeline.run()
