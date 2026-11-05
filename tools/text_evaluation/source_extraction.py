"""评测专用抽取调用：复用旧算法，通过现有参数隔离无上下文的外部别名表。"""

import re
from .sampling import SAMPLING
from utils.log_context import register_secret

NAMED_ENTITY_RULES = '''
\n# 命名实体边界
只抽取有具体名称的命名实体。人物须是可识别的姓名或明确别名，不把工作人员、参与者等泛称当成人物。
组织须是有具体名称的机构；不能把动作、方法、材料、观察结果、解释意见、段落标题或普通名词短语当作组织。
英文句首大写不表示机构名称；由冠词、指示词和普通名词组成的叙述片段通常不是命名实体。
没有具体命名实体的背景段落应返回空数组，不要为凑足结果生成实体或关系。
实体名称保留正文原语言和准确拼写；不要擅自翻译名称。
字段名为年份时，单一明确年份用四位 YYYY 字符串；年份范围不能简化成单一年份。
'''


def extract_entities_for_evaluation(*, input_text, model_params, name_mapping_policy='source'):
    from tools import extraction_model as api
    from tools import entity_extraction as extraction

    if name_mapping_policy == 'legacy':
        return api.extract_entities_with_model(input_text=input_text, model_params=model_params,
                                               sampling_params={**SAMPLING, 'max_workers': 1})
    if name_mapping_policy != 'source':
        raise ValueError('未知名称映射策略')

    # 模型地址仍使用原配置；评测固定采样策略，不修改共享配置及普通业务调用。
    config = api.get_section('llm_config')
    base_url, model, api_key = api._resolve_model_config(model_params)
    register_secret(api_key)
    client = api.OpenAI(api_key=api_key, base_url=base_url, http_client=api.httpx.Client(trust_env=False))
    def value(enabled, key, default):
        return api._enabled_value(model_params, enabled, key, config.get(default[0], default[1]))
    try:
        # 原 cleaner 合并显式覆盖表后会删去 name->name 项；利用现有参数关闭外部别名替换。
        # 模型从正文识别的全称/简称 resolutions 仍由原算法正常处理。
        source_names = {name: name for name in extraction.load_mapping_table()}
        graph = extraction.extract_text(
            input_text,
            openai_client=client,
            entity_attribute_schema=model_params.get('entityAttributeSchema', {}),
            entity_type=model_params.get('entityTypes', []),
            relation_types=model_params.get('relationTypes'),
            invalid_relation_evidence_keywords=None, invalid_established_sources=None,
            model=model,
            temperature=SAMPLING['temperature'],
            top_p=SAMPLING['top_p'],
            max_tokens=min(api._as_int(value('maxTokensEnabled', 'maxTokens', ('max_tokens', 8192)), 8192), 8192),
            frequency_penalty=SAMPLING['frequency_penalty'],
            presence_penalty=SAMPLING['presence_penalty'],
            request_extra_body={'chat_template_kwargs': {'enable_thinking': False}, 'seed': SAMPLING['seed']},
            response_format={'type': 'json_object'},
            max_len=api._as_int(model_params.get('blockSize'), 1000),
            max_workers=1,  # Document/chunk parallelism is bounded by the caller.
            system_prompt_override=model_params.get('generatedPrompt', '') + NAMED_ENTITY_RULES,
            entity_name_mapping=source_names,
        )
        # 原合并器可能为模型消歧补充兜底类型，再检查最终实体与端点的一致性。
        allowed_types = set(model_params.get('entityTypes', []))
        allowed_relations = set(model_params.get('relationTypes') or [])
        graph['entities'] = [e for e in graph['entities'] if not allowed_types or e['type'] in allowed_types]
        for entity in graph['entities']:
            for key, raw in entity.get('attributes', {}).items():
                if key.endswith('年份') or key.casefold() in ('year', 'founding_year', 'founded_year'):
                    match = re.fullmatch(r'\s*(\d{4})\s*年?\s*', str(raw))
                    if match:
                        entity['attributes'][key] = match.group(1)
        ids = {e['entity_id'] for e in graph['entities']}
        graph['relations'] = [r for r in graph['relations'] if r['head_entity_id'] in ids and r['tail_entity_id'] in ids and (not allowed_relations or r['relationCode'] in allowed_relations)]
        graph['resolutions'] = [r for r in graph['resolutions'] if r.get('entity_id') in ids]
        return graph
    finally:
        client.close()
