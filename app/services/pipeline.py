"""处理流水线协调器：串联 8 个阶段的状态机。"""

import asyncio
import re
from pathlib import Path
from datetime import datetime
from typing import Optional
from loguru import logger

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
from sqlmodel import Session

from app.config import settings
from app.models.job import Job, JobStatus
from app.database import engine
from app.workers.queue import event_queue
from app.services.downloader import VideoDownloader
from app.services.subtitle import SubtitleParser
from app.services.translator import SubtitleTranslator, PunctuationRestorer
from app.services.ai_outline import AIOutlineGenerator
from app.services.extractor import KeyframeExtractor
from app.services.optimizer import ImageOptimizer
from app.services.slide_builder import SlideBuilder


class Pipeline:
    """
    YouTube 影片处理流水线。

    8 个阶段：
    1. 获取元数据 (0-10%)
    2. 下载影片 (10-50%)
    3. 下载字幕 (50-60%)
    4. 解析字幕 (60-65%)
    5. 翻译字幕 (65-70%)
    6. 提取关键帧 (70-85%)
    7. 优化图片 (85-90%)
    8. 生成投影片 (90-100%)
    """

    def __init__(self, job_id: int):
        """
        初始化流水线。

        参数：
            job_id: 任务 ID
        """
        self.job_id = job_id
        self.job_dir: Optional[Path] = None

    async def run(self) -> None:
        """执行完整处理流水线，自动捕获异常并更新任务状态。最长运行 4 小时。"""
        try:
            await asyncio.wait_for(self._execute(), timeout=4 * 3600)
        except asyncio.TimeoutError:
            logger.error(f"流水线超时 job_id={self.job_id}（超过 4 小时）")
            await self._update_status(JobStatus.FAILED, 0, error="任务超时（超过 4 小时），已自动终止")
        except asyncio.CancelledError:
            await self._update_status(JobStatus.FAILED, 0, error="任务被取消")
        except Exception as e:
            logger.exception(f"流水线执行失败 job_id={self.job_id}: {e}")
            await self._update_status(JobStatus.FAILED, 0, error=_ANSI_RE.sub("", str(e)))

    async def _execute(self) -> None:
        """执行所有流水线阶段。"""
        job = self._load_job()
        if not job:
            raise ValueError(f"任务 {self.job_id} 不存在")

        # 初始化任务目录
        self.job_dir = settings.jobs_path / str(self.job_id)
        self.job_dir.mkdir(parents=True, exist_ok=True)

        downloader = VideoDownloader(self.job_dir, settings.ffmpeg_path)
        subtitle_langs = [l.strip() for l in job.subtitle_langs.split(",") if l.strip()]

        # ── 阶段 1：获取元数据 ────────────────────────────────
        await self._update_status(JobStatus.FETCHING_METADATA, 5, "正在获取影片信息...")
        metadata = await downloader.fetch_metadata(job.url)
        await self._update_job_metadata(metadata)
        await self._update_status(JobStatus.FETCHING_METADATA, 10, f"已获取: {metadata['title']}")

        # ── 阶段 2：下载影片 ────────────────────────────────
        await self._update_status(JobStatus.DOWNLOADING_VIDEO, 12, "开始下载影片...")

        loop = asyncio.get_event_loop()

        def video_progress(pct: int):
            asyncio.run_coroutine_threadsafe(
                self._update_status(
                    JobStatus.DOWNLOADING_VIDEO,
                    12 + int(pct * 0.38),
                    f"下载影片中... {pct}%",
                ),
                loop,
            )

        video_path = await downloader.download_video(
            job.url, job.video_quality, video_progress
        )
        await self._update_status(JobStatus.DOWNLOADING_VIDEO, 50, "影片下载完成")

        # ── 阶段 3：下载字幕 ────────────────────────────────
        await self._update_status(JobStatus.DOWNLOADING_SUBTITLES, 52, "正在下载字幕...")
        subtitle_path = await downloader.download_subtitles(job.url, subtitle_langs)

        if not subtitle_path:
            logger.warning(f"job_id={self.job_id} 无字幕，将尝试 Whisper 语音转录")
            await self._update_status(JobStatus.DOWNLOADING_SUBTITLES, 60, "未找到字幕，将使用语音转录")

        # ── 阶段 4：解析字幕 ────────────────────────────────
        await self._update_status(JobStatus.PARSING_SUBTITLES, 62, "正在解析字幕...")
        segments = []
        if subtitle_path and subtitle_path.exists():
            parser = SubtitleParser()
            segments = parser.parse(subtitle_path)

        if not segments:
            segments = await self._transcribe_or_fallback(video_path, metadata)

        await self._update_status(JobStatus.PARSING_SUBTITLES, 65, f"字幕解析完成: {len(segments)} 段")

        # ── 阶段 5：翻译字幕 / 标点恢复 ────────────────────────────────
        await self._update_status(JobStatus.TRANSLATING, 66, "准备翻译...")
        if job.translate_target and segments:
            translator = SubtitleTranslator(job.translate_target)
            await self._update_status(JobStatus.TRANSLATING, 67, f"翻译字幕到 {job.translate_target}...")
            segments = await translator.translate(segments)
        elif segments and settings.openrouter_api_key:
            # 无需翻译时，尝试为缺标点的 CJK 字幕恢复标点
            restorer = PunctuationRestorer()
            await self._update_status(JobStatus.TRANSLATING, 67, "检测字幕标点...")
            segments = await restorer.restore(segments)
        await self._update_status(JobStatus.TRANSLATING, 70, "字幕处理完成")

        # ── 阶段 5.5：AI 大纲生成 ────────────────────────────
        outline = None
        if settings.openrouter_api_key and segments:
            await self._update_status(JobStatus.GENERATING_OUTLINE, 71, "正在生成视频大纲...")
            generator = AIOutlineGenerator()
            outline = await loop.run_in_executor(None, generator.generate, segments, metadata)
            await self._update_status(JobStatus.GENERATING_OUTLINE, 73, "大纲生成完成" if outline else "大纲生成跳过")

        # ── 阶段 6：提取关键帧 ────────────────────────────────
        await self._update_status(JobStatus.EXTRACTING_FRAMES, 74, "正在提取关键帧...")
        frames_dir = self.job_dir / "frames"
        extractor = KeyframeExtractor(video_path, frames_dir, settings.ffmpeg_path)

        extracted_frames: list[Path] = []

        def frame_progress(pct: int):
            asyncio.run_coroutine_threadsafe(
                self._update_status(
                    JobStatus.EXTRACTING_FRAMES,
                    72 + int(pct * 0.13),
                    f"提取帧中... {pct}%",
                ),
                loop,
            )

        extracted_frames = await extractor.extract(segments, frame_progress)
        await self._update_status(JobStatus.EXTRACTING_FRAMES, 85, f"关键帧提取完成: {len(extracted_frames)} 帧")

        # ── 阶段 7：优化图片 ────────────────────────────────
        await self._update_status(JobStatus.OPTIMIZING_IMAGES, 87, "正在优化图片...")
        optimizer = ImageOptimizer(job.image_quality)
        total = len(extracted_frames)
        loop = asyncio.get_event_loop()
        CONCURRENCY = 8  # 同时处理的帧数

        frame_base64_list = [None] * total
        thumb_base64_list = [None] * total
        completed = 0

        for batch_start in range(0, total, CONCURRENCY):
            batch = extracted_frames[batch_start: batch_start + CONCURRENCY]
            # 主图和缩略图并行生成
            main_results = await asyncio.gather(*[
                loop.run_in_executor(None, optimizer.optimize_to_base64, fp)
                for fp in batch
            ])
            thumb_results = await asyncio.gather(*[
                loop.run_in_executor(None, optimizer.thumbnail_to_base64, fp)
                for fp in batch
            ])
            for i, (b64, thumb) in enumerate(zip(main_results, thumb_results)):
                frame_base64_list[batch_start + i] = b64
                thumb_base64_list[batch_start + i] = thumb
            completed += len(batch)
            pct = int(completed / max(total, 1) * 100)
            await self._update_status(
                JobStatus.OPTIMIZING_IMAGES,
                87 + int(pct * 0.03),
                f"优化图片中... {pct}%",
            )
        await self._update_status(JobStatus.OPTIMIZING_IMAGES, 90, "图片优化完成")

        # ── 阶段 8：生成投影片 ────────────────────────────────
        await self._update_status(JobStatus.BUILDING_SLIDES, 92, "正在生成投影片...")
        output_dir = self.job_dir / "output"
        builder = SlideBuilder(output_dir)
        output_path = builder.build(segments, frame_base64_list, metadata, thumb_base64_list, outline)

        # 完成
        with Session(engine) as session:
            db_job = session.get(Job, self.job_id)
            if db_job:
                db_job.status = JobStatus.COMPLETED
                db_job.progress = 100
                db_job.message = f"完成！共 {len(segments)} 张投影片"
                db_job.slide_count = len(segments)
                db_job.output_path = str(output_path)
                db_job.updated_at = datetime.utcnow()
                session.add(db_job)
                session.commit()

        await event_queue.publish(self.job_id, {
            "job_id": self.job_id,
            "status": "completed",
            "progress": 100,
            "message": f"完成！共 {len(segments)} 张投影片",
            "output_path": str(output_path),
        })
        logger.info(f"流水线完成 job_id={self.job_id}, output={output_path}")

    async def _update_status(
        self,
        status: JobStatus,
        progress: int,
        message: str = "",
        error: str = "",
    ) -> None:
        """更新数据库中的任务状态，并发布 SSE 事件。"""
        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                job.status = status
                job.progress = progress
                job.message = message
                if error:
                    job.error = error
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

        await event_queue.publish(self.job_id, {
            "job_id": self.job_id,
            "status": status.value,
            "progress": progress,
            "message": message,
            "error": error,
        })

    async def _update_job_metadata(self, metadata: dict) -> None:
        """将影片元数据写入数据库。"""
        try:
            import opencc
            _t2s = opencc.OpenCC("t2s").convert
        except Exception:
            _t2s = lambda x: x

        with Session(engine) as session:
            job = session.get(Job, self.job_id)
            if job:
                job.title = _t2s(metadata.get("title", ""))[:500]
                job.duration = metadata.get("duration", 0)
                job.thumbnail = metadata.get("thumbnail", "")
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

    def _load_job(self) -> Optional[Job]:
        """从数据库加载任务。"""
        with Session(engine) as session:
            return session.get(Job, self.job_id)

    async def _transcribe_or_fallback(self, video_path: Path, metadata: dict):
        """
        无字幕时，优先用 Whisper 语音转录；失败则降级为每 30 秒截一帧。

        参数：
            video_path: 影片文件路径
            metadata: 影片元数据

        返回：
            SubtitleSegment 列表
        """
        from app.services.transcriber import WhisperTranscriber

        await self._update_status(JobStatus.TRANSCRIBING, 63, f"正在用 Whisper({settings.whisper_model}) 转录语音...")
        loop = asyncio.get_event_loop()

        def transcribe_progress(pct: int):
            asyncio.run_coroutine_threadsafe(
                self._update_status(
                    JobStatus.TRANSCRIBING,
                    63 + int(pct * 0.02),
                    f"语音转录中... {pct}%",
                ),
                loop,
            )

        try:
            transcriber = WhisperTranscriber(
                model_name=settings.whisper_model,
                ffmpeg_path=settings.ffmpeg_path,
            )
            segments = await transcriber.transcribe(video_path, transcribe_progress)
            if segments:
                logger.info(f"Whisper 转录成功: {len(segments)} 个片段")
                return segments
            logger.warning("Whisper 转录结果为空，降级为场景截帧")
        except Exception as e:
            logger.exception(f"Whisper 转录失败，降级为场景截帧: {e}")

        return self._generate_scene_segments(metadata)

    def _generate_scene_segments(self, metadata: dict):
        """
        降级方案：按固定间隔生成场景片段（每 30 秒一段）。

        参数：
            metadata: 影片元数据

        返回：
            SubtitleSegment 列表
        """
        from app.services.subtitle import SubtitleSegment

        duration = metadata.get("duration", 0)
        if duration <= 0:
            return []

        interval = 30
        segments = []
        t = 0.0
        while t < duration:
            end = min(t + interval, duration)
            segments.append(SubtitleSegment(
                start=t,
                end=end,
                text=f"[{self._format_time(t)} - {self._format_time(end)}]",
            ))
            t += interval

        logger.info(f"无字幕且转录失败，按 {interval}s 间隔生成 {len(segments)} 个场景片段")
        return segments

    @staticmethod
    def _format_time(seconds: float) -> str:
        """将秒数格式化为 MM:SS 字符串。"""
        total = int(seconds)
        m = total // 60
        s = total % 60
        return f"{m:02d}:{s:02d}"
