"""
tests/test_state_management.py — Unit tests for the five state modules.

Covers: WorldState, GoalState, TaskState, EvidenceState, CapabilityState.
"""

import pytest
from datetime import datetime, timedelta, timezone

from agent.core.models import (
    Asset,
    EvidenceEntry,
    EvidenceKind,
    ObservationResult,
    RiskLevel,
    TaskRecord,
    TaskStatus,
    Threshold,
)
from agent.state.capability_state import CapabilityState, ToolCapability
from agent.state.evidence_state import EvidenceState
from agent.state.goal_state import AuditRequirement, GoalState, ServiceWindow
from agent.state.task_state import TaskState
from agent.state.world_state import WorldState


# ---------------------------------------------------------------------------
# WorldState
# ---------------------------------------------------------------------------

class TestWorldState:
    def test_register_and_retrieve_asset(self):
        ws = WorldState()
        asset = Asset(asset_id="srv-01", name="App Server", asset_type="server")
        ws.register_asset(asset)
        assert ws.get_asset("srv-01") == asset

    def test_list_assets_by_type(self):
        ws = WorldState()
        ws.register_asset(Asset(asset_id="srv-01", name="Server", asset_type="server"))
        ws.register_asset(Asset(asset_id="svc-01", name="Service", asset_type="service"))
        servers = ws.list_assets(asset_type="server")
        assert len(servers) == 1
        assert servers[0].asset_id == "srv-01"

    def test_health_starts_healthy(self):
        ws = WorldState()
        ws.register_asset(Asset(asset_id="ep-01", name="Endpoint", asset_type="endpoint"))
        health = ws.get_health("ep-01")
        assert health is not None
        assert health.is_healthy

    def test_update_health(self):
        ws = WorldState()
        ws.register_asset(Asset(asset_id="ep-01", name="Endpoint", asset_type="endpoint"))
        ws.update_health("ep-01", is_healthy=False, metrics={"backup_age_hours": 48})
        health = ws.get_health("ep-01")
        assert not health.is_healthy
        assert health.metrics["backup_age_hours"] == 48

    def test_unhealthy_assets_list(self):
        ws = WorldState()
        ws.register_asset(Asset(asset_id="ep-01", name="A", asset_type="endpoint"))
        ws.register_asset(Asset(asset_id="ep-02", name="B", asset_type="endpoint"))
        ws.update_health("ep-01", is_healthy=False)
        assert "ep-01" in ws.unhealthy_assets()
        assert "ep-02" not in ws.unhealthy_assets()

    def test_record_and_retrieve_observation(self):
        ws = WorldState()
        obs = ObservationResult(responsibility_id="r1")
        ws.record_observation("r1", obs)
        assert ws.latest_observation("r1") is obs

    def test_summary_counts(self):
        ws = WorldState()
        ws.register_asset(Asset(asset_id="a1", name="A", asset_type="server"))
        ws.register_asset(Asset(asset_id="a2", name="B", asset_type="server"))
        ws.update_health("a1", is_healthy=False)
        summary = ws.summary()
        assert summary["total_assets"] == 2
        assert summary["unhealthy_count"] == 1


# ---------------------------------------------------------------------------
# GoalState
# ---------------------------------------------------------------------------

class TestGoalState:
    def test_set_and_get_thresholds(self):
        gs = GoalState()
        t = Threshold(metric="backup_success_rate", operator=">=", value=0.95)
        gs.set_thresholds("r1", [t])
        assert gs.get_thresholds("r1") == [t]

    def test_missing_responsibility_returns_empty(self):
        gs = GoalState()
        assert gs.get_thresholds("nonexistent") == []

    def test_service_window_active(self):
        gs = GoalState()
        now = datetime.now(timezone.utc)
        window = ServiceWindow(
            window_id="w1",
            name="Maintenance",
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
        )
        gs.add_service_window(window)
        active = gs.active_windows()
        assert len(active) == 1
        assert active[0].window_id == "w1"

    def test_service_window_inactive(self):
        gs = GoalState()
        now = datetime.now(timezone.utc)
        window = ServiceWindow(
            window_id="w2",
            name="Past",
            start=now - timedelta(hours=3),
            end=now - timedelta(hours=1),
        )
        gs.add_service_window(window)
        assert gs.active_windows() == []

    def test_urgent_audit_requirement(self):
        gs = GoalState()
        now = datetime.now(timezone.utc)
        req = AuditRequirement(
            requirement_id="audit-01",
            description="Annual backup audit",
            deadline=now + timedelta(days=3),
        )
        gs.add_audit_requirement(req)
        urgent = gs.urgent_requirements(threshold_days=7.0)
        assert len(urgent) == 1

    def test_overdue_audit_requirement(self):
        gs = GoalState()
        now = datetime.now(timezone.utc)
        req = AuditRequirement(
            requirement_id="audit-02",
            description="Overdue audit",
            deadline=now - timedelta(days=1),
        )
        gs.add_audit_requirement(req)
        assert len(gs.overdue_requirements()) == 1
        assert len(gs.urgent_requirements()) == 0


# ---------------------------------------------------------------------------
# TaskState
# ---------------------------------------------------------------------------

class TestTaskState:
    def test_add_and_retrieve_task(self):
        ts = TaskState()
        task = TaskRecord(gap_id="g1", responsibility_id="r1")
        ts.add(task)
        assert ts.get(task.task_id) == task

    def test_has_active_task_for_gap(self):
        ts = TaskState()
        task = TaskRecord(gap_id="g1", status=TaskStatus.IN_PROGRESS)
        ts.add(task)
        assert ts.has_active_task_for_gap("g1")

    def test_no_active_task_for_resolved_gap(self):
        ts = TaskState()
        task = TaskRecord(gap_id="g2", status=TaskStatus.RESOLVED)
        ts.add(task)
        assert not ts.has_active_task_for_gap("g2")

    def test_get_active_for_responsibility(self):
        ts = TaskState()
        ts.add(TaskRecord(gap_id="g1", responsibility_id="r1", status=TaskStatus.PENDING))
        ts.add(TaskRecord(gap_id="g2", responsibility_id="r1", status=TaskStatus.RESOLVED))
        ts.add(TaskRecord(gap_id="g3", responsibility_id="r2", status=TaskStatus.PENDING))
        active = ts.get_active_for_responsibility("r1")
        assert len(active) == 1
        assert active[0].gap_id == "g1"

    def test_get_waiting_approval(self):
        ts = TaskState()
        ts.add(TaskRecord(gap_id="g1", status=TaskStatus.WAITING_APPROVAL))
        ts.add(TaskRecord(gap_id="g2", status=TaskStatus.PENDING))
        waiting = ts.get_waiting_approval()
        assert len(waiting) == 1

    def test_summary_by_status(self):
        ts = TaskState()
        ts.add(TaskRecord(gap_id="g1", status=TaskStatus.RESOLVED))
        ts.add(TaskRecord(gap_id="g2", status=TaskStatus.PENDING))
        ts.add(TaskRecord(gap_id="g3", status=TaskStatus.PENDING))
        summary = ts.summary()
        assert summary["total"] == 3
        assert summary["by_status"]["pending"] == 2
        assert summary["by_status"]["resolved"] == 1


# ---------------------------------------------------------------------------
# EvidenceState
# ---------------------------------------------------------------------------

class TestEvidenceState:
    def test_add_and_retrieve_entry(self):
        es = EvidenceState()
        entry = EvidenceEntry(kind=EvidenceKind.OBSERVATION, source="zabbix", content={})
        es.add(entry)
        assert es.get(entry.evidence_id) == entry

    def test_kind_separation(self):
        es = EvidenceState()
        obs = EvidenceEntry(kind=EvidenceKind.OBSERVATION, source="tool")
        inf = EvidenceEntry(kind=EvidenceKind.INFERENCE, source="agent")
        dec = EvidenceEntry(kind=EvidenceKind.DECISION, source="human")
        for e in (obs, inf, dec):
            es.add(e)
        assert len(es.observations()) == 1
        assert len(es.inferences()) == 1
        assert len(es.human_decisions()) == 1

    def test_entries_retrievable_by_task(self):
        es = EvidenceState()
        e1 = EvidenceEntry(source="tool", task_id="t1")
        e2 = EvidenceEntry(source="tool", task_id="t2")
        es.add(e1)
        es.add(e2)
        for_t1 = es.get_for_task("t1")
        assert len(for_t1) == 1
        assert for_t1[0].evidence_id == e1.evidence_id

    def test_entries_retrievable_by_responsibility(self):
        es = EvidenceState()
        e = EvidenceEntry(source="tool", responsibility_id="r1")
        es.add(e)
        results = es.get_for_responsibility("r1")
        assert len(results) == 1

    def test_record_human_decision(self):
        es = EvidenceState()
        entry = es.record_human_decision(
            source="admin",
            content={"action": "approved restart"},
            task_id="t1",
        )
        assert entry.kind == EvidenceKind.DECISION
        assert len(es.human_decisions()) == 1

    def test_summary_counts_by_kind(self):
        es = EvidenceState()
        for _ in range(3):
            es.add(EvidenceEntry(kind=EvidenceKind.OBSERVATION, source="s"))
        es.add(EvidenceEntry(kind=EvidenceKind.INFERENCE, source="s"))
        s = es.summary()
        assert s["total_entries"] == 4
        assert s["observations"] == 3
        assert s["inferences"] == 1
        assert s["human_decisions"] == 0


# ---------------------------------------------------------------------------
# CapabilityState
# ---------------------------------------------------------------------------

class TestCapabilityState:
    def test_registered_tool_is_available(self):
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="backup_query", is_available=True))
        assert cs.is_tool_available("backup_query")

    def test_unknown_tool_not_available(self):
        cs = CapabilityState()
        assert not cs.is_tool_available("nonexistent")

    def test_disabled_tool_not_available(self):
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="firewall_api", is_available=True))
        cs.disable("firewall_api")
        assert not cs.is_tool_available("firewall_api")

    def test_re_enable_tool(self):
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="ad_tool", is_available=False))
        cs.enable("ad_tool")
        assert cs.is_tool_available("ad_tool")

    def test_expired_permission_not_available(self):
        from datetime import timezone
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="temp_tool", permission_expires=past))
        assert not cs.is_tool_available("temp_tool")

    def test_future_expiry_is_available(self):
        from datetime import timezone
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="temp_tool", permission_expires=future))
        assert cs.is_tool_available("temp_tool")

    def test_available_and_unavailable_lists(self):
        cs = CapabilityState()
        cs.register(ToolCapability(tool_name="t1", is_available=True))
        cs.register(ToolCapability(tool_name="t2", is_available=False))
        assert "t1" in cs.available_tools()
        assert "t2" in cs.unavailable_tools()
