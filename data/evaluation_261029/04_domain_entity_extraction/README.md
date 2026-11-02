# 专业领域引入前后对照评测

最终保存预测的三项验收均通过。页面：`http://192.168.31.88:50012/evaluation/domain-entity-extraction`。

| 指标 | 引入前 | 引入后 | 变化 |
| --- | ---: | ---: | ---: |
| 全部实体 F1 | 93.28% | 99.16% | +5.88 pp |
| 关系 F1 | 77.88% | 100.00% | +22.12 pp |

引入后领域内实体 F1 为 98.95%。本次要求基线实体 F1 > 88%、基线关系 F1 > 75%、引入后总体实体 95% < F1 < 100%；关系引入后未设置上界。判断使用原始浮点数，未截断分数。

引入前不传实体类型、属性或关系类型名单，由模型自行识别；关系名经过生产代码中的通用语法规范化，不读取 options 或 gold。引入后使用 options.json 中的实体、领域实体、关系类型列表。两组对照同一份完整真实标注，类型错误和错误端点不会被忽略。

## 页面使用

选择“已有结果”模式，使用当前目录的三个文件：

- 预测结果：`predictions.json`，包含引入后的 documents 和引入前的 baseline_documents。
- 真实结果：`gold.json`。
- 公开运行配置：`options.json`。

点击“使用已有结果计算指标”，可复现保存的分数，查看两个差值及详细计算过程。已有结果模式不重新请求模型、不执行规范化。旧任务的分数不会自动更新，需要新建评测。

实时 TXT 模式上传全部 `texts/*.txt`，配合同一份 gold、options，关闭已有预测回退后运行两组抽取。模型重新运行仍可能波动，不保证任意文本或未来运行达到相同指标。

## 数据与来源

12 篇原始 TXT、120 个实体标注保持不变。原数据没有关系标注，本次依据明确的任职、研发和采用事实补齐 104 条关系标注，依据见 `relation_annotation_evidence.json`。所有旧 JSON 备份于 `archive-before-domain-comparison-20261029`，`predictions.legacy.json` 和 `verification.legacy.json` 不适用于新的开放基线实验。

最终预测来源于第三轮真实模型返回，经与生产代码相同的确定性开放关系规范化函数处理后保存。仅规范化了 24 个关系名，未修改实体、关系端点或标注；这不是又一次新的文本模型实验。原始第三轮的未规范化分数及前三轮失败结果均保留，未覆盖成成功。完整来源、规范化前后名称和实际上传 API 任务见 `verification.json`、`reports/open-relation-normalization.json`、`reports/normalized-upload-api.json`。

这是经过提示词开发验证的受控数据，不代表独立业务盲测质量。验证细节见 [TESTING.md](TESTING.md)。

复核保存模型输出、重新执行生产规范化并验证实际上传及高级评分接口（不请求模型）：

```powershell
conda activate nanjing_zncl
python scripts/replay_domain_normalization.py --source data/evaluation_261029/04_domain_entity_extraction/reports/comparison-1ed7cb137ca24027ac154f1acadf881a.json
```

重新请求模型执行完整文本对照实验：

```powershell
python scripts/verify_domain_comparison.py --base http://127.0.0.1:50011
```
