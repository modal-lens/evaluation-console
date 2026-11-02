"""稳定、可追踪并受数量限制的文本分段。"""

from __future__ import annotations

import re
from typing import List

from tools.schemas import TextChunk
from utils.exceptions import ValidationAppError
from utils.logger import get_logger


logger = get_logger(__name__)
SENTENCE_BOUNDARY_RE = re.compile(r"[。！？.!?…;；\n]")


class TextSplitter:
    """按字符上限切分，并优先选择句子/段落边界。"""

    def split_sentences(self, text: str) -> List[str]:
        """兼容旧调用：将文本按中英文句末标点切分。"""

        sentences = re.split(r"(?<=[。！？.!?…;；])|\n+", text)
        return [sentence.strip() for sentence in sentences if sentence.strip()]

    def split(
        self,
        text: str,
        *,
        max_len: int = 1000,
        overlap: int = 0,
        max_chunks: int = 200,
    ) -> list[TextChunk]:
        """返回带稳定 ID 和原文偏移量的分段。"""

        if max_len <= 0:
            raise ValidationAppError("max_len 必须大于 0", stage="split")
        if overlap < 0 or overlap >= max_len:
            raise ValidationAppError("overlap 必须大于等于 0 且小于 max_len", stage="split")
        if max_chunks <= 0:
            raise ValidationAppError("max_chunks 必须大于 0", stage="split")
        if not text or not text.strip():
            raise ValidationAppError("待分段文本为空", stage="split")

        chunks: list[TextChunk] = []
        start = 0
        text_length = len(text)
        while start < text_length:
            hard_end = min(start + max_len, text_length)
            end = self._choose_boundary(text, start, hard_end)
            if end <= start:
                end = hard_end

            raw = text[start:end]
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            content_start = start + leading
            content_end = end - trailing
            content = text[content_start:content_end]
            if content:
                chunks.append(
                    TextChunk(
                        chunk_id=f"chunk_{len(chunks) + 1:03d}",
                        index=len(chunks),
                        text=content,
                        start_offset=content_start,
                        end_offset=content_end,
                    )
                )
                if len(chunks) > max_chunks:
                    raise ValidationAppError(
                        f"文本分段数超过上限 {max_chunks}",
                        stage="split",
                    )

            if end >= text_length:
                break
            next_start = end - overlap
            start = next_start if next_start > start else end

        logger.info("文本分段完成 chunk_count=%s max_len=%s overlap=%s", len(chunks), max_len, overlap)
        return chunks

    @staticmethod
    def _choose_boundary(text: str, start: int, hard_end: int) -> int:
        """在窗口后半段选择最后一个句子边界。"""

        if hard_end >= len(text):
            return hard_end
        minimum = start + max(1, (hard_end - start) // 2)
        matches = list(SENTENCE_BOUNDARY_RE.finditer(text, minimum, hard_end))
        return matches[-1].end() if matches else hard_end

    def split_text_by_length(self, text: str, max_len: int = 1000) -> List[str]:
        """兼容旧 demo，返回纯文本分段列表。"""

        return [chunk.text for chunk in self.split(text, max_len=max_len)]


text_splitter = TextSplitter()


def split_text_by_length(text: str, max_len: int = 1000) -> List[str]:
    """兼容旧调用的模块级函数。"""

    return text_splitter.split_text_by_length(text, max_len)
