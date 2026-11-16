#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 CSV 播放全身关节到 /joint_states（含手指/手腕列）。
可与配套 WAV 同步播放（如 char_ret-rt-YM-with-limits-speed.csv + 机器战甲.wav）。

循环时音频与动作对齐：按「动作一圈时长」自动计算播放速率，并在 CSV 每圈开始时重播音频
（不用 mpv 自带 loop，避免音频比动作短而提前开第二遍）。

实机三终端测试时由 moveit_to_motor_bridge 订阅本话题并写 RS485，无需本节点开串口。
"""

import csv
import math
import signal
# DANCE_CANCEL_PROTOCOL_V1: SIGUSR1 requests smooth return to zero.
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rcl_interfaces.msg import SetParametersResult
from sensor_msgs.msg import JointState

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def _resolve_audio_path(csv_path: str, explicit: str) -> str:
    if explicit.strip():
        p = Path(explicit).expanduser()
        if p.is_file():
            return str(p.resolve())
        raise RuntimeError(f"audio_path 不存在: {explicit}")

    csv_p = Path(csv_path).expanduser().resolve()
    candidates = [
        csv_p.parent / "机器战甲.wav",
        csv_p.with_suffix(".wav"),
        csv_p.parent / f"{csv_p.stem}.wav",
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    return ""


def _wav_duration_sec(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        if rate <= 0:
            raise RuntimeError(f"无效采样率: {wav_path}")
        return wf.getnframes() / float(rate)


def _motion_duration_sec(num_frames: int, fps: float, speed: float) -> float:
    return num_frames / max(fps, 1e-6) / max(speed, 1e-6)


def _audio_playback_speed_for_motion(
    wav_path: str,
    num_frames: int,
    fps: float,
    speed: float,
    audio_pad_sec: float = 0.0,
) -> float:
    """使 WAV 播完墙钟时间 ≈ 动作一圈时间；audio_pad_sec>0 仅放慢音频（动作帧率不变）。"""
    wav_sec = _wav_duration_sec(wav_path)
    motion_sec = _motion_duration_sec(num_frames, fps, speed)
    if wav_sec <= 1e-6 or motion_sec <= 1e-6:
        return 1.0
    return wav_sec / (motion_sec + max(audio_pad_sec, 0.0))


def _atempo_filter(speed: float) -> str:
    speed = max(speed, 1e-6)
    if abs(speed - 1.0) < 1e-6:
        return ""
    stages: List[float] = []
    s = speed
    while s < 0.5 - 1e-9:
        stages.append(0.5)
        s /= 0.5
    while s > 2.0 + 1e-9:
        stages.append(2.0)
        s /= 2.0
    stages.append(s)
    return ",".join(f"atempo={x:.6g}" for x in stages)


class _SyncedAudioPlayer:
    """按动作圈长同步播放；loop 时由 CSV 每圈起点触发 restart，不用播放器自带循环。"""

    def __init__(self, wav_path: str, playback_speed: float, logger):
        self._proc: Optional[subprocess.Popen] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._logger = logger
        self._wav_path = wav_path
        self._playback_speed = max(playback_speed, 1e-6)
        self.start()

    def _wait_started(self, proc: subprocess.Popen, name: str, stderr_pipe: bool) -> bool:
        time.sleep(0.12)
        if proc.poll() is None:
            return True
        err = ""
        if stderr_pipe and proc.stderr:
            try:
                err = proc.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
        self._logger.warning(f"[音频] {name} 启动后立即退出(code={proc.returncode}): {err}")
        return False

    def _try_mpv(self) -> bool:
        mpv = shutil.which("mpv")
        if not mpv:
            return False
        cmd = [
            mpv,
            "--no-video",
            "--really-quiet",
            f"--speed={self._playback_speed:g}",
            "--audio-display=no",
            self._wav_path,
        ]
        try:
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            self._logger.warning(f"[音频] mpv 启动失败: {exc}")
            return False
        if not self._wait_started(proc, "mpv", True):
            return False
        self._proc = proc
        return True

    def _try_ffmpeg_aplay(self) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        aplay = shutil.which("aplay")
        if not ffmpeg or not aplay:
            return False
        filt = _atempo_filter(self._playback_speed)
        ff_cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", self._wav_path]
        if filt:
            ff_cmd += ["-filter:a", filt]
        ff_cmd += ["-f", "wav", "-"]
        try:
            ff = subprocess.Popen(ff_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            ap = subprocess.Popen(
                [aplay, "-q"],
                stdin=ff.stdout,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if ff.stdout:
                ff.stdout.close()
        except OSError as exc:
            self._logger.warning(f"[音频] ffmpeg+aplay 启动失败: {exc}")
            return False
        if not self._wait_started(ff, "ffmpeg", True):
            if ap.poll() is None:
                ap.terminate()
            return False
        self._ffmpeg_proc = ff
        self._proc = ap
        return True

    def _try_paplay(self) -> bool:
        paplay = shutil.which("paplay")
        if not paplay:
            return False
        if abs(self._playback_speed - 1.0) > 1e-3:
            self._logger.warning(
                f"[音频] paplay 无法按计算速率({self._playback_speed:g})变速，循环可能不同步"
            )
        try:
            proc = subprocess.Popen([paplay, self._wav_path], stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            self._logger.warning(f"[音频] paplay 启动失败: {exc}")
            return False
        if not self._wait_started(proc, "paplay", True):
            return False
        self._proc = proc
        return True

    def start(self) -> None:
        self.stop()
        if not self._wav_path:
            return
        for name, fn in (
            ("mpv", self._try_mpv),
            ("ffmpeg+aplay", self._try_ffmpeg_aplay),
            ("paplay", self._try_paplay),
        ):
            if fn():
                self._logger.warning(
                    f"[音频] {name} 已开始 speed={self._playback_speed:g} "
                    f"(与当前动作圈时长对齐)"
                )
                return
        self._logger.error("[音频] 播放失败，请检查 mpv/ffmpeg 与扬声器")

    def restart(self) -> None:
        self.start()

    def stop(self) -> None:
        for proc in (self._proc, self._ffmpeg_proc):
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        self._ffmpeg_proc = None


class CsvJointStatePlayer(Node):
    TARGET_JOINT_ORDER = [
        "waist_pitch_joint",
        "waist_yaw_joint",
        "left_shoulder_pitch_joint",
        "left_shoulder_roll_joint",
        "left_shoulder_yaw_joint",
        "left_elbow_pitch_joint",
        "left_elbow_yaw_joint",
        "left_wrist_pitch_joint",
        "left_finger_2_pitch_joint",
        "left_finger_3_pitch_joint",
        "left_finger_4_pitch_joint",
        "left_finger_5_pitch_joint",
        "left_finger_1_yaw_joint",
        "left_finger_1_pitch_joint",
        "left_finger_1_roll_joint",
        "right_shoulder_pitch_joint",
        "right_shoulder_roll_joint",
        "right_shoulder_yaw_joint",
        "right_elbow_pitch_joint",
        "right_elbow_yaw_joint",
        "right_wrist_pitch_joint",
        "right_finger_2_pitch_joint",
        "right_finger_3_pitch_joint",
        "right_finger_4_pitch_joint",
        "right_finger_5_pitch_joint",
        "right_finger_1_yaw_joint",
        "right_finger_1_pitch_joint",
        "right_finger_1_roll_joint",
        "neck_yaw_joint",
        "neck_pitch_joint",
    ]

    CSV_ALIASES = {
        "right_finger_1_yaw_joint": ["right_finger_1_joint"],
        "right_finger_1_joint": ["right_finger_1_yaw_joint"],
        "left_finger_1_pitch_joint": ["left_finger_1_joint"],
        "left_finger_1_joint": ["left_finger_1_pitch_joint"],
    }

    def __init__(self):
        super().__init__("csv_joint_state_player")

        self.declare_parameter("csv_path", "")
        self.declare_parameter("publish_topic", "/joint_states")
        self.declare_parameter("fps", 30.0)
        self.declare_parameter("speed", 1.0)
        self.declare_parameter("loop", False)
        self.declare_parameter("play_audio", True)
        self.declare_parameter("audio_path", "")
        self.declare_parameter("sync_audio_to_motion", True)
        self.declare_parameter("auto_fps_from_wav", False)
        self.declare_parameter("sync_motion_pad_sec", 0.0)
        # mpv 墙钟往往比按倍率推算略快 ~1s/圈，仅放慢语音、不拖慢动作（勿与 sync_motion_pad 同时加大）
        self.declare_parameter("sync_audio_pad_sec", 1.0)
        self.declare_parameter("audio_warmup_sec", 0.0)
        self.declare_parameter("motion_start_delay_sec", 0.0)
        self.declare_parameter("use_wall_clock_motion", True)
        self.declare_parameter("motion_poll_hz", 50.0)
        self.declare_parameter("audio_rate_factor", 1.0)
        self.declare_parameter("no_waist", False)
        self.declare_parameter("start_linear", False)
        self.declare_parameter("motion_accel_sec", 0.0)
        self.declare_parameter("start_from_home", False)
        self.declare_parameter("start_duration_sec", 0.6)
        self.declare_parameter("return_home", True)
        self.declare_parameter("home_duration_sec", 5.0)
        self.declare_parameter("joint_scales_yaml", "")
        self.declare_parameter("joint_scale_names", [])
        self.declare_parameter("joint_scale_values", [])

        csv_path = self.get_parameter("csv_path").get_parameter_value().string_value
        publish_topic = self.get_parameter("publish_topic").get_parameter_value().string_value
        self.fps = float(self.get_parameter("fps").value)
        self.playback_speed = float(self.get_parameter("speed").value)
        self.loop = bool(self.get_parameter("loop").value)
        play_audio = bool(self.get_parameter("play_audio").value)
        audio_path_param = self.get_parameter("audio_path").get_parameter_value().string_value
        self.sync_audio_to_motion = bool(self.get_parameter("sync_audio_to_motion").value)
        self.auto_fps_from_wav = bool(self.get_parameter("auto_fps_from_wav").value)
        self.sync_motion_pad_sec = max(
            0.0, float(self.get_parameter("sync_motion_pad_sec").value)
        )
        self.sync_audio_pad_sec = max(
            0.0, float(self.get_parameter("sync_audio_pad_sec").value)
        )
        self.audio_warmup_sec = max(
            0.0, float(self.get_parameter("audio_warmup_sec").value)
        )
        self.motion_start_delay_sec = max(
            0.0, float(self.get_parameter("motion_start_delay_sec").value)
        )
        self.audio_rate_factor = float(self.get_parameter("audio_rate_factor").value)
        self.no_waist = bool(self.get_parameter("no_waist").value)
        self.return_home = bool(self.get_parameter("return_home").value)
        self.home_duration_sec = float(self.get_parameter("home_duration_sec").value)
        if not math.isfinite(self.home_duration_sec) or self.home_duration_sec <= 0:
            raise RuntimeError("home_duration_sec 必须是大于 0 的有限数值")
        self.start_linear = bool(self.get_parameter("start_linear").value)
        self.start_from_home = bool(self.get_parameter("start_from_home").value)
        self.start_duration_sec = float(self.get_parameter("start_duration_sec").value)
        if not math.isfinite(self.start_duration_sec) or self.start_duration_sec <= 0:
            raise RuntimeError("start_duration_sec must be positive and finite")
        if self.start_from_home and bool(self.get_parameter("play_audio").value):
            raise RuntimeError("start_from_home currently requires play_audio:=false")
        self._starting = False
        self._last_positions = None
        self._homing = False
        self.playback_complete = False
        self.cancel_requested = False
        self.use_wall_clock_motion = bool(self.get_parameter("use_wall_clock_motion").value)
        self.motion_accel_sec = float(self.get_parameter("motion_accel_sec").value)
        if not math.isfinite(self.motion_accel_sec) or self.motion_accel_sec < 0:
            raise RuntimeError("motion_accel_sec must be finite and nonnegative")
        if self.motion_accel_sec > 0 and (not self.use_wall_clock_motion or bool(self.get_parameter("play_audio").value)):
            raise RuntimeError("motion_accel_sec requires use_wall_clock_motion:=true and play_audio:=false")
        self.motion_poll_hz = max(1.0, float(self.get_parameter("motion_poll_hz").value))

        if not csv_path:
            raise RuntimeError("参数 csv_path 不能为空")

        self.joint_names: List[str] = []
        self.frames: List[List[float]] = []
        self._load_csv(csv_path)
        self.joint_scales: Dict[str, float] = {}
        self._reload_joint_scales()
        self.add_on_set_parameters_callback(self._on_set_parameters)

        if not self.frames:
            raise RuntimeError(f"CSV 没有可播放数据: {csv_path}")

        self._wav_path = ""
        if play_audio:
            self._wav_path = _resolve_audio_path(csv_path, audio_path_param)

        if self.auto_fps_from_wav and self._wav_path:
            wav_sec = _wav_duration_sec(self._wav_path)
            target_sec = wav_sec + self.sync_motion_pad_sec
            self.fps = len(self.frames) / target_sec / max(self.playback_speed, 1e-6)
            pad_note = (
                f"，含 sync_motion_pad_sec={self.sync_motion_pad_sec:g}s"
                if self.sync_motion_pad_sec > 1e-6
                else ""
            )
            self.get_logger().info(
                f"auto_fps_from_wav: fps≈{self.fps:.3f} "
                f"(一圈动作目标≈{target_sec:.1f}s = WAV {wav_sec:.1f}s{pad_note})"
            )

        self.frame = 0
        self.dt = (1.0 / max(self.fps, 1e-6)) / max(self.playback_speed, 1e-6)
        self._motion_duration_sec = len(self.frames) * self.dt
        self._motion_t0 = 0.0
        self._audio: Optional[_SyncedAudioPlayer] = None
        self.timer = None
        self._motion_delay_timer = None

        self.pub = self.create_publisher(JointState, publish_topic, 10)

        if play_audio:
            if self._wav_path:
                self._start_audio()
            else:
                self.get_logger().warning(
                    "play_audio=true 但未找到 WAV（同目录 机器战甲.wav 或 -p audio_path:=...）"
                )
        else:
            self.get_logger().info("play_audio=false，不播放音频")

        self._schedule_motion_start(play_audio and bool(self._wav_path))

        motion_sec = _motion_duration_sec(len(self.frames), self.fps, self.playback_speed)
        self.get_logger().info(f"CSV 已加载: {csv_path}")
        self.get_logger().info(f"关节数={len(self.joint_names)}, 帧数={len(self.frames)}")
        self.get_logger().info(
            f"发布话题={publish_topic}, dt={self.dt:.6f}s, 动作一圈≈{motion_sec:.1f}s, "
            f"speed={self.playback_speed:g}, loop={self.loop}, "
            f"wall_clock_motion={self.use_wall_clock_motion}"
        )
        if self.use_wall_clock_motion:
            self.get_logger().info(
                f"动作按墙钟推进(每 {1.0 / self.motion_poll_hz:.3f}s 采样)，"
                f"避免 ROS 定时器逐帧累加导致播到中间动作领先语音"
            )
        self._log_joint_scales()
        if self.no_waist:
            self.get_logger().info(
                "no_waist=true：waist_pitch_joint / waist_yaw_joint 固定发布 0 rad"
            )

    def _motion_start_delay_total(self, with_audio: bool) -> float:
        if not with_audio:
            return 0.0
        return self.audio_warmup_sec + self.motion_start_delay_sec

    def _schedule_motion_start(self, with_audio: bool) -> None:
        delay = self._motion_start_delay_total(with_audio)
        if delay > 1e-6:
            parts = []
            if with_audio and self.audio_warmup_sec > 1e-6:
                parts.append(f"audio_warmup={self.audio_warmup_sec:g}s")
            if with_audio and self.motion_start_delay_sec > 1e-6:
                parts.append(f"motion_start_delay={self.motion_start_delay_sec:g}s")
            detail = ", ".join(parts) if parts else f"合计 {delay:g}s"
            self.get_logger().info(
                f"音频已拉起，{delay:g}s 后再发动作帧（等扬声器出声，避免「语音没讲动作先动」；{detail}）"
            )
            self._motion_delay_timer = self.create_timer(
                delay, self._arm_motion_timer
            )
        else:
            self._arm_motion_timer()

    def _pause_motion_for_audio(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self._motion_delay_timer is not None:
            self._motion_delay_timer.cancel()
            self._motion_delay_timer = None

    def _reset_motion_epoch(self) -> None:
        self._motion_t0 = time.monotonic()
        self.frame = 0

    def _arm_motion_timer(self) -> None:
        if self._motion_delay_timer is not None:
            self._motion_delay_timer.cancel()
            self._motion_delay_timer = None
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self._reset_motion_epoch()
        self._starting = self.start_from_home
        self._start_t0 = time.monotonic()
        period = (
            1.0 / self.motion_poll_hz
            if self.use_wall_clock_motion
            else self.dt
        )
        self.timer = self.create_timer(period, self._tick)

    def _publish_frame(self, idx: int) -> None:
        positions = self._apply_joint_scales(self.frames[idx])
        self._publish_positions(positions)
        self.frame = idx

    def _publish_positions(self, positions: List[float]) -> None:
        if self.no_waist:
            positions = [
                0.0 if name in {"waist_pitch_joint", "waist_yaw_joint"} else pos
                for name, pos in zip(self.joint_names, positions)
            ]
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = self.joint_names
        msg.position = list(positions)
        self.pub.publish(msg)
        self._last_positions = list(positions)

    def _on_motion_cycle_end(self) -> None:
        if self.loop:
            if self._audio is not None:
                self._pause_motion_for_audio()
                self._audio.restart()
                self._schedule_motion_start(True)
            else:
                self._reset_motion_epoch()
            return
        self.get_logger().info("CSV 播放完成")
        if self._audio is not None:
            self._audio.stop()
        if self.return_home and self._last_positions is not None:
            self._home_start_positions = list(self._last_positions)
            self._home_t0 = time.monotonic()
            self._homing = True
            if self.timer is not None:
                self.timer.cancel()
            self.timer = self.create_timer(1.0 / self.motion_poll_hz, self._tick)
            self.get_logger().info(
                f"自动归位：用 {self.home_duration_sec:g}s 平滑回到各关节 0 rad"
            )
            return
        self._finish_playback()

    def _finish_playback(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.playback_complete = True

    def _tick_home(self) -> None:
        ratio = min(1.0, max(0.0, (time.monotonic() - self._home_t0) / self.home_duration_sec))
        blend = ratio * ratio * (3.0 - 2.0 * ratio)
        self._publish_positions([pos * (1.0 - blend) for pos in self._home_start_positions])
        if ratio >= 1.0:
            self._homing = False
            self.get_logger().info("自动归位指令发送完成，播放器退出")
            self._finish_playback()

    def _start_audio(self) -> None:
        if not self._wav_path:
            return
        if self.sync_audio_to_motion:
            rate = _audio_playback_speed_for_motion(
                self._wav_path,
                len(self.frames),
                self.fps,
                self.playback_speed,
                self.sync_audio_pad_sec,
            )
            wav_sec = _wav_duration_sec(self._wav_path)
            motion_sec = _motion_duration_sec(len(self.frames), self.fps, self.playback_speed)
            pad_note = ""
            if self.sync_audio_pad_sec > 1e-6:
                pad_note = f", sync_audio_pad_sec={self.sync_audio_pad_sec:g}s(仅放慢语音)"
            self.get_logger().info(
                f"音频同步: speed(动作)={self.playback_speed:g}, WAV≈{wav_sec:.1f}s, "
                f"动作一圈≈{motion_sec:.1f}s, mpv倍率≈{rate:.4f}{pad_note} "
                f"(可 -p audio_rate_factor / sync_audio_pad_sec 微调)"
            )
        else:
            rate = self.playback_speed
        rate *= self.audio_rate_factor
        self._audio = _SyncedAudioPlayer(self._wav_path, rate, self.get_logger())

    def _on_set_parameters(self, params: List[Parameter]) -> SetParametersResult:
        names = {p.name for p in params}
        if names & {"joint_scales_yaml", "joint_scale_names", "joint_scale_values"}:
            self._reload_joint_scales()
            self._log_joint_scales()
        return SetParametersResult(successful=True)

    def _reload_joint_scales(self) -> None:
        scales = {name: 1.0 for name in self.joint_names}
        yaml_path = self.get_parameter("joint_scales_yaml").get_parameter_value().string_value.strip()
        if yaml_path:
            if yaml is None:
                raise RuntimeError("joint_scales_yaml 需要 PyYAML")
            path = Path(yaml_path).expanduser()
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if isinstance(raw, dict) and "joint_scales" in raw:
                raw = raw["joint_scales"]
            if isinstance(raw, dict):
                for key, val in raw.items():
                    if str(key) in scales:
                        scales[str(key)] = float(val)
        scale_names = [str(x) for x in self.get_parameter("joint_scale_names").value]
        scale_values = [float(x) for x in self.get_parameter("joint_scale_values").value]
        for name, val in zip(scale_names, scale_values):
            if name in scales:
                scales[name] = val
        self.joint_scales = scales

    def _log_joint_scales(self) -> None:
        tuned = {k: v for k, v in self.joint_scales.items() if abs(v - 1.0) > 1e-9}
        if not tuned:
            self.get_logger().info("关节倍率: 全部为 1.0（未缩放）")
            return
        parts = [f"{k}={v:g}" for k, v in sorted(tuned.items())]
        self.get_logger().info("关节倍率: " + ", ".join(parts))

    def _apply_joint_scales(self, positions: List[float]) -> List[float]:
        return [
            pos * self.joint_scales.get(name, 1.0)
            for name, pos in zip(self.joint_names, positions)
        ]

    def destroy_node(self):
        if self._motion_delay_timer is not None:
            self._motion_delay_timer.cancel()
        if self.timer is not None:
            self.timer.cancel()
        if self._audio is not None:
            self._audio.stop()
        super().destroy_node()

    def _load_csv(self, csv_path: str):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise RuntimeError(f"CSV 表头为空: {csv_path}")

            csv_headers = set(reader.fieldnames)

            self.joint_names = list(self.TARGET_JOINT_ORDER)
            missing = []
            for target_joint in self.joint_names:
                if not self._resolve_csv_column(target_joint, csv_headers):
                    missing.append(target_joint)

            if missing:
                self.get_logger().warn(
                    "CSV 缺少以下关节列，播放时将按 0.0 填充: " + ", ".join(missing)
                )

            for row in reader:
                frame = []
                for joint_name in self.joint_names:
                    col_name = self._resolve_csv_column(joint_name, csv_headers)
                    value_text = row.get(col_name, "") if col_name else ""
                    frame.append(float(value_text) if value_text else 0.0)
                self.frames.append(frame)

    def _resolve_csv_column(self, target_joint: str, csv_headers: set) -> str:
        if target_joint in csv_headers:
            return target_joint
        for alias in self.CSV_ALIASES.get(target_joint, []):
            if alias in csv_headers:
                return alias
        return ""

    def _tick(self):
        if self.cancel_requested and not self._homing:
            self._starting = False
            self.loop = False
            if self._audio is not None:
                self._audio.stop()
            if self._last_positions is None:
                self._finish_playback()
                return
            self._home_start_positions = list(self._last_positions)
            self._home_t0 = time.monotonic()
            self._homing = True
            self.get_logger().info("取消舞蹈：从最后发布姿态平滑回零")
        if self._starting:
            ratio = min(1.0, max(0.0, (time.monotonic() - self._start_t0) / self.start_duration_sec))
            blend = ratio if self.start_linear else ratio * ratio * (3.0 - 2.0 * ratio)
            target = self._apply_joint_scales(self.frames[0])
            self._publish_positions([blend * value for value in target])
            if ratio >= 1.0:
                self._starting = False
                self._reset_motion_epoch()
            return
        if self._homing:
            self._tick_home()
            return
        if self.use_wall_clock_motion:
            self._tick_wall_clock()
        else:
            self._tick_by_frame_counter()

    def _tick_wall_clock(self) -> None:
        elapsed = max(0.0, time.monotonic() - self._motion_t0)
        ramp = self.motion_accel_sec
        # Integrate smoothstep velocity: starts at zero speed and reaches
        # original speed continuously. Adds ramp/2 seconds to playback.
        if ramp > 0 and elapsed < ramp:
            x = elapsed / ramp
            motion_time = ramp * (x ** 3 - 0.5 * x ** 4)
        else:
            motion_time = elapsed - ramp * 0.5
        if motion_time >= self._motion_duration_sec:
            self._publish_frame(len(self.frames) - 1)
            self._on_motion_cycle_end()
            return
        phase = max(0.0, motion_time / self.dt)
        idx = min(int(phase), len(self.frames) - 1)
        nxt = min(idx + 1, len(self.frames) - 1)
        weight = phase - idx
        positions = [a + (b - a) * weight for a, b in zip(self.frames[idx], self.frames[nxt])]
        self._publish_positions(self._apply_joint_scales(positions))
        self.frame = idx

    def _tick_by_frame_counter(self) -> None:
        if self.frame >= len(self.frames):
            self._on_motion_cycle_end()
            return
        self._publish_frame(self.frame)
        self.frame += 1


def main():
    rclpy.init()
    node = CsvJointStatePlayer()
    # The handler only sets a flag; ROS publication and homing stay in _tick.
    previous_handler = signal.signal(signal.SIGUSR1, lambda *_: setattr(node, "cancel_requested", True))
    try:
        while rclpy.ok() and not node.playback_complete:
            rclpy.spin_once(node, timeout_sec=0.1)
        if rclpy.ok():
            # Allow the final zero-position command to leave the publisher.
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGUSR1, previous_handler)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
