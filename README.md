# 指标评测独立项目

本目录只保留 7 个指标模块及它们需要的前端、评分、抽取、融合、分类和社区算法。原项目中的代码不受修改影响。

| 模块 | 指标 |
| --- | --- |
| 实体与关系抽取 | 实体、关系 Precision / Recall / F1 |
| 跨文档融合 | 实体、关系、属性消融 F1 和均值 |
| 跨语种融合 | 实体、关系、属性消融 F1 和均值 |
| 专业领域抽取 | 领域配置引入前后的实体、关系 F1 和差值 |
| 多粒度时空消歧 | 总体及各粒度准确率 |
| 多标签分类 | Micro / Macro / 各标签 F1 |
| 社区发现 | 标签最优匹配准确率 |

保留 TXT 实时抽取、预测 JSON 直接评分、人工标注与配置上传、计算明细、下载结果，以及任务查询、取消和清理。

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
frontend/              7 个指标模块页面、组件、代理和构建资源
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

仅提供 `/api/v1/evaluation/*`、`/api/v1/evaluation-runs/*`、`/health`、`/ready` 和 FastAPI 文档。旧业务提示词与独立抽取 HTTP 接口、业务图谱任务、MQ、Redis 缓冲、关键词抽取、MinIO、Nebula 已移除。

指标需要的提示词和模型辅助函数位于 `tools/extraction_prompt.py` 和 `tools/extraction_model.py`，不再注册为业务接口。数据库客户端只保留连接池、标签读取和模型配置读取。

## 配置和安装

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