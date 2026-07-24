"""
state/goal_state.py — 目標狀態

Maintains the organisation's expectations: backup success rates, patch
deadlines, capacity thresholds, service windows, and audit requirements.

Goals are owned by humans; the Agent observes them, never modifies them
autonomously.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from agent.core.models import Threshold


@dataclass
class ServiceWindow:
    """Time-bounded permission for disruptive actions."""
    window_id: str
    name: str
    start: datetime
    end: datetime
    allowed_actions: List[str] = field(default_factory=list)

    def is_active(self) -> bool:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return self.start <= now <= self.end


@dataclass
class AuditRequirement:
    """Compliance deadline for a set of assets."""
    requirement_id: str
    description: str
    deadline: datetime
    scope: List[str] = field(default_factory=list)  # asset_ids
    is_satisfied: bool = False

    def days_remaining(self) -> float:
        from datetime import timezone
        delta = self.deadline - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400


class GoalState:
    """
    Repository of organisational goals and compliance requirements.

    The Agent reads goals to identify gaps.  Changing a goal is a human
    decision recorded in EvidenceState with kind=DECISION.
    """

    def __init__(self) -> None:
        self._thresholds: Dict[str, List[Threshold]] = {}      # responsibility_id → thresholds
        self._service_windows: Dict[str, ServiceWindow] = {}
        self._audit_requirements: Dict[str, AuditRequirement] = {}

    # --- Thresholds ---

    def set_thresholds(self, responsibility_id: str, thresholds: List[Threshold]) -> None:
        self._thresholds[responsibility_id] = list(thresholds)

    def get_thresholds(self, responsibility_id: str) -> List[Threshold]:
        return self._thresholds.get(responsibility_id, [])

    def all_thresholds(self) -> Dict[str, List[Threshold]]:
        return dict(self._thresholds)

    # --- Service windows ---

    def add_service_window(self, window: ServiceWindow) -> None:
        self._service_windows[window.window_id] = window

    def active_windows(self) -> List[ServiceWindow]:
        return [w for w in self._service_windows.values() if w.is_active()]

    # --- Audit requirements ---

    def add_audit_requirement(self, req: AuditRequirement) -> None:
        self._audit_requirements[req.requirement_id] = req

    def overdue_requirements(self) -> List[AuditRequirement]:
        return [
            r for r in self._audit_requirements.values()
            if not r.is_satisfied and r.days_remaining() <= 0
        ]

    def urgent_requirements(self, threshold_days: float = 7.0) -> List[AuditRequirement]:
        return [
            r for r in self._audit_requirements.values()
            if not r.is_satisfied and 0 < r.days_remaining() <= threshold_days
        ]

    def get_audit_requirement(self, req_id: str) -> Optional[AuditRequirement]:
        return self._audit_requirements.get(req_id)

    # --- Summary ---

    def summary(self) -> Dict:
        return {
            "responsibilities_with_goals": list(self._thresholds.keys()),
            "active_service_windows": [w.name for w in self.active_windows()],
            "overdue_audit_requirements": [r.description for r in self.overdue_requirements()],
        }
