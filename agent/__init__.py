"""
Pattern-DevOP-AI-agent
======================
An operational responsibility model for a persistent DevOps AI Agent.

The Agent is defined by a continuous loop:

    觀察環境 → 發現差距 → 建立任務 → 規劃處置
        ↑                              ↓
    更新世界模型 ← 驗證結果 ← 執行工具 ← 權限判斷

Its core unit is the Responsibility:

    責任 = 範圍 + 目標狀態 + 感測方法 + 可用工具 + 行動界線 + 驗證條件 + 升級規則
"""

from agent.core.agent_loop import AgentLoop
from agent.core.responsibility import Responsibility
from agent.core.models import (
    AgentDecision,
    EvidenceEntry,
    EvidenceKind,
    ObservationResult,
    TaskRecord,
    TaskStatus,
)

__all__ = [
    "AgentLoop",
    "Responsibility",
    "AgentDecision",
    "EvidenceEntry",
    "EvidenceKind",
    "ObservationResult",
    "TaskRecord",
    "TaskStatus",
]
