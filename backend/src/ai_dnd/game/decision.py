"""A small, validated decision for one batch of player inputs.

The model classifies each still unresolved input. The orchestrator owns every
state change and never publishes this object as narration.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_index: int = Field(ge=0)
    actor_id: str
    kind: Literal["stay", "move", "engage", "tool", "defer"]
    scene_id: str | None = None
    encounter_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # A move or resolved tool may leave a distinct action in the same input.
    continue_after: bool = False


class TurnDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionDecision]
