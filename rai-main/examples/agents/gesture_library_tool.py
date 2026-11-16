"""Read real pose assets; produce an in-memory preview only, never ROS commands."""

import json
import os
from typing import Literal, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict


class GestureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gesture: Literal["welcome", "wave", "present_left", "present_right", "nod", "none"]


class RobotGestureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gesture: Literal["welcome_gesture", "none"]


class ServerobotGestureTool(BaseTool):
    name: str = "perform_gesture"
    description: str = (
        "Choose an allowed library action for the ROBOT reply by meaning. "
        "welcome_gesture: actually welcoming a visitor. none: all other cases, "
        "including negations, quotations, facts and uncertainty. "
        "Only these two choices are enabled. Execution mode is operator configured."
    )
    args_schema: Type[BaseModel] = RobotGestureInput

    def _run(self, gesture: str) -> str:
        from serverobot_adapter import run_selected
        return json.dumps(run_selected(gesture), ensure_ascii=False)


def preview(gesture):
    if gesture == "none":
        print("[LIBRARY SKIP] no matching gesture", flush=True)
        return {"status": "no_action", "gesture": "none", "hardware_executed": False}
    from gesture_trajectory import build_trajectory
    frames, steps = build_trajectory(gesture, os.environ.get("RAI_GESTURE_LIBRARY"))
    print("[LIBRARY PREVIEW]", gesture, "poses=", [n for n, _ in steps],
          "frames=", len(frames), "simulated_home=rest", flush=True)
    if os.environ.get("RAI_GESTURE_EXECUTOR") == "1":
        from gesture_executor import EXECUTOR
        return EXECUTOR.submit(gesture, frames, 30)
    return {"status": "trajectory_preview_generated", "gesture": gesture,
            "frames": len(frames), "fps": 30, "hardware_executed": False,
            "note": "No real-time playback; rest is simulation-only home."}


class GestureLibraryTool(BaseTool):
    name: str = "perform_gesture"
    description: str = (
        "Select a gesture matching the ROBOT'S reply, or none. "
        "welcome: actual welcoming; wave: actual farewell; "
        "present_left/right: explicit positive instruction to view that direction ONLY; "
        "nod: explicit agreement with someone, not an arbitrary factual sentence. "
        "Choose none for ordinary facts, missing information, quoted titles, "
        "hypotheticals, negated actions or uncertainty. Never invent a direction or "
        "replace a forbidden direction with the opposite. none generates no trajectory. "
        "All other choices generate simulation previews only."
    )
    args_schema: Type[BaseModel] = GestureInput

    def _run(self, gesture: str) -> str:
        return json.dumps(preview(gesture), ensure_ascii=False)
