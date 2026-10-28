"""Safe, local-only Player Memory + Conversation System for Last Z.

This package deliberately has no dependency on ADB, BlueStacks, screenshots,
or the Action V1 model files. It accepts structured observations from a future
capture adapter and produces only reviewed executor handoffs.
"""

from .service import ConversationService

__all__ = ["ConversationService"]

