# 指标评测独立项目

本文说明各评测指标的匹配算法、计算公式、汇总方式和边界规则。公式与当前评分实现保持一致，不使用业务场景举例。

本项目提供 8 个指标模块。

| 模块 | 指标 |
| --- | --- |
| 实体与关系抽取 | 实体、关系 Precision / Recall / F1 |
| 跨文档融合 | 实体、关系、属性消融 F1 和均值 |
| 跨语种融合 | 实体、关系、属性消融 F1 和均值 |
| 专业领域抽取 | 领域配置引入前后的实体、关系 F1 和差值 |
| 多粒度时空消歧 | 总体及各粒度准确率 |
| 多标签分类 | Micro / Macro / 各标签 F1 |
| 社区发现 | 标签最优匹配准确率 |
| 自然语言转 SQL | 结果等价率、解析通过率、规范化严格匹配率 |

保留 TXT 实时抽取、预测 JSON 直接评分、人工标注与配置上传、计算明细、下载结果，以及任务查询、取消和清理。

## 指标计算算法

### 1. 统一符号、计数和边界

| 符号 | 定义 |
| --- | --- |
| `I(A)` | 指示函数，条件 A 成立取 1，否则取 0 |
| `P_s`、`G_s` | 样本 s 内按指定匹配键去重后的预测集合、标注集合 |
| `N` | 当前指标实际纳入的评测项数量；各指标的评测项定义见下文 |
| `TP` | 预测为正且标注为正的计数 |
| `FP` | 预测为正但标注为负的计数 |
| `FN` | 标注为正但预测未判为正的计数 |
| `TN` | 预测与标注均为负的计数，仅在对象对计算明细中统计 |
| `support` | 标注正例数，即 `TP + FN` |

集合匹配的统一计数：

```text
TP_s = |P_s ∩ G_s|
FP_s = |P_s − G_s|
FN_s = |G_s − P_s|

TP = Σ_s TP_s
FP = Σ_s FP_s
FN = Σ_s FN_s

Precision = TP / (TP + FP)
Recall = TP / (TP + FN)
F1 = 2 × TP / (2 × TP + FP + FN)
   = 2 × Precision × Recall / (Precision + Recall)  （分母非零时）

support = TP + FN
预测正例数 = TP + FP
标注正例数 = TP + FN
```

Precision、Recall、F1 各自分母为 0 时返回 0；包括预测集合与标注集合均为空的情况。TN 不参与这些公式。有效指标的数值通常在 `[0, 1]`；SQL 的 `accuracy_percent` 直接在 `[0, 100]`。

除 SQL 特定规则外，字符串使用校验后的值精确匹配；不对评分键做模糊相似度匹配。样本标识参与匹配，同一匹配键跨样本分别计数。总体指标使用原始计数计算后再格式化，不将已经四舍五入的展示值代回公式。

### 2. 实体与关系抽取

#### 2.1 匹配键与去重

```text
实体匹配键 K_e = (name, type)
关系匹配键 K_r = (head_entity_id, relationCode, tail_entity_id)

P = ⋃_s {(s, K(x)) : x ∈ 预测列表_s}
G = ⋃_s {(s, K(x)) : x ∈ 标注列表_s}

TP = |P ∩ G|
FP = |P − G|
FN = |G − P|

预测重复数_s = 预测列表长度_s − |P_s|
标注重复数_s = 标注列表长度_s − |G_s|
```

关系是有向匹配，交换首尾端点视为不同关系。直接评分要求预测与标注使用同一端点标识空间；自动评测先将端点转换为对应实体的 `(name, type)` 语义标识，再生成关系匹配键。实体类型或关系任一字段不一致，均不能作为 TP；未匹配预测与未匹配标注分别计入 FP、FN。

#### 2.2 汇总指标

分别对实体和关系计算上述 Precision、Recall、F1。总体采用 Micro 汇总，即先汇总 TP、FP、FN 再计算 F1，不平均样本 F1。实体 F1 与关系 F1 独立返回，不生成两者的综合平均分。

### 3. 跨文档融合与跨语种融合

两类融合均使用对象对聚类评价算法，分别计算实体、关系、属性三类对象的指标。

#### 3.1 对象对齐与可评测对象对

直接评分使用唯一 `item_id` 标识对象。自动评测先在每个文档内去重并按以下语义键对齐，再取预测与标注对象的并集：

```text
实体键 = (name, type)
关系键 = (头实体语义键, relationCode, 尾实体语义键)
属性键 = (所属实体语义键, 属性名, 属性值)
```

设去重对齐后的对象数量为 `M`，文档标识为 `d_i`，语言标识为 `l_i`：

```text
全部无序对象对数量 = C(M, 2) = M × (M − 1) / 2

跨文档可评测集合 Ω_doc = {(i, j) : i < j 且 d_i ≠ d_j}
跨语种可评测集合 Ω_lang = {(i, j) : i < j 且 l_i ≠ l_j}

eligible_pairs = |Ω|
excluded_pairs = C(M, 2) − |Ω|
```

仅在选定集合 Ω 内计分。跨语种规则只判断语言是否不同，不额外要求文档不同。没有可评测对象对时返回校验错误，不生成分数。

#### 3.2 对象对计数与 F1

设 `g_i` 为标注簇，`p_i` 为预测簇：

```text
Y_ij = I(g_i = g_j)
Ŷ_ij = I(p_i ≠ null 且 p_j ≠ null 且 p_i = p_j)

TP = Σ_(i,j)∈Ω I(Y_ij = 1 且 Ŷ_ij = 1)
FP = Σ_(i,j)∈Ω I(Y_ij = 0 且 Ŷ_ij = 1)
FN = Σ_(i,j)∈Ω I(Y_ij = 1 且 Ŷ_ij = 0)
TN = Σ_(i,j)∈Ω I(Y_ij = 0 且 Ŷ_ij = 0)

eligible_pairs = TP + FP + FN + TN
```

将上述 TP、FP、FN 代入统一 Precision、Recall、F1 公式。聚类标签编号无需在预测与标注之间相同，只比较各自是否同簇。预测簇缺失时不判为同簇；自动评测将额外预测对象的标注簇设为独立簇，将漏预测对象的预测簇设为空，防止将未对齐对象默认合并。

#### 3.3 对象覆盖指标

对象对 F1 之外，自动评测单独计算对象覆盖度。设 `A` 为按文档及语义键匹配的对象数，`P_o`、`G_o` 为去重后的预测、标注对象总数：

```text
TP_cov = A
FP_cov = P_o − A
FN_cov = G_o − A

Precision_cov = A / P_o
Recall_cov = A / G_o
F1_cov = 2 × A / (P_o + G_o)
```

各分母为 0 时按统一规则返回 0。覆盖分与对象对 F1 独立，不相乘或相加生成新分数；直接对象对评分接口不额外计算覆盖指标。

#### 3.4 三项平均 F1

```text
Mean_F1 = (F1_entity + F1_relation + F1_attribute) / 3
```

三项等权，不按对象数或对象对数加权。仅在三项 F1 均存在时展示均值；每一项内部仍按其全部可评测对象对汇总计数。

### 4. 指定类型抽取与基线比较

此部分对应“专业领域抽取”模块，仅描述类型筛选与指标比较算法。

设 `D` 为配置的实体类型集合：

```text
P_s^D = {x ∈ P_s : type(x) ∈ D}
G_s^D = {x ∈ G_s : type(x) ∈ D}

TP_D = Σ_s |P_s^D ∩ G_s^D|
FP_D = Σ_s |P_s^D − G_s^D|
FN_D = Σ_s |G_s^D − P_s^D|
```

全部类型、指定类型子集分别代入统一公式，得到 `overall`、`domain_only` 的 Precision、Recall、F1。不是只过滤预测，也不是将类型集合外的对象当作基线。逐类型指标通过将 `D` 替换为单个类型集合计算。

当前预测与基线预测必须具有相同样本及相同标注集合；分别计分后计算差值：

```text
ΔF1_entity = F1_current_entity − F1_baseline_entity
ΔF1_relation = F1_current_relation − F1_baseline_relation
ΔF1_pp = 100 × ΔF1
```

差值范围为 `[-1, 1]`，换算后单位为百分点；不是除以基线分数的相对增长率。未提供基线时不计算差值。关系使用与抽取相同的有向三元组匹配规则；存在当前关系标注且提供基线关系时才产生关系 F1 差值。页面还要求基线关系存在标注正例后才展示该差值。

逐类型明细中的“新增正确对象”判定为：

```text
NewCorrect(s, x) = I(x ∈ P_s^D ∩ G_s^D 且 x ∉ P_baseline,s)
新增正确对象数 = Σ_s Σ_x∈P_s^D∩G_s^D NewCorrect(s, x)
```

该计数用于解释结果，不代入 F1。未提供基线时不将对象标为新增正确。

#### 4.1 固定阈值判定

当前比较接口另返回以下固定验收判定，阈值均为严格不等式：

```text
baseline_entity_pass = I(F1_baseline_entity > 0.88)
baseline_relation_pass = I(F1_baseline_relation > 0.75)
configured_entity_pass = I(0.95 < F1_current_entity < 1.00)
passed = baseline_entity_pass ∧ baseline_relation_pass ∧ configured_entity_pass
```

对应分数缺失或 `support = 0` 时，该项状态为 `not_evaluable`，总体不通过；否则满足阈值为 `passed`，不满足为 `failed`。这些布尔判定不改变原始指标，也不替代 F1 公式。

### 5. 多粒度时空消歧

直接评分按 `(sample_id, mention_id, dimension, granularity)` 标识每个评测项；自动评测在同一样本内先按 `(mention, dimension, granularity)` 对齐预测与标注。对齐后精确比较标准值，空预测计错，不进行近似匹配或跨粒度折算。

```text
c_i = I(predicted_i = gold_i)
correct = Σ_(i=1..N) c_i
Accuracy = correct / N
```

以维度与粒度二元组划分子集 `H_(d,g)`：

```text
correct_(d,g) = Σ_i∈H_(d,g) c_i
total_(d,g) = |H_(d,g)|
Accuracy_(d,g) = correct_(d,g) / total_(d,g)

Accuracy = Σ_(d,g) correct_(d,g) / Σ_(d,g) total_(d,g)
```

总体按评测项数量加权，不直接平均分组准确率。分母为标注项数，自动评测的额外未对齐预测只记录为 `unscored_predictions`，不另加扣分或扩大分母。输入至少包含一个评测项，空输入返回校验错误。

### 6. 多标签分类

每个样本的标签列表先转为集合，`P_s`、`G_s` 分别为去重后的预测、标注标签。标签按标识精确比较，展示名称不参与评分。

#### 6.1 Micro 指标

```text
TP_micro = Σ_s |P_s ∩ G_s|
FP_micro = Σ_s |P_s − G_s|
FN_micro = Σ_s |G_s − P_s|

Precision_micro = TP_micro / (TP_micro + FP_micro)
Recall_micro = TP_micro / (TP_micro + FN_micro)
F1_micro = 2 × TP_micro / (2 × TP_micro + FP_micro + FN_micro)
```

不同样本中的相同标签独立计数。Micro F1 不是样本 F1 的算术平均。

#### 6.2 各标签指标与 Macro F1

设实际出现的标签并集为 `L = ⋃_s (P_s ∪ G_s)`。对每个标签 l：

```text
TP_l = Σ_s I(l ∈ P_s 且 l ∈ G_s)
FP_l = Σ_s I(l ∈ P_s 且 l ∉ G_s)
FN_l = Σ_s I(l ∉ P_s 且 l ∈ G_s)

Precision_l = TP_l / (TP_l + FP_l)
Recall_l = TP_l / (TP_l + FN_l)
F1_l = 2 × TP_l / (2 × TP_l + FP_l + FN_l)

Macro_F1 = (Σ_l∈L F1_l) / |L|
```

各标签等权，包含只在预测中出现的标签；不按标签频次加权，也不纳入预测和标注中均未出现的标签。`L` 为空时 Macro F1 返回 0，其余分母为 0 时按统一规则返回 0。

#### 6.3 整组准确率与重复标签

```text
exact_s = I(P_s = G_s)
Subset_Accuracy = (Σ_s exact_s) / 样本数

预测重复标签数_s = 原始预测标签数_s − |P_s|
标注重复标签数_s = 原始标注标签数_s − |G_s|
```

预测与标注均为空集时，该样本整组匹配为 1，但它不贡献 Micro TP，也不引入标签类别。至少提供一个样本，空样本列表返回校验错误。

### 7. 社区发现

节点先对齐，再计算预测社区与标注社区的最优一一映射。直接评分使用 `node_id`；自动评测按实体 `(name, type)` 跨样本去重，对应不同预测社区的冲突节点按未分配处理。自动评测以标注节点为分母；未对齐的额外预测节点不进入分母。

设 `P_a` 为预测社区 a 的节点集合，`G_b` 为标注社区 b 的节点集合，构造重叠矩阵：

```text
W_ab = |P_a ∩ G_b|
K = max(预测社区数, 标注社区数)
```

矩阵不足 `K × K` 的部分补 0。使用匈牙利算法求最大权重一一指派：

```text
π* = argmax_π Σ_(a=1..K) W_(a,π(a))
correct = Σ_(a=1..K) W_(a,π*(a))
Accuracy = correct / N

c_i = I(predicted_community_i ≠ null 且 π*(predicted_community_i) = gold_community_i)
correct = Σ_(i=1..N) c_i
```

映射到补零列的预测社区不匹配任何真实社区。社区标签本身无需一致；缺失预测、冲突预测和映射后不匹配节点计错。有多个最优映射时，展示其中一个，正确总数和准确率不变。输入至少一个节点，空输入返回校验错误。

### 8. 自然语言转 SQL

#### 8.1 分母与 SQL 提取

先按所选数据库过滤上传用例，保持原顺序取数量上限内的前 N 条。每条等权，工作流请求失败、SQL 提取失败、解析失败、转换失败和执行失败均保留在 N 中。没有可评测用例时不创建任务。

优先提取输出中第一个代码块的内容，否则使用去除首尾空白的完整输出。仅接受以 `SELECT` 或 `WITH` 开头的文本，再执行下述判定。

#### 8.2 解析通过指标

```text
S_i = I(目标方言解析成功 且 只有一条查询 且 查询通过只读检查)
syntax_pass = Σ_(i=1..N) S_i
解析通过率 = syntax_pass / N
```

只读检查拒绝写入、结构修改及其他不允许的语句节点。通过解析及只读检查后，即使后续规范化、转换或执行失败，`S_i` 仍为 1。解析通过仅表示解析器接受该查询。

#### 8.3 规范化严格匹配指标

设 `Norm_d` 为目标方言 d 下的解析与规范化 SQL 输出：

```text
E_i = I(Norm_d(generated_sql_i) = Norm_d(standard_sql_i))
exact_match = Σ_(i=1..N) E_i
规范化严格匹配率 = exact_match / N
```

无法完成前置检查或规范化时 `E_i = 0`。该指标比较规范化文本，不判断所有语义等价写法；它与结果等价率独立，不作为结果等价得分的必要条件。

#### 8.4 三组执行结果比较

根据 `fixture_profile` 选择本地模拟数据，将生成 SQL 转换为 SQLite SQL。设 `k ∈ {0, 1, 2}`，`A_ik` 为实际查询结果，`B_ik = expected_results_i[k]` 为上传的预期结果：

```text
Round5(v) = round(v, 5)  （v 为数值）
Round5(v) = v            （其他值保持原值）

M_ik = I(Round5(A_ik) = B_ik)
C_i = M_i0 × M_i1 × M_i2
    = I(M_i0 = 1 且 M_i1 = 1 且 M_i2 = 1)
```

`Round5` 对实际结果的各数值单元应用。比较按行顺序及列位置进行，要求行数、每行列数和对应值一致；保留重复行，忽略列别名，空结果与缺少执行结果分别处理。运行评分时直接使用上传的预期结果，不重新执行标准 SQL 替换预期值。

任一前置步骤失败、任一组执行错误或任一组结果不一致，均有 `C_i = 0`。执行错误后的剩余组标为未执行；已成功执行的组仍保留结果。三组全部匹配才有 `C_i = 1`，页面据此标记“正确”，否则标记“不正确”。不正确标记也包含解析、转换或模拟执行失败。

#### 8.5 总体及分组统计

```text
execution_correct = Σ_(i=1..N) C_i
结果等价率 = execution_correct / N
accuracy_percent = 100 × execution_correct / N
不正确数 = N − execution_correct

解析通过百分比 = 100 × syntax_pass / N
严格匹配百分比 = 100 × exact_match / N
```

三组共同决定一条用例的得分，结果等价率的分母为 N，不是 `3N`。

对任一数据库分组或问题类型分组 H：

```text
N_H = |H|
correct_H = Σ_i∈H C_i
Accuracy_H = correct_H / N_H
syntax_pass_H = Σ_i∈H S_i
Syntax_Rate_H = syntax_pass_H / N_H

总体结果等价率 = Σ_H correct_H / Σ_H N_H
               = Σ_H (N_H / N) × Accuracy_H
```

上述总体加权公式对同一完整划分成立，不混合不同分组维度重复计数。接口按数据库返回解析通过数；按问题类型返回用例数和结果等价数。分组分母为 0 时页面显示“—”，不显示为满分，也不生成空分组。结果基于本地 SQL 转换与模拟执行，不等同于各目标数据库实例的原生执行准确率。

### 9. 百分比、差值和计算明细约定

```text
指标百分比 = 指标原始值 × 100%
百分点变化 = (当前原始值 − 基线原始值) × 100
正确数 = Σ I(单项满足该指标的匹配规则)
不正确数 = 评测项总数 − 正确数
```

除 SQL `accuracy_percent` 外，接口返回的比例指标不预先乘 100；一般页面百分比保留两位小数，SQL 结果概览保留一位，详细公式保留两位。差值保留正负方向。详细计算中的 TP/FP/FN、对象对示例、行级差异仅解释实际评分；明细截取或按需展开不改变分母和汇总计数。

实现依据：[`tools/evaluation.py`](tools/evaluation.py)、[`tools/evaluation_schemas.py`](tools/evaluation_schemas.py)、[`tools/text_evaluation/runner.py`](tools/text_evaluation/runner.py)、[`tools/text_evaluation/domain_comparison.py`](tools/text_evaluation/domain_comparison.py)、[`tools/text_evaluation/calculation.py`](tools/text_evaluation/calculation.py)、[`tools/sql_evaluation/benchmark.py`](tools/sql_evaluation/benchmark.py)。

## 启动

使用本机 nanjing_zncl Conda 环境和 Node.js 22 或更高版本，在本目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

也可以明确指定 Python：

```powershell
.\start.ps1 -PythonExecutable 'E:\software\anaconda\envs\nanjing_zncl\python.exe'
```

激活环境后可直接运行 `python start.py`。按 Ctrl+C 同时停止前后端。

- 页面：http://127.0.0.1:50112/
- 局域网：http://192.168.31.88:50112/（以运行机器实际 IP 为准）
- 文档：http://127.0.0.1:50112/docs
- 后端：http://127.0.0.1:50111/health

更换端口：`.\start.ps1 -BackendPort 50311 -FrontendPort 50312`。本目录可整体复制到其他位置运行。精简时没有重启已经运行的服务，正在运行的旧版本需要重启后才会加载新的接口范围。

## 目录与接口

```text
frontend/              8 个指标模块页面、组件、代理和构建资源
apis/                  直接评分、自动评测及请求示例
tools/text_evaluation/ 实时预测、评分编排、任务管理、计算明细
tools/                指标依赖的抽取、提示词、分类、融合、社区算法
utils/                 数据库标签读取、JSON、日志和异常工具
conf/                  评测服务、模型、分类标签数据库配置
data/                  评测数据和实体映射表
tests/                 评测及其算法相关测试
scripts/               独立接口、真实前端代理验证
docs/                  评测接口和采样参数说明
verification/          当前精简版本验证记录
```

提供 `/api/v1/evaluation/*`、`/api/v1/evaluation-runs/*`、`/api/v1/sql-evaluation/*`、`/api/v1/mcp-console/*`、`/health`、`/ready` 和 FastAPI 文档。

指标需要的提示词和模型辅助函数位于 `tools/extraction_prompt.py` 和 `tools/extraction_model.py`。数据库客户端提供连接池、标签读取和模型配置读取。

## 配置和安装

所有评测（包括 SQL 评测与 SQL 命令行）的默认并发数统一读取 `conf/config.yml` 顶层的 `max_workers`：

```yaml
max_workers: 4
```

支持 1～8 的整数，1 为串行。修改后重启后端并刷新页面生效；配置读取沿用进程缓存。文本评测模板和 SQL 页面从后端读取该默认值，前端不再写死默认并发数。可通过现有环境变量机制 `NANJING__MAX_WORKERS` 覆盖该配置；旧配置缺少字段时兼容使用 4。

页面“模型请求并发数”对应文本任务的 `options.max_workers`，SQL 页面“并发数”对应 `workers`，命令行参数为 `--workers`。未显式提供时使用上述统一配置；明确填写的单次任务参数仍优先于配置默认值。独立文档、同一文档的文本分块、当前与基线预测以及时空标准化可并行，依赖完整预测结果的融合与评分在前序阶段完成后执行。融合内部的模型消歧批次使用同一并发配置。

文档与分块不会叠加建立嵌套线程池；每个任务的预测并发不超过本次任务设定值。完成进度按返回顺序更新，结果仍按原始文档和分块顺序合并。取消任务后停止派发新预测，已发出的 HTTP 调用需返回后退出。已有预测 JSON 评分不调用模型。并发加速幅度取决于模型服务的处理能力及请求限额。

`conf/config.yml` 保留模型和分类标签数据库的现有连接配置。上传 predictions.json 和 gold.json 时只评分，不访问模型或数据库；TXT 实时抽取需要模型服务，多标签分类 TXT 模式还需要 PostgreSQL 标签列表。

```powershell
conda activate nanjing_zncl
python -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
python start.py
```

本地已有构建好的 `frontend/dist`；运行时无需重新安装 npm 包。修改前端源码后重新构建。开发依赖在 requirements-dev.txt。日志和 SQLite 任务数据库只存储在本目录 logs/。

## 中文日志

日志级别统一读取 `conf/config.yml` 的 `log_level`。默认 `INFO` 记录运行阶段、输入规模、有效并发数、模型请求耗时、预测数量、指标汇总、取消和清理；设置为 `DEBUG` 后额外记录线程调度、SQL 各组结果比较和子进程输出。修改后重启程序生效。

```yaml
log_level: INFO
```

| 日志文件 | 内容 |
| --- | --- |
| `logs/YYYY-MM-DD.log` | 后端接口、直接评分、自动预测任务、模型调用及异常 |
| `logs/YYYY-MM-DD_error.log` | 后端 ERROR 级别及以上日志，保留异常堆栈 |
| `logs/YYYY-MM-DD_startup.log` | 启动检查、前后端进程启动与退出 |
| `logs/YYYY-MM-DD_startup_error.log` | 初始化日志完成后的启动异常 |
| `logs/YYYY-MM-DD_sql.log` | SQL 评测脚本、逐条用例、工作流 HTTP 调用及报告保存 |
| `logs/YYYY-MM-DD_sql_error.log` | SQL 评测脚本 ERROR 级别及以上日志 |
| `logs/sql_evaluation_jobs/<任务ID>/subprocess.log` | 对应 SQL 任务的完整子进程输出，写入前脱敏 |

统一日志格式包含时间、级别、进程 ID、线程名、请求编号、任务编号和来源模块。HTTP 响应的 `X-Request-ID` 与后端日志请求编号一致；后台任务和并发预测线程继承该编号及任务编号，SQL 子进程通过内部环境变量继续关联。健康检查和任务轮询的正常请求使用 DEBUG 级别，避免 INFO 日志被轮询刷满。

主日志、SQL 日志和启动日志均使用 UTF-8，每个文件达到 5 MB 时轮转，每类最多保留 20 份轮转备份。控制台同步输出中文日志，前端进程的启动与停止信息也为中文。SQL 任务的独立 `subprocess.log` 与该任务目录一同保留，不使用统一日志的轮转规则。

新增运行日志只记录原文字符数、文件/文档标识、对象数量、指标数字和执行状态，不主动输出完整原文、标注、提示词或模型原始返回。API Key、数据库密码、认证令牌在统一日志格式化时脱敏，SQL 子进程输出写盘前也会脱敏；第三方 HTTP 客户端不输出 DEBUG 请求体。

实时查看当天后端日志：

```powershell
$logDate = Get-Date -Format yyyy-MM-dd
Get-Content ".\logs\$logDate.log" -Tail 100 -Wait
```

按任务编号筛选日志：

```powershell
Select-String -Path ".\logs\*.log" -SimpleMatch "任务=<任务ID>"
```

## MCP 控制台

左侧“服务管理 → MCP 控制台”，页面路径 `/evaluation/mcp-console`。可添加、编辑、删除服务，连接/断开服务，发现及刷新工具，查看参数结构，手动提交 JSON 参数调用工具，并按服务查看、清空、复制输出。工具的文本返回若为 JSON，会格式化显示；完整 MCP 响应保留在可展开明细中。页面在连接或调用期间轮询状态，可显示 SSE 进度通知。

服务地址填写完整 MCP 端点，传输方式支持 Streamable HTTP 和旧版 HTTP+SSE；认证支持无需认证、Bearer Token 和 X-API-Key。服务配置保存在 `logs/mcp_services.sqlite`，可用 `MCP_CONSOLE_DB` 指定其他路径。令牌和协议会话只保存在后端进程内，不写入配置数据库、不返回页面、不写入浏览器持久存储；后端重启后保存的服务仍可查看，但认证服务需重新填写令牌。编辑配置会断开旧连接，需要重新连接。

兼容 2025-03-26、2025-06-18、2025-11-25 的初始化和会话式 HTTP，以及 2026-07-28 的无状态 HTTP。自动模式先尝试会话式初始化；服务明确拒绝该初始化时尝试无状态工具发现，也可在编辑框手动选择模式。支持 JSON/SSE 响应、工具分页及参数 HTTP 头注解；不启动本地 stdio 进程，不代替客户端执行采样、授权或其他交互请求。需要额外交互的工具返回按实际结果显示，工具调用不会自动重试。协议依据：[Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[2025 生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。

每个服务同一时间执行一个连接/刷新/调用操作，不同服务分别维护状态。服务请求超时可设为 5～300 秒；单个响应及保存的工具定义限制为 2 MB，最多50个服务、1000个工具、20页工具列表，每个服务保留最近200条输出事件。调用错误显示在输出中；会话过期或连接异常后需要手动重新连接。工具执行错误不伪装为服务断开。

接口前缀 `/api/v1/mcp-console`：

| 方法及路径 | 功能 |
| --- | --- |
| `GET /services` | 获取服务列表及连接状态 |
| `POST /services` | 添加服务 |
| `GET /services/{id}` | 查看工具、输出与最近一次结果 |
| `POST /services/{id}/edit` | 编辑服务配置 |
| `POST /services/{id}/connect` | 建立连接并发现工具 |
| `POST /services/{id}/disconnect` | 关闭会话及连接 |
| `POST /services/{id}/tools/refresh` | 重新获取工具列表 |
| `POST /services/{id}/tools/call` | 手动调用工具，参数为 `name` 和 `arguments` |
| `POST /services/{id}/output/clear` | 清空该服务输出 |
| `POST /services/{id}/delete` | 断开并删除服务 |

### call_mcp.py SSE 服务接入

添加服务时传输方式选择 SSE，地址填脚本的 `MCP_URL`（保留 `/sse`），认证选择 X-API-Key，令牌填脚本的 `API_KEY`，超时设为180秒，协议选择自动协商。连接后选择 `external_knowledge_search`，参数：

```json
{"input_value":"只从ES向量库检索知识图谱相关资料"}
```

旧版 SSE 使用 GET 长连接接收消息，向服务声明的同源 POST 端点发送请求。断开不自动重试工具调用。原有配置自动迁移，默认仍为 Streamable HTTP。协议参考：[HTTP+SSE](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports)。

## 验证

```powershell
python -m pytest -q
python scripts/verify_standalone.py
python start.py --host 127.0.0.1 --backend-port 50311 --frontend-port 50312 --smoke-test
cd frontend
npm test
npx playwright test --config playwright.standalone.config.mjs
```

--smoke-test 使用已有预测结果，临时启动并验证 7 个接口，结束后自动停止。页面回归使用隔离的测试算法；这些检查不代表外部模型当前可用。

当前文件清单及哈希见 COPY_MANIFEST.json。精简前备份位于本目录外的 standalone/evaluation_console_before_metrics_*.zip；删除记录见 verification/scope-cleanup.json。

