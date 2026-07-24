"""
core/models.py — Shared data-model definitions used throughout the agent.

Design principles enforced here
--------------------------------
* Observations (tool facts), Agent inferences, and Human decisions are stored
  in separate EvidenceKind buckets — they must never be merged into a single
  unstructured text field.
* Every TaskRecord carries a stop_condition so the Agent cannot remain in
  "processing" forever.
* Risk levels drive reversibility checks; only read-only / low-risk actions
  may be executed autonomously without human approval.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Lifecycle state of a TaskRecord."""
    PENDING = "pending"          # created, not yet started
    IN_PROGRESS = "in_progress"  # being actively handled
    WAITING_RETRY = "waiting_retry"
    WAITING_APPROVAL = "waiting_approval"
    ESCALATED = "escalated"      # handed to a human
    RESOLVED = "resolved"        # verified success
    FAILED = "failed"            # stop condition reached without resolution
    TIMED_OUT = "timed_out"


class RiskLevel(str, Enum):
    """
    Reversibility-driven risk classification.

    READ_ONLY   – queries, status checks; fully autonomous
    LOW         – restarts, notification sends; autonomous with logging
    MEDIUM      – config changes, account unlocks; requires audit trail
    HIGH        – firewall changes, credential resets; requires approval
    CRITICAL    – deletions, schema changes, network cuts; requires human sign-off
    """
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceKind(str, Enum):
    """
    The three legally-distinct evidence sources.

    OBSERVATION  – raw facts returned by a tool (trusted as ground truth)
    INFERENCE    – the Agent's reasoning or classification of observations
    DECISION     – a human's explicit instruction, approval, or override
    """
    OBSERVATION = "observation"
    INFERENCE = "inference"
    DECISION = "decision"


class StopReason(str, Enum):
    """Why a task stopped."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RISK_EXCEEDED = "risk_exceeded"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"
    NEEDS_ONSITE = "needs_onsite"
    NO_NEW_EVIDENCE = "no_new_evidence"   # no change after max retries
    GOAL_CONFLICT = "goal_conflict"
    HUMAN_OVERRIDE = "human_override"


class EscalationReason(str, Enum):
    """Why the Agent escalated to a human."""
    UNKNOWN_FAILURE = "unknown_failure"
    RISK_THRESHOLD = "risk_threshold"
    REPEATED_FAILURE = "repeated_failure"
    GOAL_CONFLICT = "goal_conflict"
    PERMISSION_DENIED = "permission_denied"
    AUDIT_DEADLINE = "audit_deadline"
    REQUIRES_PHYSICAL = "requires_physical"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    """A managed entity in the world (server, device, account, service, …)."""
    asset_id: str
    name: str
    asset_type: str            # e.g. "server", "endpoint", "service", "account"
    location: Optional[str] = None
    tags: tuple = field(default_factory=tuple)
    dependencies: tuple = field(default_factory=tuple)  # asset_ids this depends on


@dataclass(frozen=True)
class Threshold:
    """A numeric goal threshold (capacity, success-rate, latency, …)."""
    metric: str
    operator: str   # "<", "<=", "==", ">=", ">"
    value: float
    unit: str = ""

    def is_violated(self, measured: float) -> bool:
        ops = {
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
            ">=": lambda a, b: a >= b,
            ">": lambda a, b: a > b,
        }
        if self.operator not in ops:
            raise ValueError(f"Unknown operator: {self.operator!r}")
        # Threshold is a *goal* — it is violated when the goal is NOT met.
        return not ops[self.operator](measured, self.value)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class EvidenceEntry:
    """
    A single piece of evidence, strictly typed by kind.

    Observations come from tools; inferences come from the Agent;
    decisions come from humans. They must not be mixed.
    """
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: EvidenceKind = EvidenceKind.OBSERVATION
    source: str = ""            # tool name, agent name, or approver identity
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content: Any = None         # raw payload (dict, str, …)
    task_id: Optional[str] = None
    responsibility_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "task_id": self.task_id,
            "responsibility_id": self.responsibility_id,
        }


# ---------------------------------------------------------------------------
# Observations & Gaps
# ---------------------------------------------------------------------------


@dataclass
class ObservationResult:
    """Structured outcome of a single observation pass for one responsibility."""
    responsibility_id: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    asset_statuses: Dict[str, Any] = field(default_factory=dict)
    metric_values: Dict[str, float] = field(default_factory=dict)
    raw_evidence: List[EvidenceEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


@dataclass
class Gap:
    """
    A discovered discrepancy between world state and goal state.

    The Agent must NOT create tasks without a Gap — tasks need justification.

    gap_id is deterministic: same responsibility + threshold metric → same ID.
    This ensures that re-observing the same violation does not open duplicate
    tasks across ticks.
    """
    gap_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    responsibility_id: str = ""
    asset_id: Optional[str] = None
    description: str = ""
    severity: str = "medium"    # low | medium | high | critical
    threshold_violated: Optional[Threshold] = None
    observed_value: Optional[float] = None
    supporting_evidence: List[str] = field(default_factory=list)  # evidence_ids

    @staticmethod
    def make_id(responsibility_id: str, metric: str, asset_id: Optional[str] = None) -> str:
        """Return a deterministic ID for the (responsibility, metric, asset) triple."""
        import hashlib
        key = f"{responsibility_id}:{metric}:{asset_id or ''}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    """
    A unit of work derived from a Gap.

    Every task has a stop_condition so it cannot remain in 'in_progress' forever.
    All approvals and retry history are recorded here.
    """
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    responsibility_id: str = ""
    gap_id: str = ""
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0
    max_retries: int = 3
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    stop_reason: Optional[StopReason] = None
    stopped_at: Optional[datetime] = None
    evidence_ids: List[str] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def can_retry(self) -> bool:
        return (
            self.status in (TaskStatus.WAITING_RETRY, TaskStatus.FAILED)
            and self.retry_count < self.max_retries
        )

    def mark_stop(self, reason: StopReason, status: TaskStatus) -> None:
        self.stop_reason = reason
        self.status = status
        self.stopped_at = datetime.now(timezone.utc)
        self.touch()


# ---------------------------------------------------------------------------
# Agent decisions (used in OODA logging)
# ---------------------------------------------------------------------------


@dataclass
class AgentDecision:
    """
    Records a single decision the Agent made during its loop.

    Kept distinct from EvidenceEntry so that DECISION entries only represent
    human approvals/overrides, while AgentDecision entries represent autonomous
    agent choices.
    """
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: Optional[str] = None
    responsibility_id: Optional[str] = None
    made_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    action_taken: str = ""
    rationale: str = ""
    tool_used: Optional[str] = None
    outcome: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.READ_ONLY
