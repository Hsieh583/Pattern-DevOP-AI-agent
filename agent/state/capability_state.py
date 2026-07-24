"""
state/capability_state.py — 能力狀態

Tracks which tools are currently available to the Agent: their scope of
action, risk level, and permission expiry.

The Agent can REQUEST new capabilities through the escalation channel, but
it cannot grant itself permissions — the authorisation source is always
external to the Agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from agent.core.models import RiskLevel


@dataclass
class ToolCapability:
    """
    Describes one registered tool's current availability.

    tool_name      – matches BaseTool.name
    is_available   – whether the tool may be called right now
    risk_level     – highest-risk action this tool can perform
    scope          – description of what systems/assets this tool touches
    permission_expires  – when the current permission lapses (None = permanent)
    """
    tool_name: str
    is_available: bool = True
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    scope: str = ""
    permission_expires: Optional[datetime] = None

    def is_expired(self) -> bool:
        if self.permission_expires is None:
            return False
        return datetime.now(timezone.utc) >= self.permission_expires

    def effective_availability(self) -> bool:
        return self.is_available and not self.is_expired()


class CapabilityState:
    """
    Registry of all tools the Agent knows about and whether they are usable.

    A tool that appears here but has is_available=False (or has expired
    permissions) will be skipped by the AgentLoop — the Agent cannot
    work around this restriction on its own.
    """

    def __init__(self) -> None:
        self._capabilities: Dict[str, ToolCapability] = {}

    def register(self, capability: ToolCapability) -> None:
        self._capabilities[capability.tool_name] = capability

    def is_tool_available(self, tool_name: str) -> bool:
        cap = self._capabilities.get(tool_name)
        if cap is None:
            return False
        return cap.effective_availability()

    def get(self, tool_name: str) -> Optional[ToolCapability]:
        return self._capabilities.get(tool_name)

    def available_tools(self) -> List[str]:
        return [
            name for name, cap in self._capabilities.items()
            if cap.effective_availability()
        ]

    def unavailable_tools(self) -> List[str]:
        return [
            name for name, cap in self._capabilities.items()
            if not cap.effective_availability()
        ]

    def disable(self, tool_name: str) -> None:
        cap = self._capabilities.get(tool_name)
        if cap:
            cap.is_available = False

    def enable(self, tool_name: str) -> None:
        cap = self._capabilities.get(tool_name)
        if cap:
            cap.is_available = True

    def summary(self) -> Dict:
        return {
            "total_tools": len(self._capabilities),
            "available": self.available_tools(),
            "unavailable": self.unavailable_tools(),
        }
