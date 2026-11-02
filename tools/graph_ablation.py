"""按照 ``data/text.txt`` 定义的流程执行知识图谱消融。

处理顺序固定为：
1. 统一实体名称、别名和映射表标准名；
2. 使用规则、类型约束和相似度合并实体；
3. 对无法确定的候选实体执行可选的 LLM 确认；
4. 归一化属性键并处理属性冲突；
5. 重写关系端点、归一化关系类型并去重；
6. 输出 ``entities``、``relations`` 和 ``resolutions``。

本模块不负责读取上传文件，也不负责决定接口协议。文件读取和实体抽取由
``apis.graph_ablation_api`` 完成，所有消融规则集中在本模块中。
"""

from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from tools.entity_mapping import load_mapping_table


UNKNOWN_VALUES = {"", "未知", "无", "无信息", "不详", "none", "null", "n/a"}


@dataclass(frozen=True)
class AblationStrategy:
    """消融策略；字段对应 ``data/text.txt`` 中的可配置能力。"""

    # 实体、属性和关系分别使用独立阈值，避免一个阈值影响三类对象。
    similarity_threshold: float = 0.88
    attribute_similarity_threshold: float = 0.92
    relation_similarity_threshold: float = 0.90
    use_llm_resolution: bool = False
    llm_confidence_threshold: float = 0.75
    llm_resolution_batch_size: int = 64
    llm_resolution_workers: int = 1
    language_mapping: dict[str, str] = field(default_factory=dict)
    entity_equivalence: dict[str, str] = field(default_factory=dict)
    attribute_equivalence: dict[str, str] = field(default_factory=dict)
    relation_equivalence: dict[str, str] = field(default_factory=dict)
    translate_output_to_chinese: bool = True
    # 默认读取 data/mapping_table.csv，也允许测试或业务方传入其他映射表。
    mapping_table_path: str | None = None


def normalize_text(value: Any) -> str:
    """将输入值安全转换为字符串，并清理首尾空白。"""

    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_key(value: Any) -> str:
    """生成用于比较的键：全半角、大小写和空白差异均不影响匹配。"""

    text = normalize_text(value).casefold()
    return re.sub(r"\s+", "", text)


def name_similarity(left: Any, right: Any) -> float:
    """计算两个名称的归一化相似度。"""

    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _identity_conflicts(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """识别会改变实体身份的明显冲突，避免相似名称被误合并。"""
    conflicts: list[str] = []
    left_name = normalize_text(left.get("standard_name") or left.get("name"))
    right_name = normalize_text(right.get("standard_name") or right.get("name"))
    left_numbers = re.findall(r"\d+(?:\.\d+)?", left_name)
    right_numbers = re.findall(r"\d+(?:\.\d+)?", right_name)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        conflicts.append(f"名称数字不一致: {left_numbers} != {right_numbers}")

    left_attributes = left.get("attributes") if isinstance(left.get("attributes"), dict) else {}
    right_attributes = right.get("attributes") if isinstance(right.get("attributes"), dict) else {}
    identity_keys = {
        "发生时间", "时间", "日期", "年份", "year", "date", "time",
        "地点", "发生地点", "location", "place",
    }
    for left_key, left_value in left_attributes.items():
        normalized_left_key = normalize_key(left_key)
        if normalized_left_key not in {normalize_key(key) for key in identity_keys}:
            continue
        matching_key = next(
            (key for key in right_attributes if normalize_key(key) == normalized_left_key),
            None,
        )
        if matching_key is None:
            continue
        normalized_left_value = normalize_key(left_value)
        normalized_right_value = normalize_key(right_attributes[matching_key])
        if normalized_left_value and normalized_right_value and normalized_left_value != normalized_right_value:
            conflicts.append(f"身份属性不一致: {left_key}={left_value} != {matching_key}={right_attributes[matching_key]}")
    return conflicts


def _add_ambiguous_merge_warning(
    warnings: list[dict[str, Any]],
    left: dict[str, Any],
    right: dict[str, Any],
    conflicts: list[str],
    action: str,
) -> None:
    """记录一次实体合并拦截告警，避免同一对实体重复记录。"""
    names = {left["standard_name"], right["standard_name"]}
    if any(
        warning.get("code") == "AMBIGUOUS_ENTITY_MERGE_BLOCKED"
        and {warning.get("left"), warning.get("right")} == names
        for warning in warnings
    ):
        return
    warnings.append({
        "code": "AMBIGUOUS_ENTITY_MERGE_BLOCKED",
        "left": left["standard_name"],
        "right": right["standard_name"],
        "reasons": conflicts,
        "action": action,
    })


def _mapping_from_strategy(mapping: dict[str, str]) -> dict[str, str]:
    """将用户配置映射表转换为归一化键，支持大小写和空格差异。"""

    return {normalize_key(key): normalize_text(value) for key, value in mapping.items() if normalize_key(key)}


def resolve_mapping(value: Any, mapping: dict[str, str]) -> str:
    """按映射表查找标准值，并支持 A -> B -> C 的多级映射。"""

    current = normalize_text(value)
    seen: set[str] = set()
    while current and normalize_key(current) in mapping and normalize_key(current) not in seen:
        seen.add(normalize_key(current))
        current = normalize_text(mapping[normalize_key(current)])
    return current


def _type_compatible(left: Any, right: Any) -> bool:
    """实现类型约束：两边都有类型时必须相同，任一边为空则允许比较。"""

    left_type = normalize_key(left)
    right_type = normalize_key(right)
    return not left_type or not right_type or left_type == right_type


def _is_unknown(value: Any) -> bool:
    return normalize_text(value).casefold() in UNKNOWN_VALUES


def _stable_id(prefix: str, value: Any) -> str:
    """使用归一化内容生成稳定 ID，保证重复请求得到相同的标准 ID。"""

    import hashlib

    digest = hashlib.sha1(normalize_text(value).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _pick_standard_name(names: list[str]) -> str:
    """在没有显式映射时选择稳定名称，优先中文名，再优先较完整名称。"""

    clean_names = list(dict.fromkeys(normalize_text(name) for name in names if normalize_text(name)))
    if not clean_names:
        return ""
    chinese = [name for name in clean_names if any("\u4e00" <= char <= "\u9fff" for char in name)]
    return max(chinese or clean_names, key=lambda item: (len(item), item))


def _iter_aliases(entity: dict[str, Any]) -> list[str]:
    """收集实体名称和 aliases 字段，兼容抽取模块的不同字段形态。"""

    aliases = entity.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = [aliases]
    return list(dict.fromkeys(
        normalize_text(value)
        for value in [entity.get("name"), *aliases]
        if normalize_text(value)
    ))


def _compact_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """只保留 LLM 判断候选实体所需的信息，避免发送完整图谱。"""

    return {
        "name": entity.get("name"),
        "standard_name": entity.get("standard_name"),
        "type": entity.get("type"),
        "aliases": entity.get("aliases", []),
        "attributes": entity.get("attributes", {}),
        "description": entity.get("description", ""),
        "source_doc": entity.get("source_doc", ""),
    }


class LLMEntityResolutionService:
    """调用 OpenAI 兼容客户端确认规则无法判断的实体候选对。"""

    SYSTEM_PROMPT = "你是知识图谱实体消歧助手，只返回合法 JSON 对象，不要输出解释。"

    def __init__(self, llm_client: Any | None = None) -> None:
        if llm_client is None:
            from tools.llm_client import LLMClient

            llm_client = LLMClient()
        self.llm_client = llm_client

    def resolve(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        """单独判断一对实体，保留给批量失败时的兼容回退路径。"""

        payload = {
            "entity_a": _compact_entity(left),
            "entity_b": _compact_entity(right),
            "task": "判断两个实体是否指向同一个现实世界实体，并给出中文标准名。",
            "output_schema": {
                "same_entity": "boolean",
                "standard_name": "string",
                "aliases": ["string"],
                "confidence": "number between 0 and 1",
                "reason": "string",
            },
        }
        result = self.llm_client.chat_json(json.dumps(payload, ensure_ascii=False), system_prompt=self.SYSTEM_PROMPT)
        return self._normalize(result)

    def resolve_many(self, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
        """一次请求判断一批候选实体，默认接口优先使用该方法降低请求次数。"""

        if not pairs:
            return []
        payload = {
            "pairs": [
                {"index": index, "entity_a": _compact_entity(left), "entity_b": _compact_entity(right)}
                for index, (left, right) in enumerate(pairs)
            ],
            "task": "逐对判断两个实体是否指向同一个现实世界实体。",
            "output_schema": {"results": [{"index": "integer", "same_entity": "boolean", "standard_name": "string", "aliases": ["string"], "confidence": "number", "reason": "string"}]},
        }
        result = self.llm_client.chat_json(json.dumps(payload, ensure_ascii=False), system_prompt=self.SYSTEM_PROMPT)
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise ValueError("LLM 批量结果缺少 results 数组")
        rows = result["results"]
        if len(rows) != len(pairs):
            raise ValueError(f"LLM 批量结果数量不匹配: expected={len(pairs)} actual={len(rows)}")
        by_index: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("index"), int):
                raise ValueError("LLM 批量结果包含无效 index")
            index = row["index"]
            if index < 0 or index >= len(pairs) or index in by_index:
                raise ValueError(f"LLM 批量结果 index 无效或重复: {index}")
            by_index[index] = row
        if set(by_index) != set(range(len(pairs))):
            raise ValueError("LLM 批量结果缺少候选实体对")
        return [self._normalize(by_index[index]) for index in range(len(pairs))]

    @staticmethod
    def _normalize(result: dict[str, Any]) -> dict[str, Any]:
        aliases = result.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = [aliases]
        try:
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "same_entity": bool(result.get("same_entity", False)),
            "standard_name": normalize_text(result.get("standard_name")),
            "aliases": [normalize_text(item) for item in aliases if normalize_text(item)],
            "confidence": confidence,
            "reason": normalize_text(result.get("reason")),
        }


class GraphAblationService:
    """按固定流水线完成实体、属性、关系三类消融。"""

    def __init__(self, llm_resolver: Any | None = None) -> None:
        self.llm_resolver = llm_resolver
        self._llm_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def run(self, graphs: list[dict[str, Any]], strategy: AblationStrategy | None = None) -> dict[str, Any]:
        """执行 ``data/text.txt`` 中定义的完整消融流程。"""

        strategy = strategy or AblationStrategy()
        entity_mapping, csv_entity_mapping = self._load_entity_mapping(strategy)
        normalized_graphs = self._normalize_graphs(graphs, strategy)
        global_entities: list[dict[str, Any]] = []
        source_to_global: dict[str, str] = {}
        warnings: list[dict[str, Any]] = []
        llm_resolutions: list[dict[str, Any]] = []

        # 第一阶段：名称、别名和 data/mapping_table.csv 标准化，然后使用规则和相似度归并实体。
        for graph in normalized_graphs:
            for index, raw_entity in enumerate(graph["entities"]):
                raw_id = normalize_text(raw_entity.get("entity_id")) or f"entity_{index}"
                aliases = _iter_aliases(raw_entity)
                mapped_names = [resolve_mapping(name, entity_mapping) for name in aliases]
                # CSV 中的别名必须直接使用中文标准名；接口 strategy 中的等价规则主要负责候选合并。
                csv_standard_name = next((name for name in mapped_names if normalize_key(name) in set(csv_entity_mapping.values())), "")
                standard_name = csv_standard_name or _pick_standard_name([*aliases, *mapped_names])
                aliases = list(dict.fromkeys([*aliases, *mapped_names]))
                candidate = self._make_candidate(raw_entity, graph, raw_id, aliases, standard_name, bool(csv_standard_name))
                target = self._find_rule_target(global_entities, candidate, strategy)
                if target is not None and normalize_key(target["standard_name"]) != normalize_key(candidate["standard_name"]):
                    conflicts = _identity_conflicts(target, candidate)
                    if conflicts:
                        _add_ambiguous_merge_warning(warnings, target, candidate, conflicts, "保留原实体，未执行合并")
                        target = None
                if target is None:
                    target = self._new_global_entity(candidate)
                    global_entities.append(target)
                self._merge_entity(target, candidate, strategy, warnings)
                source_to_global[f"{graph['source_doc']}:{raw_id}"] = target["entity_id"]

        # 第二阶段：仅对规则和相似度筛选出的候选对调用 LLM，不做全量两两请求。
        if strategy.use_llm_resolution:
            # LLM 合并会删除右侧实体，因此必须把原始实体映射同步改写到左侧实体 ID。
            redirects = self._apply_llm_resolution_optimized(global_entities, strategy, llm_resolutions, warnings)
            source_to_global = {
                key: self._resolve_redirect(value, redirects)
                for key, value in source_to_global.items()
            }

        # 第三阶段：实体已经稳定，属性和关系才能安全地改写到标准实体上。
        attributes = self._build_attributes(global_entities, strategy)
        relations = self._build_relations(normalized_graphs, source_to_global, global_entities, strategy)
        resolutions = self._build_resolutions(global_entities)
        output_ids = self._output_ids(global_entities)
        # global_entities 只对外保留跨多个来源文档发生融合的实体；
        # 内部的 global_entities 仍保留全部实体，供关系端点解析和最终输出使用。
        fused_global_entities = [
            entity for entity in global_entities if len(set(entity.get("source_docs", []))) > 1
        ]

        result = {
            "entities": self._output_entities(global_entities, output_ids),
            "relations": self._output_relations(relations, global_entities, output_ids),
            "resolutions": resolutions,
            "global_entities": sorted(fused_global_entities, key=lambda item: item["entity_id"]),
            # "llm_resolutions": llm_resolutions,
            # "warnings": warnings,
            "statistics": {
                "input_graph_count": len(normalized_graphs),
                "entity_count": len(global_entities),
                "attribute_count": len(attributes),
                "relation_count": len(relations),
                "mapping_count": len(source_to_global),
                "resolution_count": len(resolutions),
                "llm_resolution_count": len(llm_resolutions),
            },
        }
        if strategy.translate_output_to_chinese:
            result = self._translate_output_to_chinese(result)
        return result

    @staticmethod
    def _load_entity_mapping(strategy: AblationStrategy) -> tuple[dict[str, str], dict[str, str]]:
        """合并 CSV 映射和请求中的映射，显式请求映射优先级更高。"""

        csv_mapping = {normalize_key(key): normalize_text(value) for key, value in load_mapping_table(strategy.mapping_table_path).items()}
        mapping = dict(csv_mapping)
        mapping.update(_mapping_from_strategy(strategy.entity_equivalence))
        return mapping, csv_mapping

    @staticmethod
    def _normalize_graphs(graphs: list[dict[str, Any]], strategy: AblationStrategy) -> list[dict[str, Any]]:
        normalized = []
        for index, graph in enumerate(graphs or []):
            item = dict(graph or {})
            item["source_doc"] = normalize_text(item.get("source_doc")) or f"graph_{index}"
            language = normalize_text(item.get("language"))
            item["language"] = strategy.language_mapping.get(language, language)
            item["entities"] = item.get("entities") if isinstance(item.get("entities"), list) else []
            item["relations"] = item.get("relations") if isinstance(item.get("relations"), list) else []
            normalized.append(item)
        return normalized

    @staticmethod
    def _make_candidate(raw: dict[str, Any], graph: dict[str, Any], raw_id: str, aliases: list[str], standard_name: str, mapping_canonical: bool = False) -> dict[str, Any]:
        attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
        return {
            "name": normalize_text(raw.get("name")) or standard_name,
            "standard_name": standard_name,
            "aliases": aliases,
            "type": normalize_text(raw.get("type")),
            "attributes": dict(attributes),
            "description": normalize_text(raw.get("description")),
            "language": graph["language"],
            "source_doc": graph["source_doc"],
            "raw_id": raw_id,
            "mapping_canonical": mapping_canonical,
        }

    @staticmethod
    def _new_global_entity(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": _stable_id("ent", candidate["standard_name"] or candidate["raw_id"]),
            "standard_name": candidate["standard_name"] or candidate["name"],
            "type": candidate["type"] or "未知",
            "aliases": [],
            "languages": [],
            "source_docs": [],
            "source_entities": [],
            "attributes": {},
            "descriptions": [],
        }

    @classmethod
    def _find_rule_target(cls, entities: list[dict[str, Any]], candidate: dict[str, Any], strategy: AblationStrategy) -> dict[str, Any] | None:
        candidate_names = {normalize_key(candidate["standard_name"]), *(normalize_key(item) for item in candidate["aliases"])}
        for entity in entities:
            if not _type_compatible(entity.get("type"), candidate.get("type")):
                continue
            existing_names = {normalize_key(entity.get("standard_name")), *(normalize_key(item) for item in entity.get("aliases", []))}
            # 显式映射、标准名或别名完全相同，属于确定性合并。
            if candidate_names & existing_names:
                return entity
            # 没有精确命中时才使用相似度，降低误合并风险。
            if any(name_similarity(left, right) >= strategy.similarity_threshold for left in candidate_names for right in existing_names):
                return entity
        return None

    @staticmethod
    def _merge_entity(target: dict[str, Any], candidate: dict[str, Any], strategy: AblationStrategy, warnings: list[dict[str, Any]]) -> None:
        """合并实体字段，并保留来源、描述和属性冲突信息。"""

        for alias in [candidate["name"], candidate["standard_name"], *candidate["aliases"]]:
            if alias and alias != target["standard_name"] and alias not in target["aliases"]:
                target["aliases"].append(alias)
        # CSV 映射标准名优先；普通等价规则则保留较完整的名称作为展示标准名。
        if candidate.get("mapping_canonical") and candidate["standard_name"]:
            target["standard_name"] = candidate["standard_name"]
        else:
            target["standard_name"] = _pick_standard_name([target["standard_name"], *target["aliases"]])
        if target["type"] == "未知" and candidate["type"]:
            target["type"] = candidate["type"]
        elif candidate["type"] and not _type_compatible(target["type"], candidate["type"]):
            warnings.append({"code": "ENTITY_TYPE_CONFLICT", "entity": target["standard_name"], "values": [target["type"], candidate["type"]]})
        for field_name, value in (("languages", candidate["language"]), ("source_docs", candidate["source_doc"])):
            if value and value not in target[field_name]:
                target[field_name].append(value)
        source = {"source_doc": candidate["source_doc"], "entity_id": candidate["raw_id"], "name": candidate["name"], "description": candidate["description"]}
        if source not in target["source_entities"]:
            target["source_entities"].append(source)
        if candidate["description"] and candidate["description"] not in target["descriptions"]:
            target["descriptions"].append(candidate["description"])
        attr_mapping = _mapping_from_strategy(strategy.attribute_equivalence)
        for key, value in candidate["attributes"].items():
            attr_key = resolve_mapping(key, attr_mapping)
            attr_value = normalize_text(value)
            if not attr_key or _is_unknown(attr_value):
                continue
            existing_key = next((item for item in target["attributes"] if normalize_key(item) == normalize_key(attr_key) or name_similarity(item, attr_key) >= strategy.attribute_similarity_threshold), attr_key)
            existing_value = target["attributes"].get(existing_key)
            if existing_value and existing_value != attr_value:
                warnings.append({"code": "ATTRIBUTE_CONFLICT", "entity": target["standard_name"], "attribute": existing_key, "values": [existing_value, attr_value]})
                continue
            target["attributes"][existing_key] = attr_value

    def _apply_llm_resolution(self, entities: list[dict[str, Any]], strategy: AblationStrategy, records: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, str]:
        """对类型兼容且名称/语种有交集的候选实体做可选 LLM 确认。"""

        pairs = []
        for left_index, left in enumerate(entities):
            for right in entities[left_index + 1:]:
                if not _type_compatible(left.get("type"), right.get("type")):
                    continue
                if normalize_key(left["standard_name"]) == normalize_key(right["standard_name"]):
                    continue
                conflicts = _identity_conflicts(left, right)
                if conflicts:
                    _add_ambiguous_merge_warning(warnings, left, right, conflicts, "保留原实体，未提交 LLM 合并")
                    continue
                if name_similarity(left["standard_name"], right["standard_name"]) >= 0.5 or set(left.get("languages", [])) != set(right.get("languages", [])):
                    pairs.append((left, right))
        if not pairs:
            return {}
        resolver = self.llm_resolver or LLMEntityResolutionService()
        self.llm_resolver = resolver
        batch_size = max(1, int(strategy.llm_resolution_batch_size))
        decisions = []
        for start in range(0, len(pairs), batch_size):
            batch = pairs[start:start + batch_size]
            try:
                decisions.extend(resolver.resolve_many(batch) if hasattr(resolver, "resolve_many") else [resolver.resolve(left, right) for left, right in batch])
            except Exception as exc:
                warnings.append({"code": "LLM_RESOLUTION_FAILED", "error": str(exc)})
                decisions.extend({"same_entity": False, "confidence": 0.0, "reason": "LLM 调用失败"} for _ in batch)
        redirects: dict[str, str] = {}
        for (left, right), decision in zip(pairs, decisions):
            decision = dict(decision or {})
            records.append({"left": left["standard_name"], "right": right["standard_name"], "cached": False, **decision})
            # pairs 是在实体删除前一次性生成的；前面的合并可能已经移除了当前候选的一侧。
            if left not in entities or right not in entities:
                continue
            if not decision.get("same_entity") or float(decision.get("confidence", 0)) < strategy.llm_confidence_threshold:
                continue
            old_right_id = right["entity_id"]
            resolved_name = normalize_text(decision.get("standard_name")) or left["standard_name"]
            # 将全局实体还原成候选实体结构后复用统一的属性、来源和冲突合并逻辑。
            candidate = {
                "name": right["standard_name"],
                "standard_name": resolved_name,
                "aliases": list(right.get("aliases", [])),
                "type": right.get("type", ""),
                "attributes": dict(right.get("attributes", {})),
                "description": right.get("descriptions", [""])[0] if right.get("descriptions") else "",
                "language": right.get("languages", [""])[0] if right.get("languages") else "",
                "source_doc": right.get("source_docs", [""])[0] if right.get("source_docs") else "",
                "raw_id": right.get("source_entities", [{}])[0].get("entity_id", old_right_id) if right.get("source_entities") else old_right_id,
                "mapping_canonical": False,
            }
            self._merge_entity(left, candidate, strategy, warnings)
            redirects[old_right_id] = left["entity_id"]
            entities.remove(right)
        return redirects

    @staticmethod
    def _candidate_bucket_keys(entity: dict[str, Any]) -> set[tuple[str, str]]:
        """按实体类型和属性上下文生成候选桶键。"""
        type_key = normalize_key(entity.get("type")) or "__unknown__"
        attributes = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
        keys = {(type_key, "__type__")}
        for attribute_name, attribute_value in attributes.items():
            attribute_key = normalize_key(attribute_name)
            value_key = normalize_key(attribute_value)
            if attribute_key:
                keys.add((type_key, f"key:{attribute_key}"))
            if attribute_key and value_key and not _is_unknown(attribute_value):
                keys.add((type_key, f"value:{value_key}"))
        return keys

    def _build_llm_candidate_pairs(
        self,
        entities: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """按类型、来源文档和属性分桶，生成去重后的安全候选实体对。"""
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entity in entities:
            for bucket_key in self._candidate_bucket_keys(entity):
                buckets.setdefault(bucket_key, []).append(entity)

        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for bucket_entities in buckets.values():
            for left_index, left in enumerate(bucket_entities):
                for right in bucket_entities[left_index + 1:]:
                    pair_key = tuple(sorted((left["entity_id"], right["entity_id"])))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    if not _type_compatible(left.get("type"), right.get("type")):
                        continue
                    if set(left.get("source_docs", [])) & set(right.get("source_docs", [])):
                        continue
                    if normalize_key(left["standard_name"]) == normalize_key(right["standard_name"]):
                        continue
                    conflicts = _identity_conflicts(left, right)
                    if conflicts:
                        _add_ambiguous_merge_warning(warnings, left, right, conflicts, "保留原实体，未提交 LLM 合并")
                        continue

                    left_names = [left["standard_name"], *left.get("aliases", [])]
                    right_names = [right["standard_name"], *right.get("aliases", [])]
                    name_score = max(
                        (name_similarity(name_a, name_b) for name_a in left_names for name_b in right_names),
                        default=0.0,
                    )
                    left_attributes = left.get("attributes") if isinstance(left.get("attributes"), dict) else {}
                    right_attributes = right.get("attributes") if isinstance(right.get("attributes"), dict) else {}
                    shared_attribute = any(
                        normalize_key(key_a) == normalize_key(key_b)
                        and normalize_key(value_a) == normalize_key(value_b)
                        for key_a, value_a in left_attributes.items()
                        for key_b, value_b in right_attributes.items()
                    )
                    # 跨文档同类型实体保留给 LLM 做语义判断；语言差异本身不再触发候选。
                    different_documents = not (set(left.get("source_docs", [])) & set(right.get("source_docs", [])))
                    if name_score >= 0.5 or shared_attribute or (different_documents and _type_compatible(left.get("type"), right.get("type"))):
                        pairs.append((left, right))
        return pairs

    def _apply_llm_resolution_optimized(
        self,
        entities: list[dict[str, Any]],
        strategy: AblationStrategy,
        records: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> dict[str, str]:
        """使用候选分桶、缓存、结果校验和并查集合并实体。"""
        pairs = self._build_llm_candidate_pairs(entities, warnings)
        if not pairs:
            return {}

        resolver = self.llm_resolver or LLMEntityResolutionService()
        self.llm_resolver = resolver
        batch_size = max(1, int(strategy.llm_resolution_batch_size))
        decisions: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
        uncached_pairs = []
        for left, right in pairs:
            key = tuple(sorted((left["entity_id"], right["entity_id"])))
            if key in self._llm_cache:
                decisions[key] = (dict(self._llm_cache[key]), True)
            else:
                uncached_pairs.append((left, right))

        batches = [
            uncached_pairs[start:start + batch_size]
            for start in range(0, len(uncached_pairs), batch_size)
        ]

        def resolve_batch(batch: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
            return (
                resolver.resolve_many(batch)
                if hasattr(resolver, "resolve_many")
                else [resolver.resolve(left, right) for left, right in batch]
            )

        batch_results: list[list[dict[str, Any]] | Exception | None] = [None] * len(batches)
        worker_count = max(1, min(int(strategy.llm_resolution_workers), len(batches) or 1))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(resolve_batch, batch): index
                for index, batch in enumerate(batches)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    batch_results[index] = future.result()
                except Exception as exc:
                    batch_results[index] = exc

        # 按原始批次顺序写缓存和警告，保证并发前后的输出顺序一致。
        for batch, batch_result in zip(batches, batch_results):
            try:
                if isinstance(batch_result, Exception):
                    raise batch_result
                batch_decisions = batch_result or []
                if len(batch_decisions) != len(batch):
                    raise ValueError(f"LLM 结果数量不匹配: expected={len(batch)} actual={len(batch_decisions)}")
                for (left, right), decision in zip(batch, batch_decisions):
                    key = tuple(sorted((left["entity_id"], right["entity_id"])))
                    normalized = dict(decision or {})
                    self._llm_cache[key] = normalized
                    decisions[key] = (normalized, False)
            except Exception as exc:
                warnings.append({"code": "LLM_RESOLUTION_FAILED", "error": str(exc), "pair_count": len(batch)})
                for left, right in batch:
                    key = tuple(sorted((left["entity_id"], right["entity_id"])))
                    decisions[key] = ({"same_entity": False, "confidence": 0.0, "reason": "LLM 调用失败"}, False)

        parent = {entity["entity_id"]: entity["entity_id"] for entity in entities}

        def find(entity_id: str) -> str:
            current = entity_id
            while parent[current] != current:
                parent[current] = parent[parent[current]]
                current = parent[current]
            return current

        entity_by_id = {entity["entity_id"]: entity for entity in entities}
        for left, right in pairs:
            key = tuple(sorted((left["entity_id"], right["entity_id"])))
            decision, cached = decisions[key]
            records.append({"left": left["standard_name"], "right": right["standard_name"], "cached": cached, **decision})
            left_root_id = find(left["entity_id"])
            right_root_id = find(right["entity_id"])
            if left_root_id == right_root_id:
                continue
            if not decision.get("same_entity") or float(decision.get("confidence", 0)) < strategy.llm_confidence_threshold:
                continue

            left_root = entity_by_id[left_root_id]
            right_root = entity_by_id[right_root_id]
            conflicts = _identity_conflicts(left_root, right_root)
            if conflicts:
                _add_ambiguous_merge_warning(warnings, left_root, right_root, conflicts, "保留原实体，未执行 LLM 合并")
                continue
            resolved_name = normalize_text(decision.get("standard_name")) or left_root["standard_name"]
            candidate = {
                "name": right_root["standard_name"],
                "standard_name": resolved_name,
                "aliases": list(right_root.get("aliases", [])),
                "type": right_root.get("type", ""),
                "attributes": dict(right_root.get("attributes", {})),
                "description": right_root.get("descriptions", [""])[0] if right_root.get("descriptions") else "",
                "language": right_root.get("languages", [""])[0] if right_root.get("languages") else "",
                "source_doc": right_root.get("source_docs", [""])[0] if right_root.get("source_docs") else "",
                "raw_id": right_root.get("source_entities", [{}])[0].get("entity_id", right_root_id) if right_root.get("source_entities") else right_root_id,
                "mapping_canonical": False,
            }
            self._merge_entity(left_root, candidate, strategy, warnings)
            for field_name in ("languages", "source_docs", "source_entities", "descriptions"):
                for value in right_root.get(field_name, []):
                    if value not in left_root[field_name]:
                        left_root[field_name].append(value)
            parent[right_root_id] = left_root_id

        redirects = {entity_id: find(entity_id) for entity_id in parent if find(entity_id) != entity_id}
        entities[:] = [entity for entity in entities if find(entity["entity_id"]) == entity["entity_id"]]
        return redirects

    @staticmethod
    def _translation_payload(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """只提取最终展示字段，避免为翻译发送来源 ID 等不可翻译内容。"""
        payload: dict[str, list[dict[str, Any]]] = {}
        payload["entities"] = [
            {
                "index": index,
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "attributes": item.get("attributes", {}),
                "description": item.get("description", ""),
            }
            for index, item in enumerate(result.get("entities", []))
        ]
        payload["global_entities"] = [
            {
                "index": index,
                "standard_name": item.get("standard_name", ""),
                "type": item.get("type", ""),
                "aliases": item.get("aliases", []),
                "attributes": item.get("attributes", {}),
                "descriptions": item.get("descriptions", []),
                "source_entities": [
                    {
                        "index": source_index,
                        "name": source.get("name", ""),
                        "description": source.get("description", ""),
                    }
                    for source_index, source in enumerate(item.get("source_entities", []))
                    if isinstance(source, dict)
                ],
            }
            for index, item in enumerate(result.get("global_entities", []))
        ]
        payload["relations"] = [
            {
                "index": index,
                "relationCode": item.get("relationCode", ""),
                "evidence": item.get("evidence", ""),
            }
            for index, item in enumerate(result.get("relations", []))
        ]
        payload["resolutions"] = [
            {
                "index": index,
                "mention": item.get("mention", ""),
                "canonical_entity": item.get("canonical_entity", ""),
                "description": item.get("description", ""),
            }
            for index, item in enumerate(result.get("resolutions", []))
        ]
        return payload

    @staticmethod
    def _contains_foreign_text(value: Any) -> bool:
        if isinstance(value, str):
            for char in value:
                if not char.isalpha():
                    continue
                codepoint = ord(char)
                is_cjk = (
                    0x3400 <= codepoint <= 0x4DBF
                    or 0x4E00 <= codepoint <= 0x9FFF
                    or 0xF900 <= codepoint <= 0xFAFF
                )
                if not is_cjk:
                    return True
            return False
        if isinstance(value, dict):
            return any(GraphAblationService._contains_foreign_text(item) for item in value.values())
        if isinstance(value, list):
            return any(GraphAblationService._contains_foreign_text(item) for item in value)
        return False

    def _translate_output_to_chinese(self, result: dict[str, Any]) -> dict[str, Any]:
        """用一次批量 LLM 请求将最终展示文本统一翻译为中文。

        输出已经没有拉丁字母时直接跳过请求；请求失败或结果校验失败时保留原结果。
        """
        translation_payload = self._translation_payload(result)
        if not self._contains_foreign_text(translation_payload):
            return result

        prompt = {
            "task": "将知识图谱最终展示内容中所有非中文自然语言统一翻译成简体中文。",
            "rules": [
                "保留每条记录的 index，不增删记录。",
                "只翻译 name、type、attributes、description、standard_name、aliases、descriptions、relationCode、evidence、mention、canonical_entity 的文本。",
                "实体名、属性值、关系类型即使是专有名词也翻译成最自然的中文；数字、年份和缩写按语义保留。",
                "只返回 JSON，不要 Markdown、解释或额外字段。",
            ],
            "output_schema": {
                "entities": [{"index": 0, "name": "中文", "type": "中文", "attributes": {}, "description": "中文"}],
                "global_entities": [{"index": 0, "standard_name": "中文", "type": "中文", "aliases": [], "attributes": {}, "descriptions": [], "source_entities": []}],
                "relations": [{"index": 0, "relationCode": "中文", "evidence": "中文"}],
                "resolutions": [{"index": 0, "mention": "中文", "canonical_entity": "中文", "description": "中文"}],
            },
            "data": translation_payload,
        }
        try:
            resolver = LLMEntityResolutionService()
            translated = resolver.llm_client.chat_json(
                json.dumps(prompt, ensure_ascii=False),
                system_prompt="你是严格的批量翻译器，只返回合法 JSON。",
            )
            if not isinstance(translated, dict):
                return result
            for section, source_rows in translation_payload.items():
                target_rows = translated.get(section)
                if not isinstance(target_rows, list) or len(target_rows) != len(source_rows):
                    return result
                by_index = {row.get("index"): row for row in target_rows if isinstance(row, dict)}
                if set(by_index) != set(range(len(source_rows))):
                    return result
                if section == "global_entities":
                    for source_row, target_row in zip(source_rows, target_rows):
                        source_entities = source_row.get("source_entities", [])
                        translated_source_entities = target_row.get("source_entities")
                        if (
                            not isinstance(translated_source_entities, list)
                            or len(translated_source_entities) != len(source_entities)
                            or {item.get("index") for item in translated_source_entities if isinstance(item, dict)}
                            != set(range(len(source_entities)))
                        ):
                            return result

            output = deepcopy(result)
            field_names = {
                "entities": ("name", "type", "attributes", "description"),
                "global_entities": ("standard_name", "type", "aliases", "attributes", "descriptions", "source_entities"),
                "relations": ("relationCode", "evidence"),
                "resolutions": ("mention", "canonical_entity", "description"),
            }
            for section, fields in field_names.items():
                translated_by_index = {
                    row.get("index"): row
                    for row in translated[section]
                    if isinstance(row, dict)
                }
                for index, target in enumerate(output.get(section, [])):
                    row = translated_by_index[index]
                    for field_name in fields:
                        if field_name in row:
                            target[field_name] = row[field_name]
                    if section == "global_entities" and isinstance(row.get("source_entities"), list):
                        source_by_index = {
                            item.get("index"): item
                            for item in row["source_entities"]
                            if isinstance(item, dict)
                        }
                        for source_index, source in enumerate(target.get("source_entities", [])):
                            translated_source = source_by_index.get(source_index)
                            if not translated_source:
                                continue
                            for field_name in ("name", "description"):
                                if field_name in translated_source:
                                    source[field_name] = translated_source[field_name]
            return output
        except Exception:
            return result

    @staticmethod
    def _resolve_redirect(entity_id: str, redirects: dict[str, str]) -> str:
        """解析可能存在多级合并的实体 ID 重定向链。"""

        current = entity_id
        seen: set[str] = set()
        while current in redirects and current not in seen:
            seen.add(current)
            current = redirects[current]
        return current

    def _build_attributes(self, entities: list[dict[str, Any]], strategy: AblationStrategy) -> list[dict[str, Any]]:
        return [{"attribute_id": _stable_id("attr", f'{entity["entity_id"]}|{key}|{value}'), "entity_id": entity["entity_id"], "entity_name": entity["standard_name"], "attribute_key": key, "attribute_value": value, "source_docs": list(entity["source_docs"])} for entity in entities for key, value in entity["attributes"].items() if not _is_unknown(value)]

    def _build_relations(self, graphs: list[dict[str, Any]], source_to_global: dict[str, str], entities: list[dict[str, Any]], strategy: AblationStrategy) -> list[dict[str, Any]]:
        """重写关系端点，再按关系映射、相似度和 evidence 合并关系。"""

        relation_mapping = _mapping_from_strategy(strategy.relation_equivalence)
        entity_by_id = {entity["entity_id"]: entity for entity in entities}
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        for graph in graphs:
            by_name = {normalize_key(item.get("name")): normalize_text(item.get("entity_id")) for item in graph["entities"]}
            for raw in graph["relations"]:
                head_raw = normalize_text(raw.get("head_entity_id") or raw.get("source_id") or by_name.get(normalize_key(raw.get("source"))))
                tail_raw = normalize_text(raw.get("tail_entity_id") or raw.get("target_id") or by_name.get(normalize_key(raw.get("target"))))
                head = source_to_global.get(f'{graph["source_doc"]}:{head_raw}')
                tail = source_to_global.get(f'{graph["source_doc"]}:{tail_raw}')
                relation_type = resolve_mapping(raw.get("relationCode") or raw.get("relation"), relation_mapping)
                if not head or not tail or head == tail or not relation_type:
                    continue
                existing_type = next((item[1] for item in result if item[0] == head and item[2] == tail and (normalize_key(item[1]) == normalize_key(relation_type) or name_similarity(item[1], relation_type) >= strategy.relation_similarity_threshold)), relation_type)
                key = (head, existing_type, tail)
                item = result.setdefault(key, {"relation_id": _stable_id("rel", "|".join(key)), "head_entity_id": head, "head_entity_name": entity_by_id[head]["standard_name"], "relation_type": existing_type, "tail_entity_id": tail, "tail_entity_name": entity_by_id[tail]["standard_name"], "source_docs": [], "evidence": []})
                if graph["source_doc"] not in item["source_docs"]:
                    item["source_docs"].append(graph["source_doc"])
                evidence = normalize_text(raw.get("evidence"))
                if evidence and evidence not in item["evidence"]:
                    item["evidence"].append(evidence)
        return list(result.values())

    @staticmethod
    def _build_resolutions(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolutions = []
        for entity in entities:
            merged = len(entity["source_entities"]) > 1
            for source in entity["source_entities"]:
                mention = normalize_text(source["name"])
                name_changed = mention != entity["standard_name"]
                if not name_changed:
                    continue
                if merged and name_changed:
                    description = "多个来源实体被融合，且原始实体名称已标准化。"
                elif merged:
                    description = "多个来源实体被判断为同一实体并完成融合。"
                elif name_changed:
                    description = "原始实体名称通过映射规则或标准化规则进行了统一。"
                else:
                    description = "实体未发生名称变化，保留原始实体与标准实体的映射关系。"
                resolutions.append({
                    "mention": mention,
                    "canonical_entity": entity["standard_name"],
                    "entity_id": entity["entity_id"],
                    "description": description,
                })
        return resolutions

    @staticmethod
    def _output_ids(entities: list[dict[str, Any]]) -> dict[str, str]:
        result = {}
        for entity in entities:
            sources = entity["source_entities"]
            result[entity["entity_id"]] = entity["entity_id"] if len(sources) > 1 else (sources[0]["entity_id"] if sources else entity["entity_id"])
        return result

    @staticmethod
    def _output_entities(entities: list[dict[str, Any]], output_ids: dict[str, str]) -> list[dict[str, Any]]:
        return [{"name": entity["standard_name"], "type": entity["type"], "attributes": dict(entity["attributes"]), "entity_id": output_ids[entity["entity_id"]], "description": "实体消融：多个来源实体已合并。" if len(entity["source_entities"]) > 1 else (entity["descriptions"][0] if entity["descriptions"] else "")} for entity in entities]

    @staticmethod
    def _output_relations(relations: list[dict[str, Any]], entities: list[dict[str, Any]], output_ids: dict[str, str]) -> list[dict[str, Any]]:
        return [{"head_entity_id": output_ids[item["head_entity_id"]], "relationCode": item["relation_type"], "tail_entity_id": output_ids[item["tail_entity_id"]], "evidence": "；".join(item["evidence"])} for item in relations]


def iter_input_graphs(graphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留旧调用方需要的输入规范化入口。"""

    return GraphAblationService._normalize_graphs(graphs, AblationStrategy())
