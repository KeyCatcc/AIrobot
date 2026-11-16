"""Simulation only: no audio, ROS commands, or hardware control."""

from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict


class WaveHandInput(BaseModel):
    """The action accepts no motion parameters."""

    model_config = ConfigDict(extra="forbid")


class WaveHandTool(BaseTool):
    name: str = "wave_hand"
    description: str = (
        "Simulate one silent wave for an actual request to wave, beckon, or "
        "physically greet someone. Do not call for ability questions, descriptions, "
        "hypothetical discussion, or negated requests. No real hardware is controlled."
    )
    args_schema: Type[BaseModel] = WaveHandInput

    def _run(self) -> str:
        print("[SIMULATION] wave_hand invoked; no hardware controlled.", flush=True)
        return '{"status":"simulated","action":"wave_hand","hardware_executed":false}'
