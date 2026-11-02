"""基于 Leiden 的社区网络发现算法。

本模块只放算法实现，不依赖 FastAPI，也不依赖 demo 脚本。调用方传入边列表后，
``discover_communities`` 会直接返回前端图谱可使用的最终结构：

{
  "communities": [...],
  "nodes": [...],
  "edges": [...]
}

其中社区包含主题、节点、节点 ID、内部密度和封闭度；节点包含唯一 ID、名称、
所属社区、坐标和是否桥节点；边包含起终点、起终点 ID、关系、权重和跨社区标记。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

import igraph as ig
import leidenalg as la

from conf.load_config import get_section
from tools.llm_client import call_llm
from utils.logger import get_logger


logger = get_logger(__name__)


class CommunityEdge(Protocol):
    """社区发现所需的边结构。

    必需字段：
    - source: 起点节点名称；
    - target: 终点节点名称。

    可选字段：
    - source_id: 起点实体唯一 ID；
    - target_id: 终点实体唯一 ID；
    - relation: 关系名称；
    - weight: 边权重，未提供时按 1.0 处理。
    """

    source: str
    target: str


@dataclass(frozen=True)
class NormalizedEdge:
    """清洗后的内部边结构。"""

    source: str
    target: str
    source_id: str
    target_id: str
    relation: str
    weight: float


# 规则版实体主题归纳使用的通用停用词。
# 这些词通常表示组织形态或泛化对象，本身不适合作为社区主题。
ENTITY_TOPIC_STOP_WORDS = {
    "有限公司",
    "公司",
    "集团",
    "科技",
    "智能",
    "平台",
    "系统",
    "研究院",
    "实验室",
    "中心",
    "项目",
    "产品",
    "机构",
    "企业",
}


# 常见领域词优先从实体名称中识别。长词放前面，避免“大模型”先截断
# “工业大模型”这类更具体的主题表达。
DOMAIN_KEYWORDS = [
    "工业大模型",
    "新能源",
    "储能电站",
    "设备巡检",
    "故障预测",
    "人工智能",
    "大模型",
    "算法",
    "能源",
    "储能",
    "电站",
    "巡检",
    "预测",
    "工业",
    "数据",
    "计算",
]

COMMUNITY_TOPIC_SYSTEM_PROMPT = (
    "你是一个图谱社区主题归纳助手。只返回合法 JSON 对象，不要输出解释。"
)


def _get_edge_value(edge: CommunityEdge, field_name: str, default: Any = None) -> Any:
    """兼容 Pydantic 对象、dataclass 对象和普通 dict。"""

    if isinstance(edge, dict):
        return edge.get(field_name, default)
    return getattr(edge, field_name, default)


def _normalize_edges(
    edges: Iterable[CommunityEdge],
    entity_id_by_name: dict[str, str] | None = None,
) -> tuple[list[NormalizedEdge], dict[str, str]]:
    """清洗输入边，并建立“实体名称 -> 唯一 ID”的映射。"""

    id_mapping = dict(entity_id_by_name or {})
    normalized_edges: list[NormalizedEdge] = []

    for edge in edges:
        source = str(_get_edge_value(edge, "source", "")).strip()
        target = str(_get_edge_value(edge, "target", "")).strip()
        if not source or not target or source == target:
            continue

        source_id = str(_get_edge_value(edge, "source_id", "") or id_mapping.get(source) or source).strip()
        target_id = str(_get_edge_value(edge, "target_id", "") or id_mapping.get(target) or target).strip()
        id_mapping.setdefault(source, source_id)
        id_mapping.setdefault(target, target_id)

        normalized_edges.append(
            NormalizedEdge(
                source=source,
                target=target,
                source_id=source_id,
                target_id=target_id,
                relation=str(_get_edge_value(edge, "relation", "")),
                weight=float(_get_edge_value(edge, "weight", 1.0)),
            )
        )

    if not normalized_edges:
        raise ValueError("edges 至少需要包含一条有效边")
    return normalized_edges, id_mapping


def _build_igraph(edges: list[NormalizedEdge], directed: bool) -> tuple[ig.Graph, list[str]]:
    """把清洗后的业务边转换为 igraph 图对象。"""

    vertices = sorted({edge.source for edge in edges} | {edge.target for edge in edges})
    vertex_to_index = {vertex: index for index, vertex in enumerate(vertices)}

    graph = ig.Graph(directed=directed)
    graph.add_vertices(len(vertices))
    graph.vs["name"] = vertices
    graph.add_edges([(vertex_to_index[edge.source], vertex_to_index[edge.target]) for edge in edges])
    graph.es["weight"] = [edge.weight for edge in edges]
    return graph, vertices


def _run_leiden(graph: ig.Graph, seed: int):
    """执行 Leiden 社区发现。"""

    community_graph = graph if not graph.is_directed() else graph.as_undirected(combine_edges=sum)
    return la.find_partition(
        community_graph,
        la.ModularityVertexPartition,
        weights="weight",
        seed=seed,
    )


def _build_community_index(
    partition,
    vertices: list[str],
    entity_id_by_name: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """把 Leiden 返回的顶点索引转换成社区列表和节点到社区的映射。"""

    communities: list[dict[str, Any]] = []
    for community_index, community in enumerate(partition):
        nodes = sorted(vertices[vertex_id] for vertex_id in community)
        communities.append(
            {
                "community_id": community_index,
                "nodes": nodes,
                "node_ids": [entity_id_by_name[node] for node in nodes],
                "node_count": len(nodes),
            }
        )

    communities.sort(key=lambda item: (-item["node_count"], item["community_id"]))
    node_to_community: dict[str, int] = {}
    for new_id, community in enumerate(communities):
        community["community_id"] = new_id
        for node in community["nodes"]:
            node_to_community[node] = new_id
    return communities, node_to_community


def _unique_pair(source: str, target: str, directed: bool) -> tuple[str, str]:
    """返回用于统计密度的边唯一键。"""

    if directed:
        return source, target
    return tuple(sorted((source, target)))


def _add_community_metrics(
    communities: list[dict[str, Any]],
    edges: list[NormalizedEdge],
    directed: bool,
) -> list[dict[str, Any]]:
    """给每个社区补充内部边数、内部密度和封闭度。"""

    for community in communities:
        node_set = set(community["nodes"])
        internal_pairs = {
            _unique_pair(edge.source, edge.target, directed)
            for edge in edges
            if edge.source in node_set and edge.target in node_set
        }
        external_pairs = {
            _unique_pair(edge.source, edge.target, directed)
            for edge in edges
            if (edge.source in node_set) != (edge.target in node_set)
        }
        max_internal_edges = len(node_set) * (len(node_set) - 1)
        if not directed:
            max_internal_edges /= 2

        community["edge_count"] = sum(
            1
            for edge in edges
            if edge.source in node_set and edge.target in node_set
        )
        community["internal_density"] = round(
            len(internal_pairs) / max_internal_edges if max_internal_edges else 0.0,
            10,
        )
        community["closure"] = round(
            len(internal_pairs) / (len(internal_pairs) + len(external_pairs))
            if internal_pairs or external_pairs
            else 0.0,
            10,
        )
    return communities


def _normalize_topic(relation: str) -> str:
    """把关系名整理成更适合作为社区主题的短文本。"""

    relation = relation.strip()
    if relation.startswith("同属") and len(relation) > 2:
        return relation[2:]
    return relation or "未命名主题"


def _extract_entity_topic_keywords(entity_name: str) -> list[str]:
    """从实体名称中抽取适合作为社区主题的关键词。

    这里是规则版主题归纳，不依赖大模型：
    1. 优先识别 DOMAIN_KEYWORDS 中的领域词；
    2. 再从实体名称中提取连续中文/英文/数字片段；
    3. 过滤“有限公司、平台、系统”等泛词；
    4. 保留较短、较稳定的关键词，避免整段实体名直接变成主题。
    """

    keywords: list[str] = []
    for keyword in DOMAIN_KEYWORDS:
        if keyword in entity_name and keyword not in keywords and not any(keyword in existing for existing in keywords):
            keywords.append(keyword)

    for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", entity_name):
        normalized = token.strip()
        for stop_word in sorted(ENTITY_TOPIC_STOP_WORDS, key=len, reverse=True):
            normalized = normalized.replace(stop_word, "")
        if len(normalized) < 2:
            continue
        if normalized in ENTITY_TOPIC_STOP_WORDS:
            continue
        if normalized not in keywords:
            keywords.append(normalized)
    return keywords


def _build_entity_topic(nodes: list[str], entity_type_by_name: dict[str, str] | None = None) -> str:
    """根据社区内实体点归纳主题。

    方案 1：统计实体名称中的领域关键词；
    方案 2：统计实体类型分布，作为主题辅助信息。

    返回空字符串表示实体点不足以生成稳定主题，需要回退到关系主题。
    """

    entity_type_by_name = entity_type_by_name or {}
    keyword_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()

    for node in nodes:
        for keyword in _extract_entity_topic_keywords(node):
            # 领域词比公司简称残片更适合作为社区主题，因此给更高权重。
            keyword_counter[keyword] += 3 if keyword in DOMAIN_KEYWORDS else 1
        entity_type = str(entity_type_by_name.get(node, "")).strip()
        if entity_type:
            type_counter[entity_type] += 1

    keyword_priority = {keyword: index for index, keyword in enumerate(DOMAIN_KEYWORDS)}
    top_keywords = [
        keyword
        for keyword, _ in sorted(
            keyword_counter.items(),
            key=lambda item: (-item[1], keyword_priority.get(item[0], len(DOMAIN_KEYWORDS)), item[0]),
        )[:3]
        if keyword not in ENTITY_TOPIC_STOP_WORDS
    ]
    dominant_type = type_counter.most_common(1)[0][0] if type_counter else ""

    if top_keywords:
        topic = "".join(top_keywords[:2])
        if dominant_type and dominant_type not in topic and len(topic) <= 8:
            topic = f"{topic}{dominant_type}"
        return topic[:12]

    if dominant_type:
        return f"{dominant_type}社区"

    return ""


def _build_relation_topic(nodes: list[str], edges: list[NormalizedEdge]) -> str:
    """根据社区内部边的高频关系生成备用主题。"""

    node_set = set(nodes)
    topic_counter = Counter(
        _normalize_topic(edge.relation)
        for edge in edges
        if edge.source in node_set and edge.target in node_set
    )
    return topic_counter.most_common(1)[0][0] if topic_counter else "未命名主题"


def _sanitize_llm_topic(value: Any) -> str:
    """清洗大模型返回的社区主题，失败时返回空字符串。"""

    topic = str(value or "").strip()
    topic = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", topic).strip()
    topic = re.sub(r"\s+", "", topic)
    topic = re.sub(r"[，。！？；：、,.!?;:]+$", "", topic)
    if len(topic) < 2:
        return ""
    if topic in {"未命名", "未命名主题", "主题"}:
        return ""
    return topic[:12]


def _community_edges_for_prompt(nodes: list[str], edges: list[NormalizedEdge]) -> list[dict[str, Any]]:
    """取社区内部边作为大模型主题归纳输入。"""

    node_set = set(nodes)
    return [
        {
            "source": edge.source,
            "target": edge.target,
            "relation": edge.relation,
            "weight": edge.weight,
        }
        for edge in edges
        if edge.source in node_set and edge.target in node_set
    ]


def _build_llm_topic_prompt(
    community: dict[str, Any],
    edges: list[NormalizedEdge],
    entity_type_by_name: dict[str, str] | None = None,
) -> str:
    """构造社区主题归纳 prompt。"""

    entity_type_by_name = entity_type_by_name or {}
    entities = [
        {
            "name": node,
            "type": str(entity_type_by_name.get(node, "")).strip(),
        }
        for node in community["nodes"]
    ]
    payload = {
        "community_id": community["community_id"],
        "entities": entities,
        "relations": _community_edges_for_prompt(community["nodes"], edges),
    }
    return (
        "请根据下面一个知识图谱社区中的实体和关系，归纳一个简短、自然、可展示的中文主题名。\n"
        "要求：\n"
        "1. 主题名优先表达共同领域、业务场景或核心事件，不要简单罗列实体名。\n"
        "2. 长度控制在 2 到 12 个中文字符，避免标点和引号。\n"
        "3. 不要使用“社区”“主题”“相关”等空泛词作为主要内容。\n"
        "4. 只返回 JSON 对象，格式为 {\"topic\":\"主题名\"}。\n\n"
        f"社区数据：{json.dumps(payload, ensure_ascii=False)}"
    )


def _build_llm_topic(
    community: dict[str, Any],
    edges: list[NormalizedEdge],
    entity_type_by_name: dict[str, str] | None = None,
    llm: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """调用大模型生成社区主题。返回空字符串表示需要规则回退。"""

    prompt = _build_llm_topic_prompt(community, edges, entity_type_by_name)
    if llm is not None:
        result = llm(prompt, system_prompt=COMMUNITY_TOPIC_SYSTEM_PROMPT)
    else:
        result = call_llm(
            prompt,
            system_prompt=COMMUNITY_TOPIC_SYSTEM_PROMPT,
            config=get_section("llm_config"),
        )
    return _sanitize_llm_topic(result.get("topic"))


def _add_community_topics(
    communities: list[dict[str, Any]],
    edges: list[NormalizedEdge],
    entity_type_by_name: dict[str, str] | None = None,
    *,
    use_llm_topic: bool = False,
    llm: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """给每个社区生成主题。

    当前实现优先使用规则版实体主题：
    - 根据社区内实体名称抽关键词；
    - 根据社区内实体类型分布补充主题语义；
    - 如果无法从实体点归纳主题，再回退到社区内部关系高频词。

    use_llm_topic 为 True 时优先调用大模型生成主题；模型不可用或返回无效主题时，
    回退到规则版主题，保证社区发现接口仍可返回结果。
    """

    for community in communities:
        if use_llm_topic:
            try:
                llm_topic = _build_llm_topic(community, edges, entity_type_by_name, llm)
            except Exception:
                llm_topic = ""
            if llm_topic:
                community["topic"] = llm_topic
                logger.info(
                    "[CommunityDiscovery] 生成社区主题 community_id=%s source=llm topic=%s node_count=%s",
                    community.get("community_id"),
                    community["topic"],
                    community.get("node_count"),
                )
                continue

        entity_topic = _build_entity_topic(community["nodes"], entity_type_by_name)
        topic_source = "rule_entity" if entity_topic else "rule_relation"
        community["topic"] = entity_topic or _build_relation_topic(community["nodes"], edges)
        logger.info(
            "[CommunityDiscovery] 生成社区主题 community_id=%s source=%s topic=%s node_count=%s",
            community.get("community_id"),
            topic_source,
            community["topic"],
            community.get("node_count"),
        )
    return communities


def _calculate_coordinates(graph: ig.Graph, vertices: list[str]) -> dict[str, dict[str, float]]:
    """使用 Fruchterman-Reingold 力导向布局生成节点坐标。"""

    layout_graph = graph if not graph.is_directed() else graph.as_undirected(combine_edges=sum)
    layout = layout_graph.layout_fruchterman_reingold(weights="weight", niter=500)
    return {
        vertex: {
            "x": round(float(layout[index][0]), 6),
            "y": round(float(layout[index][1]), 6),
        }
        for index, vertex in enumerate(vertices)
    }


def _find_bridge_node_names(edges: list[NormalizedEdge], node_to_community: dict[str, int]) -> set[str]:
    """找出连接不同社区的桥节点名称。"""

    bridge_node_names: set[str] = set()
    for edge in edges:
        if node_to_community[edge.source] == node_to_community[edge.target]:
            continue
        bridge_node_names.add(edge.source)
        bridge_node_names.add(edge.target)
    return bridge_node_names


def _build_nodes(
    vertices: list[str],
    entity_id_by_name: dict[str, str],
    node_to_community: dict[str, int],
    coordinates: dict[str, dict[str, float]],
    bridge_node_names: set[str],
) -> list[dict[str, Any]]:
    """输出图谱节点，包含唯一 ID、名称、社区 ID、坐标和桥节点标记。"""

    return [
        {
            "id": entity_id_by_name[node],
            "name": node,
            "community_id": node_to_community[node],
            "x": coordinates[node]["x"],
            "y": coordinates[node]["y"],
            "is_bridge": node in bridge_node_names,
        }
        for node in vertices
    ]


def _build_edges(edges: list[NormalizedEdge], node_to_community: dict[str, int]) -> list[dict[str, Any]]:
    """输出图谱边，保留关系、权重和是否跨社区。"""

    return [
        {
            "source": edge.source,
            "target": edge.target,
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "relation": edge.relation,
            "weight": edge.weight,
            "source_community_id": node_to_community[edge.source],
            "target_community_id": node_to_community[edge.target],
            "is_cross_community": node_to_community[edge.source] != node_to_community[edge.target],
        }
        for edge in edges
    ]


def discover_communities(
    edges: Iterable[CommunityEdge],
    *,
    directed: bool = False,
    seed: int = 42,
    entity_id_by_name: dict[str, str] | None = None,
    entity_type_by_name: dict[str, str] | None = None,
    use_llm_topic: bool = False,
    llm: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """使用 Leiden 算法执行社区网络发现。

    返回格式：
    {
      "communities": [
        {
          "community_id": 0,
          "topic": "人工智能领域",
          "nodes": ["OpenAI", "ChatGPT"],
          "node_ids": ["ent_000047", "ent_000010"],
          "node_count": 2,
          "edge_count": 1,
          "internal_density": 1.0,
          "closure": 1.0
        }
      ],
      "nodes": [
        {
          "id": "ent_000047",
          "name": "OpenAI",
          "community_id": 0,
          "x": 0.0,
          "y": 0.0,
          "is_bridge": false
        }
      ],
      "edges": [
        {
          "source": "OpenAI",
          "target": "ChatGPT",
          "source_id": "ent_000047",
          "target_id": "ent_000010",
          "relation": "同属人工智能领域",
          "weight": 1.0,
          "source_community_id": 0,
          "target_community_id": 0,
          "is_cross_community": false
        }
      ]
    }
    """

    normalized_edges, id_mapping = _normalize_edges(edges, entity_id_by_name)
    graph, vertices = _build_igraph(normalized_edges, directed)
    partition = _run_leiden(graph, seed)
    communities, node_to_community = _build_community_index(partition, vertices, id_mapping)
    communities = _add_community_metrics(communities, normalized_edges, directed)
    communities = _add_community_topics(
        communities,
        normalized_edges,
        entity_type_by_name,
        use_llm_topic=use_llm_topic,
        llm=llm,
    )
    coordinates = _calculate_coordinates(graph, vertices)
    bridge_node_names = _find_bridge_node_names(normalized_edges, node_to_community)

    return {
        "communities": communities,
        "nodes": _build_nodes(vertices, id_mapping, node_to_community, coordinates, bridge_node_names),
        "edges": _build_edges(normalized_edges, node_to_community),
    }
