import pytest

from utils.json_utils import ensure_list, loads_json_object


def test_loads_json_accepts_object_and_full_code_block() -> None:
    assert loads_json_object('{"ok":true}') == {"ok": True}
    assert loads_json_object('```json\n{"ok":true}\n```') == {"ok": True}


def test_loads_json_rejects_natural_language_wrapper() -> None:
    with pytest.raises(ValueError):
        loads_json_object('结果如下：{"ok":true}')


def test_ensure_list() -> None:
    assert ensure_list(None) == []
    assert ensure_list("x") == ["x"]
    assert ensure_list(["x"]) == ["x"]
