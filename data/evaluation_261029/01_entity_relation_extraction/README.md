# 实体与关系抽取：指定分数区间测试样例

gold.json、options.json 和全部 TXT 原文保持不变。predictions.json 基于原预测，在 18 篇文档中人工模拟 20 个实体漏抽，并移除其关联关系，保留原有预测错误。这是用于页面和计分接口的受控测试样例，不代表模型实际准确率。

实体 F1：91.7293%；关系 F1：84.8168%。

页面选择“实体与关系抽取”，真实结果上传 gold.json，预测结果上传 predictions.json，公开运行配置上传 options.json。使用预测 JSON 时只计算指标；重新上传 TXT 调用模型，分数可能不同。

通过真实路由的进程内 multipart 上传、任务执行和结果查询验证，未启动监听服务，未调用大模型，未改动业务代码或计分公式。逐项变更、指标、输入 SHA256 见 verification.json，完整接口结果见 reports/controlled-upload-score.json。

调整前文件保存在 archive-before-controlled-f1-20261030-092735/。
