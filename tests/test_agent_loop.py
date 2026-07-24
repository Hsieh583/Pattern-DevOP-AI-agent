"""
tests/test_agent_loop.py — Integration tests for the AgentLoop.

Uses stub tools (no real network calls) to verify the full
observe → gap → task → plan → execute → verify → update cycle.
"""

import pytest
from typing import Any, Dict

from agent.core.agent_loop import AgentLoop
from agent.core.models import (
    EscalationReason,
    EvidenceKind,
    RiskLevel,
    TaskStatus,
    Threshold,
)
from agent.core.responsibility import (
    ActionBoundary,
    EscalationRule,
    Responsibility,
    SensingMethod,
    VerificationCondition,
)
from agent.escalation.escalation_manager import EscalationManager
from agent.state.capability_state import CapabilityState, ToolCapability
from agent.state.evidence_state import EvidenceState
from agent.state.goal_state import GoalState
from agent.state.task_state import TaskState
from agent.state.world_state import WorldState
from agent.tools.base_tool import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Stub tools
# ---------------------------------------------------------------------------

class BackupQueryTool(BaseTool):
    """Returns a configurable backup success rate."""
    def __init__(self, rate: float = 0.70):
        self._rate = rate

    @property
    def name(self) -> str:
        return "backup_query"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.READ_ONLY

    @property
    def description(self) -> str:
        return "Query backup system for success rates"

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            data={"backup_success_rate": self._rate},
        )


class BackupRestartTool(BaseTool):
    """Simulates triggering a backup retry."""
    def __init__(self, should_succeed: bool = True):
        self._should_succeed = should_succeed

    @property
    def name(self) -> str:
        return "backup_restart"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        return "Trigger a backup retry on a target endpoint"

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        if self._should_succeed:
            return ToolResult(success=True, data={"restarted": True})
        return ToolResult(success=False, error="Agent offline")


class VerifyBackupTool(BaseTool):
    """Verification tool: confirms success rate has recovered."""
    def __init__(self, verified_rate: float = 0.98):
        self._rate = verified_rate

    @property
    def name(self) -> str:
        return "verify_backup"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.READ_ONLY

    @property
    def description(self) -> str:
        return "Verify backup success rate from monitoring"

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            data={"backup_success_rate": self._rate},
        )


class FailingTool(BaseTool):
    """Always fails, used to test retry logic."""
    @property
    def name(self) -> str:
        return "failing_tool"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        return "A tool that always fails"

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        return ToolResult(success=False, error="always fails")


# ---------------------------------------------------------------------------
# Fixture factory
# ---------------------------------------------------------------------------

def build_loop(
    sensing_rate: float = 0.70,       # observed backup rate (below threshold → gap)
    restart_succeeds: bool = True,
    verified_rate: float = 0.98,      # rate after fix (above threshold → verified)
    max_retries: int = 3,
    available_tools: list = None,
    extra_tools: dict = None,
    escalation_rules: list = None,
    action_boundary: ActionBoundary = None,
    verification_conditions: list = None,
):
    sensing_tool = BackupQueryTool(rate=sensing_rate)
    restart_tool = BackupRestartTool(should_succeed=restart_succeeds)
    verify_tool = VerifyBackupTool(verified_rate=verified_rate)
    failing = FailingTool()

    all_tools = {
        sensing_tool.name: sensing_tool,
        restart_tool.name: restart_tool,
        verify_tool.name: verify_tool,
        failing.name: failing,
    }
    if extra_tools:
        all_tools.update(extra_tools)

    cap = CapabilityState()
    for t in all_tools.values():
        cap.register(ToolCapability(tool_name=t.name, is_available=True))

    if verification_conditions is None:
        verification_conditions = [
            VerificationCondition(
                tool_name="verify_backup",
                check_fn=lambda obs: obs.metric_values.get("backup_success_rate", 0) >= 0.95,
            )
        ]

    if action_boundary is None:
        action_boundary = ActionBoundary(
            max_autonomous_risk=RiskLevel.LOW,
            requires_approval_above=RiskLevel.HIGH,
        )

    resp = Responsibility(
        name="Backup Compliance",
        scope=["all_endpoints"],
        goal_thresholds=[
            Threshold(metric="backup_success_rate", operator=">=", value=0.95),
        ],
        sensing_methods=[SensingMethod(tool_name="backup_query")],
        available_tools=available_tools or ["backup_restart"],
        action_boundary=action_boundary,
        verification_conditions=verification_conditions,
        escalation_rules=escalation_rules or [],
        max_retries=max_retries,
    )

    loop = AgentLoop(
        responsibilities=[resp],
        tools=all_tools,
        world_state=WorldState(),
        goal_state=GoalState(),
        task_state=TaskState(),
        evidence_state=EvidenceState(),
        capability_state=cap,
        escalation_manager=EscalationManager(),
    )
    return loop, resp


# ---------------------------------------------------------------------------
# Happy path: gap detected → task resolved
# ---------------------------------------------------------------------------

class TestAgentLoopHappyPath:
    def test_tick_returns_summary_dict(self):
        loop, _ = build_loop()
        summary = loop.tick()
        assert "tick_at" in summary
        assert "gaps_found" in summary
        assert "tasks_created" in summary

    def test_gap_detected_when_below_threshold(self):
        loop, _ = build_loop(sensing_rate=0.70)
        summary = loop.tick()
        assert summary["gaps_found"] == 1

    def test_no_gap_when_above_threshold(self):
        loop, _ = build_loop(sensing_rate=0.98)
        summary = loop.tick()
        assert summary["gaps_found"] == 0
        assert summary["tasks_created"] == 0

    def test_task_resolved_on_success(self):
        loop, _ = build_loop(sensing_rate=0.70, restart_succeeds=True, verified_rate=0.98)
        summary = loop.tick()
        assert summary["tasks_created"] == 1
        assert summary["tasks_resolved"] == 1

    def test_world_state_updated_after_tick(self):
        loop, resp = build_loop(sensing_rate=0.70)
        loop.tick()
        obs = loop.world.latest_observation(resp.responsibility_id)
        assert obs is not None

    def test_observations_stored_as_observation_kind(self):
        loop, _ = build_loop(sensing_rate=0.70)
        loop.tick()
        observations = loop.evidence.observations()
        assert len(observations) >= 1

    def test_inferences_stored_as_inference_kind(self):
        loop, _ = build_loop(sensing_rate=0.70)
        loop.tick()
        inferences = loop.evidence.inferences()
        assert len(inferences) >= 1

    def test_agent_decisions_recorded(self):
        loop, _ = build_loop(sensing_rate=0.70)
        loop.tick()
        assert len(loop.decisions) >= 1


# ---------------------------------------------------------------------------
# No-duplicate task creation
# ---------------------------------------------------------------------------

class TestNoDuplicateTasks:
    def test_second_tick_does_not_create_duplicate_task(self):
        loop, _ = build_loop(sensing_rate=0.70, restart_succeeds=False)
        loop.tick()
        # Modify tool to succeed on second call
        loop.tools["backup_restart"] = BackupRestartTool(should_succeed=True)
        loop.tools["verify_backup"] = VerifyBackupTool(verified_rate=0.98)
        summary2 = loop.tick()
        # No new tasks should be created for the same (already-active) gap
        assert summary2["tasks_created"] == 0


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    def test_task_queued_for_retry_on_tool_failure(self):
        loop, _ = build_loop(sensing_rate=0.70, restart_succeeds=False)
        loop.tick()
        waiting_retry = [
            t for t in loop.tasks.all_tasks()
            if t.status == TaskStatus.WAITING_RETRY
        ]
        assert len(waiting_retry) == 1

    def test_task_fails_after_max_retries(self):
        loop, _ = build_loop(sensing_rate=0.70, restart_succeeds=False, max_retries=1)
        loop.tick()   # attempt 1: fail → waiting_retry, retry_count=1
        loop.tick()   # attempt 2: escalate/stop (retry_count >= max_retries)
        stopped = [
            t for t in loop.tasks.all_tasks()
            if t.status in (TaskStatus.FAILED, TaskStatus.ESCALATED)
        ]
        assert len(stopped) >= 1


# ---------------------------------------------------------------------------
# Permission / approval enforcement
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_task_waits_for_approval_when_risk_high(self):
        """HIGH-risk tool requires human approval; task should block."""
        class HighRiskTool(BaseTool):
            @property
            def name(self): return "high_risk_tool"
            @property
            def risk_level(self): return RiskLevel.HIGH
            @property
            def description(self): return "high risk"
            def execute(self, params): return ToolResult(success=True, data={})

        high_risk = HighRiskTool()
        loop, _ = build_loop(
            sensing_rate=0.70,
            available_tools=["high_risk_tool"],
            extra_tools={"high_risk_tool": high_risk},
            action_boundary=ActionBoundary(
                max_autonomous_risk=RiskLevel.LOW,
                requires_approval_above=RiskLevel.HIGH,
            ),
        )
        loop.tick()
        waiting_approval = loop.tasks.get_waiting_approval()
        assert len(waiting_approval) == 1

    def test_unavailable_tool_skipped(self):
        """If the remediation tool is disabled, the task stays pending."""
        loop, _ = build_loop(sensing_rate=0.70)
        loop.capabilities.disable("backup_restart")
        loop.tick()
        # No tasks should be resolved; they stay pending
        resolved = [t for t in loop.tasks.all_tasks() if t.status == TaskStatus.RESOLVED]
        assert len(resolved) == 0


# ---------------------------------------------------------------------------
# Verification: command success ≠ task complete
# ---------------------------------------------------------------------------

class TestVerification:
    def test_task_not_resolved_when_verification_fails(self):
        """Even if the restart tool succeeds, a bad verify rate must leave task as retry."""
        loop, _ = build_loop(
            sensing_rate=0.70,
            restart_succeeds=True,
            verified_rate=0.50,   # still below 0.95 threshold → verification fails
        )
        loop.tick()
        resolved = [t for t in loop.tasks.all_tasks() if t.status == TaskStatus.RESOLVED]
        assert len(resolved) == 0

    def test_task_resolved_when_verification_passes(self):
        loop, _ = build_loop(
            sensing_rate=0.70,
            restart_succeeds=True,
            verified_rate=0.98,
        )
        loop.tick()
        resolved = [t for t in loop.tasks.all_tasks() if t.status == TaskStatus.RESOLVED]
        assert len(resolved) == 1


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalation_triggered_by_rule(self):
        escalated_calls = []

        def notifier(record):
            escalated_calls.append(record)

        rule = EscalationRule(
            reason=EscalationReason.REPEATED_FAILURE,
            condition=lambda task, ev: task.retry_count >= 1,
            priority="high",
            contact="oncall",
        )
        loop, resp = build_loop(
            sensing_rate=0.70,
            restart_succeeds=False,
            max_retries=5,
            escalation_rules=[rule],
        )
        loop.escalation._notifier = notifier

        loop.tick()  # retry_count becomes 1 → escalation triggered next tick
        loop.tick()

        assert len(escalated_calls) >= 1
        escalated_tasks = [t for t in loop.tasks.all_tasks() if t.status == TaskStatus.ESCALATED]
        assert len(escalated_tasks) >= 1
