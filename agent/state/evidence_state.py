"""
state/evidence_state.py — 證據狀態

Persists all evidence: observation sources, timestamps, query results, and
before/after differences for every action.

Critical design rule: OBSERVATION, INFERENCE, and DECISION entries are stored
in separate buckets and never fused into a single unstructured text blob.
This separation is what allows later auditing to distinguish "what the tool
reported", "what the Agent concluded", and "what a human decided."
"""

from __future__ import annotations

from typing import Dict, List, Optional

from agent.core.models import EvidenceEntry, EvidenceKind


class EvidenceState:
    """
    Append-only evidence log (never mutate or delete individual entries).

    Lookup indexes allow fast retrieval by task, responsibility, or kind.
    """

    def __init__(self) -> None:
        self._store: Dict[str, EvidenceEntry] = {}
        self._by_task: Dict[str, List[str]] = {}            # task_id → evidence_ids
        self._by_responsibility: Dict[str, List[str]] = {}  # resp_id → evidence_ids
        self._by_kind: Dict[EvidenceKind, List[str]] = {k: [] for k in EvidenceKind}

    def add(self, entry: EvidenceEntry) -> None:
        self._store[entry.evidence_id] = entry

        if entry.task_id:
            self._by_task.setdefault(entry.task_id, []).append(entry.evidence_id)

        if entry.responsibility_id:
            self._by_responsibility.setdefault(
                entry.responsibility_id, []
            ).append(entry.evidence_id)

        self._by_kind[entry.kind].append(entry.evidence_id)

    def get(self, evidence_id: str) -> Optional[EvidenceEntry]:
        return self._store.get(evidence_id)

    def get_for_task(self, task_id: str) -> List[EvidenceEntry]:
        ids = self._by_task.get(task_id, [])
        return [self._store[i] for i in ids if i in self._store]

    def get_for_responsibility(self, responsibility_id: str) -> List[EvidenceEntry]:
        ids = self._by_responsibility.get(responsibility_id, [])
        return [self._store[i] for i in ids if i in self._store]

    def get_by_kind(self, kind: EvidenceKind) -> List[EvidenceEntry]:
        ids = self._by_kind.get(kind, [])
        return [self._store[i] for i in ids if i in self._store]

    def observations(self) -> List[EvidenceEntry]:
        return self.get_by_kind(EvidenceKind.OBSERVATION)

    def inferences(self) -> List[EvidenceEntry]:
        return self.get_by_kind(EvidenceKind.INFERENCE)

    def human_decisions(self) -> List[EvidenceEntry]:
        return self.get_by_kind(EvidenceKind.DECISION)

    def record_human_decision(
        self,
        source: str,
        content,
        task_id: Optional[str] = None,
        responsibility_id: Optional[str] = None,
    ) -> EvidenceEntry:
        """Convenience method to record a human approval / override."""
        entry = EvidenceEntry(
            kind=EvidenceKind.DECISION,
            source=source,
            content=content,
            task_id=task_id,
            responsibility_id=responsibility_id,
        )
        self.add(entry)
        return entry

    def summary(self) -> Dict:
        return {
            "total_entries": len(self._store),
            "observations": len(self._by_kind[EvidenceKind.OBSERVATION]),
            "inferences": len(self._by_kind[EvidenceKind.INFERENCE]),
            "human_decisions": len(self._by_kind[EvidenceKind.DECISION]),
        }
