"""
tests/test_models.py — Unit tests for core data models.

Covers: Threshold, EvidenceEntry, TaskRecord, Gap, AgentDecision
"""

import pytest
from datetime import timezone, datetime

from agent.core.models import (
    AgentDecision,
    Asset,
    EvidenceEntry,
    EvidenceKind,
    Gap,
    ObservationResult,
    RiskLevel,
    StopReason,
    TaskRecord,
    TaskStatus,
    Threshold,
)


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------

class TestThreshold:
    def test_not_violated_when_condition_met(self):
        t = Threshold(metric="backup_success_rate", operator=">=", value=0.95)
        assert not t.is_violated(1.0)
        assert not t.is_violated(0.95)

    def test_violated_when_below_threshold(self):
        t = Threshold(metric="backup_success_rate", operator=">=", value=0.95)
        assert t.is_violated(0.80)

    def test_less_than_operator(self):
        t = Threshold(metric="disk_usage_pct", operator="<", value=90.0)
        assert not t.is_violated(85.0)
        assert t.is_violated(92.0)

    def test_equal_operator(self):
        t = Threshold(metric="replica_count", operator="==", value=3.0)
        assert not t.is_violated(3.0)
        assert t.is_violated(2.0)

    def test_unknown_operator_raises(self):
        t = Threshold(metric="x", operator="!=", value=0.0)
        with pytest.raises(ValueError, match="Unknown operator"):
            t.is_violated(1.0)


# ---------------------------------------------------------------------------
# EvidenceEntry — kind separation
# ---------------------------------------------------------------------------

class TestEvidenceEntry:
    def test_default_kind_is_observation(self):
        e = EvidenceEntry(source="zabbix", content={"cpu": 42})
        assert e.kind == EvidenceKind.OBSERVATION

    def test_as_dict_contains_required_keys(self):
        e = EvidenceEntry(
            kind=EvidenceKind.INFERENCE,
            source="agent_loop",
            content={"gap": "disk full"},
            task_id="t1",
        )
        d = e.as_dict()
        assert d["kind"] == "inference"
        assert d["source"] == "agent_loop"
        assert d["task_id"] == "t1"
        assert "timestamp" in d

    def test_decision_kind_for_human_approvals(self):
        e = EvidenceEntry(kind=EvidenceKind.DECISION, source="admin@corp", content="approved")
        assert e.kind == EvidenceKind.DECISION

    def test_evidence_ids_are_unique(self):
        ids = {EvidenceEntry().evidence_id for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# TaskRecord
# ---------------------------------------------------------------------------

class TestTaskRecord:
    def test_new_task_is_pending(self):
        t = TaskRecord(title="fix backup", gap_id="g1")
        assert t.status == TaskStatus.PENDING
        assert t.retry_count == 0

    def test_can_retry_within_limit(self):
        t = TaskRecord(gap_id="g1", max_retries=3)
        t.status = TaskStatus.WAITING_RETRY
        t.retry_count = 2
        assert t.can_retry()

    def test_cannot_retry_at_limit(self):
        t = TaskRecord(gap_id="g1", max_retries=3)
        t.status = TaskStatus.WAITING_RETRY
        t.retry_count = 3
        assert not t.can_retry()

    def test_mark_stop_sets_all_fields(self):
        t = TaskRecord(gap_id="g1")
        t.mark_stop(StopReason.TIMEOUT, TaskStatus.TIMED_OUT)
        assert t.stop_reason == StopReason.TIMEOUT
        assert t.status == TaskStatus.TIMED_OUT
        assert t.stopped_at is not None

    def test_touch_updates_updated_at(self):
        t = TaskRecord(gap_id="g1")
        before = t.updated_at
        t.touch()
        assert t.updated_at >= before

    def test_task_ids_are_unique(self):
        ids = {TaskRecord(gap_id="g").task_id for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class TestAsset:
    def test_asset_creation(self):
        a = Asset(asset_id="srv-01", name="Web Server", asset_type="server")
        assert a.asset_id == "srv-01"
        assert a.asset_type == "server"

    def test_asset_with_dependencies(self):
        a = Asset(
            asset_id="svc-api",
            name="API Service",
            asset_type="service",
            dependencies=("srv-01", "db-01"),
        )
        assert "srv-01" in a.dependencies


# ---------------------------------------------------------------------------
# ObservationResult
# ---------------------------------------------------------------------------

class TestObservationResult:
    def test_no_errors_by_default(self):
        obs = ObservationResult(responsibility_id="r1")
        assert not obs.has_errors

    def test_errors_flag(self):
        obs = ObservationResult(responsibility_id="r1", errors=["tool unreachable"])
        assert obs.has_errors


# ---------------------------------------------------------------------------
# Gap
# ---------------------------------------------------------------------------

class TestGap:
    def test_gap_creation(self):
        threshold = Threshold(metric="backup_success_rate", operator=">=", value=0.95)
        gap = Gap(
            responsibility_id="r1",
            description="Backup success rate below target",
            severity="high",
            threshold_violated=threshold,
            observed_value=0.72,
        )
        assert gap.responsibility_id == "r1"
        assert gap.severity == "high"
        assert gap.threshold_violated.metric == "backup_success_rate"

    def test_gap_ids_are_unique(self):
        # Gaps with different responsibility IDs must have different gap_ids
        ids = {Gap.make_id(f"resp-{i}", "metric") for i in range(50)}
        assert len(ids) == 50

    def test_gap_id_is_deterministic(self):
        id1 = Gap.make_id("resp-1", "backup_success_rate")
        id2 = Gap.make_id("resp-1", "backup_success_rate")
        assert id1 == id2

    def test_gap_id_differs_by_metric(self):
        id1 = Gap.make_id("resp-1", "metric_a")
        id2 = Gap.make_id("resp-1", "metric_b")
        assert id1 != id2


# ---------------------------------------------------------------------------
# AgentDecision
# ---------------------------------------------------------------------------

class TestAgentDecision:
    def test_default_risk_is_read_only(self):
        d = AgentDecision(action_taken="query zabbix")
        assert d.risk_level == RiskLevel.READ_ONLY

    def test_decision_ids_are_unique(self):
        ids = {AgentDecision().decision_id for _ in range(50)}
        assert len(ids) == 50
