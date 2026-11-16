"""Strict, local-only registry for long-form CSV dance motions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


DEFAULT_DANCE_CATALOG = Path(__file__).resolve().parents[1] / "configs" / "dances" / "dance_catalog.json"


def _version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def load_dance_catalog(path: Path = DEFAULT_DANCE_CATALOG) -> tuple[dict[str, dict], str]:
    path = path.resolve(strict=True)
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    rows = document.get("dances")
    if not isinstance(rows, list):
        raise ValueError("dance catalog must contain a dances list")

    catalog: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each dance catalog entry must be an object")
        name = row.get("name")
        csv_path = row.get("csv_path")
        if not isinstance(name, str) or not name or name in catalog:
            raise ValueError("dance name must be unique and non-empty")
        if not isinstance(csv_path, str) or not csv_path:
            raise ValueError(f"dance {name!r} must define csv_path")
        enabled = bool(row.get("enabled", False))
        # Disabled entries document planned assets without preventing the
        # normal action service from starting before conversion/upload.
        resolved_csv = (path.parent / csv_path).resolve(strict=enabled)
        fps = float(row.get("fps", 30.0))
        speed = float(row.get("speed", 0.5))
        if not 1.0 <= fps <= 60.0:
            raise ValueError(f"dance {name!r}: fps must be within [1, 60]")
        if not 0.05 <= speed <= 1.0:
            raise ValueError(f"dance {name!r}: speed must be within [0.05, 1.0]")
        if bool(row.get("loop", False)):
            raise ValueError(f"dance {name!r}: looping is not allowed through the action service")
        if not bool(row.get("return_home", True)):
            raise ValueError(f"dance {name!r}: return_home must be true")
        item = dict(row)
        item.update(
            name=name,
            csv_path=str(resolved_csv),
            enabled=enabled,
            fps=fps,
            speed=speed,
            loop=False,
            return_home=True,
            play_audio=bool(row.get("play_audio", False)),
            use_wall_clock_motion=bool(row.get("use_wall_clock_motion", True)),
            motion_poll_hz=float(row.get("motion_poll_hz", 100.0)),
            no_waist=bool(row.get("no_waist", True)),
            home_duration_sec=float(row.get("home_duration_sec", 6.0)),
        )
        if not 1.0 <= item["motion_poll_hz"] <= 200.0:
            raise ValueError(f"dance {name!r}: motion_poll_hz must be within [1, 200]")
        if not 0.5 <= item["home_duration_sec"] <= 30.0:
            raise ValueError(f"dance {name!r}: home_duration_sec must be within [0.5, 30]")
        audio_path = row.get("audio_path", "")
        if audio_path:
            item["audio_path"] = str((path.parent / str(audio_path)).resolve(strict=enabled))
        catalog[name] = item
    return catalog, _version(raw)
