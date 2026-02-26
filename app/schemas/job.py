"""API 请求与响应 Schema。"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, HttpUrl, field_validator
from app.models.job import JobStatus


class JobCreate(BaseModel):
    """创建任务的请求 Schema。"""

    url: str
    video_quality: str = "best"
    subtitle_langs: str = "zh-Hans,zh,en"
    translate_target: str = ""
    image_quality: int = 75

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        """验证是否为有效的 YouTube URL。"""
        v = v.strip()
        if not any(domain in v for domain in ["youtube.com", "youtu.be", "youtube-nocookie.com"]):
            raise ValueError("请提供有效的 YouTube URL")
        return v

    @field_validator("image_quality")
    @classmethod
    def validate_image_quality(cls, v: int) -> int:
        """验证图片质量范围。"""
        if not 1 <= v <= 95:
            raise ValueError("图片质量必须在 1-95 之间")
        return v


class JobResponse(BaseModel):
    """任务响应 Schema。"""

    id: int
    url: str
    title: Optional[str]
    duration: Optional[int]
    thumbnail: Optional[str]
    status: JobStatus
    progress: int
    message: Optional[str]
    error: Optional[str]
    video_quality: str
    subtitle_langs: str
    translate_target: str
    image_quality: int
    slide_count: Optional[int]
    output_path: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SSEEvent(BaseModel):
    """SSE 事件数据结构。"""

    job_id: int
    status: JobStatus
    progress: int
    message: Optional[str] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
