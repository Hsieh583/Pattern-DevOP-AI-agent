"""
tests/test_responsibility.py — Unit tests for the Responsibility model.

Covers: gap detection, escalation rules, stop conditions, ActionBoundary.
"""

import pytest

from agent.core.models import (
    EscalationReason,
    ObservationResult,
    RiskLevel,
    StopReason,
    TaskRecord,
    TaskStatus,
    Threshold,
)
from agent.core.responsibility import (
    ActionBoundary,
    EscalationRule,
    Responsibility,
    SensingMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_observation(resp_id: str, metrics: dict) -> ObservationResult:
    obs = ObservationResult(responsibility_id=resp_id)
    obs.metric_values.update(metrics)
    return obs


def always_escalate(task, evidence):
    return True


def never_escalate(task, evidence):
    return False


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

class TestResponsibilityGapDetection:
    def test_no_gaps_when_all_thresholds_met(self):
        resp = Responsibility(
            name="Backup",
            goal_thresholds=[
                Threshold(metric="backup_success_rate", operator=">=", value=0.95),
            ],
        )
        obs = make_observation(resp.responsibility_id, {"backup_success_rate": 0.98})
        assert resp.detect_gaps(obs) == []

    def test_gap_detected_when_threshold_violated(self):
        resp = Responsibility(
            name="Backup",
            goal_thresholds=[
                Threshold(metric="backup_success_rate", operator=">=", value=0.95),
            ],
        )
        obs = make_observation(resp.responsibility_id, {"backup_success_rate": 0.70})
        gaps = resp.detect_gaps(obs)
        assert len(gaps) == 1
        assert gaps[0].threshold_violated.metric == "backup_success_rate"
        assert gaps[0].observed_value == pytest.approx(0.70)

    def test_missing_metric_produces_no_gap(self):
        resp = Responsibility(
            name="Backup",
            goal_thresholds=[
                Threshold(metric="backup_success_rate", operator=">=", value=0.95),
            ],
        )
        obs = make_observation(resp.responsibility_id, {})  # metric absent
        assert resp.detect_gaps(obs) == []

    def test_multiple_thresholds_multiple_gaps(self):
        resp = Responsibility(
            name="Infra",
            goal_thresholds=[
                Threshold(metric="disk_pct", operator="<", value=90.0),
                Threshold(metric="cpu_pct", operator="<", value=80.0),
            ],
        )
        obs = make_observation(
            resp.responsibility_id, {"disk_pct": 95.0, "cpu_pct": 85.0}
        )
        gaps = resp.detect_gaps(obs)
        assert len(gaps) == 2

    def test_severity_critical_for_large_deviation(self):
        resp = Responsibility(
            name="Backup",
            goal_thresholds=[
                Threshold(metric="backup_success_rate", operator=">=", value=1.0),
            ],
        )
        # Deviation = |0.2 - 1.0| / 1.0 = 0.8 ≥ 0.5 → critical
        obs = make_observation(resp.responsibility_id, {"backup_success_rate": 0.2})
        gaps = resp.detect_gaps(obs)
        assert gaps[0].severity == "critical"

    def test_severity_low_for_small_deviation(self):
        resp = Responsibility(
            name="Backup",
            goal_thresholds=[
                Threshold(metric="backup_success_rate", operator=">=", value=1.0),
            ],
        )
        # Deviation = |0.97 - 1.0| / 1.0 = 0.03 < 0.10 → low
        obs = make_observation(resp.responsibility_id, {"backup_success_rate": 0.97})
        gaps = resp.detect_gaps(obs)
        assert gaps[0].severity == "low"


# ---------------------------------------------------------------------------
# Escalation rules
# ---------------------------------------------------------------------------

class TestEscalationRules:
    def test_escalation_triggered_when_condition_true(self):
        rule = EscalationRule(
            reason=EscalationReason.REPEATED_FAILURE,
            condition=always_escalate,
            priority="high",
        )
        resp = Responsibility(name="Test", escalation_rules=[rule])
        task = TaskRecord(gap_id="g1", responsibility_id=resp.responsibility_id)
        assert resp.should_escalate(task, []) == rule

    def test_no_escalation_when_condition_false(self):
        rule = EscalationRule(
            reason=EscalationReason.REPEATED_FAILURE,
            condition=never_escalate,
        )
        resp = Responsibility(name="Test", escalation_rules=[rule])
        task = TaskRecord(gap_id="g1")
        assert resp.should_escalate(task, []) is None

    def test_broken_escalation_condition_does_not_crash(self):
        """A buggy escalation rule must not bring down the Agent."""
        def broken_condition(task, evidence):
            raise RuntimeError("broken")

        rule = EscalationRule(
            reason=EscalationReason.UNKNOWN_FAILURE,
            condition=broken_condition,
        )
        resp = Responsibility(name="Test", escalation_rules=[rule])
        task = TaskRecord(gap_id="g1")
        # Should not raise
        assert resp.should_escalate(task, []) is None

    def test_first_matching_rule_returned(self):
        rule1 = EscalationRule(reason=EscalationReason.RISK_THRESHOLD, condition=never_escalate)
        rule2 = EscalationRule(reason=EscalationReason.REPEATED_FAILURE, condition=always_escalate)
        resp = Responsibility(name="Test", escalation_rules=[rule1, rule2])
        task = TaskRecord(gap_id="g1")
        matched = resp.should_escalate(task, [])
        assert matched == rule2


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------

class TestStopConditions:
    def test_stop_when_max_retries_exceeded(self):
        resp = Responsibility(name="Test", max_retries=3)
        task = TaskRecord(gap_id="g1", retry_count=3)
        assert resp.stop_reason_for_task(task) == StopReason.NO_NEW_EVIDENCE

    def test_no_stop_when_retries_below_max(self):
        resp = Responsibility(name="Test", max_retries=3)
        task = TaskRecord(gap_id="g1", retry_count=2)
        assert resp.stop_reason_for_task(task) is None


# ---------------------------------------------------------------------------
# ActionBoundary
# ---------------------------------------------------------------------------

class TestActionBoundary:
    def test_all_tools_allowed_when_no_whitelist(self):
        ab = ActionBoundary()
        assert ab.is_tool_allowed("any_tool_name")

    def test_only_whitelisted_tools_allowed(self):
        ab = ActionBoundary(allowed_tools=["backup_query", "zabbix"])
        assert ab.is_tool_allowed("backup_query")
        assert not ab.is_tool_allowed("firewall_api")

    def test_autonomous_within_max_level(self):
        ab = ActionBoundary(max_autonomous_risk=RiskLevel.LOW)
        assert ab.is_autonomous(RiskLevel.READ_ONLY)
        assert ab.is_autonomous(RiskLevel.LOW)
        assert not ab.is_autonomous(RiskLevel.MEDIUM)

    def test_requires_approval_above_threshold(self):
        ab = ActionBoundary(requires_approval_above=RiskLevel.HIGH)
        assert ab.requires_approval(RiskLevel.HIGH)
        assert ab.requires_approval(RiskLevel.CRITICAL)
        assert not ab.requires_approval(RiskLevel.MEDIUM)


# ---------------------------------------------------------------------------
# Sensing methods
# ---------------------------------------------------------------------------

class TestSensingMethod:
    def test_sensing_method_attached_to_responsibility(self):
        sm = SensingMethod(tool_name="backup_query")
        resp = Responsibility(name="Backup", sensing_methods=[sm])
        assert resp.sensing_methods[0].tool_name == "backup_query"
