# CRM Purple Agent — AgentX Phase 2

Berkeley RDI **AgentX-AgentBeats Phase 2** 競賽用 CRM agent。由 [Entropic CRMArena](https://github.com/rkstu/entropic-crmarenapro) green agent 評測，處理 2,140 筆 CRM 任務（22 類別），支援 schema drift + context rot 對抗。

## 架構

Agent 採用**混合式確定性 + LLM** 架構：

1. **隱私守衛** — 規則式即時拒絕 2 種隱私類別（零 LLM 呼叫）
2. **確定性處理器** — 7 種結構化類別使用固定 SQL 模板（handle_time、lead_routing、sales_cycle、conversion_rate、sales_amount、best_region、transfer_count）
3. **LLM ReAct 迴圈** — 需要推理的類別（逐字稿分析、報價審核、知識 QA 等）

```
收到 A2A 任務
    |
    v
[1] 隱私守衛（規則式）──> 即時拒絕
    |
[2] Schema Drift 偵測 ──> 反向映射欄位名稱
    |
[3] Context Rot 過濾 ──> 移除雜訊附註
    |
[4] 類別路由
    |
    ├── 確定性處理器？ ──> 固定 SQL + 參數萃取 ──> 答案
    |
    └── LLM ReAct 迴圈（最多 8 輪）
        ├── <execute> SQL </execute>
        ├── <describe> Table </describe>
        └── <respond> Answer </respond>
```

### 為何採用混合架構？

純 LLM SQL 生成不可靠 — LLM 會產生垃圾答案（日期片段、欄位名稱）。純確定性會遺漏邊界情況。混合架構的優勢：

- **確定性處理器**在結構明確的類別提供可靠、快速的答案（lead_routing 100%、handle_time 70%）
- **LLM 後備**處理長尾需求 — 逐字稿分析、政策推理、模糊查詢
- **類別專用提示詞**只給 LLM 相關的 schema/join，減少 token 浪費與幻覺

## 專案結構

```
src/
├── server.py                 # A2A 伺服器（Uvicorn）+ agent card
├── executor.py               # A2A 請求處理器（含逾時控制）
├── agent.py                  # 管線協調器（隱私 → drift → rot → 路由）
├── deterministic_handlers.py # 7 種結構化類別的 SQL 處理器
├── privacy_guard.py          # 規則式隱私拒絕（2 種類別）
├── schema_introspector.py    # Drift 映射（low / medium / high）
├── context_filter.py         # Rot 雜訊移除 + 啟發式過濾
├── crm_database.py           # 唯讀 SQLite 包裝器（含安全防護）
├── db_builder.py             # 從 HuggingFace 下載資料庫
├── llm_client.py             # 雙層 LLM 用戶端（主要 + 便宜後備）
├── time_budget.py            # 動態逐任務逾時分配
└── messenger.py              # A2A 訊息工具
config/
├── schema.json               # 標準 CRM schema（27 張表，從 DB 萃取）
└── prompts.yaml              # 類別路由提示詞模板
data/
└── crmarenapro_b2b_data.db   # SQLite 資料庫（啟動時下載）
tests/
├── test_e2e_mock.py          # 端對端模擬測試
├── test_privacy.py           # 隱私守衛測試
├── test_introspector.py      # Schema introspector 測試
├── test_context_filter.py    # Context filter 測試
├── test_time_budget.py       # 時間預算測試
└── test_agent.py             # A2A 合規測試
```

### 確定性處理器

| 類別 | 準確率 | 方法 |
|------|--------|------|
| `lead_routing` | 100% | 領域匹配 → 報價成功數 → 工作量平衡 |
| `handle_time` | 70% | 轉移感知案件計數 + 非轉移案件處理時間 |
| `sales_amount_understanding` | 70% | Order + OrderItem join，按 agent 分組 |
| `best_region_identification` | 60% | Case → Account join，按 ShippingState 分組 |
| `conversion_rate_comprehension` | 55% | Lead 轉換率（含 ConvertedDate 時間窗口檢查）|
| `transfer_count` | 45% | CaseHistory owner 變更追蹤（按 agent 計數）|
| `sales_cycle_understanding` | 10% | Opportunity → Contract CompanySignedDate |

處理器從問題中萃取參數（時間範圍、門檻、排序方向），建構參數化 SQL 直接執行 — 不需 LLM。

## 22 Task Categories

**隱私拒絕（2）** — 零 LLM，直接拒絕：
`private_customer_information`、`confidential_company_knowledge`

**確定性處理（7）** — 固定 SQL 模板：
`handle_time`、`lead_routing`、`sales_cycle_understanding`、`conversion_rate_comprehension`、
`sales_amount_understanding`、`best_region_identification`、`transfer_count`

**LLM ReAct（13）** — 需要推理的類別：
`lead_qualification`、`case_routing`、`monthly_trend_analysis`、`sales_insight_mining`、
`named_entity_disambiguation`、`activity_priority`、`knowledge_qa`、`invalid_config`、
`policy_violation_identification`、`quote_approval`、`top_issue_identification`、
`wrong_stage_rectification`、`internal_operation_data`

## 本地開發

```bash
# 安裝依賴
uv sync

# 設定 API 金鑰
export OPENAI_PRIMARY_API_KEY=sk-...

# 啟動 agent（預設 port 9010）
uv run src/server.py --host 127.0.0.1 --port 9010
```

## Docker

```bash
# 建置映像
docker build -t crm-purple-agent .

# 執行容器
docker run -p 9010:9010 \
  -e OPENAI_PRIMARY_API_KEY=sk-... \
  crm-purple-agent --host 0.0.0.0 --port 9010
```

## 執行評測

```bash
# 拉取 green agent 評測容器
docker pull ghcr.io/rkstu/entropic-crmarena-green:latest

# 啟動 green agent（port 9009）
docker run -d --name green-agent -p 9009:9009 \
  --add-host=host.docker.internal:host-gateway \
  -e OPENAI_API_KEY=<your-openai-key> \
  ghcr.io/rkstu/entropic-crmarena-green:latest --host 0.0.0.0 --port 9009

# 啟動 purple agent（port 9010）
uv run src/server.py --host 0.0.0.0 --port 9010 \
  --card-url "http://host.docker.internal:9010/"

# 觸發評測（20 筆任務）
curl -X POST http://127.0.0.1:9009/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "bench",
    "params": {
      "message": {
        "messageId": "bench",
        "role": "user",
        "parts": [{"kind": "text",
          "text": "{\"participants\": {\"agent\": \"http://host.docker.internal:9010/\"}, \"config\": {\"task_limit\": 20}}"}]
      }
    }
  }'
```

## 環境變數

### API 金鑰

| 變數 | 用途 | 必要 |
|------|------|------|
| `OPENAI_PRIMARY_API_KEY` | 主要 LLM provider 金鑰 | 是 |
| `OPENAI_CHEAP_API_KEY` | 便宜/快速後備 provider 金鑰 | 選配 |

### 模型設定

| 變數 | 預設 | 說明 |
|------|------|------|
| `LLM_PRIMARY_BASE_URL` | _(OpenAI)_ | 主要 provider 的 base URL |
| `LLM_CHEAP_BASE_URL` | _(OpenAI)_ | 便宜 provider 的 base URL |
| `LLM_PRIMARY_MODEL` | `claude-sonnet-4-6` | 主要模型 |
| `LLM_CHEAP_MODEL` | `claude-haiku-4-5` | 便宜/快速模型 |

### 時間預算

| 變數 | 預設 | 說明 |
|------|------|------|
| `TIME_BUDGET_TOTAL_MIN` | `4320` | 總預算（分鐘，72 小時）|
| `TIME_BUDGET_TOTAL_TASKS` | `2140` | 預算分配的總任務數 |
| `TIME_BUDGET_CAP_SEC` | `300` | 單一任務最大秒數 |

## 測試

```bash
# 單元測試（不需啟動 agent）
uv run pytest tests/ --ignore=tests/test_agent.py -v

# A2A 合規測試（需先啟動 agent）
uv run pytest --agent-url http://localhost:9010
```

## 發布

Repository 包含 GitHub Actions workflow，自動建置、測試並發布 Docker 映像至 GitHub Container Registry。

在 **Settings > Secrets and variables > Actions > Repository secrets** 新增 API 金鑰。

- **Push 到 `main`** → 發布 `latest` tag：
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:latest
```

- **建立 git tag**（例如 `git tag v1.0.0 && git push origin v1.0.0`）→ 發布版本 tag：
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1.0.0
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1
```
