"""应用配置模块，使用 Pydantic Settings 管理环境变量。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置，从 .env 文件或环境变量读取。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # 应用配置
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # 数据存储根目录
    data_dir: str = "data"

    # FFmpeg 可执行路径
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # 下载画质
    video_quality: str = "best"

    # 字幕语言优先级（逗号分隔字符串）
    subtitle_langs: str = "zh-Hans,zh,en"

    # 翻译目标语言（空字符串表示不翻译）
    translate_target: str = ""

    # 图片质量（1-95）
    image_quality: int = 75

    # 最大并发任务数
    max_concurrent_jobs: int = 2

    # Cookie 文件路径（用于绕过 YouTube 机器人检测，留空则不使用）
    cookies_file: str = ""

    # Node.js 可执行文件路径（用于 yt-dlp JS 挑战解算，留空则自动检测）
    node_path: str = ""

    # OpenRouter 翻译配置
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"

    # Whisper 语音转录配置（无字幕时使用）
    whisper_model: str = "small"  # tiny / base / small / medium / large

    @property
    def data_path(self) -> Path:
        """返回数据根目录的 Path 对象。"""
        return Path(self.data_dir)

    @property
    def db_path(self) -> Path:
        """返回 SQLite 数据库文件路径。"""
        return self.data_path / "db" / "youtube_slides.db"

    @property
    def jobs_path(self) -> Path:
        """返回任务数据目录路径。"""
        return self.data_path / "jobs"

    @property
    def subtitle_langs_list(self) -> list[str]:
        """将字幕语言字符串解析为列表。"""
        return [lang.strip() for lang in self.subtitle_langs.split(",") if lang.strip()]


settings = Settings()
