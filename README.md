# Pattern-DevOP-AI-agent

> **可執行的維運責任模型** — An Operational Responsibility Model for a Persistent DevOps AI Agent

---

## 核心概念 Core Concept

這個 Agent 不是「等待人類交付一包工作的腳本執行器」，而是一個**持續處在公司運作環境中**的責任承擔者：依時間、事件與未完成承諾自行醒來，觀察狀態、形成任務、調用工具、驗證結果，再決定結案、重試或升級給人。

**起點是「責任範圍與當前世界狀態」，不是資料夾。**

---

## Agent 控制迴圈 Control Loop

```
觀察環境 → 發現差距 → 建立任務 → 規劃處置
    ↑                              ↓
更新世界模型 ← 驗證結果 ← 執行工具 ← 權限判斷
```

---

## 五種持久化狀態 Five Persistent States

| 狀態              | 模組                                | Agent 必須知道什麼                                 |
|-----------------|-----------------------------------|----------------------------------------------|
| **世界狀態** WorldState  | `agent/state/world_state.py`      | 有哪些據點、設備、服務、帳號與相依關係，目前是否正常                 |
| **目標狀態** GoalState   | `agent/state/goal_state.py`       | 備份成功率、修補期限、容量門檻、服務時段、稽核要求                  |
| **任務狀態** TaskState   | `agent/state/task_state.py`       | 哪些問題正在處理、等待什麼、何時重試、誰曾核准                    |
| **證據狀態** EvidenceState | `agent/state/evidence_state.py` | 觀察來源、時間、查詢結果、執行前後差異（觀察/推論/決策三欄分離）         |
| **能力狀態** CapabilityState | `agent/state/capability_state.py` | 可以使用哪些工具、作用範圍、風險級別與權限期限              |

---

## 核心單位：責任 The Responsibility

責任是 Agent 的基本工作單元，而非 Prompt 或排程指令。

```
責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則
```

```python
from agent.core.responsibility import (
    Responsibility, SensingMethod, ActionBoundary,
    VerificationCondition, EscalationRule
)
from agent.core.models import Threshold, RiskLevel, EscalationReason

responsibility = Responsibility(
    name="確保所有主管電腦完成備份",
    scope=["executive_endpoints"],
    goal_thresholds=[
        Threshold(metric="backup_success_rate", operator=">=", value=0.95),
    ],
    sensing_methods=[SensingMethod(tool_name="backup_query")],
    available_tools=["backup_restart"],
    action_boundary=ActionBoundary(
        max_autonomous_risk=RiskLevel.LOW,
        requires_approval_above=RiskLevel.HIGH,
    ),
    verification_conditions=[
        VerificationCondition(
            tool_name="verify_backup",
            check_fn=lambda obs: obs.metric_values.get("backup_success_rate", 0) >= 0.95,
        )
    ],
    escalation_rules=[
        EscalationRule(
            reason=EscalationReason.REPEATED_FAILURE,
            condition=lambda task, ev: task.retry_count >= 3,
            priority="high",
            contact="oncall",
        )
    ],
    max_retries=3,
)
```

---

## 專案結構 Project Structure

```
agent/
├── core/
│   ├── models.py           # 資料模型：TaskRecord, EvidenceEntry, Gap, Threshold, …
│   ├── responsibility.py   # Responsibility, ActionBoundary, EscalationRule, …
│   └── agent_loop.py       # 主控制迴圈 AgentLoop
├── state/
│   ├── world_state.py      # 世界狀態
│   ├── goal_state.py       # 目標狀態
│   ├── task_state.py       # 任務狀態
│   ├── evidence_state.py   # 證據狀態（觀察/推論/決策三欄分離）
│   └── capability_state.py # 能力狀態
├── tools/
│   └── base_tool.py        # BaseTool 介面（風險分級、dry_run）
└── escalation/
    └── escalation_manager.py  # 升級管理（人工介入）
tests/
├── test_models.py
├── test_responsibility.py
├── test_state_management.py
└── test_agent_loop.py
```

---

## 設計約束 Design Constraints

These constraints prevent the Agent from drifting, self-amplifying errors, or
acting beyond its mandate:

1. **觀察與推論分離** — `EvidenceKind.OBSERVATION` (tool facts), `.INFERENCE`
   (agent reasoning), `.DECISION` (human approvals) are stored in separate
   buckets and never merged.

2. **命令成功 ≠ 任務完成** — After every action the Agent re-observes via
   `VerificationCondition` before closing a task.

3. **沒有新證據就不能無限推理** — `max_retries` on every Responsibility enforces
   a hard stop when there is no change in evidence.

4. **可逆性決定自治權** — `RiskLevel` + `ActionBoundary` determine whether the
   Agent acts autonomously, requests approval, or escalates immediately.

5. **所有責任都有停止條件** — Tasks reach `RESOLVED`, `FAILED`, `TIMED_OUT`, or
   `ESCALATED`; none can remain `IN_PROGRESS` forever.

6. **Agent 不能自行擴張權限** — `CapabilityState` is controlled externally;
   the Agent can request new capabilities but cannot grant them to itself.

---

## 工具介面 Tool Interface

All integrations (Zabbix, NAS, AD, SQL, firewall API, backup agent, …) implement
the same `BaseTool` interface.  The Agent treats them all equally; none is
privileged.

```python
from agent.tools.base_tool import BaseTool, ToolResult
from agent.core.models import RiskLevel

class BackupQueryTool(BaseTool):
    @property
    def name(self) -> str:
        return "backup_query"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.READ_ONLY

    @property
    def description(self) -> str:
        return "Query backup system for endpoint success rates"

    def execute(self, params):
        # ... call your real API here ...
        return ToolResult(success=True, data={"backup_success_rate": 0.98})
```

---

## 人與 Agent 的分工 Human–Agent Division of Labour

| Agent 主責                          | 人類主責                    |
|-------------------------------------|-------------------------|
| 持續巡查、交叉比對、重試與追蹤                    | 決定公司願意承受什麼風險              |
| 已知故障分類與標準 Runbook                  | 處理未知情境及制度衝突               |
| 保持台帳、證據和時間線完整                      | 與主管、使用者、供應商談判             |
| 在授權範圍內恢復預期狀態                       | 核准不可逆、跨邊界或高衝擊行動           |
| 找出重複事件與長期趨勢                        | 修改目標、政策與責任範圍              |

Agent 把人類注意力視為昂貴資源：只在「無法判斷、缺少權限、風險超標、目標互相衝突」時才升級。

---

## 安裝與測試 Installation & Tests

```bash
pip install -e ".[dev]"
pytest
```
