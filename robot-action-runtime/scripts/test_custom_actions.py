#!/usr/bin/env python3
"""自定义动作 JSON 的解析、校验与实机下发工具。

默认只解析并打印动作；只有显式传入 --execute 才会向实机话题发布。
同名动作可同时包含身体轨迹与手指姿态；只配置其中一种时只下发对应通道。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GESTURE_DIR = ROOT / "configs" / "gestures"
DEFAULT_ACTIONS = GESTURE_DIR / "custom_actions.json"
DEFAULT_GESTURES = GESTURE_DIR / "custom_gestures.json"
DEFAULT_CONFIG = ROOT / "configs" / "config.yaml"
DEFAULT_MOTOR_MAP = ROOT / "configs" / "robot" / "ymbot_h2" / "mapping" / "motor.yaml"

# custom_gestures.json 的 12 维顺序（单位：度，相对中位）。
LEGACY_JOINT_NAMES = [
    "neck_yaw_joint",
    "neck_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_pitch_joint",
    "left_elbow_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_pitch_joint",
    "right_elbow_yaw_joint",
]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("缺少 PyYAML，请在项目运行环境安装 requirements 后重试") from exc
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML 顶层必须是对象: {path}")
    return value


class GestureLibrary:
    def __init__(self, actions_path: Path, gestures_path: Path) -> None:
        self.actions_path = actions_path
        self.gestures_path = gestures_path
        self.actions = load_json(actions_path)
        self.gestures = load_json(gestures_path)
        self.base: Dict[str, Any] = dict(self.gestures.get("base_gestures") or {})
        self.sequences: Dict[str, Any] = dict(self.gestures.get("action_sequences") or {})
        self.fingers: Dict[str, Any] = dict(self.gestures.get("finger_gestures") or {})
        self.command_mappings: Dict[str, Any] = dict(self.actions.get("command_mappings") or {})
        self.action_mappings: Dict[str, Any] = dict(self.actions.get("action_mappings") or {})
        self.aliases: Dict[str, Any] = dict(self.actions.get("action_aliases") or {})
        self.durations: Dict[str, Any] = dict(self.actions.get("action_durations") or {})

    def has_motion(self, name: str) -> bool:
        return name in self.base or name in self.sequences

    def has_action(self, name: str) -> bool:
        return self.has_motion(name) or name in self.fingers

    def _alias_target(self, text: str) -> str | None:
        folded = text.casefold()
        for canonical, aliases in self.aliases.items():
            candidates = aliases if isinstance(aliases, list) else [aliases]
            if canonical.casefold() == folded:
                return canonical
            if any(str(item).casefold() == folded for item in candidates):
                return canonical
        return None

    def resolve(self, value: str, *, command: bool = False) -> Tuple[str, List[str]]:
        text = str(value).strip()
        if not text:
            raise ValueError("动作名/触发词不能为空")
        trace = [text]

        if command:
            command_value = None
            folded = text.casefold()
            for key, mapped in self.command_mappings.items():
                if key.casefold() == folded:
                    command_value = mapped
                    break
            if command_value is None:
                raise KeyError(f"未知触发指令: {text}")
            text = str(command_value)
            trace.append(text)

        # 有些旧配置经过 command -> alias -> action_mapping 多次跳转。
        seen = set()
        for _ in range(8):
            if text in seen:
                break
            seen.add(text)
            if self.has_action(text):
                return text, trace
            alias = self._alias_target(text)
            if alias is not None and alias != text:
                text = alias
                trace.append(text)
                continue
            mapped = self.action_mappings.get(text)
            if mapped is not None and isinstance(mapped, str) and mapped != text:
                text = mapped
                trace.append(text)
                continue
            break
        if self.has_action(text):
            return text, trace
        raise KeyError(
            f"解析到 {text!r}，但 base_gestures/action_sequences/finger_gestures 中没有该动作"
        )

    def duration_for(self, requested: str, resolved: str, default: float) -> float:
        for key in (requested, resolved):
            try:
                value = float(self.durations[key])
                if value > 0:
                    return value
            except (KeyError, TypeError, ValueError):
                pass
        return default

    def steps(self, name: str, default_duration: float) -> List[Tuple[str, np.ndarray, float]]:
        if name in self.base:
            angles = _angles12(self.base[name], name)
            return [(name, angles, default_duration)]
        raw_steps = self.sequences.get(name)
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"动作序列为空或格式错误: {name}")
        result = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise ValueError(f"序列 {name} 第 {index + 1} 步不是对象")
            gesture = str(raw.get("gesture") or raw.get("gesture_name") or "").strip()
            if gesture not in self.base:
                raise KeyError(f"序列 {name} 第 {index + 1} 步引用不存在手势: {gesture!r}")
            duration = float(raw.get("duration") or default_duration)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(f"序列 {name} 第 {index + 1} 步 duration 非法: {duration}")
            result.append((gesture, _angles12(self.base[gesture], gesture), duration))
        return result

    def finger_events(
        self,
        name: str,
        steps: Sequence[Tuple[str, np.ndarray, float]],
        default_duration: float,
    ) -> List[Tuple[float, Dict[str, Any]]]:
        """Return (seconds-from-start, high-level finger command) events."""
        if not steps:
            pose = self._finger_pose(name)
            return [(0.0, self._finger_command(pose, default_duration))] if pose else []
        events = []
        offset = 0.0
        for step_name, _angles, duration in steps:
            pose = self._finger_pose(step_name)
            if pose:
                events.append((offset, self._finger_command(pose, duration)))
            offset += duration
        # 单个身体动作的手指配置通常挂在动作名而不是步骤名上。
        if not events and name in self.fingers:
            events.append((0.0, self._finger_command(self._finger_pose(name), default_duration)))
        return events

    def _finger_pose(self, name: str) -> Dict[str, Any] | None:
        raw = self.fingers.get(name)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"手指姿态 {name!r} 必须是对象")
        return dict(raw)

    @staticmethod
    def _finger_command(pose: Dict[str, Any] | None, duration: float) -> Dict[str, Any]:
        if not pose:
            raise ValueError("手指姿态不能为空")
        hand = str(pose.pop("hand", "both")).strip().lower()
        return {
            "hand": hand,
            "duration_ms": max(20, min(10000, int(round(duration * 1000.0)))),
            "pose": pose,
        }


def _angles12(value: Any, name: str) -> np.ndarray:
    angles = np.asarray(value, dtype=np.float64)
    if angles.shape != (12,):
        raise ValueError(f"手势 {name!r} 应为 12 维，实际为 {angles.shape}")
    if not np.isfinite(angles).all():
        raise ValueError(f"手势 {name!r} 含 NaN 或无穷值")
    return angles


def smoothstep(value: np.ndarray) -> np.ndarray:
    return value * value * (3.0 - 2.0 * value)


def build_relative_frames(
    steps: Sequence[Tuple[str, np.ndarray, float]],
    *,
    fps: float,
    amplitude: float,
    return_home_sec: float,
) -> np.ndarray:
    if fps <= 0 or amplitude < 0:
        raise ValueError("fps 必须大于 0，amplitude 不能小于 0")
    current = np.zeros(12, dtype=np.float64)
    chunks: List[np.ndarray] = []
    for _name, target_raw, duration in steps:
        target = np.asarray(target_raw, dtype=np.float64) * amplitude
        count = max(1, int(round(duration * fps)))
        alpha = smoothstep(np.linspace(0.0, 1.0, count + 1, dtype=np.float64)[1:])
        chunks.append(current[None, :] + (target - current)[None, :] * alpha[:, None])
        current = target
    if return_home_sec > 0:
        count = max(1, int(round(return_home_sec * fps)))
        alpha = smoothstep(np.linspace(0.0, 1.0, count + 1, dtype=np.float64)[1:])
        chunks.append(current[None, :] * (1.0 - alpha[:, None]))
    return np.concatenate(chunks, axis=0)


def to_motor_command(relative_deg: np.ndarray, fps: float, motor_map_path: Path):
    """通过项目现有 motor_map 转为 14 维绝对电机角。"""
    from modules.base import RobotMotion
    from modules.motor_map import load_robot_table, map_robot_motion

    radians = np.radians(np.asarray(relative_deg, dtype=np.float64))
    motion = RobotMotion(
        qpos=np.empty((radians.shape[0], 0), dtype=np.float64),
        fps=float(fps),
        joint_names=list(LEGACY_JOINT_NAMES),
        dof_pos=radians,
    )
    table = load_robot_table(str(motor_map_path))
    return map_robot_motion(motion, table)


def validate_library(library: GestureLibrary) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    for name, value in library.base.items():
        try:
            _angles12(value, name)
        except Exception as exc:
            errors.append(str(exc))
    for name in library.sequences:
        try:
            library.steps(name, 1.0)
        except Exception as exc:
            errors.append(str(exc))
    allowed_fingers = {"thumb", "index", "middle", "ring", "pinky", "thumb_gap", "servo7"}
    for name, raw_pose in library.fingers.items():
        if not isinstance(raw_pose, dict):
            errors.append(f"手指姿态 {name!r} 必须是对象")
            continue
        hand = str(raw_pose.get("hand", "both")).strip().lower()
        if hand not in {"left", "right", "both"}:
            errors.append(f"手指姿态 {name!r} 的 hand 非法: {hand!r}")
        for finger, value in raw_pose.items():
            if finger == "hand":
                continue
            if finger not in allowed_fingers:
                errors.append(f"手指姿态 {name!r} 含未知字段: {finger!r}")
                continue
            try:
                percentage = float(value)
            except (TypeError, ValueError):
                errors.append(f"手指姿态 {name!r} 的 {finger} 不是数字")
                continue
            if not 0.0 <= percentage <= 100.0:
                errors.append(f"手指姿态 {name!r} 的 {finger} 超出 0..100: {percentage}")
    for trigger in library.command_mappings:
        try:
            library.resolve(trigger, command=True)
        except Exception as exc:
            warnings.append(f"触发指令 {trigger!r}: {exc}")
    for action in library.action_mappings:
        try:
            library.resolve(action)
        except Exception as exc:
            warnings.append(f"动作映射 {action!r}: {exc}")
    return errors, warnings


def print_command_summary(name: str, steps: Sequence[Tuple[str, np.ndarray, float]], command: Any) -> None:
    frames = np.asarray(command.targets_deg, dtype=np.float64)
    duration = frames.shape[0] / float(command.fps)
    print(f"动作: {name}")
    print(f"步骤: {len(steps)}  轨迹: {frames.shape[0]} 帧 × {frames.shape[1]} 维  fps={command.fps:g}")
    print(f"时长: {duration:.2f}s（含回中轨迹）")
    print(f"绝对角范围: {frames.min():.2f}° .. {frames.max():.2f}°")
    print("首帧:", np.array2string(frames[0], precision=2, suppress_small=True))
    print("末帧:", np.array2string(frames[-1], precision=2, suppress_small=True))


def iter_resolvable_actions(library: GestureLibrary) -> Iterable[str]:
    yielded = set()
    for name in [
        *library.action_mappings.keys(), *library.base.keys(),
        *library.sequences.keys(), *library.fingers.keys(),
    ]:
        try:
            resolved, _ = library.resolve(name)
        except Exception:
            continue
        if resolved not in yielded:
            yielded.add(resolved)
            yield resolved


def confirm_execute(name: str, amplitude: float) -> bool:
    answer = input(
        f"即将向实机下发 {name!r} 的已配置手臂/手指通道"
        f"（手臂幅度 {amplitude:.2f}），确认？[y/N] "
    ).strip().lower()
    return answer in {"y", "yes"}


def exception_chain(exc: BaseException) -> str:
    messages = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip() or current.__class__.__name__
        messages.append(f"{current.__class__.__name__}: {message}")
        current = current.__cause__ or current.__context__
    return "\n  caused by: ".join(messages)


def play_one(
    library: GestureLibrary,
    requested: str,
    *,
    command_trigger: bool,
    args: argparse.Namespace,
    config: Dict[str, Any],
) -> None:
    resolved, trace = library.resolve(requested, command=command_trigger)
    default_duration = library.duration_for(requested, resolved, args.duration)
    steps = library.steps(resolved, default_duration) if library.has_motion(resolved) else []
    command = None
    if steps:
        relative = build_relative_frames(
            steps,
            fps=args.fps,
            amplitude=args.amplitude,
            return_home_sec=args.return_home,
        )
        command = to_motor_command(relative, args.fps, args.motor_map)
    finger_events = (
        [] if args.skip_fingers
        else library.finger_events(resolved, steps, default_duration)
    )
    print("\n解析:", " -> ".join(trace + ([resolved] if trace[-1] != resolved else [])))
    if command is not None:
        print_command_summary(resolved, steps, command)
    else:
        print(f"动作: {resolved}\n身体: 无（不会下发手臂）")
    if args.skip_fingers:
        print("手指: 已跳过（--skip-fingers），不发布、不等待手指订阅者")
    elif finger_events:
        print(f"手指: {len(finger_events)} 个下发事件")
        for offset, event in finger_events:
            print(f"  t={offset:.2f}s hand={event['hand']} T={event['duration_ms']}ms pose={event['pose']}")
    else:
        print("手指: 无（不会下发手指）")
    if not args.execute:
        print("DRY-RUN：未连接或驱动实机。")
        return
    if command is None and not finger_events:
        print("无可下发通道：此动作没有身体轨迹，手指已跳过。")
        return
    if not args.yes and not confirm_execute(resolved, args.amplitude):
        print("已跳过。")
        return
    from modules.action_pub import play_action_on_robot

    play_action_on_robot(command, finger_events, dict(config.get("shm") or {}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试 custom_actions/custom_gestures 并可选下发实机")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="列出基础手势、序列和触发指令")
    mode.add_argument("--validate", action="store_true", help="校验 JSON 结构和映射引用")
    mode.add_argument("--action", help="按动作/别名播放")
    mode.add_argument("--trigger", help="按 command_mappings 触发指令播放")
    mode.add_argument("--all", action="store_true", help="依次测试所有可解析动作")
    parser.add_argument("--execute", action="store_true", help="真实发布；不加此参数时永远是 dry-run")
    parser.add_argument("--skip-fingers", action="store_true", help="只执行身体电机轨迹，跳过手指事件及订阅等待")
    parser.add_argument("--yes", action="store_true", help="跳过每个动作前的人工确认")
    parser.add_argument("--unsafe-full-amplitude", action="store_true", help="允许实机幅度超过 0.25")
    parser.add_argument("--amplitude", type=float, default=0.80, help="动作幅度比例，默认 0.20")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--duration", type=float, default=1.5, help="单姿态缺省过渡时长")
    parser.add_argument("--return-home", type=float, default=2.0, help="动作末尾回中时长")
    parser.add_argument("--actions-json", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--gestures-json", type=Path, default=DEFAULT_GESTURES)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--motor-map", type=Path, default=DEFAULT_MOTOR_MAP)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.amplitude < 0:
        raise SystemExit("--amplitude 不能小于 0")
    if args.execute and args.amplitude > 0.25 and not args.unsafe_full_amplitude:
        raise SystemExit("安全锁：实机幅度默认不得超过 0.25；确认安全后可显式加 --unsafe-full-amplitude")
    if args.execute and args.yes and not sys.stdin.isatty():
        print("警告：非交互环境使用了 --yes，将直接下发实机。")

    library = GestureLibrary(args.actions_json.resolve(), args.gestures_json.resolve())
    if args.list:
        print(f"基础手势 ({len(library.base)}):")
        print("  " + "\n  ".join(sorted(library.base)))
        print(f"\n动作序列 ({len(library.sequences)}):")
        print("  " + "\n  ".join(sorted(library.sequences)))
        print(f"\n手指姿态 ({len(library.fingers)}):")
        print("  " + "\n  ".join(sorted(library.fingers)))
        print(f"\n触发指令 ({len(library.command_mappings)}):")
        for key, value in library.command_mappings.items():
            print(f"  {key} -> {value}")
        return 0

    if args.validate:
        errors, warnings = validate_library(library)
        print(
            f"基础手势={len(library.base)}，动作序列={len(library.sequences)}，"
            f"手指姿态={len(library.fingers)}，触发指令={len(library.command_mappings)}"
        )
        for item in errors:
            print(f"[ERROR] {item}")
        for item in warnings:
            print(f"[WARN ] {item}")
        print(f"校验完成：errors={len(errors)} warnings={len(warnings)}")
        return 1 if errors else 0

    config = load_yaml(args.config.resolve())
    if args.all:
        names = list(iter_resolvable_actions(library))
        print(f"将测试 {len(names)} 个可解析动作；execute={args.execute}")
        failures = 0
        for index, name in enumerate(names, 1):
            print(f"\n===== [{index}/{len(names)}] {name} =====")
            try:
                play_one(library, name, command_trigger=False, args=args, config=config)
            except Exception as exc:
                failures += 1
                print(f"[ERROR] {name}: {exc}", file=sys.stderr)
        print(f"\n批量测试完成：成功={len(names) - failures}，失败={failures}")
        return 1 if failures else 0

    requested = args.trigger if args.trigger is not None else args.action
    play_one(
        library,
        str(requested),
        command_trigger=args.trigger is not None,
        args=args,
        config=config,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n用户中断。")
        raise SystemExit(130)
    except Exception as exc:
        print(f"[ERROR] {exception_chain(exc)}", file=sys.stderr)
        raise SystemExit(1)
