# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 开发规范

- 使用 `uv` 管理 Python 环境，所有 Python 程序通过 `uv run` 执行
- Docstring 以**简体中文**书写，记录函数和类的说明

## 常用命令

```bash
# 启动开发服务器
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 安装/同步依赖
uv sync

# 验证依赖是否正确安装
uv run python -c "import yt_dlp, ffmpeg, webvtt, PIL; print('OK')"

# 添加新依赖
uv add <package>

# 快速测试字幕解析（用已有 VTT 文件）
uv run python -c "
from pathlib import Path
from app.services.subtitle import SubtitleParser
segs = SubtitleParser().parse(Path('data/jobs/<job_id>/subtitles/original.*.vtt'))
for s in segs[:10]: print(f'[{s.start:.1f}-{s.end:.1f}s] {s.text}')
"

# 快速测试 Whisper 转录
uv run python -c "
import os
from pathlib import Path
from app.config import settings
os.environ['PATH'] = str(Path(settings.ffmpeg_path).parent) + os.pathsep + os.environ.get('PATH', '')
import whisper
m = whisper.load_model(settings.whisper_model)
r = m.transcribe('data/jobs/<job_id>/video.mp4', verbose=False)
for s in r['segments'][:5]: print(f'[{s[\"start\"]:.1f}-{s[\"end\"]:.1f}] {s[\"text\"].strip()}')
"

# 测试标点检测
uv run python -c "
from app.services.subtitle import SubtitleSegment
from app.services.translator import _needs_punctuation
segs = [SubtitleSegment(0,2,'你好朋友们今天来聊一下'), SubtitleSegment(2,4,'这个很重要的问题')]
print(_needs_punctuation(segs))  # True
"
```

## 架构概览

这是一个将 YouTube 影片转换为静态 HTML 投影片的 Web 应用。

**请求流程：**
1. 前端 (`static/index.html`) 提交 URL → `POST /api/jobs`
2. 相同 URL 已完成时直接返回缓存任务；活跃任务超过 `max_concurrent_jobs` 时返回 429
3. FastAPI 创建 `Job` 记录，通过 `BackgroundTasks` 启动 `Pipeline`
4. 前端跳转到 `/job/{id}`，通过 `GET /api/events/{id}` 建立 SSE 连接接收实时进度
5. 完成后跳转到 `/viewer/{id}`，从 `/api/jobs/{id}/view` 获取 HTML 投影片并以 iframe 嵌入展示

**处理流水线（`app/services/pipeline.py`）— 核心协调器：**

| 阶段 | 服务 | 进度区间 |
|------|------|----------|
| 获取元数据 | `downloader.fetch_metadata()` | 0–10% |
| 下载影片 | `downloader.download_video()` | 10–50% |
| 下载字幕 | `downloader.download_subtitles()` | 50–60% |
| 解析字幕 | `SubtitleParser.parse()` | 60–65% |
| 语音转录 | `WhisperTranscriber.transcribe()` | 63–65%（无字幕时触发）|
| 翻译/标点恢复 | `SubtitleTranslator.translate()` 或 `PunctuationRestorer.restore()` | 65–70% |
| AI 大纲生成 | `AIOutlineGenerator.generate()` | 70–73% |
| 提取关键帧 | `KeyframeExtractor.extract()` | 73–85% |
| 优化图片 | `ImageOptimizer.optimize_to_base64()` | 85–90% |
| 生成投影片 | `SlideBuilder.build()` | 90–100% |

无字幕时降级链：`Whisper 转录` → 若结果为空 → `_generate_scene_segments()`（每 30 秒截一帧）。

**翻译/标点恢复逻辑（`app/services/translator.py`）：**
- 设置了 `translate_target`：走 `SubtitleTranslator.translate()`，结果写入 `seg.translation`
- 未设置 `translate_target` + OpenRouter API key 存在 + 字幕为 CJK 且缺标点（`_needs_punctuation()` 检测：>80% 片段末尾无 `。！？，；…、`）：走 `PunctuationRestorer.restore()`，直接修改 `seg.text`
- 两者均使用 OpenRouter API，每批 50 条，`run_in_executor` 同步调用，超时 60 秒，失败回退原文

**SSE 进度推送（`app/workers/queue.py`）：**
`Pipeline._update_status()` 同时写入 SQLite 与 `event_queue`。`queue.py` 的 `stream()` 生成器使用嵌套 try/except：外层 `while True` 负责保活，内层捕获 `asyncio.TimeoutError` 发送心跳包（`{"type":"ping"}`），避免 Whisper 长时间转录时 SSE 连接中断。

**投影片输出（`app/services/slide_builder.py`）：**
帧图片以 Base64 内嵌于单个 HTML 文件。侧边栏缩略图使用独立低质量版本（240×135、quality=25，由 `optimizer.thumbnail_to_base64()` 生成），主视图使用原始 quality。支持键盘左右键导航、缩略图侧边栏、全部展开模式、字幕全文搜索（`/` 键），以及 AI 大纲侧边抽屉（`📑 大纲` 按钮，仅在大纲生成成功时显示）。

**字幕解析（`app/services/subtitle.py`）：**
YouTube 自动字幕采用滚动窗口格式，`_parse_youtube_rolling_vtt()` 只提取含词级时间戳（`<HH:MM:SS.mmm>`）的新词行。解析后依次经过 `_collapse_rolling()` → `_deduplicate()` → `_merge_short()` → `_merge_cjk_sentences()`。
- `_merge_cjk_sentences()`：仅对 CJK 文本生效，合并片段直到遇到句末标点（`。！？…`），最大间隔 2 秒，最大字符数 120

**yt-dlp 下载（`app/services/downloader.py`）：**
`_base_opts()` 包含 `cookiefile`（绕过机器人检测）和 `js_runtimes`（Node.js，用于 nsig JS 挑战解算）。下载参数：`retries=15`、`fragment_retries=15`、`concurrent_fragment_downloads=2`、`http_chunk_size=10MB`（防止 YouTube 限速断连）。

**繁体→简体转换：**
`opencc.OpenCC("t2s")` 在三处执行：`transcriber.py`（Whisper 输出）、`pipeline.py`（元数据标题）、`slide_builder.py`（slides.html 标题）。

**Whisper 语音转录（`app/services/transcriber.py`）：**
仅在无字幕时触发，延迟加载模型。关键细节：
- `_run_whisper()` 临时将 `FFMPEG_PATH` 父目录追加到 `os.environ["PATH"]`，否则报 `FileNotFoundError`
- 幻觉过滤：`no_speech_threshold=0.6`、已知幻觉短语集合、正则（纯标点、重复词、循环短句）

## 关键配置（`.env`）

| 变量 | 说明 |
|------|------|
| `FFMPEG_PATH` | FFmpeg 可执行文件完整路径 |
| `FFPROBE_PATH` | ffprobe 可执行文件完整路径 |
| `DATA_DIR` | 运行时数据根目录（默认 `data/`） |
| `MAX_CONCURRENT_JOBS` | 最大并发任务数（默认 2，超限返回 429） |
| `VIDEO_QUALITY` | yt-dlp 画质（`best`/`1080p`/`720p`/`480p`） |
| `SUBTITLE_LANGS` | 字幕语言优先级，逗号分隔（如 `zh-Hans,zh,en`） |
| `TRANSLATE_TARGET` | 翻译目标语言，留空不翻译（如 `zh-CN`） |
| `WHISPER_MODEL` | Whisper 模型大小（`tiny`/`base`/`small`/`medium`/`large`，默认 `small`） |
| `COOKIES_FILE` | YouTube cookies 文件路径（如 `cookies.txt`） |
| `NODE_PATH` | Node.js 可执行路径（留空自动检测） |
| `OPENROUTER_API_KEY` | OpenRouter API 密钥（翻译、标点恢复、AI 大纲均需要） |
| `OPENROUTER_MODEL` | 使用的模型（默认 `openai/gpt-4o-mini`） |

## 数据目录结构

```
data/
├── db/youtube_slides.db        # SQLite 数据库
└── jobs/{job_id}/
    ├── video.mp4
    ├── subtitles/original.*.vtt
    ├── frames/frame_00001.jpg ...
    └── output/slides.html      # 最终产出
```

## 系统依赖

- **FFmpeg**：需手动安装并在 `.env` 中配置路径。当前已配置：`C:/Users/lq_ka/Desktop/ffmpeg/bin/`
- **Node.js**：yt-dlp 解算 YouTube nsig JS 挑战所需。当前路径：`C:/Program Files/nodejs/node.EXE`
- **yt-dlp-ejs**：`pip install yt-dlp-ejs==0.5.0`，配合 Node.js 完成 JS 挑战解算
- **Whisper 模型文件**：首次使用时自动下载到 `~/.cache/whisper/`；若下载损坏（SHA256 不匹配），手动删除对应 `.pt` 文件后重新下载
