"""字幕翻译与标点恢复服务，使用 OpenRouter API 批量处理字幕片段。"""

import asyncio
import re
from typing import Optional
from loguru import logger

from app.services.subtitle import SubtitleSegment
from app.config import settings

_CJK_RANGE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')
_PUNCT_ENDS = frozenset('。！？，；…、')


def _needs_punctuation(segments: list[SubtitleSegment]) -> bool:
    """检测 CJK 字幕是否缺少标点（超过 80% 的片段末尾无标点）。"""
    if not segments:
        return False
    cjk_count = sum(
        1 for s in segments
        if _CJK_RANGE.search(s.text) and len(_CJK_RANGE.findall(s.text)) / max(len(s.text), 1) > 0.3
    )
    if cjk_count < len(segments) * 0.5:
        return False  # 非 CJK 为主
    has_punct = sum(1 for s in segments if s.text.rstrip() and s.text.rstrip()[-1] in _PUNCT_ENDS)
    return has_punct / max(len(segments), 1) < 0.2


class SubtitleTranslator:
    """字幕翻译器，通过 OpenRouter 调用 LLM 分批翻译。"""

    BATCH_SIZE = 50       # 每批翻译的片段数
    BATCH_DELAY = 0.3     # 批次间延迟（秒）
    TIMEOUT = 60.0        # 单批超时（秒）

    def __init__(self, target_lang: str):
        """
        初始化翻译器。

        参数：
            target_lang: 目标语言代码（如 'zh-CN', 'en', 'ja'）
        """
        self.target_lang = target_lang
        self._lang_name = {
            "zh-CN": "简体中文",
            "zh-TW": "繁体中文",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
        }.get(target_lang, target_lang)

    async def translate(
        self,
        segments: list[SubtitleSegment],
        progress_callback: Optional[callable] = None,
    ) -> list[SubtitleSegment]:
        """
        异步批量翻译字幕片段，翻译结果写入 segment.translation 字段。

        参数：
            segments: 字幕片段列表
            progress_callback: 进度回调，接受 0-100 整数

        返回：
            填充了翻译文本的字幕片段列表
        """
        if not settings.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY 未配置，跳过翻译")
            return segments

        if not self.target_lang:
            return segments

        total = len(segments)
        translated = 0

        for batch_start in range(0, total, self.BATCH_SIZE):
            batch = segments[batch_start: batch_start + self.BATCH_SIZE]
            texts = [seg.text for seg in batch]

            try:
                results = await asyncio.wait_for(
                    self._translate_batch(texts), timeout=self.TIMEOUT
                )
                for seg, trans in zip(batch, results):
                    seg.translation = trans
                translated += len(batch)
            except asyncio.TimeoutError:
                logger.warning(f"翻译批次 {batch_start} 超时，跳过")
                translated += len(batch)
            except Exception as e:
                logger.error(f"翻译批次 {batch_start} 失败: {e}")
                translated += len(batch)

            if progress_callback:
                progress_callback(int(translated / total * 100))

            if batch_start + self.BATCH_SIZE < total:
                await asyncio.sleep(self.BATCH_DELAY)

        logger.info(f"翻译完成: {translated}/{total} 片段")
        return segments

    async def _translate_batch(self, texts: list[str]) -> list[str]:
        """
        在线程池中调用 OpenRouter API 批量翻译。

        参数：
            texts: 待翻译文本列表

        返回：
            翻译结果列表（顺序与输入一致）
        """
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
        prompt = (
            f"将以下字幕逐条翻译为{self._lang_name}。"
            f"严格保持原有编号顺序，每行输出格式为「编号. 译文」，不添加任何解释。\n\n"
            f"{numbered}"
        )

        def _sync_call() -> list[str]:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
            )
            resp = client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = resp.choices[0].message.content or ""
            return self._parse_numbered(raw, texts)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)

    @staticmethod
    def _parse_numbered(raw: str, originals: list[str]) -> list[str]:
        """
        解析 LLM 返回的带编号译文，容错处理缺行或格式偏差。

        参数：
            raw: LLM 原始输出
            originals: 原文列表（用于缺失时回退）

        返回：
            与原文等长的译文列表
        """
        results = [None] * len(originals)
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # 匹配 "1. 译文" 或 "1、译文" 或 "1) 译文"
            for sep in (". ", "、", ") ", "） "):
                if sep in line:
                    head, _, body = line.partition(sep)
                    if head.isdigit():
                        idx = int(head) - 1
                        if 0 <= idx < len(originals):
                            results[idx] = body.strip()
                        break
        # 缺失的条目回退到原文
        return [r if r is not None else originals[i] for i, r in enumerate(results)]


class PunctuationRestorer:
    """为缺少标点的 CJK 字幕批量补充中文标点符号。"""

    BATCH_SIZE = 50
    BATCH_DELAY = 0.3
    TIMEOUT = 60.0

    async def restore(
        self,
        segments: list[SubtitleSegment],
        progress_callback: Optional[callable] = None,
    ) -> list[SubtitleSegment]:
        """
        为字幕片段补充标点，直接修改 seg.text。

        参数：
            segments: 字幕片段列表
            progress_callback: 进度回调，接受 0-100 整数

        返回：
            补充标点后的字幕片段列表
        """
        if not settings.openrouter_api_key:
            return segments
        if not _needs_punctuation(segments):
            return segments

        total = len(segments)
        done = 0

        for batch_start in range(0, total, self.BATCH_SIZE):
            batch = segments[batch_start: batch_start + self.BATCH_SIZE]
            texts = [seg.text for seg in batch]

            try:
                results = await asyncio.wait_for(
                    self._restore_batch(texts), timeout=self.TIMEOUT
                )
                for seg, new_text in zip(batch, results):
                    seg.text = new_text
                done += len(batch)
            except asyncio.TimeoutError:
                logger.warning(f"标点恢复批次 {batch_start} 超时，跳过")
                done += len(batch)
            except Exception as e:
                logger.error(f"标点恢复批次 {batch_start} 失败: {e}")
                done += len(batch)

            if progress_callback:
                progress_callback(int(done / total * 100))

            if batch_start + self.BATCH_SIZE < total:
                await asyncio.sleep(self.BATCH_DELAY)

        logger.info(f"标点恢复完成: {done}/{total} 片段")
        return segments

    async def _restore_batch(self, texts: list[str]) -> list[str]:
        """调用 OpenRouter 为一批字幕添加标点。"""
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
        prompt = (
            "以下是视频字幕，请为每条字幕补充恰当的中文标点符号（句号、逗号、问号等），"
            "保持原文内容不变，仅添加标点。\n"
            "严格保持编号顺序，每行格式为「编号. 内容」，不添加任何解释。\n\n"
            f"{numbered}"
        )

        def _sync_call() -> list[str]:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
            )
            resp = client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = resp.choices[0].message.content or ""
            return SubtitleTranslator._parse_numbered(raw, texts)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)
