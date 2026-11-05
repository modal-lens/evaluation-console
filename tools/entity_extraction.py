import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import httpx
import secrets
import traceback
import logging
from openai import OpenAI
# from phoenix.otel import register
# from openinference.instrumentation.openai import OpenAIInstrumentor
from tools.text_splitter import split_text_by_length
import json
from tools.prompts import system_prompt,user_prompt
from tools.entity_mapping import load_mapping_table
# 这个 demo 演示“实体 + 关系”抽取的完整流程：
# 1. 定义允许抽取的实体类型、实体属性 schema 和关系类型；
# 2. 构造严格的 system prompt，要求模型输出实体、实体属性、关系和消歧结果；
# 3. 调用 OpenAI 兼容接口获取 JSON；
# 4. 对模型结果做二次清洗，兜底过滤非法属性、非法关系和明显误映射；
# 5. 长文本按段抽取后再合并，降低单次输出过长导致 JSON 截断的风险。

# 注册 tracer
# OpenTelemetry/Phoenix 链路追踪配置：记录每次 OpenAI 兼容接口请求，
# 便于在脚本外查看模型调用明细和耗时。
# tracer_provider = register(
#     project_name="vllm-entity-extraction",
#     endpoint="http://192.168.31.98:60013/v1/traces"
# )
# #
# # 👇 关键：传 tracer_provider
# OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)

# 系统提示词：
# - 明确抽取实体、实体属性、实体间关系和消歧结果；
# - 属性必须来自 ENTITY_ATTRIBUTE_SCHEMA，且属性不存在时不输出；
# - 明确需要做指代消歧、时间消歧和空间消歧；
# - 明确 relation 必须从 RELATION_TYPES 中选择；
# - 对容易误判的“建立于”关系增加方向约束和负例。


# 用户提示词模板。
# 实际调用时用 YOUR_TEXT_HERE 替换占位符，传入当前待抽取文本或分段文本。


# demo 使用的示例文本。
# main() 会对这段文本执行抽取；如果想测试其它文本，建议通过 main(text=...) 传入。

# Qwen3 模型可通过 extra_body 关闭 thinking。
# 当前保留配置但没有启用；如果服务端需要，可以在请求里取消注释 extra_body=extra_body。
extra_body = {"chat_template_kwargs": {"enable_thinking": False}}


def clean_extraction_result(
    result,
    entity_attribute_schema,
    entity_type,
    relation_types,
    invalid_relation_evidence_keywords,
    invalid_established_sources,
    entity_name_mapping=None,
):
    """清洗模型输出，保证最终结果只包含合法实体、合法属性、合法关系和消歧映射。

    清洗策略保持保守：
    - 只保留 ENTITY_type 中定义过的实体类型；
    - 只保留 ENTITY_ATTRIBUTE_SCHEMA 中该实体类型允许的属性；
    - 删除空值属性，属性不存在时不输出对应键；
    - 对完全相同的实体名称去重，但不合并简称、别名或指代；
    - relation_types 有值时只保留 RELATION_TYPES 中定义过的关系；
    - relation_types 为 None 或空时不限制关系类型，按模型从原文抽取的关系保留；
    - 只保留 source/target 都存在于 entities 的关系；
    - 过滤由常见负面证据触发的明显错误关系。
    - 保留非空 mention -> canonical_entity 消歧映射，并确保 canonical_entity 同步到实体列表。
    """
    valid_entity_types = set(entity_type or [])
    restrict_entity_types = bool(valid_entity_types)
    valid_relations = set(relation_types or [])
    restrict_relations = bool(valid_relations)
    invalid_relation_evidence_keywords = invalid_relation_evidence_keywords or {}
    invalid_established_sources = invalid_established_sources or set()

    def normalize_text_value(value):
        """清理模型偶发输出的包装符号，保留原文实体名本身。"""
        if not isinstance(value, str):
            return value
        return value.strip().strip("/").strip()

    merged_entity_name_mapping = load_mapping_table().copy()
    merged_entity_name_mapping.update(entity_name_mapping or {})
    entity_name_mapping = {
        normalize_text_value(source): normalize_text_value(target)
        for source, target in merged_entity_name_mapping.items()
        if normalize_text_value(source)
        and normalize_text_value(target)
        and normalize_text_value(source) != normalize_text_value(target)
    }

    def map_entity_name(name):
        name = normalize_text_value(name)
        return entity_name_mapping.get(name, name)

    def resolve_entity_name(name):
        """把关系端点尽量对齐到 entities 中已有实体名。"""
        name = normalize_text_value(name)
        if not name:
            return name
        if name in entity_names:
            return name

        # 处理模型输出“170舰/兰州号驱逐舰”而实体中是“170舰”或“兰州号”的情况。
        candidates = [
            entity_name
            for entity_name in entity_names
            if entity_name in name or name in entity_name
        ]
        if candidates:
            return max(candidates, key=len)
        return name

    def is_reverse_full_name_resolution(mention, canonical_entity):
        """识别“全称 -> 简称”的反向消歧。"""
        return (
            isinstance(mention, str)
            and isinstance(canonical_entity, str)
            and len(mention) > len(canonical_entity)
            and canonical_entity in mention
        )

    def promote_entity_name(short_name, full_name):
        """把已抽取的简称实体提升为全称实体，并修正常见反向简称属性。"""
        if not short_name or not full_name or short_name == full_name:
            return

        short_entity = next((entity for entity in entities if entity["name"] == short_name), None)
        full_entity = next((entity for entity in entities if entity["name"] == full_name), None)

        if short_entity and not full_entity:
            short_entity["name"] = full_name
            entity_names.discard(short_name)
            entity_names.add(full_name)
            full_entity = short_entity

        if not full_entity:
            return

        attributes = full_entity.get("attributes") or {}
        if not isinstance(attributes, dict):
            return
        for attribute_name, attribute_value in list(attributes.items()):
            if "简称" in str(attribute_name) and attribute_value == full_name:
                attributes[attribute_name] = short_name

    entities = []
    entity_names = set()
    resolutions = []
    resolution_keys = set()
    mention_to_canonical = {}

    def add_resolution(mention, canonical_entity):
        mention = normalize_text_value(mention)
        canonical_entity = normalize_text_value(canonical_entity)
        if not mention or not canonical_entity or mention == canonical_entity:
            return
        key = (mention, canonical_entity)
        if key in resolution_keys:
            return
        resolutions.append({"mention": mention, "canonical_entity": canonical_entity})
        resolution_keys.add(key)
        mention_to_canonical[mention] = canonical_entity

    for entity in result.get("entities", []):
        if not isinstance(entity, dict):
            continue
        raw_name = normalize_text_value(entity.get("name"))
        name = map_entity_name(raw_name)
        entity_type = normalize_text_value(entity.get("type"))
        if raw_name and name != raw_name:
            add_resolution(raw_name, name)

        # 跳过缺少名称、类型非法的实体，避免下游关系引用脏数据。
        if not name or (restrict_entity_types and entity_type not in valid_entity_types):
            continue

        # 只做“完全同名”去重；例如“中国人民武装警察部队”和“武警部队”不合并。
        if name in entity_names:
            continue

        # 主动重建实体对象，避免模型输出未定义字段进入最终结果。
        # attributes 只保留当前实体类型 schema 中允许的非空属性。
        valid_attribute_names = set(entity_attribute_schema.get(entity_type, {}))
        restrict_attributes = bool(valid_attribute_names)
        raw_attributes = entity.get("attributes") or {}
        cleaned_attributes = {}
        if isinstance(raw_attributes, dict):
            for attribute_name, attribute_value in raw_attributes.items():
                if restrict_attributes and attribute_name not in valid_attribute_names:
                    continue
                if attribute_value in ("", None, "未知", "无", "不详"):
                    continue
                if entity_type == "时间" and attribute_name == "名称" and attribute_value != name:
                    continue
                cleaned_attributes[attribute_name] = attribute_value

        cleaned_entity = {
            "name": name,
            "type": entity_type,
            "attributes": cleaned_attributes,
            "description": entity.get("description", ""),
        }
        entities.append(cleaned_entity)
        entity_names.add(name)

    for resolution in result.get("resolutions", []):
        if not isinstance(resolution, dict):
            continue
        raw_mention = normalize_text_value(resolution.get("mention"))
        raw_canonical_entity = normalize_text_value(resolution.get("canonical_entity"))
        mention = map_entity_name(raw_mention)
        canonical_entity = map_entity_name(raw_canonical_entity)
        if raw_mention and mention != raw_mention:
            add_resolution(raw_mention, mention)
        if raw_canonical_entity and canonical_entity != raw_canonical_entity:
            add_resolution(raw_canonical_entity, canonical_entity)
        if not mention or not canonical_entity or mention == canonical_entity:
            continue

        if is_reverse_full_name_resolution(mention, canonical_entity):
            full_name = mention
            short_name = canonical_entity
            promote_entity_name(short_name, full_name)
            mention = short_name
            canonical_entity = full_name

        add_resolution(mention, canonical_entity)

        # 消歧结果中的 canonical_entity 必须能在 entities 中找到。
        # 如果模型漏掉了标准实体，则根据 mention 实体的类型补一个最小实体；
        # 如果 mention 本身也不是实体，则用“其他军事实体”兜底，避免关系指向孤立节点。
        if canonical_entity not in entity_names:
            mention_entity = next((entity for entity in entities if entity["name"] == mention), None)
            entities.append(
                {
                    "name": canonical_entity,
                    "type": mention_entity["type"] if mention_entity else "其他军事实体",
                    "attributes": mention_entity["attributes"] if mention_entity else {},
                    "description": mention_entity["description"] if mention_entity else "由消歧结果补充的标准实体",
                }
            )
            entity_names.add(canonical_entity)

    relationships = []
    for relationship in result.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        source = map_entity_name(relationship.get("source"))
        relation = normalize_text_value(relationship.get("relation"))
        target = map_entity_name(relationship.get("target"))
        source = resolve_entity_name(mention_to_canonical.get(source, source))
        target = resolve_entity_name(mention_to_canonical.get(target, target))

        # relation_types 未配置时允许模型按文本语义自由抽取关系；配置后才做白名单过滤。
        if restrict_relations and relation not in valid_relations:
            continue

        # source/target 必须来自当前实体列表，避免出现孤立关系节点。
        if source not in entity_names or target not in entity_names:
            continue

        evidence = relationship.get("evidence", "")
        invalid_keywords = invalid_relation_evidence_keywords.get(relation, [])

        # 过滤“撤销/裁撤/升级”等被硬套成“建立于/提出”的关系。
        if any(keyword in evidence for keyword in invalid_keywords):
            continue

        # 过滤“中共中央 建立于 日期”这类把决策主体误当成被建立实体的关系。
        if relation == "建立于" and any(invalid_source in source for invalid_source in invalid_established_sources):
            continue

        relationships.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "evidence": evidence,
            }
        )

    return {"entities": entities, "relationships": relationships, "resolutions": resolutions}


def generate_entity_id(entity_name):
    """使用实体名称和随机数生成哈希 ID。

    每次运行都会生成新的随机 salt，因此同一实体名在不同运行中的 ID 不固定；
    同一次结果中会通过 entity_name_to_id 复用该 ID，确保关系和消歧引用一致。
    """
    random_salt = secrets.token_hex(8)
    raw_id = f"{entity_name}:{random_salt}"
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]


def add_entity_ids(result):
    """给最终结果手动添加实体 ID，并把关系、消歧改为 ID 引用。

    这里不依赖大模型生成 ID，而是使用“实体名 + 随机数”本地生成哈希 ID：
    - entities 增加 entity_id；
    - relationships 转换为 relations；
    - relationCode 保留原 relation 文本；
    - source/target 名称转换为 head_entity_id/tail_entity_id；
    - resolutions 中 canonical_entity 对应的 entity_id 会同步写入。
    """
    entity_name_to_id = {}
    entities_with_ids = []

    for entity in result.get("entities", []):
        entity_type = entity["type"]
        entity_id = generate_entity_id(entity["name"])

        entity_with_id = {
            "name": entity["name"],
            "type": entity_type,
            "attributes": entity.get("attributes", {}),
            "entity_id": entity_id,
            "description": entity.get("description", ""),
        }
        entities_with_ids.append(entity_with_id)
        entity_name_to_id[entity["name"]] = entity_id

    relations = []
    for relationship in result.get("relationships", []):
        head_entity_id = entity_name_to_id.get(relationship.get("source"))
        tail_entity_id = entity_name_to_id.get(relationship.get("target"))
        if not head_entity_id or not tail_entity_id:
            continue
        relations.append(
            {
                "head_entity_id": head_entity_id,
                "relationCode": relationship.get("relation"),
                "tail_entity_id": tail_entity_id,
                "evidence": relationship.get("evidence", ""),
            }
        )

    resolutions = []
    for resolution in result.get("resolutions", []):
        canonical_entity = resolution.get("canonical_entity")
        entity_id = entity_name_to_id.get(canonical_entity)
        if not entity_id:
            continue
        resolutions.append(
            {
                "mention": resolution.get("mention"),
                "canonical_entity": canonical_entity,
                "entity_id": entity_id,
            }
        )

    return {"entities": entities_with_ids, "relations": relations, "resolutions": resolutions}


def build_system_prompt(entity_attribute_schema, entity_type, relation_types):
    """把实体类型、属性 schema 和关系类型注入 system prompt。

    使用 json.dumps(..., ensure_ascii=False) 可以保持中文可读，
    同时让提示词中的列表格式更稳定，减少模型理解偏差。
    """
    relation_rule = (
        json.dumps(relation_types, ensure_ascii=False)
        if relation_types
        else "不指定关系类型；请根据原文中的明确语义生成简短、准确的中文关系名称"
    )
    return system_prompt.replace(
        "{{ENTITY_TYPE}}", json.dumps(entity_type, ensure_ascii=False)
    ).replace("{{ENTITY_ATTRIBUTE_SCHEMA}}", json.dumps(entity_attribute_schema, ensure_ascii=False)
    ).replace("{{RELATION_TYPES}}", relation_rule)


def parse_response_content(content):
    """解析模型返回的 JSON 字符串，并在失败时给出原始输出预览。

    长文本抽取时模型可能输出不完整 JSON；这里抛出带预览的异常，
    便于快速定位是截断、格式错误，还是模型返回了非 JSON 内容。
    """
    text = (content or "").strip()
    candidates = [text]
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidates.append("\n".join(lines[1:-1]).strip())
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(text[object_start:object_end + 1])

    first_error = None
    for candidate in dict.fromkeys(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
            continue
        if isinstance(parsed, dict):
            return parsed

    recovered = recover_partial_extraction_json(text)
    if any(recovered.values()):
        return recovered
    preview = text[:500] + "..." if len(text) > 500 else text
    raise ValueError(f"模型返回的 JSON 无法解析: {first_error}; 原始输出预览: {preview}") from first_error


def recover_partial_extraction_json(content):
    """从残缺 JSON 中尽量恢复已完整生成的抽取结果。

    模型在长输出时可能把顶层 JSON 截断在某个数组或对象中间。
    这里不尝试猜测缺失内容，只扫描 entities / relationships / resolutions
    数组中已经完整闭合的对象，避免一个分段的尾部截断导致全部结果丢失。
    """
    return {
        "entities": recover_array_objects(content, "entities"),
        "relationships": recover_array_objects(content, "relationships"),
        "resolutions": recover_array_objects(content, "resolutions"),
    }


def recover_array_objects(content, field_name):
    """恢复指定数组字段中已经完整闭合的 JSON 对象。"""
    marker = f'"{field_name}"'
    marker_index = content.find(marker)
    if marker_index == -1:
        return []

    array_start = content.find("[", marker_index)
    if array_start == -1:
        return []

    objects = []
    depth = 0
    object_start = None
    in_string = False
    escape = False
    for index in range(array_start + 1, len(content)):
        char = content[index]
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and object_start is not None:
                raw_object = content[object_start:index + 1]
                try:
                    objects.append(json.loads(raw_object))
                except json.JSONDecodeError:
                    pass
                object_start = None
        elif char == "]" and depth == 0:
            break

    return objects


def extract_chunk(
    text,
    openai_client,
    entity_attribute_schema,
    entity_type,
    relation_types,
    invalid_relation_evidence_keywords,
    invalid_established_sources,
    model,
    temperature,
    top_p,
    max_tokens,
    frequency_penalty,
    presence_penalty,
    request_extra_body,
    response_format,
    system_prompt_override=None,
    entity_name_mapping=None,
):
    """抽取单个文本分段中的实体、属性、关系和消歧结果。

    这里不直接返回模型原始输出，而是立刻调用 clean_extraction_result，
    让每个分段结果在合并前就满足统一结构。
    """
    request_kwargs = {}
    if request_extra_body is not None:
        request_kwargs["extra_body"] = request_extra_body

    system_content = system_prompt_override or build_system_prompt(entity_attribute_schema, entity_type, relation_types)

    response = openai_client.chat.completions.create(
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,              # 属性会增加输出长度，适当放大上限以降低 JSON 截断风险
                frequency_penalty=frequency_penalty,        # 避免对同一名称实体抑制
                presence_penalty=presence_penalty,
                messages=[
                    {"role": "system", "content": system_content},
                    # 分段文本也使用同一份 user_prompt，保证单段和全文测试的行为一致。
                    {"role": "user", "content": user_prompt.replace("{{YOUR_TEXT_HERE}}",json.dumps(text,ensure_ascii=False))},
                ],
                response_format=response_format,
                **request_kwargs,
            )
    return clean_extraction_result(
        parse_response_content(response.choices[0].message.content),
        entity_attribute_schema,
        entity_type,
        relation_types,
        invalid_relation_evidence_keywords,
        invalid_established_sources,
        entity_name_mapping,
    )


def merge_results(results):
    """合并多个分段的抽取结果。

    合并时先做完全同名实体去重，再根据 resolutions 做简称/别名融合；
    关系按 source、relation、target、evidence 四元组去重；
    消歧按 mention、canonical_entity 二元组去重。
    """
    merged_entities = []
    entity_names = set()
    for result in results:
        for entity in result["entities"]:
            if entity["name"] in entity_names:
                continue
            merged_entities.append(entity)
            entity_names.add(entity["name"])

    merged_relationships = []
    relationship_keys = set()
    for result in results:
        for relationship in result["relationships"]:
            # 分段抽取后合并实体，如果关系两端实体不在最终实体集合中，跳过。
            if relationship["source"] not in entity_names or relationship["target"] not in entity_names:
                continue
            key = (relationship["source"], relationship["relation"], relationship["target"], relationship["evidence"])
            if key in relationship_keys:
                continue
            merged_relationships.append(relationship)
            relationship_keys.add(key)

    merged_resolutions = []
    resolution_keys = set()
    for result in results:
        for resolution in result.get("resolutions", []):
            canonical_entity = resolution["canonical_entity"]
            if canonical_entity not in entity_names:
                merged_entities.append(
                    {
                        "name": canonical_entity,
                        "type": "其他军事实体",
                        "attributes": {},
                        "description": "由消歧结果补充的标准实体",
                    }
                )
                entity_names.add(canonical_entity)
            key = (resolution["mention"], canonical_entity)
            if key in resolution_keys:
                continue
            merged_resolutions.append(resolution)
            resolution_keys.add(key)

    return merge_resolved_entities(merged_entities, merged_relationships, merged_resolutions)


def merge_resolved_entities(entities, relationships, resolutions):
    """根据消歧结果合并重复实体，并同步改写关系端点。

    模型有时会同时输出全称实体和简称实体，例如：
    - entities: 中科宏润（北京）科技有限公司
    - entities: 中科宏润
    - resolutions: 中科宏润 -> 中科宏润（北京）科技有限公司

    这种情况下最终 entities 中只应保留规范实体，简称保留在 resolutions 中；
    原来指向简称实体的关系端点要统一改为规范实体。
    """

    entity_by_name = {entity["name"]: entity for entity in entities}
    resolution_candidates_by_mention = {}
    for resolution in resolutions:
        mention = resolution.get("mention")
        canonical_entity = resolution.get("canonical_entity")
        if not mention or not canonical_entity or mention == canonical_entity:
            continue
        if canonical_entity not in entity_by_name:
            continue
        resolution_candidates_by_mention.setdefault(mention, []).append(canonical_entity)

    def canonical_score(entity_name):
        """规范实体择优分数：名称更完整、属性更多、描述更长时优先。"""

        entity = entity_by_name.get(entity_name, {})
        attributes = entity.get("attributes") or {}
        attribute_count = len(attributes) if isinstance(attributes, dict) else 0
        description_length = len(str(entity.get("description") or ""))
        return (len(entity_name), attribute_count, description_length)

    def choose_canonical_entity(mention, canonical_candidates):
        """为同一个 mention 选择唯一规范实体；冲突明显时返回 None。

        规则保持保守：
        - 同名候选去重；
        - 候选只有一个时直接使用；
        - 候选实体类型不一致，且 mention 很短，通常说明上下文歧义较强，跳过；
        - 候选类型一致时，选择更完整、更长、属性更多的实体名。
        """

        candidates = list(dict.fromkeys(canonical_candidates))
        if len(candidates) == 1:
            return candidates[0]

        candidate_types = {
            entity_by_name[candidate].get("type")
            for candidate in candidates
            if candidate in entity_by_name
        }
        if len(candidate_types) > 1 and len(str(mention)) <= 2:
            return None

        if len(candidate_types) > 1:
            # 类型不同但 mention 不短时，仍然选择名称最完整的候选，避免“马斯克”一类简称残留。
            return max(candidates, key=canonical_score)

        return max(candidates, key=canonical_score)

    alias_to_canonical = {}
    for mention, canonical_candidates in resolution_candidates_by_mention.items():
        canonical_entity = choose_canonical_entity(mention, canonical_candidates)
        if canonical_entity:
            alias_to_canonical[mention] = canonical_entity

    if not alias_to_canonical:
        return {"entities": entities, "relationships": relationships, "resolutions": resolutions}

    def resolve_name(name):
        """递归解析别名，避免 A -> B、B -> C 这种链式消歧只解析一层。"""

        seen = set()
        current = name
        while current in alias_to_canonical and current not in seen:
            seen.add(current)
            current = alias_to_canonical[current]
        return current

    for alias, canonical in alias_to_canonical.items():
        alias_entity = entity_by_name.get(alias)
        canonical_entity = entity_by_name.get(canonical)
        if not alias_entity or not canonical_entity:
            continue

        # 如果简称实体携带了规范实体没有的属性，补到规范实体上。
        # 已有属性不覆盖，避免简称段落里的粗略属性覆盖全称段落里的更准确信息。
        canonical_attributes = canonical_entity.setdefault("attributes", {})
        alias_attributes = alias_entity.get("attributes") or {}
        if isinstance(canonical_attributes, dict) and isinstance(alias_attributes, dict):
            for attribute_name, attribute_value in alias_attributes.items():
                if attribute_name not in canonical_attributes and attribute_value not in ("", None, "未知", "无", "不详"):
                    canonical_attributes[attribute_name] = attribute_value

        if not canonical_entity.get("description") and alias_entity.get("description"):
            canonical_entity["description"] = alias_entity["description"]

    merged_entities = [
        entity
        for entity in entities
        if resolve_name(entity["name"]) == entity["name"]
    ]
    merged_entity_names = {entity["name"] for entity in merged_entities}

    merged_relationships = []
    relationship_keys = set()
    for relationship in relationships:
        source = resolve_name(relationship.get("source"))
        target = resolve_name(relationship.get("target"))
        if source not in merged_entity_names or target not in merged_entity_names:
            continue
        if source == target:
            continue

        merged_relationship = {
            "source": source,
            "relation": relationship.get("relation"),
            "target": target,
            "evidence": relationship.get("evidence", ""),
        }
        key = (
            merged_relationship["source"],
            merged_relationship["relation"],
            merged_relationship["target"],
            merged_relationship["evidence"],
        )
        if key in relationship_keys:
            continue
        merged_relationships.append(merged_relationship)
        relationship_keys.add(key)

    merged_resolutions = []
    resolution_keys = set()
    for mention, raw_canonical_entity in alias_to_canonical.items():
        canonical_entity = resolve_name(raw_canonical_entity)
        if not mention or not canonical_entity or mention == canonical_entity:
            continue
        if canonical_entity not in merged_entity_names:
            continue
        key = (mention, canonical_entity)
        if key in resolution_keys:
            continue
        merged_resolutions.append({"mention": mention, "canonical_entity": canonical_entity})
        resolution_keys.add(key)

    return {"entities": merged_entities, "relationships": merged_relationships, "resolutions": merged_resolutions}


def extract_text(
    text,
    openai_client,
    entity_attribute_schema,
    entity_type,
    relation_types,
    invalid_relation_evidence_keywords,
    invalid_established_sources,
    model="Qwen3-32B",
    temperature=0.2,
    top_p=0.9,
    max_tokens=8192,
    frequency_penalty=0.0,
    presence_penalty=0.0,
    request_extra_body=None,
    response_format=None,
    max_len=1000,
    max_workers=8,
    system_prompt_override=None,
    entity_name_mapping=None,
):
    """抽取完整文本。

    max_len 控制每段最大字符数。分段越短，单次返回越不容易截断；
    分段越长，跨句关系保留得越完整。当前 600 是带属性和消歧输出后的折中值。
    max_workers 控制并发调用模型的线程数。并发可以缩短多分段文本的总耗时；
    最终合并前会按原始分段顺序还原结果，避免输出顺序随线程完成顺序变化。
    """
    chunks = split_text_by_length(text, max_len=max_len)
    chunks = chunks[:4]
    if response_format is None:
        response_format = {"type": "json_object"}
    if not chunks:
        return {"entities": [], "relations": [], "resolutions": []}
    if len(chunks) == 1:
        return add_entity_ids(merge_results([
            extract_chunk(
                chunks[0],
                openai_client,
                entity_attribute_schema,
                entity_type,
                relation_types,
                invalid_relation_evidence_keywords,
                invalid_established_sources,
                model,
                temperature,
                top_p,
                max_tokens,
                frequency_penalty,
                presence_penalty,
                request_extra_body,
                response_format,
                system_prompt_override,
                entity_name_mapping,
            )
        ]))
    worker_count = max(1, min(max_workers, len(chunks)))
    ordered_results = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(
                extract_chunk,
                chunk,
                openai_client,
                entity_attribute_schema,
                entity_type,
                relation_types,
                invalid_relation_evidence_keywords,
                invalid_established_sources,
                model,
                temperature,
                top_p,
                max_tokens,
                frequency_penalty,
                presence_penalty,
                request_extra_body,
                response_format,
                system_prompt_override,
                entity_name_mapping,
            ): index
            for index, chunk in enumerate(chunks)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                ordered_results[index] = future.result()
            except Exception as exc:
                logging.getLogger(__name__).exception('文本分段抽取失败，沿用原逻辑跳过该段：分块序号=%d，异常类型=%s', index + 1, type(exc).__name__)
                ordered_results[index] = {"entities": [], "relationships": [], "resolutions": []}

    return add_entity_ids(merge_results(ordered_results))
