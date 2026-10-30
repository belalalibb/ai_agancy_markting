"""``content`` — drafts, revisions and the human approval loop (SPRINT-06 Part B, D-055).

Nothing in this module talks to Meta or publishes anything: it ends at ``scheduled``.
"""

from src.modules.content.router import router

__all__ = ["router"]
