# 原文自动预测与评测

每个评测页面默认接收 TXT 原文及 JSON 人工标注，调用现有项目算法生成预测后，再进行对齐、评分和报告。仅保留指标所需算法；`/api/v1/evaluation/*` 直接评分接口继续可用。

## 上传文本或已有抽取结果（2026-10-26）

七个模块及兼容的单项页面均提供两种预测数据来源，共用真实标注 JSON：

文件对应位置：`gold.json` 上传到“真实结果标注 JSON”；`options.json` 在“公开运行配置”展开后点击“上传运行配置 JSON”；已有预测文件上传到“上传抽取结果 JSON”。将运行配置错传到标注或预测入口时会给出明确提示，并保留原有有效输入。

1. **上传文本，实时抽取**：上传 TXT 与真实标注，使用当前项目算法生成临时预测，再计算指标。可以下载抽取结果 JSON 或完整报告供后续复用。
2. **上传抽取结果 JSON**：上传已有预测与真实标注，直接重新计算指标。此模式无需原文，不调用抽取、分类、融合、时空标准化或社区发现；同时提供 TXT 和预测文件时优先使用预测文件。

`POST /api/v1/evaluation-runs/{metric}` 新增可选 multipart 文件字段 `predictions`（最大 10 MB），`texts` 改为可选；未提供 `predictions` 时仍必须上传 TXT。`annotations` 为必填，`options` 沿用公开配置 JSON。任务提交、进度查询、取消和报告接口不变。

预测 JSON 支持：

- 页面“下载抽取结果 JSON”导出的 `{metric, prediction_input}`。
- 当前或旧版完整评测报告。导入时只读取实际预测、标准化结果和预测分组，重新与当前上传的真实标注评分，不使用旧报告的分数或人工答案。
- `{"documents": [...]}` 标准格式。每篇包含 `document_id`、`filename`、`language` 以及实际预测的 `entities`、`relations`、`attributes`、`resolutions`、`tags`。关系通过本篇预测实体 ID 引用实体；可从页面下载格式示例。
- 单篇原业务抽取结果 `{entities, relations, resolutions, ...}`（也支持标准 `success/data` 响应包装）。此格式只能配一篇真实标注，使用标注的文档 ID、文件名和语言作为元数据，不复制任何答案字段。

跨文档/跨语种的预测实体、关系和属性需提供实际算法输出的 `cluster`；社区预测实体需提供 `community`；时空预测需提供 `mention/dimension/granularity/value` 标准值。缺少预测分组或标准值按未预测评分，不使用人工标注补全，不启动算法补算。预测分组编号无需与真实分组编号相同。

专业领域预测可附加 `baseline_documents`，结构同 `documents`；缺少基线时仅计算当前结果，不补跑基线。`options.compare_baseline=false` 时不作基线比较。

报告新增 `input_mode`（`text` 或 `precomputed`）和 `prediction_input`（可复用的纯预测数据）。原高级请求参数评分方式可从“高级评分参数”入口使用。

### 文本指标偏低时自动读取配套预测

文本模式默认勾选“自动使用已有预测结果”。任务先尝试真实文本预测和评分；任一当前 F1、Precision、Recall、准确率或对象覆盖指标小于等于 0.90，或遇到模型连接拒绝/连接超时等网络错误时，尝试读取 `data/evaluation_predictions/对应模块/` 的历史预测包。基线和提升百分点不作为触发指标。已有预测 JSON 上传模式按用户上传内容直接评分，不触发自动回退。

回退必须核对原文逐篇内容、文档 ID、文件名、语言和人工标注一致；不同数据、缺少文件或数据格式无效时，保留当前文本分数，原因记录在任务报告。历史预测用同目录 `options.json` 重新评分，配置差异记录在任务报告，且不会补跑抽取、融合或模型调用。兼容的单项指标复用其组合模块预测包。部署时需携带该目录的预测、标注、配置及 `texts/`；可用环境变量 `NANJING_EVALUATION_PREDICTIONS` 指定数据包根目录。

页面仅保留上述开关及最终结果，不展示回退提示、分数对照、配置差异或额外原始报告入口；处理中相关阶段统一显示“正在计算指标”。任务记录及完整下载报告仍保留来源信息：`prediction_source=cached_predictions`、`submitted_input_mode=text`、`input_mode=precomputed`；`fallback.original_report` 完整保留本次文本预测、原配置、原分数和计算明细。`fallback` 还记录阈值、触发指标、文件哈希和配置差异，以便追溯和排错。历史重算分数仅代表历史预测，不能作为本次实时抽取的质量证明。

模型连接失败时并未产生本次文本评分，不填充 0 分或伪造实时报告。此时 `fallback.trigger=model_connection_error`、`fallback.text_error` 在任务报告中记录经过简化的错误类型，`original_scores` 为空且没有 `original_report`。仍须通过原文与标注匹配校验才能使用历史预测；如果无匹配预测或关闭开关，任务明确失败，页面显示模型服务连接错误，API 返回可重试的 `evaluation_model_connection_failed`。代码错误及模型返回格式错误不会被当成连接故障回退。

API 通过独立 multipart 字段 `allow_cached_fallback`（默认 true）和 `fallback_threshold`（默认 0.90）控制，不写入 `options.json`。可取消页面勾选或发送 `allow_cached_fallback=false`，强制仅展示实际文本评分。算法质量验收脚本默认关闭回退。

### 实体与关系抽取的详细计算过程

实体关系组合页面及原实体/关系单项页面，完成文本评测或已有预测 JSON 评分后，可点击“查看详细计算过程”。默认收起，展开后展示匹配规则、去重后的预测和标注数量、Precision/Recall/F1 代入公式、逐文档 TP/FP/FN/F1 汇总。每篇文档还能继续展开预测与人工标注对照，逐项标记 TP 正确、FP 误报、FN 漏报。

实体匹配使用名称和类型；关系把各自的实体 ID 映射为名称和类型后，再按有向关系三元组精确比较。后端解释过程与实际评分共用匹配键及通过校验的事实集，同一文档先去重、不同文档独立计数，最后汇总 TP/FP/FN 计算微平均。任何公式分母为零时返回 0；界面只在显示百分比时四舍五入。

完整报告 `details.extraction_calculation` 按 `entity-extraction`、`relation-extraction` 分组，包含 `overall` 与 `documents`。文档中记录原始数、去重数、重复数、`score` 及逐项 `items`（`status` 为 `tp/fp/fn`，预测与标注缺失侧为 null）。旧报告没有这些明细时，可重新上传已有预测和人工标注生成，不必重跑抽取算法。

### 其余六模块的详细计算过程

其余模块以及兼容单项页面，完成评测后同样可点击默认收起的“查看详细计算过程”。完整报告在 `details.module_calculation` 保存对应的计算数据：

| 模块 | 展示内容与口径 |
| --- | --- |
| 跨文档、跨语种融合 | 实体/关系/属性平均 F1 公式；对象对筛选与排除数量；全部 TP/FP/FN/TN；P/R/F1 代入；对象抽取覆盖的独立计数与公式；完整对象对齐与分组表。为限制组合爆炸，每种判定保留前 50 个对象对例子并显示样本数与总数，完整计数使用所有可评测对象对。TN 不进入 F1。 |
| 专业领域实体抽取 | 领域类型列表；总体、领域内、引入前基线各自的 TP/FP/FN、公式、逐文档汇总与实体对照；F1 变化按当前总体减基线乘 100，单位为百分点。未提供基线时不补算。 |
| 多粒度时空消歧 | 总体与各维度粒度的正确数/总数公式；文档、提及、粒度、预测值、真实值逐项判定；缺失预测为错误。沿用原准确率口径，未匹配标注的额外预测不进入分母，并显示这些预测的数量。 |
| 多标签分类 | 去重后的标签集合、TP/FP/FN 标签、重复数和整组是否一致；Micro F1 汇总代入；逐标签计数及 P/R/F1；Macro F1 求和与除数；整组准确率。Macro 范围为预测与标注出现过的标签并集。 |
| 社区网络发现 | 真实评分所用的最优一一映射、预测社区与真实社区重叠矩阵、正确数/节点数公式以及每个节点的映射后判定。未分配计错，同名同类型节点去重，多个最优解时展示实际采用的映射。 |

专业领域页面的结果区还直接展示“领域内实体识别”：以标签突出本次实际领域类型，按类型列出 TP/FP/FN/P/R/F1，以及具体实体名称、预测类型、真实类型、判定、基线对比和来源文档。没有预测或标注样本的类型显示“无样本”。只有当前正确匹配、且基线未输出同名同类型实体时，才标记“引入后新增正确识别”；未提供基线不作新增判断。可展开“全部实体的领域归属”，查看领域内与领域外实体分别进入哪种统计。清单来自 `details.domain_entities`，复用现有评分对齐；不改变指标公式。

矩阵、对象对以及逐对象大表按点击展开时再创建，计算过程默认收起。两种输入模式共用评分与解释流程；所有额外信息仅作为报告输出，不写入预测 JSON 的必填输入字段。

### 评测融合的实体 ID 冲突处理

评测适配层在创建内部实体时检查 ID 唯一性。同名但不同类型、被原合并规则保留为不同对象的实体，发生名称 ID 冲突时会根据标准名、类型及原始来源分配不同内部 ID，再建立属性、关系、重定向和来源映射。不同文档使用相同原始 `e1/e2` 时，最终输出也会消除 ID 冲突，社区节点和关系端点使用一致的唯一 ID。正常同一实体仍沿用原合并规则。

同一文档中原始实体 ID 已重复时，无法可靠判断关系指向哪一个对象，会在融合前明确拒绝；重复来源文档 ID 也会被拒绝。修复位于评测适配层，旧任务的失败状态不会自动重算，请重新提交任务。

## 七个评测模块（2026-10-22）

首页和侧栏现在展示 7 个模块，完整保留原 12 项指标：

| 模块 | 页面路径（`/evaluation/` 后缀） | 指标 |
| --- | --- | --- |
| 实体与关系抽取 | `entity-relation-extraction` | 实体 F1、关系 F1 |
| 跨文档消融 | `cross-document/fusion` | 实体、关系、属性消融 F1 |
| 跨语种消融 | `cross-language/fusion` | 实体、关系、属性消融 F1 |
| 专业领域实体抽取 | `domain-entity-extraction` | 总体、领域内 F1 与基线对比 |
| 多粒度时空消歧 | `spatiotemporal-resolution` | 总体及各粒度准确率 |
| 多标签分类 | `multilabel-classification` | Micro/Macro F1、整组准确率 |
| 社区网络发现 | `community-discovery` | 社区匹配准确率 |

前三个模块新增同后缀的 `POST /api/v1/evaluation-runs/{模块后缀}`。上传与任务查询协议沿用原接口。
实体与关系任务每篇仅运行一次抽取；两个消融模块每篇运行一次抽取、整个任务运行一次融合，再分别计算三项分数。
这里“一次”指同一份输入与配置的预测流程，长文分段仍会产生多次底层模型调用。
组合报告的 `metrics` 按原单项指标后缀分组，例如 `metrics["entity-extraction"]` 和 `metrics["relation-extraction"]`。
消融报告的 `details.fusion_alignment` 也按三个原指标后缀分组，共享的融合图保留在 `details.fused_graph`。

组合消融必须同时完整标注 entities、relations、attributes 的 cluster，每类至少有两个符合场景的对象；缺失会在预测前报错。
下载模板包含全部需要的标注。原十二类长文数据仍保留；七模块配套长文包位于 `data/evaluation_modules/`，各模块使用一份完整标注。

### 抽取名称策略

自动评测的公开配置增加 `name_mapping_policy`，默认值为 `source`：复用现有模型、提示词和清洗算法，但通过旧算法已有的显式名称覆盖参数，隔离外部 CSV 别名替换。同时将公开的实体类型、属性配置和关系白名单传给现有清洗器，不仅在提示词中约束；合并后再次核对类型和关系端点，避免消歧兜底类型绕过公开配置。
模型根据正文明确识别的全称、简称及其 resolutions 仍正常处理。这一策略只影响自动评测，不修改原业务接口、CSV、人工标注或评分公式。

需要复现原业务的 CSV 映射时，可在公开配置中设置 `"name_mapping_policy": "legacy"`。
原名称表是跨类型共享的，例如包含南京→南京日报、苏州→苏州日报；用于地点抽取时可能把城市错误改成报社名称，同时使关系端点出错。
报告的 `options.name_mapping_policy` 和各篇 `predictions[].graph.processing.name_mapping_policy` 记录实际策略，`processing.schema_enforced` 记录是否启用上述配置约束，以便复核。
旧任务报告保留原预测与分数；修复生效后需要重新提交产生新任务。

本地接口检查：python scripts/verify_standalone.py。它会使用配套预测 JSON 验证全部七模块，不调用在线模型。

### 七模块质量复验

运行 `python scripts/verify_standalone.py` 验证全部指标上传与评分。
运行 `python start.py --host 127.0.0.1 --backend-port 50311 --frontend-port 50312 --smoke-test` 验证实际前端代理和后端评分，结束后自动停止。
它们使用 data/evaluation_261029 下的 gold.json、predictions.json、options.json。
真实 TXT 预测可在页面发起，结果仍取决于原文、配置和外部模型。

专业领域数据的公开配置必须覆盖实际评测类型。配套长文包含人物、组织、装备、技术，默认模板使用基础类型人物/组织、领域类型装备/技术。
如果仍使用只有 `"domain_entity_types": ["装备"]` 的旧配置，技术会被过滤或错分成装备，不能复现完整配置下的分数。
请上传配套 gold.json 后，同时粘贴同目录 options.json；已有任务的公开配置和报告不会被模板更新自动改写。

source 模式在预测层采用以下规则，人工标注和评分公式不变：

- 对单一明确“年份”属性输出 YYYY；不能把年份范围简化成某个年份。
- 仅抽取有具体名称的实体，英文句首大写、普通叙述短语不能单独作为机构命名证据。
- 融合沿用 source 名称策略，不再套用外部 CSV 或进行无必要的输出翻译；实际实体消歧确认多个地点译名指向同一实体后，将引用这些实体的属性值规范到同一标准名，并记录原属性到融合属性的来源。缺少这种证据或有不同实体冲突时不合并属性。
- 对原文明示的完整日期、公开参考日期支持的相对日期，以及预测地理实体中的明确行政区成分做确定性标准化，LLM 补充其他提及。没有确定解释且模型输出相互矛盾的提及保留 resolution_warnings 并不输出该项，评分按漏预测处理。

“已有预测直接评分”模式通过按钮切换具体指标，各指标独立保留输入和成功结果，仍调用原十二个直接评分接口。
原十二个页面地址可继续访问单项页面并恢复旧任务；首页和侧栏只列出七个模块。旧自动接口的报告结构不变。

## 本地运行

```powershell
conda activate nanjing_zncl
python fastapi_worker.py
```

另一终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

前端 `http://127.0.0.1:50112`；后端文档 `http://127.0.0.1:50111/docs`。前端通过同源代理访问本地 50111。真实模型、分类标签库等依赖沿用 `conf/config.yml`，不会复制或写死生产凭据。

当前自动任务执行器为单进程内的有界线程池，最多 2 个执行、4 个排队。请使用上述单进程启动方式，不要以多个 Uvicorn worker 共享同一个任务数据库。多实例部署需独立任务库或另行接入持久任务调度。

## 页面操作

1. 进入任一子页面，保持“上传原文自动评测”。
2. 下载标注模板；准备自己的完整标注，确保 filename 与 TXT 文件名一致。多个文件一次选择上传。
3. 上传标注或在 JSON 编辑区填写。不需要填写 predicted，标注中也不允许该字段。
4. 在公开配置里设置允许抽取的类型、属性、关系、领域类型、粒度与消融开关。公开配置是算法输入，标注答案不是。
5. 提交后查看阶段和进度。离开页面不取消后台任务，带 `?job=任务ID` 的页面地址可恢复查询。
6. 查看算法原始预测、对齐记录和指标，下载包含本次实际原文、标注、配置、预测、中间结果和分数的报告。

“加载示例原文与标注”提供格式示范，分数依赖当前算法；不是模型一定能达到满分的验收答案。尤其分类模板的标签 1 要替换为数据库中的真实标签 ID；标签不一致会如实得到低分或零分。

## 文本与标注格式

第一版仅支持 TXT 原文和 UTF-8 JSON 标注。原文支持 UTF-8（含 BOM）、GB18030。每次 1～20 篇文本，每篇不超过 512 KB，所有原文最多 10 万字符；标注最多 1 MB；文本、标注和配置总数据不超过 4.5 MB，适配前端 5 MB 代理限制。

最小实体标注：

```json
{"documents":[{"document_id":"doc-1","filename":"document-1.txt","language":"zh","entities":[{"entity_id":"manual-1","name":"南京大学","type":"组织"}]}]}
```

每个文档可使用以下字段；与当前评测无关的数组可以为空：

| 字段 | 作用 |
| --- | --- |
| `entities` | entity_id、name、type；消融评测增加 cluster，社区评测增加 community |
| `relations` | relation_id、head_entity_id、relationCode、tail_entity_id；关系消融增加 cluster |
| `attributes` | attribute_id、entity_id、key、value；属性消融增加 cluster |
| `resolutions` | mention、dimension（time/space）、granularity、value |
| `tags` | 文本的全部真实分类标签 ID，统一使用字符串 |

同文档实体 ID 唯一；关系和属性引用本文档的实体 ID。语义匹配键不允许重复。不要把未标注对象当作“不存在”，应使用完整标注集。语言使用 zh、en、ja 等代码，大小写和区域变体会统一到基础语言；EN/en、zh-CN/zh-TW 不作为两种不同语言。

实体是名称＋类型级评测，同文档相同名称和类型计一次，不是字符跨度或多次提及级 NER。消歧同文档相同 mention、dimension、granularity 只允许一条标注；存在同名不同上下文的消歧时应拆分文档或使用不同提及文本，本版不做字符跨度级消歧。

## 自动任务接口

统一前缀 `/api/v1/evaluation-runs`。兼容的十二个单项入口和上文三个新增组合入口均为 POST，使用 multipart/form-data，参数为：

- `texts`：一个或多个 TXT 文件。
- `annotations`：一个 JSON 标注文件。
- `options`：公开运行配置 JSON 字符串，可省略，默认为 `{}`。

| 路径后缀 | 评测 |
| --- | --- |
| `/entity-extraction` | 实体抽取 |
| `/relation-extraction` | 关系抽取 |
| `/domain-entity-extraction` | 专业领域实体抽取，默认同时运行引入前基线 |
| `/cross-document/entity-fusion` | 跨文档实体消融 |
| `/cross-document/relation-fusion` | 跨文档关系消融 |
| `/cross-document/attribute-fusion` | 跨文档属性消融 |
| `/cross-language/entity-fusion` | 跨语种实体消融 |
| `/cross-language/relation-fusion` | 跨语种关系消融 |
| `/cross-language/attribute-fusion` | 跨语种属性消融 |
| `/spatiotemporal-resolution` | 多粒度时空消歧 |
| `/multilabel-classification` | 多标签分类 |
| `/community-discovery` | 社区发现 |

上传成功 HTTP 202，仍使用 `success/data/error` 包装，data 包含 job_id、status、stage、progress。管理入口：

| 方法与路径 | 行为 |
| --- | --- |
| GET `/templates/{评测路径后缀}` | 返回示例原文、标注、公开配置及相应 JSON Schema |
| GET `/jobs/{job_id}` | 查询阶段、进度及最终预测和指标 |
| POST `/jobs/{job_id}/cancel` | 标记取消；当前模型调用返回后停止，不覆盖为成功 |
| POST `/jobs/{job_id}/delete` | 清理已结束任务的原文、标注和结果记录；运行中的任务需先取消 |

任务状态为 queued、running、succeeded、failed、cancelled。查询接口返回 success=true 只表示查询成功；执行结果以 data.status 为准。失败不会伪装成空预测成功，前端展示错误并保留明确标记的上一次成功报告。

任务输入和结果保存在项目 logs 下的独立 SQLite 数据库（默认 evaluation_runs.sqlite），可用 `NANJING_EVALUATION_DB` 指定。刷新不会丢失任务，进程重启会把中断任务标记为失败并保留已完成报告。每次提交时清理超过一天的结束记录，最多保留 200 条；也可主动清理单条任务。取消不会强制终止已进入旧 SDK 的网络请求，但取消后的结果不会写成成功。

## 算法复用与评分边界

- 抽取调用原 `extract_entities_with_model`，提示词由已有构造函数按公开配置生成；原文逐段调用并用原合并函数汇总，避免旧函数仅处理前四段的范围限制。
- 分类调用原 `classify_text`，候选标签仍查数据库。新编排逐段运行后取预测标签并集，避免旧函数只读取前 2048 字；这是新增全文编排策略，不改变原分类函数。
- 实体按同文档名称和类型精确匹配；关系先把预测和标注的端点转换到相同语义身份，再匹配有向三元组，不直接比较随机 ID。
- 六个消融流程先独立抽取，再运行原 `GraphAblationService`。新增只读追踪适配记录原算法中的来源、分组和最终 ID，测试对比其业务输出与原类一致。未匹配的真实对象和额外预测均保留；主指标为 pairwise cluster F1，并另外返回对象抽取覆盖指标。属性冲突被原算法丢弃时，不把它伪造为合并成功。
- 跨语种效果取决于原有映射、名称相似度和 `use_llm_resolution` 配置。公开配置可启用原有 LLM 对齐；不根据人工 cluster 自动生成等价映射来提高分数。
- 专业领域采用两组实验：基线不传任何实体类型、实体属性或关系类型列表，使用开放抽取提示词；引入后使用 `entity_types + domain_entity_types`、`entity_attributes` 和 `relation_types`。两组对照同一份完整实体及关系标注，分别返回实体与关系 F1 差值；`compare_baseline=false` 可只运行引入后版本，此时不能完成基线指标验收。
- 时空流程是新增标准化适配：在原抽取结果基础上，通过项目已有 LLM 客户端，仅从原文、已有预测和公开粒度/参考日期生成标准值。year/month/day 使用 YYYY、YYYY-MM、YYYY-MM-DD；空间使用规范行政区名称。标注值和提及不会作为查询答案传入模型。缺少证据不猜测，漏预测按错计，矛盾预测明确失败。
- 社区发现从预测图谱运行原 Leiden 算法，再通过来源追踪对齐标注，避免展示名称翻译影响身份匹配。原算法没有分配的孤立节点按未分配计错，不从标注分母中删除；社区编号做最优一一匹配。

所有算法预测调用只接收 InputDocument 和 PredictionOptions，评分环节才读取人工标注。字段定义、允许类型或语言是公开任务配置，真实 cluster、community、关系答案、分类答案不是算法配置。

### 专业领域对照指标（2026-10-29）

`overall` 为引入列表后的全部实体 F1，`domain_only` 仅统计领域类型子集；`baseline` 为完全不提供类型列表时的全部实体 F1，并非把标注中的领域外实体单独计算。`relations`、`baseline_relations` 分别为两组关系 F1。实体按名称和类型匹配；关系按端点名称、端点类型、方向和关系名称匹配，两组采用相同规则，不忽略错误类型或未抽出的端点。

`f1_delta = overall.f1 - baseline.f1`，`relation_f1_delta = relations.f1 - baseline_relations.f1`，页面乘以 100 展示百分点。

`acceptance.checks` 严格检查：基线实体 F1 > 0.88、基线关系 F1 > 0.75、引入后总体实体 0.95 < F1 < 1。缺少基线或真实关系标注时为 `not_evaluable`；低于门槛或引入后满分时为 `failed`。这是对真实分数的验收，不是调分策略，不保证任意文本达到指标。

已有结果模式继续支持 `documents` 与 `baseline_documents`，关系包含在各文档的 `relations` 中；只重算两组已有预测，不请求模型、不按新 options 重新抽取。旧预测如果是在受限基线下生成，不能作为无约束实验的证明，应重新抽取。对应配置记录见 `details.comparison_configuration`，预计算结果标为 `uploaded_predictions`，不会声称现场执行了开放抽取。

开放抽取的关系名使用通用格式规则：原文中的连续核心动词、2至8个汉字，不含占位符。若返回不合规关系名，仅以相同原文及格式要求再请求模型一次，不提供类型白名单或标注答案。前一次输出及未修正项记录在基线图谱的 `processing.open_relation_retries` 中；重试输出继续交给通用规范化，不替换为标注关系或删除以提高分数。此规则只用于无约束基线，不改变普通业务抽取。

开放基线在输出合并后还执行通用关系名规范化：移除句式占位符及开头的介词，移除模型自身推断的目标实体类别后缀；仅在剩余连续谓词实际出现在原文、且不违背已有 evidence 时采用。规范化函数不接收 options 或 gold，不使用实体/关系列表，不修改端点或实体类型，变更记录在 `processing.open_relation_normalization`。不满足这些条件的原始关系名保留，错误照常计分；已有预测上传模式不执行此规范化。

独立高级评分接口保留旧实体参数，并可选新增 `relation_samples` 与 `baseline_relation_samples`；后者必须同时提供基线实体和当前关系样本，且两组关系标注必须完全相同。

## 验证

```powershell
conda activate nanjing_zncl
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_text_evaluation_runner.py tests/test_text_evaluation_algorithms.py tests/test_evaluation_runs_api.py tests/test_evaluation_api.py -q -p no:cacheprovider
cd frontend
npm run typecheck
npm test
$env:PYTHON = (Get-Command python).Source
npm run test:e2e
```

真实模型与数据库联调（会调用配置中的真实服务）：

```powershell
python frontend/scripts/smoke_automatic.py --metrics all
```

脚本逐个上传模板原文并保存实际预测及指标到前端忽略的 artifacts。浏览器自动化测试只替换模型边界，真实服务联调单独执行。完整旧测试集若引用仓库不存在的旧模块，应报告原有收集错误，不能通过修改旧业务规避。
