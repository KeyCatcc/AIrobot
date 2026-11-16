from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Union
import os

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


DEFAULT_PCM = "pcm"
PCM_S16LE = "pcm_s16le"
OGG_OPUS = "ogg_opus"
SPEECH_OPUS = "speech_opus"


@dataclass
class DemoConfig:
    company_context_enabled: bool = False
    api_key: str = ""
    search_api_key: str = ""
    model: str = "1.2.6.1"
    instructions: str = "You are a creative assistant that helps with design tasks."
    speaker: str = "zh_male_xiaotian_jupiter_bigtts"
    asr_format: str = DEFAULT_PCM
    tts_format: str = PCM_S16LE
    endpoint_url: str = "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
    raw: Dict[str, Any] = field(default_factory=dict)


def _section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    value = data.get(name)
    if isinstance(value, dict):
        return value
    return {}


def load_config(path: Union[str, Path] = "config.toml") -> DemoConfig:
    config_path = Path(path)
    with config_path.open("rb") as f:
        data = tomllib.load(f)

    auth = _section(data, "auth")
    search = _section(data, "search")
    session = _section(data, "session")
    endpoint = _section(data, "endpoint")

    cfg = DemoConfig(raw=data)
    cfg.api_key = auth.get("api_key") or cfg.api_key
    cfg.search_api_key = search.get("api_key") or cfg.search_api_key
    cfg.model = session.get("model") or cfg.model
    cfg.instructions = session.get("instructions") or cfg.instructions
    cfg.speaker = session.get("speaker") or cfg.speaker
    cfg.asr_format = session.get("asr_format") or cfg.asr_format
    cfg.tts_format = session.get("tts_format") or cfg.tts_format
    cfg.endpoint_url = endpoint.get("url") or cfg.endpoint_url
    profile = os.environ.get("COMPANY_PROFILE") or session.get("company_profile_file")
    if profile:
        from company_context import build_company_instructions
        profile_path = Path(profile).expanduser()
        if not profile_path.is_absolute():
            profile_path = config_path.resolve().parent / profile_path
        # Company reception replaces the generic demo role rather than appending
        # potentially conflicting personas from the original demo config.
        cfg.instructions = build_company_instructions(profile_path)
        cfg.company_context_enabled = True
        print("[Company context] loaded:", profile_path.resolve(), "(new sessions only)")
    if (os.environ.get('ARDY_ENABLED') == '1' or os.environ.get('RAI_WAVE_BRIDGE') == '1') and os.environ.get('ROBOT_DIALOGUE_ENABLED', '1') == '1':
        from robot_dialogue import build_robot_instructions
        base = cfg.instructions
        if base == DemoConfig.instructions:
            base = ''
        execution_enabled = (os.environ.get('RAI_WAVE_BRIDGE') == '1' or
                             all(os.environ.get(key) == '1' for key in
                                 ('ARDY_PIPELINE', 'ARDY_UPLOAD', 'ARDY_AUTOPLAY')))
        cfg.instructions = build_robot_instructions(base, execution_enabled)
        print('[Robot dialogue] embodied instructions loaded (new sessions only)')
    return cfg
