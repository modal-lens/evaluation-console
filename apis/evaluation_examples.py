"""Swagger 请求示例，每个接口均可直接执行，不依赖外部服务。"""


def example(summary: str, description: str, value: dict) -> dict:
    return {"default": {"summary": summary, "description": description, "value": value}}


ENTITY = {"name": "南京大学", "type": "组织"}
RELATION = {"head_entity_id": "person-1", "relationCode": "任职", "tail_entity_id": "org-1"}
DOMAIN_ENTITY = {"name": "相控阵雷达", "type": "装备"}

REQUEST_EXAMPLES = {
    "entity-extraction": example(
        "实体名称与类型精确匹配",
        "predicted 为算法预测，gold 为人工标注；本例漏抽一个实体，F1 为 2/3。",
        {"samples": [{"sample_id": "doc-1", "predicted": [ENTITY], "gold": [ENTITY, {"name": "张三", "type": "人物"}]}]},
    ),
    "relation-extraction": example(
        "有向关系三元组匹配",
        "预测与标注须使用同一实体 ID；本例三元组完全一致，F1 为 1。",
        {"samples": [{"sample_id": "doc-1", "predicted": [RELATION], "gold": [RELATION]}]},
    ),
    "domain-entity-extraction": example(
        "专业领域抽取与引入前基线对比",
        "两组使用相同实体与关系真实标注，分别计算 F1 差值。本例引入后实体满分，因此严格小于1的验收不通过；不人为降低分数。",
        {
            "domain_entity_types": ["装备"],
            "samples": [{"sample_id": "military-doc-1", "predicted": [ENTITY, DOMAIN_ENTITY], "gold": [ENTITY, DOMAIN_ENTITY]}],
            "baseline_samples": [{"sample_id": "military-doc-1", "predicted": [], "gold": [ENTITY, DOMAIN_ENTITY]}],
            "relation_samples": [{"sample_id": "military-doc-1", "predicted": [{"head_entity_id": "org-1", "relationCode": "研发", "tail_entity_id": "equipment-1"}], "gold": [{"head_entity_id": "org-1", "relationCode": "研发", "tail_entity_id": "equipment-1"}]}],
            "baseline_relation_samples": [{"sample_id": "military-doc-1", "predicted": [], "gold": [{"head_entity_id": "org-1", "relationCode": "研发", "tail_entity_id": "equipment-1"}]}],
        },
    ),
    "spatiotemporal-resolution": example(
        "按年、日、城市粒度评测",
        "time 表示时间、space 表示空间；值须提前标准化。null 表示漏预测，本例总体准确率为 2/3。",
        {"items": [
            {"sample_id": "doc-1", "mention_id": "time-1", "dimension": "time", "granularity": "year", "gold": "2026", "predicted": "2026"},
            {"sample_id": "doc-1", "mention_id": "time-1", "dimension": "time", "granularity": "day", "gold": "2026-10-21", "predicted": "2026-10-21"},
            {"sample_id": "doc-1", "mention_id": "place-1", "dimension": "space", "granularity": "city", "gold": "南京", "predicted": None},
        ]},
    ),
    "multilabel-classification": example(
        "多样本、多标签评测",
        "标签使用统一字符串 ID；本例 micro F1 为 0.8，标签整组准确率为 0.5。",
        {"samples": [
            {"sample_id": "doc-1", "predicted": ["1", "2"], "gold": ["1", "2"]},
            {"sample_id": "doc-2", "predicted": [], "gold": ["3"]},
        ]},
    ),
    "community-discovery": example(
        "社区编号对齐与漏分配节点",
        "预测与标注社区编号可不同；最优一一映射后计分，null 按错误计，本例准确率为 0.75。",
        {"items": [
            {"node_id": "entity-1", "gold_community": "科技", "predicted_community": "cluster-2"},
            {"node_id": "entity-2", "gold_community": "科技", "predicted_community": "cluster-2"},
            {"node_id": "entity-3", "gold_community": "金融", "predicted_community": "cluster-1"},
            {"node_id": "entity-4", "gold_community": "金融", "predicted_community": None},
        ]},
    ),
}


for scope, scope_name in (("cross-document", "跨文档"), ("cross-language", "跨语种")):
    for kind, kind_name in (("entity", "实体"), ("relation", "关系"), ("attribute", "属性")):
        # 同语种跨文档与不同语种跨文档的示例分别演示两个评测范围。
        REQUEST_EXAMPLES[f"{scope}/{kind}-fusion"] = example(
            f"{scope_name}{kind_name}合并评测",
            "item_id 是对象的一次出现 ID；gold_cluster 表示人工合并分组，predicted_cluster 表示算法分组。"
            "本例正确合并前两个对象，第三个不合并，F1 为 1。",
            {"items": [
                {"item_id": f"doc-1-{kind}-1", "document_id": "doc-1", "language": "zh", "gold_cluster": "gold-1", "predicted_cluster": "prediction-A"},
                {"item_id": f"doc-2-{kind}-1", "document_id": "doc-2", "language": "zh" if scope == "cross-document" else "en", "gold_cluster": "gold-1", "predicted_cluster": "prediction-A"},
                {"item_id": f"doc-3-{kind}-1", "document_id": "doc-3", "language": "zh" if scope == "cross-document" else "fr", "gold_cluster": "gold-2", "predicted_cluster": "prediction-B"},
            ]},
        )
