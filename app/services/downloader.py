"""yt-dlp 封装服务：影片元数据获取、影片下载、字幕下载。"""

import asyncio
from pathlib import Path
from typing import Callable, Optional
import yt_dlp
from loguru import logger
from app.config import settings


class VideoDownloader:
    """封装 yt-dlp 实现影片与字幕下载功能。"""

    def __init__(self, job_dir: Path, ffmpeg_path: str = "ffmpeg"):
        """
        初始化下载器。

        参数：
            job_dir: 任务工作目录
            ffmpeg_path: FFmpeg 可执行文件路径
        """
        self.job_dir = job_dir
        self.ffmpeg_path = ffmpeg_path
        self.video_path = job_dir / "video.mp4"
        self.subtitles_dir = job_dir / "subtitles"
        self.subtitles_dir.mkdir(parents=True, exist_ok=True)

    def _base_opts(self) -> dict:
        """返回 yt-dlp 通用配置。"""
        opts = {
            "ffmpeg_location": self.ffmpeg_path,
            "quiet": True,
            "no_warnings": True,
            "no_color": True,       # 禁用 ANSI 颜色码，避免错误消息含转义字符
            "socket_timeout": 30,   # 单次连接超时（秒），避免无限等待
            "retries": 15,          # 下载失败自动重试次数
            "fragment_retries": 15, # 分片下载失败重试次数
            "http_chunk_size": 10485760,  # 每次 HTTP 请求 10MB，减少断线风险
        }
        # 若配置了 Cookie 文件则使用，绕过机器人检测
        if settings.cookies_file and Path(settings.cookies_file).exists():
            opts["cookiefile"] = settings.cookies_file
        # 配置 Node.js 路径用于解算 YouTube JS 挑战（n 参数）
        node = settings.node_path or self._find_node()
        if node:
            opts["js_runtimes"] = {"node": {"path": node}}
        return opts

    @staticmethod
    def _find_node() -> str:
        """自动检测 Node.js 可执行文件路径。"""
        import shutil
        return shutil.which("node") or ""

    async def fetch_metadata(self, url: str) -> dict:
        """
        异步获取影片元数据，不下载影片。

        参数：
            url: YouTube 影片 URL

        返回：
            包含 title, duration, thumbnail, subtitles 等字段的字典
        """
        opts = {**self._base_opts(), "skip_download": True}

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _extract)

        return {
            "title": info.get("title", "未知标题"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "description": info.get("description", ""),
            "uploader": info.get("uploader", ""),
            "view_count": info.get("view_count", 0),
            "chapters": info.get("chapters", []),
            "subtitles": info.get("subtitles", {}),
            "automatic_captions": info.get("automatic_captions", {}),
        }

    async def download_video(
        self,
        url: str,
        quality: str = "best",
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """
        异步下载影片到任务目录。

        参数：
            url: YouTube 影片 URL
            quality: 画质选项（best/720p/1080p 等）
            progress_callback: 进度回调函数，接受 0-100 整数

        返回：
            下载完成的影片文件路径
        """
        format_str = self._build_format_string(quality)

        def _progress_hook(d: dict):
            if d["status"] == "downloading" and progress_callback:
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    pct = int(downloaded / total * 100)
                    progress_callback(pct)

        opts = {
            **self._base_opts(),
            "format": format_str,
            "outtmpl": str(self.video_path.with_suffix("")),
            "merge_output_format": "mp4",
            "progress_hooks": [_progress_hook],
            "concurrent_fragment_downloads": 2,  # 并发下载 DASH 分片（降低并发防止被 YouTube 限速断连）
        }

        def _download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _download)

        # yt-dlp 可能产生 .mp4 或带扩展名的文件，找到实际文件
        actual = self._find_video_file()
        if actual and actual != self.video_path:
            actual.rename(self.video_path)

        logger.info(f"影片下载完成: {self.video_path}")
        return self.video_path

    def _build_format_string(self, quality: str) -> str:
        """根据画质选项构建 yt-dlp format 字符串。"""
        quality_map = {
            "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
            "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
            "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        }
        return quality_map.get(quality, quality_map["best"])

    def _find_video_file(self) -> Optional[Path]:
        """在任务目录中寻找下载的影片文件。"""
        for ext in [".mp4", ".mkv", ".webm", ".avi"]:
            candidates = list(self.job_dir.glob(f"video{ext}"))
            if candidates:
                return candidates[0]
        return None

    async def download_subtitles(
        self, url: str, preferred_langs: list[str]
    ) -> Optional[Path]:
        """
        异步下载字幕，采用多策略兜底：
        1. 按偏好语言下载（含自动字幕）
        2. 若无结果，单独尝试英文自动字幕
        3. 若仍无结果，抓取所有可用语言中的第一条

        参数：
            url: YouTube 影片 URL
            preferred_langs: 语言优先级列表，如 ['zh-Hans', 'zh', 'en']

        返回：
            下载的字幕文件路径，若所有策略均失败则返回 None
        """
        loop = asyncio.get_event_loop()

        # 确保 en 在兜底列表中
        fallback_langs = preferred_langs.copy()
        if "en" not in fallback_langs:
            fallback_langs.append("en")

        # 策略 1：偏好语言，VTT 格式（自动字幕使用 yt-dlp 内置转换，不需要 ffmpeg）
        result = await self._attempt_subtitle_download(loop, url, fallback_langs, "vtt/best")
        if result:
            return result

        # 策略 2：仅英文，放宽格式限制
        logger.warning("偏好语言字幕未找到，尝试仅下载英文自动字幕...")
        result = await self._attempt_subtitle_download(loop, url, ["en"], "vtt/ttml/srv3/srv2/srv1/json3/best")
        if result:
            return result

        logger.warning("所有字幕下载策略均失败，将降级为场景截帧")
        return None

    async def _attempt_subtitle_download(
        self,
        loop,
        url: str,
        langs: list[str],
        fmt: str,
    ) -> Optional[Path]:
        """
        执行一次字幕下载尝试。

        参数：
            loop: asyncio 事件循环
            url: YouTube 影片 URL
            langs: 语言代码列表
            fmt: yt-dlp subtitlesformat 字符串

        返回：
            找到的字幕文件路径，或 None
        """
        opts = {
            **self._base_opts(),
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": fmt,
            "outtmpl": str(self.subtitles_dir / "original").replace("\\", "/"),
        }

        def _download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        try:
            await loop.run_in_executor(None, _download)
        except Exception as e:
            logger.warning(f"字幕下载异常 langs={langs}: {e}")
            return None

        found = self._find_subtitle_file(langs)
        if found:
            logger.info(f"字幕下载成功: {found.name}")
        return found

    def _find_subtitle_file(self, preferred_langs: list[str]) -> Optional[Path]:
        """按语言优先级查找字幕文件，支持 VTT 和 SRT 格式。"""
        all_subs = list(self.subtitles_dir.glob("*.vtt")) + list(self.subtitles_dir.glob("*.srt"))
        if not all_subs:
            return None

        # 按语言优先级匹配
        for lang in preferred_langs:
            for f in all_subs:
                if lang in f.name:
                    return f

        # 返回任意找到的字幕文件
        return all_subs[0]
