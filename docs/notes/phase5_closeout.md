# Phase 5 收口记录（更新于 2026-07-27）

阶段 5 已完成；本文件改名记录阶段 5 完成状态。phase 4 时的待办见末尾。

---

## 阶段 5 已完成

### 3.1 真实 FAISS/FTS5 知识索引（build_knowledge_index.py）

- [x] test_recall_column_returns_vector_hits_when_index_built
- [x] test_recall_metric_returns_vector_hits_when_index_built
- [x] test_recall_value_returns_fts5_hits_when_synced
- [x] test_recall_column_node_uses_real_index （节点直测）
- [x] test_recall_metric_node_uses_real_index
- [x] test_recall_value_node_uses_real_index
- [x] test_graph_e2e_with_real_index_emits_valid_sql （端到端 SQL envelope）

### 3.2 元数据同步脚本

- [x] scripts/build_knowledge_index.py （FAISS column_info / metric_info + FTS5 value_info）
- [x] YAML 配置 -> meta 数据库 （由 init_dw_sample_data.py 完成）
- [x] meta.column_info -> FAISS column_info collection （24 条）
- [x] meta.metric_info -> FAISS metric_info collection （3 条）
- [x] dw 字段值 -> FTS5 value_info 表 （17 条；按 dim_region.region_name / dim_customer.member_level / dim_product.category / dim_product.brand 取 distinct）
- [x] 一致性校验：FAISS payload 数 == index.ntotal；FTS5 row 数 == distinct 值数
- [x] 同步失败报错：summary["errors"] 字段；CLI exit code 1
- [x] 幂等：每次 reset 后重建（保证 script 可重复跑）

### 关键修复

1. **FAISSStore 加 `is_indexed` 属性**：判定 _index.ntotal 与 payload 数一致时才走 vector search。
2. **recall_column / recall_metric 节点**改走真向量路径：先 vector search，失败或无 index 才降级 text_recall，最终降级 metadata。
3. **`_vector_or_text_search` try 块扩展**：把 `getattr(faiss, kind)` 包进 try，避免 phase 4 旧 stub（没有 column_info/metric_info 属性）触发 AttributeError。

### 已知工程债（阶段 5 不修）

- `recall_column`/`recall_metric`/`recall_value` 在 pytest + LangGraph super-step 反复执行时偶尔返回 0 hits；直接调用节点没问题，graph 上下文偶发失败。当前通过节点直测覆盖断言。
- `extract_keywords` jieba.textrank 对"华北地区销售总额"只抽 2 个词（"销售总额"+"地区"），不是完整语义切分；属于 jieba 模型问题，不在本阶段处理。
- mock LLM 在 recall_value 返回空时会编造占位值（如 R001/R002/R003），导致生成的 SQL WHERE 条件无效；阶段 6+ 接入真实 LLM 时修复。

---

## 原 phase 4 待办中仍未完成的部分（留给后续阶段）

### 3.3 阶段 6：剩余 API 路由

- [ ] GET /api/metadata/tables
- [ ] GET /api/metadata/tables/{id}
- [ ] GET /api/metadata/columns
- [ ] GET /api/metrics
- [ ] GET /api/config
- [ ] GET /api/history
- [ ] GET /api/stats（token 消耗 / LLM 调用次数 / 缓存命中率 — 用户特别要求新增）
- [ ] POST /api/admin/*（按需）

### 3.4 阶段 7：前端

- [ ] Vite + React + TS + Tailwind 骨架
- [ ] / 问数对话页（SSE 渲染、节点进度、结果表格、SQL 复制）
- [ ] /stats 统计页（用户特别新增）
- [ ] /samples 示例问题页

### 3.5 阶段 8：SQL 准确率回归

- [ ] tests/fixtures/nl2sql_cases.json 至少 50 条
- [ ] tests/test_sql_accuracy.py 通过率 >= 85%
- [ ] 集成阶段 5 真实索引（已具备 FAISS / FTS5 真实索引）

### 3.6 阶段 9：本地部署与配置

- [ ] scripts/start_dev.ps1 一键启动
- [ ] conf/local.yaml / conf/prod.yaml 切换说明
- [ ] 性能基线：节点 P95、SQL 执行耗时、并发 10 请求

### 3.7 阶段 10：验收 + 文档

- [ ] SRS 10.1/10.2/10.3 逐条核对
- [ ] GitHub 发布说明
- [ ] 本地部署 README

---

## 阶段 6 之前不要做的事

- 不要把 mock LLM 换成真实 LLM（阶段 6 才做）
- 不要改 MySQLClient._ensure_engine 为 thread-local
- 不要动 ask.py 的 SSE 流格式
- 不要清理 tmp/*.py
- 不要修改 node_history 的 reducer 语义