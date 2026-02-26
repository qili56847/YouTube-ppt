"""关键帧提取服务：根据字幕时间戳使用 FFmpeg 截取影片帧。"""

import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Callable
from loguru import logger

from app.services.subtitle import SubtitleSegment


class KeyframeExtractor:
    """使用 FFmpeg 从影片中提取字幕对应的关键帧。"""

    def __init__(
        self,
        video_path: Path,
        frames_dir: Path,
        ffmpeg_path: str = "ffmpeg",
    ):
        """
        初始化关键帧提取器。

        参数：
            video_path: 影片文件路径
            frames_dir: 输出帧目录
            ffmpeg_path: FFmpeg 可执行文件路径
        """
        self.video_path = video_path
        self.frames_dir = frames_dir
        self.ffmpeg_path = ffmpeg_path
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    async def extract(
        self,
        segments: list[SubtitleSegment],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> list[Path]:
        """
        批量提取关键帧，每个字幕片段截取片段中间时间点的帧。

        参数：
            segments: 字幕片段列表
            progress_callback: 进度回调，接受 0-100 整数

        返回：
            已提取的帧文件路径列表（与 segments 一一对应）
        """
        frame_paths: list[Path] = []
        total = len(segments)

        for idx, seg in enumerate(segments):
            # 取片段时间中间点截帧，与字幕显示内容最贴合
            timestamp = (seg.start + seg.end) / 2.0
            frame_path = self.frames_dir / f"frame_{idx:05d}.jpg"

            if not frame_path.exists():
                await self._extract_frame(timestamp, frame_path)

            frame_paths.append(frame_path)

            if progress_callback and total > 0:
                progress_callback(int((idx + 1) / total * 100))

        logger.info(f"关键帧提取完成: {len(frame_paths)} 帧")
        return frame_paths

    async def _extract_frame(self, timestamp: float, output_path: Path) -> None:
        """
        异步调用 FFmpeg 提取指定时间戳的帧。

        参数：
            timestamp: 时间戳（秒）
            output_path: 输出图片路径
        """
        cmd = [
            self.ffmpeg_path,
            "-ss", f"{timestamp:.3f}",
            "-i", str(self.video_path),
            "-vframes", "1",
            "-q:v", "2",          # JPEG 质量（2 为高质量）
            "-y",                  # 覆盖已有文件
            str(output_path),
        ]

        def _run():
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg 错误: {result.stderr[-500:]}")

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.error(f"提取帧失败 t={timestamp:.3f}s: {e}")
            # 提取失败时创建空文件占位
            output_path.touch()

    async def get_video_dimensions(self) -> tuple[int, int]:
        """
        获取影片分辨率。

        返回：
            (宽, 高) 像素元组
        """
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(self.video_path),
        ]

        def _run():
            import json
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    return stream.get("width", 1280), stream.get("height", 720)
            return 1280, 720

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _run)
