# 页面评测的稳定采样

/api/v1/evaluation-runs/* 的实时推理使用 temperature=0、top_p=1、seed=42、frequency_penalty=0、presence_penalty=0，关闭 thinking。抽取分块和消融 LLM 批次按评测策略串行处理。

覆盖源名称抽取、legacy 抽取、分类、时空标准化、消融候选判断及必要的翻译。实际策略保存在图谱 processing.sampling 或 sampling。

相同原文、标注、配置、提示词和模型版本有助于复现。随机实体 ID、任务时间以及模型服务侧 GPU 或批次调度仍可能不同，固定参数不能保证文本预测绝对一致。

上传同一组 predictions.json 与 gold.json 只重新评分，不请求模型，指标按相同规则确定计算。

配套数据见 data/evaluation_261029 和 data/evaluation_predictions。接口验证：

```powershell
python scripts/verify_standalone.py
python start.py --host 127.0.0.1 --backend-port 50311 --frontend-port 50312 --smoke-test
```

实时推理仍需当前配置的模型服务；多标签分类 TXT 模式还需标签数据库。开发数据只证明对应样本的结果，不能作为新文本效果保证。
