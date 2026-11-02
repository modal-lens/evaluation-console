import pytest

from tools.text_splitter import TextSplitter
from utils.exceptions import ValidationAppError


def test_splitter_preserves_text_and_offsets() -> None:
    text = "第一句。第二句比较长！第三句。"
    chunks = TextSplitter().split(text, max_len=8, overlap=0)

    assert "".join(chunk.text for chunk in chunks) == text
    assert [chunk.chunk_id for chunk in chunks] == [f"chunk_{index:03d}" for index in range(1, len(chunks) + 1)]
    assert all(text[chunk.start_offset : chunk.end_offset] == chunk.text for chunk in chunks)


def test_splitter_hard_splits_long_sentence_and_limits_size() -> None:
    chunks = TextSplitter().split("A" * 25, max_len=10, overlap=0)
    assert [len(chunk.text) for chunk in chunks] == [10, 10, 5]


def test_splitter_rejects_too_many_chunks() -> None:
    with pytest.raises(ValidationAppError):
        TextSplitter().split("A" * 30, max_len=5, max_chunks=3)
