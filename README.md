# YouTube 影片转投影片

将任意 YouTube 影片自动转换为可交互的静态 HTML 投影片，支持字幕提取、AI 翻译、关键帧截取与 AI 大纲生成。

## 功能特性

- **自动字幕提取**：优先使用 YouTube 自带字幕（支持滚动窗口格式），无字幕时自动回退到 Whisper 本地语音转录
- **繁体→简体转换**：所有 CJK 文本统一转换为简体中文
- **AI 标点恢复**：对缺少标点的 CJK 字幕自动补全句末标点
- **AI 翻译**：可选将字幕翻译为目标语言（通过 OpenRouter API）
- **AI 大纲生成**：自动提炼视频结构，生成可侧边展开的大纲抽屉
- **关键帧截取**：按字幕时间轴提取视频帧，Base64 内嵌于单个 HTML 文件
- **实时进度推送**：通过 SSE 实时显示处理进度，Whisper 转录期间保持心跳不断连
- **投影片交互**：键盘左右键翻页、缩略图侧边栏、全部展开模式、`/` 键全文搜索字幕

## 系统要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- [FFmpeg](https://ffmpeg.org/download.html)（需手动安装）
- [Node.js](https://nodejs.org/)（用于 yt-dlp nsig JS 挑战解算）

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/youtube-ppt.git
cd youtube-ppt
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置环境变量

复制并编辑 `.env` 文件：

```bash
cp .env.example .env
```

最少需要配置 FFmpeg 路径：

```env
FFMPEG_PATH=/usr/local/bin/ffmpeg
FFPROBE_PATH=/usr/local/bin/ffprobe
```

完整配置项见下方[配置说明](#配置说明)。

### 4. 启动服务

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

打开浏览器访问 `http://localhost:8000`，输入 YouTube 链接即可开始转换。

## 配置说明

在项目根目录创建 `.env` 文件，支持以下配置项：

| 变量 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `FFMPEG_PATH` | 是 | FFmpeg 可执行文件完整路径 | — |
| `FFPROBE_PATH` | 是 | ffprobe 可执行文件完整路径 | — |
| `DATA_DIR` | 否 | 运行时数据根目录 | `data/` |
| `MAX_CONCURRENT_JOBS` | 否 | 最大并发任务数，超限返回 429 | `2` |
| `VIDEO_QUALITY` | 否 | 下载画质：`best` / `1080p` / `720p` / `480p` | `best` |
| `SUBTITLE_LANGS` | 否 | 字幕语言优先级，逗号分隔 | `zh-Hans,zh,en` |
| `TRANSLATE_TARGET` | 否 | 翻译目标语言，留空不翻译 | — |
| `WHISPER_MODEL` | 否 | Whisper 模型：`tiny` / `base` / `small` / `medium` / `large` | `small` |
| `COOKIES_FILE` | 否 | YouTube cookies 文件路径，用于绕过机器人检测 | — |
| `NODE_PATH` | 否 | Node.js 可执行路径，留空自动检测 | — |
| `OPENROUTER_API_KEY` | 否 | OpenRouter API 密钥（翻译、标点恢复、AI 大纲需要） | — |
| `OPENROUTER_MODEL` | 否 | 使用的 AI 模型 | `openai/gpt-4o-mini` |

### AI 功能说明

`OPENROUTER_API_KEY` 为可选项，但不配置时以下功能将不可用：

- 字幕翻译
- CJK 字幕标点恢复
- AI 大纲生成

### YouTube cookies

若遇到下载被 YouTube 拦截，可导出浏览器 cookies 到 `cookies.txt`（Netscape 格式），并设置 `COOKIES_FILE=cookies.txt`。

## 处理流水线

```
提交 URL
  → 获取元数据         (0–10%)
  → 下载影片           (10–50%)
  → 下载字幕           (50–60%)
  → 解析字幕           (60–65%)
      ↳ 无字幕时 Whisper 转录
  → 翻译 / 标点恢复    (65–70%)
  → AI 大纲生成        (70–73%)
  → 提取关键帧         (73–85%)
  → 优化图片           (85–90%)
  → 生成投影片 HTML    (90–100%)
```

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

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + uvicorn |
| 数据库 | SQLite（通过 SQLModel） |
| 视频下载 | yt-dlp + yt-dlp-ejs |
| 语音转录 | OpenAI Whisper |
| 图像处理 | Pillow + ffmpeg-python |
| 字幕解析 | webvtt-py |
| 繁简转换 | opencc-python-reimplemented |
| AI 服务 | OpenRouter API（兼容 OpenAI SDK） |
| 进度推送 | Server-Sent Events（SSE） |
| 前端 | 原生 HTML / CSS / JS |

## 开发

```bash
# 验证依赖安装
uv run python -c "import yt_dlp, ffmpeg, webvtt, PIL; print('OK')"

# 测试字幕解析
uv run python -c "
from pathlib import Path
from app.services.subtitle import SubtitleParser
segs = SubtitleParser().parse(Path('data/jobs/<job_id>/subtitles/original.*.vtt'))
for s in segs[:10]: print(f'[{s.start:.1f}-{s.end:.1f}s] {s.text}')
"
```

## License

MIT

## 贡献

欢迎提交 Issue 反馈问题，或通过 Pull Request 贡献代码。
