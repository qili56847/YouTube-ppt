"""语音转录服务：使用本地 Whisper 模型将视频音频转录为字幕片段。"""

import asyncio
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable

from loguru import logger

from app.services.subtitle import SubtitleSegment

# no_speech_prob 超过此值的片段视为无语音，直接跳过
_NO_SPEECH_THRESHOLD = 0.6

# 已知幻觉短语（Whisper 在静默/背景音中频繁输出）
_HALLUCINATION_PHRASES: set[str] = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "please subscribe", "subscribe", "like and subscribe",
    "you", ".", ",", "...", "the", "a",
    "[music]", "[applause]", "[laughter]", "[silence]", "♪", "♫",
}

# 幻觉模式：纯标点、连续重复词、短句循环
_HALLUCINATION_RES = [
    re.compile(r"^\s*[.!?,;:…\-–—♪♫\[\]()]+\s*$"),   # 纯标点/符号
    re.compile(r"(\b\w+\b)(\s+\1){2,}", re.I),          # 同一单词连续重复 ≥3 次
    re.compile(r"^(.{1,30})\1{2,}$"),                    # 短句循环重复 ≥3 次
]


class WhisperTranscriber:
    """使用 OpenAI Whisper 本地模型进行语音识别，输出 SubtitleSegment 列表。"""

    def __init__(self, model_name: str = "small", ffmpeg_path: str = "ffmpeg"):
        """
        初始化转录器。

        参数：
            model_name: Whisper 模型名称（tiny/base/small/medium/large）
            ffmpeg_path: FFmpeg 可执行文件路径
        """
        self.model_name = model_name
        self.ffmpeg_path = ffmpeg_path
        self._model = None  # 延迟加载，首次转录时才下载模型

    def _load_model(self):
        """延迟加载 Whisper 模型（首次调用时自动下载到 ~/.cache/whisper/）。"""
        if self._model is None:
            import whisper
            logger.info(f"加载 Whisper 模型: {self.model_name}（首次运行将自动下载模型文件）")
            self._model = whisper.load_model(self.model_name)
            logger.info(f"Whisper 模型加载完成: {self.model_name}")
        return self._model

    async def transcribe(
        self,
        video_path: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> list[SubtitleSegment]:
        """
        转录视频语音，返回带时间戳的字幕片段列表。

        参数：
            video_path: 视频文件路径
            progress_callback: 进度回调，接受 0-100 整数

        返回：
            SubtitleSegment 列表，按时间顺序排列
        """
        loop = asyncio.get_event_loop()

        if progress_callback:
            progress_callback(5)

        # 提取音频到临时 WAV 文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_path = Path(tmp.name)

        try:
            await self._extract_audio(video_path, audio_path)
            if progress_callback:
                progress_callback(20)

            # Whisper 是 CPU 密集型，在线程池中运行
            result = await loop.run_in_executor(
                None, self._run_whisper, audio_path
            )

            if progress_callback:
                progress_callback(95)

            segments = self._to_segments(result)
            logger.info(f"Whisper 转录完成: {len(segments)} 个片段，语言={result.get('language', '?')}")
            return segments

        finally:
            audio_path.unlink(missing_ok=True)

    async def _extract_audio(self, video_path: Path, audio_path: Path) -> None:
        """使用 FFmpeg 提取 16kHz 单声道 WAV 音频（Whisper 推荐输入格式）。"""
        cmd = [
            self.ffmpeg_path,
            "-i", str(video_path),
            "-vn",             # 只处理音频
            "-acodec", "pcm_s16le",
            "-ar", "16000",    # 16kHz 采样率
            "-ac", "1",        # 单声道
            "-y",
            str(audio_path),
        ]

        def _run():
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg 音频提取失败: {result.stderr[-500:]}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run)

    def _run_whisper(self, audio_path: Path) -> dict:
        """在线程池中同步执行 Whisper 转录。"""
        import os
        model = self._load_model()
        # Whisper 内部调用 ffmpeg 读取音频，需确保 ffmpeg 在 PATH 中
        ffmpeg_dir = str(Path(self.ffmpeg_path).parent)
        env_path = os.environ.get("PATH", "")
        if ffmpeg_dir not in env_path:
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + env_path
        return model.transcribe(
            str(audio_path),
            verbose=False,
            word_timestamps=False,
            no_speech_threshold=_NO_SPEECH_THRESHOLD,  # 低置信度片段直接跳过
            initial_prompt="",                          # 空提示词减少幻觉
            condition_on_previous_text=False,           # 避免错误文本向后传播
        )

    @staticmethod
    def _to_segments(result: dict) -> list[SubtitleSegment]:
        """将 Whisper 输出的 segments 转换为 SubtitleSegment 列表，过滤幻觉片段。"""
        # 若检测语言为中文，自动将繁体转为简体
        converter = None
        if result.get("language") in ("zh", "chinese"):
            try:
                import opencc
                converter = opencc.OpenCC("t2s")
            except Exception:
                pass

        segments = []
        skipped = 0
        for seg in result.get("segments", []):
            # 过滤 no_speech_prob 高的片段（模型认为这段没有语音）
            if seg.get("no_speech_prob", 0.0) > _NO_SPEECH_THRESHOLD:
                skipped += 1
                continue

            text = seg.get("text", "").strip()
            if not text or len(text) < 2:
                skipped += 1
                continue

            # 过滤已知幻觉短语
            if text.lower() in _HALLUCINATION_PHRASES:
                skipped += 1
                continue

            # 过滤幻觉模式（纯标点、重复词、循环短句）
            if any(pattern.search(text) for pattern in _HALLUCINATION_RES):
                skipped += 1
                continue

            if converter:
                text = converter.convert(text)

            segments.append(SubtitleSegment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                text=text,
            ))

        if skipped:
            logger.info(f"Whisper 幻觉过滤：跳过 {skipped} 个低置信度/幻觉片段，保留 {len(segments)} 个")
        return segments
