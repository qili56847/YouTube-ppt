"""任务数据模型，定义 Job 表和 JobStatus 枚举。"""

from datetime import datetime
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field


class JobStatus(str, Enum):
    """任务处理状态枚举。"""
    PENDING = "pending"
    FETCHING_METADATA = "fetching_metadata"
    DOWNLOADING_VIDEO = "downloading_video"
    DOWNLOADING_SUBTITLES = "downloading_subtitles"
    PARSING_SUBTITLES = "parsing_subtitles"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    GENERATING_OUTLINE = "generating_outline"
    EXTRACTING_FRAMES = "extracting_frames"
    OPTIMIZING_IMAGES = "optimizing_images"
    BUILDING_SLIDES = "building_slides"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(SQLModel, table=True):
    """YouTube 处理任务数据模型。"""

    __tablename__ = "jobs"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True, description="YouTube 影片 URL")
    title: Optional[str] = Field(default=None, description="影片标题")
    duration: Optional[int] = Field(default=None, description="影片时长（秒）")
    thumbnail: Optional[str] = Field(default=None, description="缩略图 URL")

    status: JobStatus = Field(default=JobStatus.PENDING, description="任务状态")
    progress: int = Field(default=0, description="处理进度（0-100）")
    message: Optional[str] = Field(default=None, description="当前状态描述")
    error: Optional[str] = Field(default=None, description="错误信息")

    # 配置项（序列化为 JSON 字符串）
    video_quality: str = Field(default="best", description="下载画质")
    subtitle_langs: str = Field(default="zh-Hans,zh,en", description="字幕语言优先级")
    translate_target: str = Field(default="", description="翻译目标语言")
    image_quality: int = Field(default=75, description="图片压缩质量")

    # 产出
    slide_count: Optional[int] = Field(default=None, description="生成的投影片数量")
    output_path: Optional[str] = Field(default=None, description="输出 HTML 文件路径")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
