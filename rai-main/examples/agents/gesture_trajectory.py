"""Pure simulation trajectory builder; relative degrees, no ROS or hardware."""
import json
import math
from pathlib import Path


def build_trajectory(gesture, path):
    if not path:
        raise ValueError("Set RAI_GESTURE_LIBRARY to custom_gestures.json")
    library = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    poses = library["base_gestures"]
    sequences = {
        "welcome": [("welcome_gesture", 0.8)],
        "wave": [("wave_both_prepare", 1.2), ("wave_both_out", 0.3),
                 ("wave_both_in", 0.3), ("wave_both_out", 0.3)],
        "present_left": [("present_left", 0.8)],
        "present_right": [("present_right", 0.8)],
        "nod": [("nod_emphatic", 0.5)],
    }
    # rest is only a simulation reference, NOT a validated hardware Home Pose.
    configured = library.get("action_sequences", {}).get(gesture)
    if configured is not None:
        if not isinstance(configured, list) or not configured:
            raise ValueError("Invalid action sequence: " + gesture)
        selected = []
        for item in configured:
            duration = float(item["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Invalid step duration")
            selected.append((item.get("gesture") or item.get("gesture_name"), duration))
    else:
        selected = sequences[gesture]
    steps = selected + [("rest", 0.8)]
    names = ["rest"] + [name for name, _ in steps]
    for name in names:
        row = poses[name]
        if len(row) != 12 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                 or not math.isfinite(v) for v in row):
            raise ValueError("Invalid 12-component pose: " + name)
    current = poses["rest"]
    frames = []
    for name, duration in steps:
        target = poses[name]
        for index in range(1, max(1, round(duration * 30)) + 1):
            t = index / max(1, round(duration * 30))
            weight = t * t * (3 - 2 * t)
            frames.append([a + (b - a) * weight for a, b in zip(current, target)])
        current = target
    return frames, steps
