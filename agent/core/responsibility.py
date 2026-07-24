"""
core/responsibility.py — The Responsibility: the fundamental unit of the Agent.

    責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則

A Responsibility is *not* a cron job or a script.  It is a persistent contract
between the organisation and the Agent: "You are accountable for keeping X in
state Y; here is how to observe it, fix it, confirm it, and know when to ask."
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
# ---------------------------------------------------------------------------


@dataclass
class SensingMethod:
    """
    Describes how the Agent collects observations for a Responsibility.

    tool_name      – which capability to invoke (e.g. "backup_query", "zabbix")
    query_params   – static parameters to pass to the tool
    interval       – how often to poll when not event-driven
    event_triggers – external event types that should wake the Agent early
    """
    tool_name: str
    query_params: Dict[str, Any] = field(default_factory=dict)
    interval: timedelta = field(default_factory=lambda: timedelta(hours=1))
    event_triggers: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Escalation rule
# ---------------------------------------------------------------------------


@dataclass
class EscalationRule:
    """
    Defines when and how to escalate to a human.

    condition  – callable(task, evidence_list) → bool
    reason     – why we're escalating (used in the alert message)
    priority   – "low" | "normal" | "high" | "urgent"
    contact    – identifier of the escalation target (e.g. oncall alias)
    """
    reason: EscalationReason
    condition: Callable[[TaskRecord, list], bool]
    priority: str = "normal"
    contact: str = "oncall"


# ---------------------------------------------------------------------------
# Verification condition
# ---------------------------------------------------------------------------


@dataclass
class VerificationCondition:
    """
    Specifies how to confirm that an action actually resolved the Gap.

    The Agent MUST re-observe from the user / monitoring perspective after
    executing an action — command success alone is not sufficient.

    tool_name    – the tool used for verification (may differ from the fix tool)
    check_fn     – callable(ObservationResult) → bool; True means verified OK
    timeout      – how long to wait before declaring verification failed
    """
    tool_name: str
    check_fn: Callable[[ObservationResult], bool]
    timeout: timedelta = field(default_factory=lambda: timedelta(minutes=30))


# ---------------------------------------------------------------------------
# Action boundary
# ---------------------------------------------------------------------------


@dataclass
class ActionBoundary:
    """
    Limits what the Agent may do autonomously for this Responsibility.

    max_autonomous_risk  – highest RiskLevel allowed without human approval
    allowed_tools        – whitelist of tool names (empty = all capability tools)
    forbidden_actions    – explicit list of disallowed action descriptions
    requires_approval_above – escalate for approval if task risk >= this level
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
# Responsibility
# ---------------------------------------------------------------------------


@dataclass
class Responsibility:
    """
    The core unit of the Operational Responsibility Model.

    Fields map directly to the formula:
        責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則

    scope              – asset IDs or asset-type patterns this applies to
    goal_thresholds    – numeric targets that must be maintained
    sensing_methods    – how to observe the current state
    available_tools    – capability names the Agent may call for this responsibility
    action_boundary    – reversibility and approval rules
    verification       – how to confirm a fix actually worked
    escalation_rules   – when and how to involve a human
    max_retries        – stop-condition: give up after this many failed attempts
    deadline           – hard stop-condition by wall-clock time
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

    # Stop-conditions
    max_retries: int = 3
    deadline: Optional[Any] = None   # datetime, or None for no deadline

    # ---------------------------------------------------------------------------
    # Derived helpers
    # ---------------------------------------------------------------------------

    def detect_gaps(self, observation: ObservationResult) -> List[Gap]:
        """
        Compare an ObservationResult against goal_thresholds and return
        the list of Gaps discovered.

        Gap IDs are deterministic: re-observing the same threshold violation
        always produces the same gap_id, preventing duplicate tasks across ticks.

        This is a pure function: it does NOT modify state.
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
        """Heuristic: larger deviation → higher severity."""
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
        """Return the first escalation rule whose condition is satisfied, or None."""
        for rule in self.escalation_rules:
            try:
                if rule.condition(task, evidence):
                    return rule
            except Exception:
                # A broken escalation condition must never prevent the agent from running
                continue
        return None

    def stop_reason_for_task(self, task: TaskRecord) -> Optional[StopReason]:
        """Return a StopReason if the task should be stopped, else None."""
        if task.retry_count >= self.max_retries:
            return StopReason.NO_NEW_EVIDENCE
        return None
