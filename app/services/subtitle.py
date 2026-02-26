"""字幕解析服务：VTT / SRT 格式解析、去重、合并。"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import webvtt
from loguru import logger

# ── CJK 断句辅助 ────────────────────────────────────────────
_CJK_RANGE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')
_CJK_SENTENCE_ENDS = frozenset('。！？…')
_CJK_MAX_GAP = 2.0    # 超过 2 秒间隔不再合并
_CJK_MAX_CHARS = 120  # 超过 120 字不再合并


def _is_cjk(text: str) -> bool:
    """文本是否以 CJK 字符为主（比例 >30%）。"""
    if not text:
        return False
    return len(_CJK_RANGE.findall(text)) / len(text) > 0.3


def _is_sentence_complete(text: str) -> bool:
    """CJK 文本是否以句末标点结尾。"""
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in _CJK_SENTENCE_ENDS


@dataclass
class SubtitleSegment:
    """字幕片段数据结构。"""
    start: float   # 开始时间（秒）
    end: float     # 结束时间（秒）
    text: str      # 字幕文本
    translation: Optional[str] = None  # 翻译文本


class SubtitleParser:
    """字幕文件解析器，支持 VTT 和 SRT 格式，含去重和短片段合并。"""

    # 最小片段时长（秒），低于此值的片段将被合并
    MIN_DURATION = 1.0
    # 合并间隔（秒），小于此间隔的相邻片段将被合并
    MERGE_GAP = 0.5

    def parse(self, sub_path: Path) -> list[SubtitleSegment]:
        """
        解析字幕文件（自动识别 VTT / SRT 格式），返回去重合并后的片段列表。

        参数：
            sub_path: 字幕文件路径（.vtt 或 .srt）

        返回：
            SubtitleSegment 列表，按时间顺序排列
        """
        ext = sub_path.suffix.lower()
        if ext == ".srt":
            segments = self._parse_srt(sub_path)
        else:
            segments = self._parse_vtt(sub_path)

        segments = self._collapse_rolling(segments)
        segments = self._deduplicate(segments)
        segments = self._merge_short(segments)
        segments = self._merge_cjk_sentences(segments)
        logger.info(f"字幕解析完成: {len(segments)} 个片段 ({ext})")
        return segments

    def _parse_vtt(self, vtt_path: Path) -> list[SubtitleSegment]:
        """解析 VTT 字幕：YouTube 滚动式字幕使用原始内容解析，普通 VTT 用 webvtt-py。"""
        content = vtt_path.read_text(encoding="utf-8", errors="replace")
        # 检测 YouTube 词级时间戳（如 <00:00:01.392>），存在则用专用解析器
        if re.search(r"<\d+:\d+:\d+\.\d+>", content):
            logger.debug("检测到 YouTube 滚动式 VTT，使用专用解析器")
            return self._parse_youtube_rolling_vtt(content)
        # 普通 VTT 用 webvtt-py
        try:
            raw_captions = list(webvtt.read(str(vtt_path)))
        except Exception as e:
            logger.warning(f"VTT 解析失败，尝试 SRT 兼容模式: {e}")
            return self._parse_srt_content(content)
        segments = []
        for cap in raw_captions:
            text = self._clean_text(cap.text)
            if not text:
                continue
            segments.append(SubtitleSegment(
                start=self._time_to_seconds(cap.start),
                end=self._time_to_seconds(cap.end),
                text=text,
            ))
        return segments

    @staticmethod
    def _parse_youtube_rolling_vtt(content: str) -> list[SubtitleSegment]:
        """专门解析 YouTube 滚动式 VTT：只提取含词级时间戳的新词行，跳过重复上下文行。

        YouTube 自动字幕的双行格式：
          第一行 = 旧内容（无词级时间戳）
          第二行 = 新词（带 <HH:MM:SS.mmm> 词级时间戳）
        仅保留含词级时间戳的行，去掉冗余旧内容。
        """
        _TIME_RANGE_RE = re.compile(
            r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
        )
        _WORD_TIMING_RE = re.compile(r"<\d+:\d+:\d+\.\d+>")
        _ALL_TAGS_RE = re.compile(r"<[^>]+>")

        def _to_sec(ts: str) -> float:
            parts = ts.replace(",", ".").split(":")
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            m, s = parts
            return int(m) * 60 + float(s)

        segments = []
        # 仅在真正空行处分割（YouTube VTT 的时间行与内容行间有仅含空格的行，不能用 \s*）
        for block in re.split(r"\n\n+", content.strip()):
            lines = block.strip().splitlines()
            time_match = None
            time_idx = -1
            for i, line in enumerate(lines):
                m = _TIME_RANGE_RE.search(line)
                if m:
                    time_match = m
                    time_idx = i
                    break
            if not time_match:
                continue

            start = _to_sec(time_match.group(1))
            end = _to_sec(time_match.group(2))

            # 只取含词级时间戳的行（新内容）；无标签的行是旧上下文，跳过
            tagged = [
                l for l in lines[time_idx + 1:]
                if _WORD_TIMING_RE.search(l)
            ]
            if not tagged:
                continue

            text = " ".join(
                _ALL_TAGS_RE.sub("", l).strip()
                for l in tagged if l.strip()
            )
            text = " ".join(text.split())
            if text:
                segments.append(SubtitleSegment(start=start, end=end, text=text))

        return segments

    def _parse_srt(self, srt_path: Path) -> list[SubtitleSegment]:
        """解析 SRT 格式字幕文件。"""
        content = srt_path.read_text(encoding="utf-8", errors="replace")
        return self._parse_srt_content(content)

    @staticmethod
    def _parse_srt_content(content: str) -> list[SubtitleSegment]:
        """从字符串内容解析 SRT 格式字幕。"""
        # SRT 块：序号 → 时间轴 → 文本 → 空行
        _TIME_RE = re.compile(
            r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})"
        )
        _TAG_RE = re.compile(r"<[^>]+>")

        def _to_sec(ts: str) -> float:
            parts = ts.replace(",", ".").split(":")
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)

        segments = []
        blocks = re.split(r"\n\s*\n", content.strip())
        for block in blocks:
            lines = block.strip().splitlines()
            for i, line in enumerate(lines):
                m = _TIME_RE.search(line)
                if m:
                    text_lines = lines[i + 1:]
                    text = " ".join(
                        _TAG_RE.sub("", ln).strip()
                        for ln in text_lines if ln.strip()
                    )
                    if text:
                        segments.append(SubtitleSegment(
                            start=_to_sec(m.group(1)),
                            end=_to_sec(m.group(2)),
                            text=text,
                        ))
                    break
        return segments

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理字幕文本：去除 HTML 标签、时间戳注释、多余空白。"""
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"<\d+:\d+:\d+\.\d+>", "", text)
        text = re.sub(r"\{\\an\d+\}", "", text)
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        return text.strip()

    @staticmethod
    def _time_to_seconds(time_str: str) -> float:
        """将 HH:MM:SS.mmm 或 MM:SS.mmm 格式转为秒数。"""
        parts = time_str.replace(",", ".").split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])

    @staticmethod
    def _collapse_rolling(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
        """折叠 YouTube 滚动式自动字幕：连续片段逐词累积，保留每组最长文本。

        YouTube 自动字幕常以「滚动窗口」格式输出，每个 cue 在前一 cue 基础上追加词汇，
        导致相邻片段内容高度重叠。本方法将同一滚动组内的片段合并，只保留最长的那段文本。
        """
        if not segments:
            return []

        def _is_extension(prev_text: str, curr_text: str) -> bool:
            """判断 curr_text 是否是 prev_text 的延伸（前缀匹配或高度重叠）。"""
            p = prev_text.strip()
            c = curr_text.strip()
            if not p or not c:
                return False
            # curr 以 prev 开头（滚动追加）
            if c.startswith(p):
                return True
            # prev 以 curr 开头（窗口收缩，换行时常见）
            if p.startswith(c):
                return True
            # 词级别重叠比例 > 60%
            p_words = p.split()
            c_words = c.split()
            if not p_words or not c_words:
                return False
            overlap = sum(1 for w in p_words if w in c_words)
            ratio = overlap / max(len(p_words), len(c_words))
            return ratio > 0.6

        result: list[SubtitleSegment] = []
        # 当前滚动组的起始时间、结束时间、最长文本
        group_start = segments[0].start
        group_end = segments[0].end
        group_text = segments[0].text

        for seg in segments[1:]:
            time_gap = seg.start - group_end
            if time_gap <= 1.5 and _is_extension(group_text, seg.text):
                # 同一滚动组：扩展时间范围，保留更长文本
                group_end = seg.end
                if len(seg.text) > len(group_text):
                    group_text = seg.text
            else:
                result.append(SubtitleSegment(start=group_start, end=group_end, text=group_text))
                group_start = seg.start
                group_end = seg.end
                group_text = seg.text

        result.append(SubtitleSegment(start=group_start, end=group_end, text=group_text))
        return result

    @staticmethod
    def _deduplicate(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
        """去除相邻的重复字幕（YouTube 自动字幕常有此问题）。"""
        if not segments:
            return []
        result = [segments[0]]
        for seg in segments[1:]:
            if seg.text != result[-1].text:
                result.append(seg)
        return result

    def _merge_short(self, segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
        """合并过短片段和间隔极小的相邻片段。"""
        if not segments:
            return []
        result = [segments[0]]
        for seg in segments[1:]:
            last = result[-1]
            gap = seg.start - last.end
            duration = last.end - last.start
            if gap <= self.MERGE_GAP and duration < self.MIN_DURATION:
                sep = "" if _is_cjk(last.text) else " "
                result[-1] = SubtitleSegment(
                    start=last.start,
                    end=seg.end,
                    text=last.text + sep + seg.text,
                )
            else:
                result.append(seg)
        return result

    def _merge_cjk_sentences(self, segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
        """CJK 文本专用：合并不完整的句子，直到遇到句末标点或超出限制。"""
        if not segments or not _is_cjk(segments[0].text):
            return segments
        result: list[SubtitleSegment] = []
        buf: Optional[SubtitleSegment] = None
        for seg in segments:
            if buf is None:
                buf = seg
                continue
            gap = seg.start - buf.end
            too_long = len(buf.text) + len(seg.text) > _CJK_MAX_CHARS
            too_far = gap > _CJK_MAX_GAP
            if _is_sentence_complete(buf.text) or too_long or too_far:
                result.append(buf)
                buf = seg
            else:
                buf = SubtitleSegment(start=buf.start, end=seg.end, text=buf.text + seg.text)
        if buf:
            result.append(buf)
        logger.debug(f"CJK 断句合并: {len(segments)} → {len(result)} 个片段")
        return result
