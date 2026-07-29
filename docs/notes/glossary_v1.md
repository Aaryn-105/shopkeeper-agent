# Business Glossary Injection (V1.1)

**状态**：已上线 / commit `66f1214` 已推 `origin/main`
**动机**：V1.0 阶段真实 LLM（DeepSeek）拿到用户带"上个月 + 华东"的问题时只生成 `SELECT SUM(order_amount) AS GMV FROM fact_order;`，漏掉日期 / 区域两个维度。原因是 prompt 里没有把这些业务约定的 SQL 写法明示给模型。

---

## 设计决策

### 为什么用结构化 YAML 而不是文档 RAG

对比三种方案：

| 方案 | 优点 | 缺点 | 决策 |
|---|---|---|---|
| **结构化业务词典（本期）** | 确定性、维护简单、prompt 体积可控 | 不支持长文自由问答 | ✅ 优先 |
| FAISS `doc_info` 文档 RAG | 支持 wiki / FAQ 自由文本 | 召回噪声大、注入位置不清 | 暂缓 |
| 把 5 个 SRS 文档直接进 FAISS | 看起来内容多 | 长度长、噪声多，污染检索 | ❌ 明确不做 |

SRS / phase 笔记 / README 这类内容是给人看的，不是给 LLM 用的。当前阶段只需要把"业务约定"显式喂给模型。

### 数据形态：3 类 × 28 条

`conf/glossary.yaml` 三个 top-level key：

```
date_expressions:  14 条  (上个月 / 本月 / 最近7天 / 上季度 / 年初至今 ...)
regions:            7 条  (华东 / 华北 / 华南 / 华中 / 西南 / 西北 / 东北 + provinces)
metric_formulas:    7 条  (GMV / ORDER_CNT / AOV / QTY / UV / PAY_CNT / PAY_RATE)
```

每条目结构：
- `key`：主名（用户最常说的叫法）
- `aliases`：同义词 / 别名列表，命中任意一个即触发渲染
- 字段（按类别不同）：
  - date → `expression` (SQL 片段) + 可选 `note`
  - region → `region_name` (大区名) + `provinces` (省列表)
  - metric → `sql` (聚合表达式) + `description` (业务说明)

### 渲染逻辑：规则匹配 + 懒加载

`app/prompt/glossary_injection.py`：

- `load_glossary()` 用 `@lru_cache(maxsize=1)` 缓存，文件读一次
- `_match_entries(query, entries)` 对每个 entry 检查 `key` + 全部 `aliases` 是否在 query 里（子串匹配，大小写敏感，因为中文不需要折叠，英文别名只有 GMV / QTY / AOV / YTD 等固定写法）
- `render_glossary_for_query(query)` 只渲染命中类别，每类一段 markdown。无命中返回空串，prompt 那段自动消失
- `matched_categories(query)` 返回 `["date", "region", "metric"]` 之一或组合，供日志 / metrics 用

性能：纯字典查找 + 子串扫描，<1ms / query，不进 LLM。

### Prompt 注入点

`app/prompt/generate_sql.prompt` 新增：

```
9. 当下方「业务词典」给出某条目的 SQL 片段时，必须采用对应的 SQL 写法（日期范围、JOIN 维度、指标公式）。

# 业务词典（仅相关条目；空表示无匹配）
{glossary_block}
```

模板渲染时机：`generate_sql` 节点构造 prompt 时，调用 `render_glossary_for_query(query)` 填入 `{glossary_block}` 占位。

### 日志埋点

`generate_sql` 节点在命中时打一条结构化日志（loguru → `app_YYYY-MM-DD.log`）：

```
node generate_sql glossary: categories=date,region,metric block_chars=412
```

无命中不打（避免噪声）。

---

## 验证

### 单元测试

```
tests/test_glossary.py   28 passed   (0.18s)
```

覆盖：
- 28 条基线条目都在
- 三类组合查询都正确触发对应 section
- 别名解析（江浙沪 → 华东、京津冀 → 华北、客单价 → AOV、独立用户 → UV 等）
- `reload_glossary()` 强制重读
- `matched_categories()` 顺序固定

### 真实接口回归

通过 **Python urllib** 显式 UTF-8 编码发送（PowerShell `Invoke-WebRequest` 默认 cp1252 会把中文替换为 `?`，详见"已知坑"）。

```
query: 上个月华东地区的 GMV 是多少？
sql:
  SELECT SUM(f.order_amount) AS GMV
  FROM fact_order f
  JOIN dim_region r ON f.region_id = r.region_id
  WHERE r.region_name = '华东'
    AND DATE_FORMAT(STR_TO_DATE(CAST(f.date_id AS CHAR), '%Y%m%d'), '%Y-%m') =
        DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m');
```

DeepSeek 在有 glossary 时：自动补上 JOIN dim_region + 月份 DATE_FORMAT 子句 + 华东 region_name 过滤。
无 glossary 时（早期）：只生成 `SELECT SUM(order_amount) AS GMV FROM fact_order;`，漏全。

### 阶段回归

```
tests/test_phase6_v1_9.py  50 passed
tests/test_glossary.py     28 passed
tests/test_phase4.py       36 passed
ruff check                 全清
```

---

## 已知坑

### PowerShell 客户端 cp1252 编码

```powershell
$body = @{ query = '上个月华东' } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri http://127.0.0.1:8001/api/ask -Body $body
#  ↑ 后端收到的是 '????????'，17 字节全 ASCII
```

`Invoke-WebRequest -Body <String>` 默认按 Windows ANSI 编码发送，非 ASCII 字符会被替换为 `?`。**不影响前端浏览器请求**（浏览器天然 UTF-8），但用 PowerShell 调试时会误导排查方向。

**正确写法**：
```powershell
$body = @{ query = '上个月华东' } | ConvertTo-Json -Compress
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
Invoke-WebRequest ... -Body $bytes
```

或直接用 Python urllib（本期已用此方法做验证）。

---

## 维护指南

修改任何业务约定（日期表达 / 区域 / 指标公式）：

1. 编辑 `conf/glossary.yaml`
2. 跑 `uv run pytest tests/test_glossary.py -q` 验证基线
3. 若新增类别或关键条目，在 `tests/test_glossary.py::test_glossary_has_baseline_entries` 加断言
4. 跑真实接口抽样验证

词典文件是项目内**唯一事实源**。prompt / mock LLM / 真实 LLM 都不应硬编码业务日期写法或省份列表。
