"""
tools/base_tool.py — Base interface for all Agent tools.

All tools — Zabbix, NAS, AD, SQL, firewall API, backup query, etc. —
must implement this interface.  The BaseTool design enforces:

* Explicit risk level declaration: the Agent uses this for permission checks.
* Structured ToolResult: success/data/error — not bare exceptions or strings.
* A dry_run() capability so the Agent can plan without side-effects.

Tools are interfaces, not owners.  They point to data; they do not import it
into the Agent's local state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.core.models import RiskLevel


@dataclass
class ToolResult:
    """
    Structured result from a tool execution.

    success  – True only if the operation completed without error.
    data     – the raw payload returned by the tool (dict preferred).
    error    – human-readable error message when success=False.
    metadata – additional context (execution time, tool version, …).
    """
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """
    Abstract base for every Agent capability.

    Subclasses represent real integrations (backup query, SNMP, REST APIs,
    PowerShell remoting, …) or test stubs.

    Mandatory properties
    --------------------
    name        – unique identifier matching CapabilityState registration
    risk_level  – highest RiskLevel this tool can reach (read-only tools
                  should always return READ_ONLY)
    description – human-readable one-liner used in audit logs and escalations
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def risk_level(self) -> RiskLevel: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> ToolResult:
        """
        Execute the tool with the given parameters.

        This method MUST be idempotent for READ_ONLY tools.
        For mutating tools it MUST return success=False rather than raise
        if the operation did not complete cleanly.
        """
        ...

    def dry_run(self, params: Dict[str, Any]) -> ToolResult:
        """
        Simulate execution without side-effects.

        Default implementation returns a placeholder result.  Subclasses
        should override to provide realistic simulation.
        """
        return ToolResult(
            success=True,
            data={"dry_run": True, "params": params},
            metadata={"tool": self.name, "risk_level": self.risk_level.value},
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, risk={self.risk_level.value})"
