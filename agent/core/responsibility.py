"""
core/responsibility.py — The Responsibility: the fundamental unit of the Agent.

    責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則

A Responsibility is *not* a cron job or a script.  It is a persistent contract
between the organisation and the Agent: "You are accountable for keeping X in
state Y; here is how to observe it, fix it, confirm it, and know when to ask."

Responsibility 不是排程工作或腳本，而是組織與 Agent 之間的持續契約：
「你要對維持 X 於 Y 狀態負責；以下定義如何觀測、修復、確認，以及何時求助。」
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from agent.core.models import (
    EscalationReason,
    Gap,
    ObservationResult,
    RiskLevel,
    StopReason,
    TaskRecord,
    Threshold,
)


# ---------------------------------------------------------------------------
# Sensing method (how to observe the world for this responsibility)
# 感測方法（如何為此責任觀測世界狀態）
# ---------------------------------------------------------------------------


@dataclass
class SensingMethod:
    """
    Describes how the Agent collects observations for a Responsibility.
    描述 Agent 如何為某個 Responsibility 收集觀測資料。

    tool_name      – which capability to invoke (e.g. "backup_query", "zabbix")
    tool_name      – 要呼叫的能力名稱（例如 "backup_query", "zabbix"）
    query_params   – static parameters to pass to the tool
    query_params   – 傳給工具的固定參數
    interval       – how often to poll when not event-driven
    interval       – 非事件驅動時的輪詢頻率
    event_triggers – external event types that should wake the Agent early
    event_triggers – 應提前喚醒 Agent 的外部事件類型
    """
    tool_name: str
    query_params: Dict[str, Any] = field(default_factory=dict)
    interval: timedelta = field(default_factory=lambda: timedelta(hours=1))
    event_triggers: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Escalation rule / 升級規則
# ---------------------------------------------------------------------------


@dataclass
class EscalationRule:
    """
    Defines when and how to escalate to a human.
    定義何時以及如何升級給人員處理。

    condition  – callable(task, evidence_list) → bool
    condition  – 可呼叫條件函式 callable(task, evidence_list) → bool
    reason     – why we're escalating (used in the alert message)
    reason     – 升級原因（用於告警訊息）
    priority   – "low" | "normal" | "high" | "urgent"
    priority   – 優先級："low" | "normal" | "high" | "urgent"
    contact    – identifier of the escalation target (e.g. oncall alias)
    contact    – 升級對象識別（例如 oncall 別名）
    """
    reason: EscalationReason
    condition: Callable[[TaskRecord, list], bool]
    priority: str = "normal"
    contact: str = "oncall"


# ---------------------------------------------------------------------------
# Verification condition / 驗證條件
# ---------------------------------------------------------------------------


@dataclass
class VerificationCondition:
    """
    Specifies how to confirm that an action actually resolved the Gap.
    定義如何確認某動作確實解決了 Gap。

    The Agent MUST re-observe from the user / monitoring perspective after
    executing an action — command success alone is not sufficient.
    Agent 在執行動作後必須從使用者/監控視角重新觀測，
    僅有指令成功不足以代表問題已解決。

    tool_name    – the tool used for verification (may differ from the fix tool)
    tool_name    – 驗證使用的工具（可不同於修復工具）
    check_fn     – callable(ObservationResult) → bool; True means verified OK
    check_fn     – callable(ObservationResult) → bool；True 代表驗證通過
    timeout      – how long to wait before declaring verification failed
    timeout      – 宣告驗證失敗前的等待時間
    """
    tool_name: str
    check_fn: Callable[[ObservationResult], bool]
    timeout: timedelta = field(default_factory=lambda: timedelta(minutes=30))


# ---------------------------------------------------------------------------
# Action boundary / 行動界線
# ---------------------------------------------------------------------------


@dataclass
class ActionBoundary:
    """
    Limits what the Agent may do autonomously for this Responsibility.
    限制 Agent 在此 Responsibility 下可自治執行的行為範圍。

    max_autonomous_risk  – highest RiskLevel allowed without human approval
    max_autonomous_risk  – 無需人工核准可接受的最高風險等級
    allowed_tools        – whitelist of tool names (empty = all capability tools)
    allowed_tools        – 工具白名單（空清單代表允許所有可用能力工具）
    forbidden_actions    – explicit list of disallowed action descriptions
    forbidden_actions    – 明確禁止的動作描述清單
    requires_approval_above – escalate for approval if task risk >= this level
    requires_approval_above – 當任務風險 >= 此等級時需升級請求核准
    """
    max_autonomous_risk: RiskLevel = RiskLevel.LOW
    allowed_tools: List[str] = field(default_factory=list)
    forbidden_actions: List[str] = field(default_factory=list)
    requires_approval_above: RiskLevel = RiskLevel.HIGH

    def is_tool_allowed(self, tool_name: str) -> bool:
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return True

    def requires_approval(self, risk: RiskLevel) -> bool:
        order = list(RiskLevel)
        return order.index(risk) >= order.index(self.requires_approval_above)

    def is_autonomous(self, risk: RiskLevel) -> bool:
        order = list(RiskLevel)
        return order.index(risk) <= order.index(self.max_autonomous_risk)


# ---------------------------------------------------------------------------
# Responsibility / 責任定義
# ---------------------------------------------------------------------------


@dataclass
class Responsibility:
    """
    The core unit of the Operational Responsibility Model.
    Operational Responsibility Model 的核心單位。

    Fields map directly to the formula:
        責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則

    scope              – asset IDs or asset-type patterns this applies to
    scope              – 適用的資產 ID 或資產類型樣式
    goal_thresholds    – numeric targets that must be maintained
    goal_thresholds    – 必須維持的數值目標
    sensing_methods    – how to observe the current state
    sensing_methods    – 如何觀測當前狀態
    available_tools    – capability names the Agent may call for this responsibility
    available_tools    – 此責任可呼叫的能力工具名稱
    action_boundary    – reversibility and approval rules
    action_boundary    – 可逆性與核准規則
    verification       – how to confirm a fix actually worked
    verification       – 如何確認修復確實有效
    escalation_rules   – when and how to involve a human
    escalation_rules   – 何時以及如何讓人員介入
    max_retries        – stop-condition: give up after this many failed attempts
    max_retries        – 停止條件：失敗達此次數後放棄
    deadline           – hard stop-condition by wall-clock time
    deadline           – 依實際時間的硬性停止條件
    """
    responsibility_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""

    # 範圍: which assets are in scope
    scope: List[str] = field(default_factory=list)         # asset_ids or type patterns

    # 目標狀態: what "healthy" looks like
    goal_thresholds: List[Threshold] = field(default_factory=list)

    # 感測方法: how to observe
    sensing_methods: List[SensingMethod] = field(default_factory=list)

    # 可用工具: tool names available for remediation
    available_tools: List[str] = field(default_factory=list)

    # 行動界線: autonomy constraints
    action_boundary: ActionBoundary = field(default_factory=ActionBoundary)

    # 驗證條件: post-action verification
    verification_conditions: List[VerificationCondition] = field(default_factory=list)

    # 升級規則: when to call a human
    escalation_rules: List[EscalationRule] = field(default_factory=list)

    # Stop-conditions / 停止條件
    max_retries: int = 3
    deadline: Optional[Any] = None   # datetime, or None for no deadline

    # ---------------------------------------------------------------------------
    # Derived helpers / 衍生輔助方法
    # ---------------------------------------------------------------------------

    def detect_gaps(self, observation: ObservationResult) -> List[Gap]:
        """
        Compare an ObservationResult against goal_thresholds and return
        the list of Gaps discovered.
        將 ObservationResult 與 goal_thresholds 比對，回傳發現的 Gap 清單。

        Gap IDs are deterministic: re-observing the same threshold violation
        always produces the same gap_id, preventing duplicate tasks across ticks.
        Gap ID 為決定性：重複觀測同一門檻違規會得到相同 gap_id，
        可避免跨 tick 產生重複任務。

        This is a pure function: it does NOT modify state.
        此為純函式：不會修改任何狀態。
        """
        gaps: List[Gap] = []
        for threshold in self.goal_thresholds:
            measured = observation.metric_values.get(threshold.metric)
            if measured is None:
                continue
            if threshold.is_violated(measured):
                evidence_ids = [e.evidence_id for e in observation.raw_evidence]
                gap_id = Gap.make_id(self.responsibility_id, threshold.metric)
                gaps.append(Gap(
                    gap_id=gap_id,
                    responsibility_id=self.responsibility_id,
                    description=(
                        f"{threshold.metric} = {measured}{threshold.unit} "
                        f"violates goal {threshold.operator} {threshold.value}{threshold.unit}"
                    ),
                    severity=self._severity_for_threshold(threshold, measured),
                    threshold_violated=threshold,
                    observed_value=measured,
                    supporting_evidence=evidence_ids,
                ))
        return gaps

    @staticmethod
    def _severity_for_threshold(threshold: Threshold, measured: float) -> str:
        """Heuristic: larger deviation → higher severity.
        啟發式規則：偏差越大，嚴重度越高。
        """
        if threshold.value == 0:
            return "high"
        deviation = abs(measured - threshold.value) / abs(threshold.value)
        if deviation >= 0.5:
            return "critical"
        if deviation >= 0.25:
            return "high"
        if deviation >= 0.10:
            return "medium"
        return "low"

    def should_escalate(self, task: TaskRecord, evidence: list) -> Optional[EscalationRule]:
        """Return the first escalation rule whose condition is satisfied, or None.
        回傳第一個符合條件的升級規則；若無則回傳 None。
        """
        for rule in self.escalation_rules:
            try:
                if rule.condition(task, evidence):
                    return rule
            except Exception:
                # A broken escalation condition must never prevent the agent from running
                # 升級條件就算寫壞，也不能阻斷 agent 主流程
                continue
        return None

    def stop_reason_for_task(self, task: TaskRecord) -> Optional[StopReason]:
        """Return a StopReason if the task should be stopped, else None.
        若任務應停止則回傳 StopReason，否則回傳 None。
        """
        if task.retry_count >= self.max_retries:
            return StopReason.NO_NEW_EVIDENCE
        return None
