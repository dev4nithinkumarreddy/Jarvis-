"""FakeBrain implementation replaying scripted responses for deterministic testing."""

from __future__ import annotations

from typing import Any

from jarvis.brain.base import Brain, BrainResponse
from jarvis.core.types import ToolSpec


class FakeBrain(Brain):
    """Replays scripted responses in order without network or LLM calls."""

    def __init__(self, responses: list[BrainResponse] | None = None) -> None:
        self.responses: list[BrainResponse] = list(responses or [])
        self.call_history: list[dict[str, Any]] = []

    def add_response(self, response: BrainResponse) -> None:
        """Add a response to the replay queue."""
        self.responses.append(response)

    def next_step(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[ToolSpec] | None = None,
    ) -> BrainResponse:
        """Record the call and return the next scripted response."""
        self.call_history.append({
            "messages": [dict(m) for m in messages],
            "tool_specs": list(tool_specs or []),
        })
        if not self.responses:
            return BrainResponse(text="[FakeBrain: No further scripted responses]")
        return self.responses.pop(0)
