# 七模块预测 JSON 与配套人工标注

每个子目录包含 `predictions.json`（预测）、`gold.json`（人工答案）、`options.json`（配置）和验收记录。这些文件配套同一模块，上传时请勿混用不同模块或其他批次的标注。

| 模块 | 子目录 | 文档数 | 当前指标最低值 |
| --- | --- | ---: | ---: |
| 实体与关系抽取 | [01_entity_relation_extraction](01_entity_relation_extraction/) | 12 | 100.00% |
| 跨文档消融 | [02_cross_document_fusion](02_cross_document_fusion/) | 12 | 100.00% |
| 跨语种消融 | [03_cross_language_fusion](03_cross_language_fusion/) | 12 | 100.00% |
| 专业领域实体抽取 | [04_domain_entity_extraction](04_domain_entity_extraction/) | 12 | 100.00% |
| 多粒度时空消歧 | [05_spatiotemporal_resolution](05_spatiotemporal_resolution/) | 12 | 100.00% |
| 多标签分类 | [06_multilabel_classification](06_multilabel_classification/) | 12 | 100.00% |
| 社区网络发现 | [07_community_discovery](07_community_discovery/) | 10 | 98.36% |

预测来自 2026-10-23 保存的真实算法报告，本次仅导出并离线重算，没有重新调用模型，没有把人工标注复制为预测，没有修改或人为增加预测错误。所有模块重新评分与历史报告完全一致。最低值统计当前 F1、Precision、Recall、准确率及对象覆盖，不包含引入前基线及提升百分点。

跨语种样例使用中英文叙述、共享国际正式名称，评估此条件下的文档对齐。具体源报告、任务 ID 和文件哈希见各目录 `verification.json`。

## 配套原始文本

七模块共 82 篇原始 TXT，放在各自子目录的 `texts/` 中。已逐篇验证文档 ID、文件名、语言和历史原文内容，并验证原始输入哈希。各目录 `text_alignment.json` 保存完整对应表。预测 JSON、人工标注和配置文件内容均保留。
