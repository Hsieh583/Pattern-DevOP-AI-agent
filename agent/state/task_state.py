"""
state/task_state.py — 任務狀態

Tracks all active, waiting, and historical tasks: what is being handled,
what is blocked, when to retry, and who approved what.

Every task in this store is traceable back to a Gap, which in turn traces
back to an Observation and a Responsibility.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from agent.core.models import TaskRecord, TaskStatus


class TaskState:
    """
    In-memory task registry.

    In production this would be backed by a persistent store (e.g. SQL).
    The interface is intentionally storage-agnostic.
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._gap_index: Dict[str, Set[str]] = {}  # gap_id → set of task_ids

    def add(self, task: TaskRecord) -> None:
        self._tasks[task.task_id] = task
        self._gap_index.setdefault(task.gap_id, set()).add(task.task_id)

    def get(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def has_active_task_for_gap(self, gap_id: str) -> bool:
        """Return True if there is already a non-terminal task for this gap."""
        terminal = {TaskStatus.RESOLVED, TaskStatus.FAILED, TaskStatus.TIMED_OUT}
        for task_id in self._gap_index.get(gap_id, set()):
            task = self._tasks.get(task_id)
            if task and task.status not in terminal:
                return True
        return False

    def get_active_for_responsibility(self, responsibility_id: str) -> List[TaskRecord]:
        """Return tasks in active (non-terminal) states for a given responsibility."""
        active_statuses = {
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.WAITING_RETRY,
        }
        return [
            t for t in self._tasks.values()
            if t.responsibility_id == responsibility_id and t.status in active_statuses
        ]

    def get_waiting_approval(self) -> List[TaskRecord]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.WAITING_APPROVAL]

    def get_escalated(self) -> List[TaskRecord]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.ESCALATED]

    def all_tasks(self) -> List[TaskRecord]:
        return list(self._tasks.values())

    def summary(self) -> Dict:
        by_status: Dict[str, int] = {}
        for t in self._tasks.values():
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        return {"total": len(self._tasks), "by_status": by_status}
