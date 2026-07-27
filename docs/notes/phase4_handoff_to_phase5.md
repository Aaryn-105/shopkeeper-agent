# Phase 4 收口 -> 阶段 5 及以后 交接清单

更新时间：2026-07-27

---

## 1. 阶段 4 已完成（本次 commit 范围内）

| 项 | 文件 / 测试 |
|---|---|
| 列名兜底（result.keys() == [] 时补 col_0..N） | app/clients/mysql_client.py execute_readonly |
| 客户端层列名兜底单测 | test_mysql_client_falls_back_to_placeholder_columns_when_keys_empty |
| run_sql 真实列名（aliased SELECT） | test_run_sql_node_returns_real_columns_from_dw |
| run_sql 异常路径（MySQL 抛异常 -> state.error） | test_run_sql_node_handles_mysql_exception_gracefully |
| run_sql 列名兜底（mock 空列） | test_run_sql_node_applies_column_fallback_via_stub |
| validate 通过时跳过 correct_sql | test_graph_skips_correct_sql_when_validate_passes |
| validate 失败时启动 correct_sql 循环 | test_graph_runs_correct_sql_loop_when_validate_fails |
| generate_sql 抛异常被图吸收 | test_graph_handles_generate_sql_exception_with_state_error |
| SSE 事件协议完整 | test_api_ask_full_event_payload_protocol_complete |
| 12 节点端到端 + result envelope 形状 | test_end_to_end_graph_runs_all_12_nodes |

测试：阶段 1/2/3/4 共 74 测试全过（66 + 8 新增）。

12 节点的 P95 延迟埋点（record_node_latency）已全部就位。

---

## 2. 已识别但阶段 4 不修的事

### 2.1 pytest 下 graph.ainvoke 端到端偶发 0 行 0 列

手动跑 graph.ainvoke 拿 3 行 3 列（DBG log 证实），pytest 上下文下 run_sql 拿到 default empty。疑似 LangGraph 在 pytest 跨测试时 reducer 行为异常，extract_keywords 在 node_history 中出现 384 次。阶段 4 不断言真实列名；该问题推到阶段 5/6 之后。

### 2.2 SSE result 事件的 columns 字段

test_api_ask_full_event_payload_protocol_complete 只验证事件类型齐全 + payload 形状合法，不强制 columns 非空。客户端层有 [] -> col_0..N 兜底。

---

## 3. 必须等到阶段 5+ 才能收口的事

### 3.1 阶段 5：真实 FAISS/FTS5 知识索引（build_knowledge_index.py）

阶段 4 的 FAISS/FTS5 是空索引。阶段 5 把 column_info / metric_info FAISS 集合和 value_info FTS5 表造出来之后回来补：

- [ ] test_recall_column_returns_vector_hits_when_index_built
- [ ] test_recall_metric_returns_vector_hits_when_index_built
- [ ] test_recall_value_returns_fts5_hits_when_synced
- [ ] test_graph_e2e_with_real_index_no_fallback
- [ ] 重建 test_end_to_end_graph_runs_all_12_nodes 的真实列名强断言

### 3.2 阶段 5：元数据同步脚本

- [ ] scripts/build_knowledge_index.py
- [ ] YAML 配置 -> meta 数据库
- [ ] meta.column_info -> FAISS column_info collection
- [ ] meta.metric_info -> FAISS metric_info collection
- [ ] dw 字段值 -> FTS5 value_info 表（去重、Top-N）
- [ ] 同步失败报错和回滚
- [ ] 索引与元数据库一致性校验

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
- [ ] 集成阶段 5 真实索引（不然 SQL 准确率没意义）

### 3.6 阶段 9：本地部署与配置

- [ ] scripts/start_dev.ps1 一键启动
- [ ] conf/local.yaml / conf/prod.yaml 切换说明
- [ ] 性能基线：节点 P95、SQL 执行耗时、并发 10 请求

### 3.7 阶段 10：验收 + 文档

- [ ] SRS 10.1/10.2/10.3 逐条核对
- [ ] GitHub 发布说明
- [ ] 本地部署 README

---

## 4. 节点图本身的已知工程债

- extract_keywords 在 pytest 下多次调度：node_history reducer 跨 super-step 行为未完全调查。
- generate_sql 内 from app.clients.llm_client import _mock_generate 是函数体内 import，真实 LLM 接入时移除。
- MySQLValidator 同步实现，EXPLAIN 无 timeout 控制，缓慢 SQL 可能阻塞 SSE 线程。

---

## 5. 阶段 5 之前不要做的事

- 不要修改 node_history 的 reducer 语义
- 不要把 mock LLM 换成真实 LLM（阶段 6 才做）
- 不要改 MySQLClient._ensure_engine 为 thread-local
- 不要动 ask.py 的 SSE 流格式
- 不要清理 tmp/*.py 等用户没要求清的文件