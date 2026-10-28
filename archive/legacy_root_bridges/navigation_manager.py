"""Mostek zgodności do planowania nawigacji zachowanego starego runtime."""

from archive.legacy_runtime.navigation_manager import (
    CHAT_SCREENS,
    NAVIGATION_GRAPH,
    SAFE_ANCHORS,
    NavigationEdge,
    NavigationManager,
    NavigationPlan,
    NavigationStatus,
    NavigationTargetType,
    NavigationTask,
)

__all__ = [
    "CHAT_SCREENS",
    "NAVIGATION_GRAPH",
    "SAFE_ANCHORS",
    "NavigationEdge",
    "NavigationManager",
    "NavigationPlan",
    "NavigationStatus",
    "NavigationTargetType",
    "NavigationTask",
]
