"""
escalation/escalation_manager.py — Human handoff and escalation.

The Agent treats human attention as an expensive resource.  It escalates
only when it cannot decide, lacks permissions, the risk is too high, goals
conflict, or the situation needs physical presence.

Every escalation is recorded as a DECISION-kind EvidenceEntry so the
audit trail shows exactly when a human was involved and why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from agent.core.models import EscalationReason, EvidenceEntry, EvidenceKind, TaskRecord

logger = logging.getLogger(__name__)


@dataclass
class EscalationRecord:
    """One escalation event."""
    escalation_id: str
    task_id: str
    reason: EscalationReason
    priority: str
    contact: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolution_note: Optional[str] = None


class EscalationManager:
    """
    Manages escalations from the Agent to humans.

    In production this would integrate with PagerDuty, Teams, Outlook, etc.
    Here we provide an in-memory implementation with a pluggable notifier.

    The notifier is a callable(EscalationRecord) → None injected at
    construction time, making it easy to swap for real integrations.
    """

    def __init__(
        self,
        notifier: Optional[Callable[[EscalationRecord], None]] = None,
    ) -> None:
        self._records: Dict[str, EscalationRecord] = {}
        self._notifier = notifier or self._default_notifier

    # ------------------------------------------------------------------
    # Core escalation
    # ------------------------------------------------------------------

    def escalate(
        self,
        task: TaskRecord,
        rule: Any,         # EscalationRule from responsibility.py
        evidence: List[EvidenceEntry],
    ) -> EscalationRecord:
        """
        Raise an escalation for a task.

        Records an EscalationRecord, calls the notifier, and returns the record
        so the caller can attach it to the task's evidence.
        """
        import uuid
        message = self._build_message(task, rule, evidence)
        record = EscalationRecord(
            escalation_id=str(uuid.uuid4()),
            task_id=task.task_id,
            reason=rule.reason,
            priority=rule.priority,
            contact=rule.contact,
            message=message,
        )
        self._records[record.escalation_id] = record
        logger.warning(
            "ESCALATION [%s/%s] → %s: %s",
            rule.reason.value, rule.priority, rule.contact, message[:120],
        )
        self._notifier(record)
        return record

    # ------------------------------------------------------------------
    # Acknowledgement (called externally when a human responds)
    # ------------------------------------------------------------------

    def acknowledge(
        self,
        escalation_id: str,
        acknowledged_by: str,
        resolution_note: str = "",
    ) -> Optional[EscalationRecord]:
        record = self._records.get(escalation_id)
        if record is None:
            return None
        record.acknowledged_at = datetime.now(timezone.utc)
        record.acknowledged_by = acknowledged_by
        record.resolution_note = resolution_note
        logger.info(
            "Escalation %s acknowledged by %s: %s",
            escalation_id, acknowledged_by, resolution_note[:80],
        )
        return record

    def to_evidence_entry(self, record: EscalationRecord) -> EvidenceEntry:
        """Convert an escalation record into a DECISION-kind evidence entry."""
        return EvidenceEntry(
            kind=EvidenceKind.DECISION,
            source=record.contact,
            content={
                "escalation_id": record.escalation_id,
                "reason": record.reason.value,
                "priority": record.priority,
                "message": record.message,
                "acknowledged_by": record.acknowledged_by,
                "resolution_note": record.resolution_note,
            },
            task_id=record.task_id,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def pending_escalations(self) -> List[EscalationRecord]:
        return [r for r in self._records.values() if r.acknowledged_at is None]

    def all_escalations(self) -> List[EscalationRecord]:
        return list(self._records.values())

    def summary(self) -> Dict[str, Any]:
        pending = self.pending_escalations()
        return {
            "total": len(self._records),
            "pending": len(pending),
            "pending_ids": [r.escalation_id for r in pending],
        }

    # ------------------------------------------------------------------
    # Default notifier (logs; replace with Teams/PagerDuty/etc.)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_notifier(record: EscalationRecord) -> None:
        logger.warning(
            "[NOTIFY → %s | %s] %s",
            record.contact, record.priority.upper(), record.message,
        )

    # ------------------------------------------------------------------
    # Message builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(
        task: TaskRecord,
        rule: Any,
        evidence: List[EvidenceEntry],
    ) -> str:
        evidence_summary = "; ".join(
            str(e.content)[:80] for e in evidence[-3:]  # last 3 entries
        )
        return (
            f"Task '{task.title}' requires human attention.\n"
            f"Reason: {rule.reason.value}\n"
            f"Retries: {task.retry_count}/{task.max_retries}\n"
            f"Recent evidence: {evidence_summary}"
        )
