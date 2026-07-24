"""
core/agent_loop.py — The main OODA-style control loop.

    觀察環境 → 發現差距 → 建立任務 → 規劃處置
        ↑                              ↓
    更新世界模型 ← 驗證結果 ← 執行工具 ← 權限判斷

Design contracts
----------------
* Every phase is independently logged and auditable.
* Observation facts, Agent inferences, and Human decisions are stored
  in separate EvidenceKind buckets — they must never be fused.
* An action is only executed if:
  - The tool is allowed by the Responsibility's ActionBoundary.
  - The risk level is within the autonomous threshold, OR approval exists.
* After every execution, the Agent must re-observe (verify) before closing.
* If max_retries is reached with no change in evidence, the loop stops.
* The Agent cannot grant itself new permissions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agent.core.models import (
    AgentDecision,
    EvidenceEntry,
    EvidenceKind,
    Gap,
    ObservationResult,
    RiskLevel,
    StopReason,
    TaskRecord,
    TaskStatus,
)
from agent.core.responsibility import Responsibility
from agent.escalation.escalation_manager import EscalationManager
from agent.state.capability_state import CapabilityState
from agent.state.evidence_state import EvidenceState
from agent.state.goal_state import GoalState
from agent.state.task_state import TaskState
from agent.state.world_state import WorldState
from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    The persistent DevOps Agent control loop.

    The Agent is not a script executor; it is a continuous responsibility
    carrier.  On each tick it:

    1. **Observes** — calls sensing tools and records raw evidence.
    2. **Detects gaps** — compares observations to goal thresholds.
    3. **Creates tasks** — one task per gap, avoiding duplicates.
    4. **Plans** — chooses the best available tool action.
    5. **Checks permissions** — enforces ActionBoundary and approval rules.
    6. **Executes** — calls the tool if authorised.
    7. **Verifies** — re-observes to confirm the gap is actually resolved.
    8. **Updates the world model** — records the new state.
    9. **Escalates** — involves a human when stop conditions are met.
    """

    def __init__(
        self,
        responsibilities: List[Responsibility],
        tools: Dict[str, BaseTool],
        world_state: WorldState,
        goal_state: GoalState,
        task_state: TaskState,
        evidence_state: EvidenceState,
        capability_state: CapabilityState,
        escalation_manager: EscalationManager,
    ) -> None:
        self.responsibilities = {r.responsibility_id: r for r in responsibilities}
        self.tools = tools
        self.world = world_state
        self.goals = goal_state
        self.tasks = task_state
        self.evidence = evidence_state
        self.capabilities = capability_state
        self.escalation = escalation_manager
        self._decisions: List[AgentDecision] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def tick(self) -> Dict[str, Any]:
        """
        Execute one complete cycle of the agent loop across all responsibilities.

        Returns a summary dict for monitoring / logging purposes.
        """
        summary: Dict[str, Any] = {
            "tick_at": datetime.now(timezone.utc).isoformat(),
            "responsibilities_processed": 0,
            "gaps_found": 0,
            "tasks_created": 0,
            "tasks_resolved": 0,
            "tasks_escalated": 0,
            "tasks_stopped": 0,
        }

        for resp_id, resp in self.responsibilities.items():
            logger.info("Processing responsibility: %s (%s)", resp_id, resp.name)
            result = self._process_responsibility(resp)
            summary["responsibilities_processed"] += 1
            summary["gaps_found"] += result.get("gaps_found", 0)
            summary["tasks_created"] += result.get("tasks_created", 0)
            summary["tasks_resolved"] += result.get("tasks_resolved", 0)
            summary["tasks_escalated"] += result.get("tasks_escalated", 0)
            summary["tasks_stopped"] += result.get("tasks_stopped", 0)

        return summary

    # ------------------------------------------------------------------
    # Phase 1: Observe
    # ------------------------------------------------------------------

    def _observe(self, resp: Responsibility) -> ObservationResult:
        """
        Call all sensing tools for a responsibility and collect raw evidence.
        Facts are stored as OBSERVATION evidence — never fused with inferences.
        """
        observation = ObservationResult(responsibility_id=resp.responsibility_id)

        for method in resp.sensing_methods:
            tool = self.tools.get(method.tool_name)
            if tool is None:
                msg = f"Sensing tool '{method.tool_name}' not registered"
                logger.warning(msg)
                observation.errors.append(msg)
                continue

            if not self.capabilities.is_tool_available(method.tool_name):
                msg = f"Sensing tool '{method.tool_name}' not available in capability state"
                logger.warning(msg)
                observation.errors.append(msg)
                continue

            try:
                result: ToolResult = tool.execute(method.query_params)
                entry = EvidenceEntry(
                    kind=EvidenceKind.OBSERVATION,
                    source=method.tool_name,
                    content=result.data,
                    responsibility_id=resp.responsibility_id,
                )
                self.evidence.add(entry)
                observation.raw_evidence.append(entry)

                # Merge metrics from tool result into observation
                if isinstance(result.data, dict):
                    for k, v in result.data.items():
                        if isinstance(v, (int, float)):
                            observation.metric_values[k] = float(v)
                    # Allow tools to report asset statuses directly
                    if "asset_statuses" in result.data:
                        observation.asset_statuses.update(result.data["asset_statuses"])

            except Exception as exc:
                msg = f"Tool '{method.tool_name}' raised exception: {exc}"
                logger.error(msg)
                observation.errors.append(msg)

        return observation

    # ------------------------------------------------------------------
    # Phase 2: Detect gaps
    # ------------------------------------------------------------------

    def _detect_gaps(
        self, resp: Responsibility, observation: ObservationResult
    ) -> List[Gap]:
        """
        Compare observation to goal thresholds and return discovered gaps.
        Inferences (classification of gap severity) are stored as INFERENCE evidence.
        """
        gaps = resp.detect_gaps(observation)
        for gap in gaps:
            inference = EvidenceEntry(
                kind=EvidenceKind.INFERENCE,
                source="agent_loop",
                content={
                    "gap_id": gap.gap_id,
                    "description": gap.description,
                    "severity": gap.severity,
                },
                responsibility_id=resp.responsibility_id,
            )
            self.evidence.add(inference)
            logger.info("Gap detected [%s]: %s", gap.severity.upper(), gap.description)

        return gaps

    # ------------------------------------------------------------------
    # Phase 3: Create tasks
    # ------------------------------------------------------------------

    def _create_tasks(
        self, resp: Responsibility, gaps: List[Gap]
    ) -> List[TaskRecord]:
        """Create a TaskRecord for each new Gap (deduplicated by gap_id)."""
        new_tasks: List[TaskRecord] = []
        for gap in gaps:
            if self.tasks.has_active_task_for_gap(gap.gap_id):
                logger.debug("Active task already exists for gap %s — skipping", gap.gap_id)
                continue
            task = TaskRecord(
                responsibility_id=resp.responsibility_id,
                gap_id=gap.gap_id,
                title=f"[{resp.name}] {gap.description[:80]}",
                description=gap.description,
                status=TaskStatus.PENDING,
                risk_level=self._estimate_risk(resp, gap),
                max_retries=resp.max_retries,
                evidence_ids=list(gap.supporting_evidence),
            )
            self.tasks.add(task)
            new_tasks.append(task)
            logger.info("Task created: %s (risk=%s)", task.task_id, task.risk_level.value)

        return new_tasks

    @staticmethod
    def _estimate_risk(resp: Responsibility, gap: Gap) -> RiskLevel:
        """
        Heuristic: map gap severity to a default risk level.
        The Responsibility's ActionBoundary may override this downstream.
        """
        mapping = {
            "critical": RiskLevel.HIGH,
            "high": RiskLevel.MEDIUM,
            "medium": RiskLevel.LOW,
            "low": RiskLevel.READ_ONLY,
        }
        return mapping.get(gap.severity, RiskLevel.LOW)

    # ------------------------------------------------------------------
    # Phase 4 & 5: Plan + permission check
    # ------------------------------------------------------------------

    def _plan_and_check(
        self, resp: Responsibility, task: TaskRecord
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Choose the best available tool action and verify permissions.

        Returns (tool_name, params) if authorised, or (None, None) if blocked.
        The Agent can REQUEST new capabilities but cannot grant them itself.
        """
        for tool_name in resp.available_tools:
            if not resp.action_boundary.is_tool_allowed(tool_name):
                continue
            tool = self.tools.get(tool_name)
            if tool is None:
                continue
            if not self.capabilities.is_tool_available(tool_name):
                logger.warning("Tool '%s' not available — skipping", tool_name)
                continue

            tool_risk = tool.risk_level
            if resp.action_boundary.requires_approval(tool_risk):
                if task.approved_by is None:
                    task.status = TaskStatus.WAITING_APPROVAL
                    task.touch()
                    logger.info(
                        "Task %s requires approval for %s (risk=%s)",
                        task.task_id, tool_name, tool_risk.value,
                    )
                    return None, None
            elif not resp.action_boundary.is_autonomous(tool_risk):
                logger.info(
                    "Tool '%s' risk=%s exceeds max autonomous level — skipping",
                    tool_name, tool_risk.value,
                )
                continue

            return tool_name, {"task_id": task.task_id, "gap_id": task.gap_id}

        return None, None

    # ------------------------------------------------------------------
    # Phase 6: Execute
    # ------------------------------------------------------------------

    def _execute(
        self,
        resp: Responsibility,
        task: TaskRecord,
        tool_name: str,
        params: Dict[str, Any],
    ) -> ToolResult:
        """Execute a tool and record the decision + outcome as evidence."""
        tool = self.tools[tool_name]
        decision = AgentDecision(
            task_id=task.task_id,
            responsibility_id=resp.responsibility_id,
            action_taken=f"Execute tool '{tool_name}'",
            rationale=f"Remediating gap: {task.description[:120]}",
            tool_used=tool_name,
            risk_level=tool.risk_level,
        )

        try:
            task.status = TaskStatus.IN_PROGRESS
            task.touch()
            result: ToolResult = tool.execute(params)
            decision.outcome = "success" if result.success else f"failure: {result.error}"
        except Exception as exc:
            result = ToolResult(success=False, error=str(exc))
            decision.outcome = f"exception: {exc}"

        self._decisions.append(decision)
        exec_evidence = EvidenceEntry(
            kind=EvidenceKind.OBSERVATION,
            source=tool_name,
            content={"result": result.data, "success": result.success, "error": result.error},
            task_id=task.task_id,
            responsibility_id=resp.responsibility_id,
        )
        self.evidence.add(exec_evidence)
        task.evidence_ids.append(exec_evidence.evidence_id)
        task.retry_count += 1
        task.touch()

        return result

    # ------------------------------------------------------------------
    # Phase 7: Verify
    # ------------------------------------------------------------------

    def _verify(
        self, resp: Responsibility, task: TaskRecord
    ) -> Tuple[bool, ObservationResult]:
        """
        Re-observe after an action to confirm the gap is genuinely resolved.

        Command success alone is NOT sufficient — verification must come from
        monitoring or the affected user's perspective.
        """
        if not resp.verification_conditions:
            # No verification configured: accept tool result as-is (with warning)
            logger.warning(
                "No verification conditions for responsibility %s — relying on tool result",
                resp.responsibility_id,
            )
            return True, ObservationResult(responsibility_id=resp.responsibility_id)

        observation = ObservationResult(responsibility_id=resp.responsibility_id)
        for vc in resp.verification_conditions:
            v_tool = self.tools.get(vc.tool_name)
            if v_tool is None:
                logger.warning("Verification tool '%s' not found", vc.tool_name)
                continue
            try:
                v_result = v_tool.execute({"task_id": task.task_id})
                entry = EvidenceEntry(
                    kind=EvidenceKind.OBSERVATION,
                    source=vc.tool_name,
                    content=v_result.data,
                    task_id=task.task_id,
                    responsibility_id=resp.responsibility_id,
                )
                self.evidence.add(entry)
                observation.raw_evidence.append(entry)
                if isinstance(v_result.data, dict):
                    for k, v in v_result.data.items():
                        if isinstance(v, (int, float)):
                            observation.metric_values[k] = float(v)

                if not vc.check_fn(observation):
                    return False, observation
            except Exception as exc:
                logger.error("Verification tool '%s' raised: %s", vc.tool_name, exc)
                return False, observation

        return True, observation

    # ------------------------------------------------------------------
    # Phase 8: Update world model
    # ------------------------------------------------------------------

    def _update_world(
        self, resp: Responsibility, observation: ObservationResult
    ) -> None:
        """Persist the latest observation into WorldState."""
        self.world.record_observation(resp.responsibility_id, observation)

    # ------------------------------------------------------------------
    # Phase 9: Escalate / stop
    # ------------------------------------------------------------------

    def _maybe_escalate(
        self, resp: Responsibility, task: TaskRecord
    ) -> bool:
        """Return True if the task was escalated or stopped."""
        task_evidence = self.evidence.get_for_task(task.task_id)

        # Check escalation rules first
        rule = resp.should_escalate(task, task_evidence)
        if rule is not None:
            self.escalation.escalate(task, rule, task_evidence)
            task.mark_stop(StopReason.RISK_EXCEEDED, TaskStatus.ESCALATED)
            return True

        # Check intrinsic stop conditions
        stop_reason = resp.stop_reason_for_task(task)
        if stop_reason is not None:
            task.mark_stop(stop_reason, TaskStatus.FAILED)
            logger.warning(
                "Task %s stopped: %s (retries=%d/%d)",
                task.task_id, stop_reason.value, task.retry_count, resp.max_retries,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def _process_responsibility(self, resp: Responsibility) -> Dict[str, Any]:
        result = {
            "gaps_found": 0,
            "tasks_created": 0,
            "tasks_resolved": 0,
            "tasks_escalated": 0,
            "tasks_stopped": 0,
        }

        # --- Phase 1: Observe ---
        observation = self._observe(resp)
        self._update_world(resp, observation)

        # --- Phase 2: Detect gaps ---
        gaps = self._detect_gaps(resp, observation)
        result["gaps_found"] = len(gaps)

        # --- Phase 3: Create tasks ---
        new_tasks = self._create_tasks(resp, gaps)
        result["tasks_created"] = len(new_tasks)

        # --- Process all pending / retry tasks for this responsibility ---
        active_tasks = self.tasks.get_active_for_responsibility(resp.responsibility_id)
        for task in active_tasks:
            # Check stop / escalation before attempting any action
            if self._maybe_escalate(resp, task):
                result["tasks_escalated" if task.status == TaskStatus.ESCALATED
                        else "tasks_stopped"] += 1
                continue

            # --- Phase 4 & 5: Plan + permission check ---
            tool_name, params = self._plan_and_check(resp, task)
            if tool_name is None:
                continue

            # --- Phase 6: Execute ---
            exec_result = self._execute(resp, task, tool_name, params)

            if not exec_result.success:
                task.status = TaskStatus.WAITING_RETRY
                task.touch()
                continue

            # --- Phase 7: Verify ---
            verified, v_obs = self._verify(resp, task)
            self._update_world(resp, v_obs)

            if verified:
                task.mark_stop(StopReason.SUCCESS, TaskStatus.RESOLVED)
                result["tasks_resolved"] += 1
                logger.info("Task %s resolved ✓", task.task_id)
            else:
                task.status = TaskStatus.WAITING_RETRY
                task.touch()
                logger.info("Task %s verification failed — queued for retry", task.task_id)

        return result

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def decisions(self) -> List[AgentDecision]:
        return list(self._decisions)
