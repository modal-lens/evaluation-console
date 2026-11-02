# 评测接口

本模块接收**算法预测结果和人工标注**，计算指标，不执行文本抽取、图谱合并、分类或社区发现，不访问数据库，不修改已有业务功能。专业领域接口评价调用方已经引入类型列表后的预测，不会自动修改提示词或调用模型。原文自动运行、上传文件和任务查询由 /api/v1/evaluation-runs/* 提供，见 automatic_evaluation.md。

统一前缀 `/api/v1/evaluation`，以下接口均为 POST，使用 JSON 请求。
服务沿用 `conf/config.yml` 的 `server.port`（当前 50111），不启用额外端口。
Swagger：`http://<服务地址>:50111/docs`，标签 `evaluation`。

| 路径后缀 | 指标 |
| --- | --- |
| `/entity-extraction` | 实体抽取 F1 |
| `/relation-extraction` | 关系抽取 F1 |
| `/cross-document/entity-fusion` | 跨文档实体消融 F1 |
| `/cross-document/relation-fusion` | 跨文档关系消融 F1 |
| `/cross-document/attribute-fusion` | 跨文档属性消融 F1 |
| `/cross-language/entity-fusion` | 跨语种实体消融 F1 |
| `/cross-language/relation-fusion` | 跨语种关系消融 F1 |
| `/cross-language/attribute-fusion` | 跨语种属性消融 F1 |
| `/domain-entity-extraction` | 引入专业领域实体类型列表后的实体抽取 F1 |
| `/spatiotemporal-resolution` | 多粒度时空消歧准确率 |
| `/multilabel-classification` | 多标签分类 F1 |
| `/community-discovery` | 社区网络发现准确率 |

## 统一口径

- 分数范围 0～1；F1 接口返回 TP、FP、FN、precision、recall、f1、support。support 为标注正例数。
- `precision = TP/(TP+FP)`，`recall = TP/(TP+FN)`，`F1 = 2TP/(2TP+FP+FN)`。分母为零时返回 0，双空集合不会记为满分。
- 抽取和分类按样本隔离后做集合匹配，相同样本中的重复输出只计算一次。样本 ID 不可重复。实体使用名称与类型精确匹配，区分大小写，不进行语义模糊匹配或别名归一化，输入字符串去除首尾空白。
- 实体抽取为名称级指标，不是字符跨度级 NER 指标；同一文档重复提及相同名称、类型仅计一次。
- `samples`、`items` 不可为空。漏预测必须显式提交空列表或 null，不能省略该标注样本。请求中的未定义字段会返回 422，避免字段拼错被忽略。
- 评测数据覆盖范围由调用方保证；评测接口无法发现未提交的标注。

## 实体抽取

```json
{"samples":[{"sample_id":"doc-1","predicted":[{"name":"南京大学","type":"组织"}],"gold":[{"name":"南京大学","type":"组织"}]}]}
```

返回示例：

```json
{"success":true,"data":{"averaging":"micro","matching":"exact_set","tp":1,"fp":0,"fn":0,"precision":1.0,"recall":1.0,"f1":1.0,"support":1},"error":null}
```

## 关系抽取

```json
{"samples":[{"sample_id":"doc-1","predicted":[{"head_entity_id":"e1","relationCode":"任职","tail_entity_id":"e2"}],"gold":[{"head_entity_id":"e1","relationCode":"任职","tail_entity_id":"e2"}]}]}
```

按有向 `(head_entity_id, relationCode, tail_entity_id)` 三元组精确匹配。调用方须先将预测实体 ID 与标注实体 ID 对齐；不能直接比较两次运行随机生成的 ID。模型的关系描述和 evidence 不参与评分，提交时只保留示例字段。

## 跨文档 / 跨语种实体、关系、属性消融

此处“消融”指重复对象的合并/对齐，使用 **pairwise cluster F1**，不表示移除算法模块的消融实验，也不表示融合后图谱内容抽取 F1。六个接口采用相同结构，item 分别表示实体、关系或属性的一次出现：

```json
{"items":[{"item_id":"d1-e1","document_id":"d1","language":"zh","gold_cluster":"gold-1","predicted_cluster":"pred-9"},{"item_id":"d2-e7","document_id":"d2","language":"en","gold_cluster":"gold-1","predicted_cluster":"pred-9"}]}
```

`gold_cluster` 相同表示人工认为应合并，`predicted_cluster` 相同表示算法进行了合并。两个簇编号空间无需一致。漏预测或未合并可使用 `predicted_cluster: null`，两个 null 不视为同簇。应提交所有待评价对象，包括不应合并的对象，以衡量错误合并。

跨文档接口仅统计 document_id 不同的对象对；跨语种接口仅统计 language 不同的对象对。语言代码请预先统一，例如 zh/en。没有符合场景的对象对返回 400；有对象对但没有正例时，F1 按零分母规则为 0。每次最多 1000 个对象，按所有符合条件的无序对象对计算，时间复杂度 O(n²)。拆分评测集时不能丢失跨分组的待评价对象对。

## 专业领域实体抽取

```json
{"domain_entity_types":["装备"],"samples":[{"sample_id":"d1","predicted":[{"name":"雷达","type":"装备"}],"gold":[{"name":"雷达","type":"装备"}]}],"baseline_samples":[{"sample_id":"d1","predicted":[],"gold":[{"name":"雷达","type":"装备"}]}]}
```

返回 `overall`（全部类型 F1）和 `domain_only`（类型列表内的 F1）。可选 `baseline_samples` 提供引入类型列表前的预测，返回 baseline 与总体 f1_delta。基线的样本 ID 和标注必须与引入后的评测完全一致。类型列表只是指定领域统计范围，列表本身不能证明模型实际使用了它。

## 多粒度时空消歧

```json
{"items":[{"sample_id":"d1","mention_id":"t1","dimension":"time","granularity":"day","gold":"2026-10-21","predicted":"2026-10-21"},{"sample_id":"d1","mention_id":"s1","dimension":"space","granularity":"city","gold":"南京","predicted":null}]}
```

返回总体 accuracy、correct、total 与按 dimension/granularity 分组的准确率。预测与标注均应为对应粒度下的标准化值；接口精确比较，不自动解析日期、经纬度或父子行政区。一个提及可以在多个粒度分别标注，null 按错误计。此例总体准确率为 0.5。

## 多标签分类

```json
{"samples":[{"sample_id":"d1","predicted":["1","2"],"gold":["1"]}]}
```

标签采用统一的字符串 ID。返回 micro 指标、macro_f1、per_label 和 subset_accuracy（整组标签完全正确的样本比例）。macro_f1 按预测与标注的标签并集计算；空标签集 macro_f1 为 0。每个样本重复标签会去重。

## 社区发现

```json
{"items":[{"node_id":"e1","gold_community":"A","predicted_community":"2"},{"node_id":"e2","gold_community":"B","predicted_community":"1"}]}
```

每个节点只能属于一个社区。先使用最优一一映射对齐预测和标注社区标签，再计算匹配节点数 / 总标注节点数，避免社区编号不同导致误判。漏分配节点传 null 并计错。支持预测与标注社区数量不同，最多 5000 个节点、两侧社区标签并集最多 200 个。不能仅上传成功分配的节点；孤立节点也应包含在标注集内。

## 验证

```bash
python -m pytest tests/test_evaluation_api.py -q
```
