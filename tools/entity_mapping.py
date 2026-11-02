"""实体别名映射表读取工具。

本模块只负责读取“别名 -> 标准实体名”的映射关系；实体融合、关系重写
等业务逻辑由调用方完成。映射表可能由 Excel 导出，因此需要兼容多种中文编码。
"""

from __future__ import annotations

import csv
import io
from functools import lru_cache
from pathlib import Path


# 以当前文件所在目录向上定位项目根目录，避免依赖进程启动时的工作目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 默认映射表位于项目 data 目录；调用方也可以传入测试用的临时文件。
MAPPING_TABLE_CSV_PATH = PROJECT_ROOT / "data" / "mapping_table.csv"
MAPPING_ALIAS_FIELD = "别名"
MAPPING_STANDARD_NAME_FIELD = "中文标准名称"


@lru_cache(maxsize=1)
def load_mapping_table(csv_path: Path | str | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if csv_path is None:
        # 未显式传路径时，统一使用项目默认映射表。
        csv_path = MAPPING_TABLE_CSV_PATH
    csv_path = Path(csv_path)
    if not csv_path.exists():
        # 映射表属于可选增强能力，文件缺失时不阻断实体抽取流程。
        return mapping

    # 先读取二进制，再手动尝试多种编码；不能固定使用 utf-8-sig，
    # 因为 Windows/Excel 导出的中文 CSV 常见为 GBK 或 GB18030。
    raw_bytes = csv_path.read_bytes()
    csv_text = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            csv_text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            # 当前编码无法解码时继续尝试下一种编码。
            continue
    if csv_text is None:
        raise UnicodeDecodeError("csv", raw_bytes, 0, 1, "无法识别映射表编码")

    # 只读取前两列，不依赖表头名称。这样即使历史文件的表头存在乱码，
    # 只要数据列顺序保持不变，映射仍然可以正常加载。
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    next(reader, None)
    for row in reader:
        # 异常短行或空行不参与映射。
        if len(row) < 2:
            continue
        alias = row[0].strip()
        standard_name = row[1].strip()
        # 别名与标准名相同时没有必要保存，避免产生无效映射。
        if alias and standard_name and alias != standard_name:
            mapping[alias] = standard_name

    return mapping
