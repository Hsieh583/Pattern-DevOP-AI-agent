"""
state/world_state.py — 世界狀態

Tracks every known asset, its current health status, and service/account
relationships.  This is the Agent's live picture of "what exists and how
it's doing right now."

The Agent never keeps data hostage on its own disk; it knows where assets
live, how to query them, and how confident it is in each data point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent.core.models import Asset, ObservationResult


@dataclass
class AssetHealth:
    """Current health snapshot for one Asset."""
    asset_id: str
    is_healthy: bool = True
    last_seen: Optional[datetime] = None
    last_error: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    raw_status: Any = None

    def update(self, is_healthy: bool, metrics: Dict[str, float], raw: Any = None) -> None:
        self.is_healthy = is_healthy
        self.metrics.update(metrics)
        self.raw_status = raw
        self.last_seen = datetime.now(timezone.utc)


class WorldState:
    """
    Persistent world model.

    Stores assets and their health.  The Agent updates this after every
    observation and verification pass so the world model stays current.
    """

    def __init__(self) -> None:
        self._assets: Dict[str, Asset] = {}
        self._health: Dict[str, AssetHealth] = {}
        # Map from responsibility_id → latest ObservationResult
        self._latest_observations: Dict[str, ObservationResult] = {}

    # --- Asset registry ---

    def register_asset(self, asset: Asset) -> None:
        self._assets[asset.asset_id] = asset
        if asset.asset_id not in self._health:
            self._health[asset.asset_id] = AssetHealth(asset_id=asset.asset_id)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        return self._assets.get(asset_id)

    def list_assets(self, asset_type: Optional[str] = None) -> List[Asset]:
        if asset_type is None:
            return list(self._assets.values())
        return [a for a in self._assets.values() if a.asset_type == asset_type]

    # --- Health tracking ---

    def update_health(
        self,
        asset_id: str,
        is_healthy: bool,
        metrics: Optional[Dict[str, float]] = None,
        raw: Any = None,
    ) -> None:
        if asset_id not in self._health:
            self._health[asset_id] = AssetHealth(asset_id=asset_id)
        self._health[asset_id].update(is_healthy, metrics or {}, raw)

    def get_health(self, asset_id: str) -> Optional[AssetHealth]:
        return self._health.get(asset_id)

    def unhealthy_assets(self) -> List[str]:
        return [aid for aid, h in self._health.items() if not h.is_healthy]

    # --- Observation store ---

    def record_observation(
        self, responsibility_id: str, observation: ObservationResult
    ) -> None:
        self._latest_observations[responsibility_id] = observation

    def latest_observation(self, responsibility_id: str) -> Optional[ObservationResult]:
        return self._latest_observations.get(responsibility_id)

    # --- Summary ---

    def summary(self) -> Dict[str, Any]:
        return {
            "total_assets": len(self._assets),
            "unhealthy_count": len(self.unhealthy_assets()),
            "unhealthy_ids": self.unhealthy_assets(),
            "responsibilities_observed": list(self._latest_observations.keys()),
        }
