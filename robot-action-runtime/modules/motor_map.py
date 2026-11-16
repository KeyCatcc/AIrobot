'''
机器人关节弧度 → 电机绝对角 (度), 不打开 MuJoCo, 不写共享内存
'''

from __future__ import annotations

import numpy as np
from typing import Any, Dict, List, Sequence

from utils.utils import read_yaml
from .base import MotorCommand, RobotMotion


def load_robot_table(path: str) -> Dict[str, Any]:
    table = read_yaml(path) or {}
    if not isinstance(table, dict):
        raise ValueError(f"机器人表不是 mapping: {path}")
    motors = table.get("motors") or []
    if not motors:
        raise ValueError(f"机器人表缺少 motors: {path}")
    return table


def _dof_columns(motion: RobotMotion) -> tuple:
    names = [str(n) for n in motion.joint_names]
    if motion.dof_pos is not None:
        dof = np.asarray(motion.dof_pos, dtype=np.float64)
    else:
        qpos = np.asarray(motion.qpos, dtype=np.float64)
        if qpos.ndim == 1:
            qpos = qpos.reshape(1, -1)
        if qpos.shape[1] < 8:
            raise ValueError(f"qpos 宽度 {qpos.shape[1]} 无法取出关节")
        dof = qpos[:, 7:]
    if dof.ndim == 1:
        dof = dof.reshape(1, -1)
    if len(names) == dof.shape[1] + 1:
        names = names[1:]
    if len(names) != dof.shape[1]:
        raise ValueError(f"关节名 {len(names)} 与 dof 列数 {dof.shape[1]} 不一致")
    return names, dof


def map_robot_motion(
    motion: RobotMotion,
    table: Dict[str, Any],
    *,
    shm_path: str = "",
    no_neck: bool = False,
    no_waist: bool = False,
) -> MotorCommand:
    names, dof = _dof_columns(motion)
    name_to_col = {n: i for i, n in enumerate(names)}
    motors: Sequence[Dict[str, Any]] = list(table.get("motors") or [])
    n = max(int(spec["index"]) for spec in motors) + 1
    home = float(table.get("home_deg") or 180.0)
    targets = np.full((dof.shape[0], n), home, dtype=np.float64)
    joint_names = [""] * n
    motor_ids = [-1] * n
    neck_idx = set(int(i) for i in (table.get("neck_indices") or []))
    waist_idx = set(int(i) for i in (table.get("waist_indices") or []))
    for spec in motors:
        joint = str(spec["joint"])
        index = int(spec["index"])
        sign = float(spec.get("sign") or 1.0)
        scale = float(spec.get("scale") or 1.0)
        joint_names[index] = joint
        motor_ids[index] = int(spec.get("motor_id") or -1)
        if (no_neck and index in neck_idx) or (no_waist and index in waist_idx):
            continue
        col = name_to_col.get(joint)
        if col is None:
            continue
        deg = np.degrees(dof[:, col]) * scale * sign
        targets[:, index] = home + deg
        lo = spec.get("min_deg")
        hi = spec.get("max_deg")
        if lo is not None or hi is not None:
            targets[:, index] = np.clip(
                targets[:, index],
                float(lo if lo is not None else -1e9),
                float(hi if hi is not None else 1e9),
            )
    return MotorCommand(
        targets_deg=targets,
        fps=float(motion.fps or 30),
        home_deg=home,
        joint_names=joint_names,
        motor_ids=motor_ids,
        shm_path=shm_path or str(table.get("shm_path") or "/dev/shm/arm_shared_memory_j14"),
        path=motion.path,
    )
