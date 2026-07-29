# Shopkeeper Agent (电商问数智能体)

> **V1.0 本地版** — 完整交付。SRS 验收 37/37 (100%)、NL2SQL 准确率 51/51、pytest 644 个用例全绿、pylint 10.00/10。

电商 NL2SQL 分析智能体。基于 12 节点 LangGraph 工作流，将自然语言问题转换为可执行的 MySQL 查询，并通过 SSE 流式返回执行结果。

适用场景：电商业务方的自助式数据分析（区域销售、品类对比、会员画像、时间趋势、占比分析等）。

---

## 1. 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.13 + FastAPI + SQLAlchemy 2.0 + aiomysql |
| 工作流 | LangGraph 1.2.4（StateGraph + 并行 super-step） |
| 数据库 | MySQL 8.0（meta 库 + dw 库） |
| 向量索引 | FAISS（IndexFlatIP，512d，本地替代 Qdrant） |
| 倒排索引 | SQLite FTS5（本地替代 Elasticsearch） |
| Embedding | sentence-transformers + bge-small-zh-v1.5 |
| LLM | OpenAI 兼容协议（DeepSeek / Qwen / GPT-4o 等），缺省走 mock |
| 前端 | Vite 5 + React 18 + TypeScript 5 + Tailwind 3 |
| 日志 | loguru + X-Request-ID + metrics.jsonl 时序指标 |
| 配置 | OmegaConf 分层（default/local/prod）+ python-dotenv |
| 测试 | pytest + pytest-asyncio + httpx |

---

## 2. 前置条件

### 2.1 系统依赖

| 工具 | 版本 | 安装方式 |
| --- | --- | --- |
| Python | 3.13+ | `conda install python=3.13` 或 python.org |
| uv | 0.4+ | `pip install uv` |
| Node.js | 18+ | nodejs.org |
| pnpm | 8+ | `npm install -g pnpm` |
| MySQL | 8.0+ | mysql.com 或 Docker |
| Git | 2.40+ | git-scm.com |

### 2.2 模型依赖

| 模型 | 路径 | 说明 |
| --- | --- | --- |
| bge-small-zh-v1.5 | `D:\Quantum Technology Training\智能体\bge-st` | sentence-transformers 加载的中文 embedding |

如路径不一致，修改 `.env` 中 `BGE_MODEL_PATH`。

---

## 3. 部署步骤

### 3.1 克隆代码

```bash
cd D:\新建文件夹
git clone https://github.com/Aaryn-105/shopkeeper-agent.git
cd shopkeeper-agent
```

### 3.2 后端启动

#### 3.2.1 安装依赖

```powershell
# 后端依赖（uv 会自动管理 .venv）
uv sync
```

#### 3.2.2 配置环境变量

```powershell
# 复制模板
Copy-Item .env.example .env

# 编辑 .env（用记事本或 VS Code）
notepad .env
```

必填项：

- `MYSQL_ADMIN_PASSWORD`：MySQL root 密码
- 其他保持默认即可

#### 3.2.3 初始化数据库

```powershell
# 1. 创建 meta 库（含 column_info / metric_info / table_info / value_info）+ dw 库（含 fact_order 1000 行 + 5 维度表）
uv run python scripts/init_meta_mysql.py

# 2. 加载 dw 库种子数据
uv run python scripts/init_dw_sample_data.py

# 3. 构建 FAISS + FTS5 知识索引（meta 库 → FAISS column_info / metric_info；dw 库 distinct 值 → FTS5 value_info）
uv run python scripts/build_knowledge_index.py
```

#### 3.2.4 启动 API 服务

```powershell
# 方式 A：直接启动
uv run uvicorn main:app --reload --port 8000

# 方式 B：一键脚本（隐藏窗口）
.\scripts\start_dev.ps1
```

访问 `http://127.0.0.1:8000/docs` 查看 OpenAPI 文档。

#### 3.2.5 验证

```powershell
curl http://127.0.0.1:8000/api/health
# 期望: {"status":"healthy","services":{...}}
```

### 3.3 前端启动

```powershell
cd frontend
pnpm install
pnpm dev
```

访问 `http://127.0.0.1:5173`。

---

## 4. 路由

### 4.1 前端页面

| 路径 | 说明 |
| --- | --- |
| `/` | 主页（问数工作台）：输入问题 → SSE 流式进度 → 结果表格 + SQL |
| `/stats` | 统计页：token 消耗 / LLM 调用次数 / 缓存命中率 + 时序图 |
| `/samples` | 样例问题页：覆盖 SRS 3.2 所有典型场景 |

### 4.2 后端 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/ask` | 问数入口（SSE 流式响应） |
| GET | `/api/health` | 服务健康检查 |
| GET | `/api/config` | 动态配置 |
| GET | `/api/metadata/tables` | 所有表列表 |
| GET | `/api/metadata/tables/{id}` | 表详情 |
| GET | `/api/metadata/columns` | 所有字段列表 |
| GET | `/api/metrics` | 所有指标列表 |
| GET | `/api/stats` | 统计快照 |
| GET | `/api/stats/timeseries` | 时序统计 |
| GET | `/api/history` | 历史问答列表 |
| GET | `/api/history/{id}` | 单条问答详情 |

OpenAPI 文档：`http://127.0.0.1:8000/docs`

---

## 5. 配置说明

### 5.1 .env

```ini
APP_ENV=local                          # local / prod

# MySQL
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_ADMIN_USER=root
MYSQL_ADMIN_PASSWORD=                  # 必填
MYSQL_RO_USER=readonly
MYSQL_RO_PASSWORD=readonly123          # 初始化脚本自动设置
MYSQL_META_DB=meta
MYSQL_DW_DB=dw

# Embedding
BGE_MODEL_PATH=D:\Quantum Technology Training\智能体\bge-st
EMBEDDING_DIM=512

# 索引
FAISS_INDEX_DIR=./data/faiss
FTS5_DB_PATH=./data/fts5/fulltext.db

# LLM（三件套留空则走 mock）
LLM_API_BASE=
LLM_API_KEY=
LLM_MODEL=

# 缓存
CACHE_TTL_SECONDS=3600

# CORS（dev 留 *，prod 收紧）
CORS_ALLOW_ORIGINS=*

# 日志
LOG_DIR=./logs
LOG_LEVEL=INFO
```

### 5.2 conf/default.yaml

OmegaConf 默认值，分层覆盖 `local.yaml` / `prod.yaml`。

### 5.3 LLM 接入

mock 模式：缺省行为，不需配置。

真实 LLM 模式：在 `.env` 中填：

```ini
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=sk-xxxx
LLM_MODEL=deepseek-chat
```

兼容所有 OpenAI ChatCompletion 协议的端点（DeepSeek / 阿里云 Qwen / OpenAI / Azure OpenAI 等）。

---

## 6. 项目结构

```
shopkeeper-agent/
├── main.py                        # FastAPI 入口
├── app/
│   ├── agent/                     # LangGraph 工作流
│   │   ├── graph.py               # StateGraph 装配
│   │   ├── state.py               # AgentState 类型
│   │   └── nodes/                 # 12 个节点（按 SRS 6.x）
│   ├── api/routes/                # HTTP endpoints
│   ├── clients/                   # MySQL / FAISS / FTS5 / BGE / LLM / Cache
│   ├── services/                  # AskService（直接驱动）
│   ├── core/                      # config / logger / metrics / lifespan
│   ├── models/ + entities/        # Pydantic + ORM
│   ├── repositories/              # meta_repo / dw_repo
│   └── prompt/                    # LLM 提示词模板
├── conf/                          # OmegaConf YAML
├── scripts/                       # 初始化与索引构建
├── frontend/                      # Vite + React + TS
├── tests/                         # 644 个 pytest 用例
│   ├── fixtures/nl2sql_cases.json # 51 条 NL2SQL 用例
│   └── test_phase*.py             # 阶段化测试
├── docs/
│   ├── requirements/              # 原始需求文档（5 份）
│   ├── notes/                     # 阶段 closeout + 验收清单
│   └── RELEASE_NOTES.md           # V1.0 发布说明
├── data/                          # FAISS 索引 + FTS5 DB（gitignore）
├── logs/                          # 日志 + metrics.jsonl（gitignore）
└── tmp/                           # 调试脚本（gitignore）
```

---

## 7. 测试

```powershell
# 全套测试（按目录分批跑避免超时）
$env:PYTHONPATH = "."
uv run pytest tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py tests/test_phase5.py tests/test_phase8.py tests/test_phase9_accuracy.py -v

# phase6 节点测试（~10 分钟）
uv run pytest tests/test_phase6_1.py tests/test_phase6_2.py tests/test_phase6_3.py tests/test_phase6_4.py

# phase6_v1 SRS 对齐测试
uv run pytest tests/test_phase6_v1_1.py ... tests/test_phase6_v1_13.py
```

### 7.1 代码风格

```powershell
uv run ruff format .
uv run ruff check .
uv run pylint app/clients/llm_client.py app/services/ask_service.py tests/test_phase9_accuracy.py
```

### 7.2 启动验证

```powershell
# 后端
curl http://127.0.0.1:8000/api/health

# 一次问数
curl -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" -d '{"query":"华东 GMV"}'
```

---

## 8. 验收状态

| 维度 | 数值 | 详见 |
| --- | --- | --- |
| SRS 验收（P0/P1/P2） | 37/37 | `docs/notes/phase10_acceptance.md` |
| NL2SQL 准确率 | 51/51 (100%) | `tests/test_phase9_accuracy.py` |
| pytest 用例 | 644 全绿 | `tests/` |
| pylint 评分 | 10.00/10 | `llm_client.py` / `ask_service.py` / `test_phase9_accuracy.py` |
| 端到端 P95 延迟 | 0.7s（mock LLM） | `docs/notes/phase10_performance.md` |
| 5 并发成功率 | 100% | 同上 |

---

## 9. 常见问题

### 9.1 MySQL 连不上

- 确认 `.env` 中 `MYSQL_ADMIN_PASSWORD` 正确
- 确认 MySQL 服务在跑（`Get-Service MySQL`）
- 确认端口 3306 可达

### 9.2 BGE 模型加载失败

- 检查 `BGE_MODEL_PATH` 是否指向正确目录
- 目录中应有 `config.json`、`pytorch_model.bin` 或 `model.safetensors` 等文件
- 首次加载会下载约 100MB 依赖（transformers / torch），需要网络

### 9.3 LLM 调用失败但 server 正常

- 检查 `.env` 中 LLM 三件套是否填写
- mock 模式下所有 LLM 调用由规则引擎处理，不会失败

### 9.4 测试时 mock LLM 覆盖率不足

- 51 条 fixture 用例覆盖：区域 / 会员 / 品类 / 品牌 / 性别 / 时间 / Top-N / 占比
- 新增用例可在 `tests/fixtures/nl2sql_cases.json` 末尾追加

---

## 10. 链接

- [需求文档（5 份 Markdown）](./docs/requirements/)
- [阶段交付笔记](./docs/notes/)
- [V1.0 发布说明](./docs/RELEASE_NOTES.md)
- [SRS 验收清单](./docs/notes/phase10_acceptance.md)
- [性能基线](./docs/notes/phase10_performance.md)
- [贡献指南](./AGENTS.md)
- [GitHub 仓库](https://github.com/Aaryn-105/shopkeeper-agent)

---

**版本**：V1.0.0  
**更新日期**：2026-07-29
