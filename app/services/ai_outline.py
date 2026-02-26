"""AI 大纲生成服务：根据字幕内容调用 OpenRouter API 生成视频结构化大纲。"""

from loguru import logger

from app.config import settings
from app.services.subtitle import SubtitleSegment

# 大纲生成超时（秒）
_TIMEOUT = 90.0
# 发送给 AI 的最大字幕字符数（控制 token 消耗）
_MAX_CHARS = 3000


def _format_duration(seconds: float) -> str:
    """将秒数格式化为 MM:SS 或 HH:MM:SS 字符串。"""
    s = int(seconds)
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _markdown_to_html(md: str) -> str:
    """将简单 Markdown 转换为 HTML（仅处理标题、列表、粗体）。"""
    import re
    lines = md.strip().splitlines()
    html_parts = []
    in_ul = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append("")
            continue
        # ## 标题
        if stripped.startswith("## "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            text = stripped[3:].strip()
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            html_parts.append(f"<h3>{text}</h3>")
        # ### 标题
        elif stripped.startswith("### "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            text = stripped[4:].strip()
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            html_parts.append(f"<h4>{text}</h4>")
        # - 列表项
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            text = stripped[2:].strip()
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            html_parts.append(f"<li>{text}</li>")
        # 普通段落
        else:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
            html_parts.append(f"<p>{text}</p>")
    if in_ul:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


class AIOutlineGenerator:
    """调用 OpenRouter API 生成视频内容大纲。"""

    def generate(self, segments: list[SubtitleSegment], metadata: dict) -> str | None:
        """
        根据字幕片段和元数据生成视频大纲。

        参数：
            segments: 字幕片段列表
            metadata: 影片元数据（包含 title、duration 等）

        返回：
            Markdown 格式的大纲字符串，失败时返回 None
        """
        if not settings.openrouter_api_key:
            return None

        title = metadata.get("title", "未知标题")
        duration = metadata.get("duration", 0)
        duration_str = _format_duration(float(duration)) if duration else "未知"

        # 拼接字幕文本，限制长度
        full_text = "".join(seg.text for seg in segments)
        if len(full_text) > _MAX_CHARS:
            full_text = full_text[:_MAX_CHARS] + "..."

        prompt = (
            f"你是一位专业的视频内容分析师。以下是一段 YouTube 视频的字幕文本。\n"
            f"视频标题：{title}\n"
            f"视频时长：{duration_str}\n\n"
            f"字幕内容：\n{full_text}\n\n"
            f"请用简体中文生成该视频的结构化大纲，严格按照以下 Markdown 格式输出，不要添加其他内容：\n\n"
            f"## 主题\n"
            f"（1-2句话概括视频核心主题）\n\n"
            f"## 内容结构\n"
            f"- **章节名**：简短说明\n"
            f"（列出3-6个主要章节）\n\n"
            f"## 核心要点\n"
            f"- 要点1\n"
            f"- 要点2\n"
            f"（列出3-5条最重要的观点或结论）"
        )

        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=_TIMEOUT,
            )
            resp = client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            outline = (resp.choices[0].message.content or "").strip()
            if outline:
                logger.info(f"AI 大纲生成成功，共 {len(outline)} 字")
                return outline
            return None
        except Exception as e:
            logger.warning(f"AI 大纲生成失败（将跳过）: {e}")
            return None
