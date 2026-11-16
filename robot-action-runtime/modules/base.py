import numpy as np
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, List, Tuple

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ASRResult:
    text: str
    language: Optional[str] = None
    emotion: Optional[str] = None
    events: Optional[List[str]] = None
    raw: Any = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VLMResult:
    text: str
    need_vision: bool = False
    used_vision: bool = False
    raw: Any = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TTSResult:
    wav: np.ndarray
    sample_rate: int
    path: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


BBox = Tuple[float, float, float, float]   # x1, y1, x2, y2

@dataclass
class FaceTrackResult:
    track_id: int
    bbox: BBox
    landmarks: Optional[np.ndarray] = None
    confidence: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EmotionResult:
    track_id: int
    label: str
    score: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    bbox: Optional[BBox] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GazeResult:
    track_id: int
    pitch_deg: float
    yaw_deg: float
    vector: Optional[np.ndarray] = None
    bbox: Optional[BBox] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HeadPoseResult:
    track_id: int
    pitch_deg: float
    yaw_deg: float
    roll_deg: float
    bbox: Optional[BBox] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FaceIdentityResult:
    track_id: int
    person_id: int
    name: str
    confidence: float
    is_known: bool
    bbox: Optional[BBox] = None
    feature: Optional[np.ndarray] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualFrameResult:
    '''
    单帧综合结果, 供下游调用, 可拔插更换后端时格式不变
    '''
    frame_id: int
    timestamp: float
    tracks: List[FaceTrackResult] = field(default_factory=list)
    emotions: List[EmotionResult] = field(default_factory=list)
    gazes: List[GazeResult] = field(default_factory=list)
    head_poses: List[HeadPoseResult] = field(default_factory=list)
    identities: List[FaceIdentityResult] = field(default_factory=list)
    backends: Dict[str, str] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        def _box(b: Optional[BBox]):
            return list(b) if b is not None else None

        safe_extras: Dict[str, Any] = {}
        for k, v in self.extras.items():
            if k in ("inspire_tracks", "gaze_tracks", "live_tracks", "spoof_tracks", "vis_image"):
                continue
            if hasattr(v, "track_id") and hasattr(v, "bbox"):
                continue
            if isinstance(v, (str, int, float, bool, type(None), list, dict)):
                safe_extras[k] = v
            elif isinstance(v, Path):
                safe_extras[k] = str(v)

        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "backends": self.backends,
            "tracks": [
                {
                    "track_id": t.track_id,
                    "bbox": _box(t.bbox),
                    "confidence": t.confidence,
                    "extras": {
                        kk: vv
                        for kk, vv in t.extras.items()
                        if isinstance(vv, (str, int, float, bool, type(None), list, dict))
                    },
                }
                for t in self.tracks
            ],
            "emotions": [
                {
                    "track_id": e.track_id,
                    "label": e.label,
                    "score": e.score,
                    "scores": e.scores,
                    "bbox": _box(e.bbox),
                }
                for e in self.emotions
            ],
            "gazes": [
                {
                    "track_id": g.track_id,
                    "pitch_deg": g.pitch_deg,
                    "yaw_deg": g.yaw_deg,
                    "vector": None if g.vector is None else g.vector.tolist(),
                    "bbox": _box(g.bbox),
                }
                for g in self.gazes
            ],
            "head_poses": [
                {
                    "track_id": h.track_id,
                    "pitch_deg": h.pitch_deg,
                    "yaw_deg": h.yaw_deg,
                    "roll_deg": h.roll_deg,
                    "bbox": _box(h.bbox),
                }
                for h in self.head_poses
            ],
            "identities": [
                {
                    "track_id": i.track_id,
                    "person_id": i.person_id,
                    "name": i.name,
                    "confidence": i.confidence,
                    "is_known": i.is_known,
                    "bbox": _box(i.bbox),
                }
                for i in self.identities
            ],
            "extras": safe_extras,
        }
    

@dataclass
class MotionResult:
    poses: np.ndarray
    expressions: Optional[np.ndarray] = None
    trans: Optional[np.ndarray] = None
    fps: int = 30
    path: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RobotMotion:
    '''映射后端的统一输出, preview 只认这个结构, 不认是否来自 GMR'''

    qpos: np.ndarray
    fps: float = 30.0
    xml_path: str = ""
    robot: str = ""
    joint_names: List[str] = field(default_factory=list)
    root_pos: Optional[np.ndarray] = None
    root_rot: Optional[np.ndarray] = None   # wxyz
    dof_pos: Optional[np.ndarray] = None
    path: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def split_qpos(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self.root_pos is not None and self.root_rot is not None and self.dof_pos is not None:
            return self.root_pos, self.root_rot, self.dof_pos
        q = np.asarray(self.qpos)
        if q.ndim == 1:
            q = q.reshape(1, -1)
        if q.shape[1] < 7:
            raise ValueError(f"qpos 宽度 {q.shape[1]} < 7, 无法拆浮动基座")
        return q[:, :3], q[:, 3:7], q[:, 7:]
    

@dataclass
class MotorCommand:
    '''映射结果: 共享内存下标顺序的绝对角, 单位度'''

    targets_deg: np.ndarray
    fps: float = 30.0
    home_deg: float = 180.0
    joint_names: List[str] = field(default_factory=list)
    motor_ids: List[int] = field(default_factory=list)
    shm_path: str = "/dev/shm/arm_shared_memory_j14"
    path: Optional[str] = None

    def frame(self, index: int) -> "MotorCommand":
        targets = np.asarray(self.targets_deg, dtype=np.float64)
        if targets.ndim == 1:
            targets = targets.reshape(1, -1)
        row = targets[int(index)]
        return MotorCommand(
            targets_deg=row.reshape(1, -1),
            fps=self.fps,
            home_deg=self.home_deg,
            joint_names=list(self.joint_names),
            motor_ids=list(self.motor_ids),
            shm_path=self.shm_path,
            path=self.path,
        )


class AudioIOBase(ABC):
    '''麦克风默认异步监听，队列供后续打断使用'''

    def start(self) -> None:
        '''启动后台监听, 可反复调用'''

    def stop(self) -> None:
        '''停止后台监听'''

    def get_utterance(self, timeout: Optional[float] = None) -> Optional[np.ndarray]:
        '''从队列取一句 float32 mono; 超时/空则 None'''
        return None

    @abstractmethod
    def record(self, seconds: float, sample_rate: int) -> np.ndarray:
        '''定长录音（调试）'''
        ...

    def record_utterance(self, sample_rate: int) -> np.ndarray:
        '''阻塞取一句'''
        self.start()
        while True:
            wav = self.get_utterance(timeout=1.0)
            if wav is not None:
                return wav

    @abstractmethod
    def play(self, wav: np.ndarray, sample_rate: int) -> None: ...


class ASRBase(ABC):
    '''语音转文本, 非流式转写'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def transcribe(self, wav: np.ndarray, sample_rate: int) -> ASRResult:
        '''非流式: 整段 float32 mono 转写'''
        ...

    def transcribe_file(self, path: str) -> ASRResult:
        '''非流式: 从 wav 文件转写'''
        from utils.utils import read_wav

        wav, sr = read_wav(path)
        return self.transcribe(wav, int(sr))


class StreamingASRBase(ASRBase):
    '''语音转文本, 流式转写'''

    @abstractmethod
    def stream_reset(self) -> None:
        '''清空 cache, 开始新一句'''
        ...

    @abstractmethod
    def stream_chunk(self, wav: np.ndarray, sample_rate: int, is_final: bool = False) -> ASRResult:
        '''喂入一块音频, is_final=True 强制收尾'''


class CameraBase(ABC):
    '''获取相机彩色图像'''

    def start(self) -> None:
        '''启动采集'''

    def stop(self) -> None:
        '''停止采集'''

    @abstractmethod
    def get_color(self, timeout: float = 2.0) -> Optional[np.ndarray]:
        '''返回 BGR HxWx3 uint8, 超时/失败则 None'''
        ...


class VLMBase(ABC):
    '''文本(可选图像)生成导览回答'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def decide_need_vision(self, question: str) -> bool:
        '''本轮是否需要看相机画面'''
        ...

    @abstractmethod
    def answer(
        self,
        question: str,
        image: Optional[np.ndarray] = None,
        image_path: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> VLMResult:
        '''生成回复, image 为 BGR ndarray 或传 image_path'''
        ...

    def reset_history(self) -> None:
        '''若实现方维护内部历史, 可清空'''
        return
    

class TTSBase(ABC):
    '''文本转语音(float32 mono), 可拔插后端'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def synthesize(self, text: str, out_path: Optional[str] = None) -> TTSResult:
        '''合成语音(float32 mono)。落盘由 pipeline/scripts 负责'''
        ...

    def stream_synthesize(self, text: str) -> Iterator[np.ndarray]:
        '''流式合成: yield float32 mono 块. 默认退化为整句一次 yield'''
        res = self.synthesize(text)
        if getattr(res, "wav", None) is not None and res.wav.size:
            yield np.asarray(res.wav, dtype=np.float32)

    def close(self) -> None:
        '''释放资源(sidecar进程等), 默认空操作'''
        return


class MotionBase(ABC):
    '''文本和音频生成对应语义的动作'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def generate(
        self,
        text: str,
        wav_path: str,
        out_path: Optional[str] = None,
        speaker_id: Optional[int] = None,
    ) -> MotionResult:
        '''text是大模型的回复输出, wav_path是TTS的输出结果'''
        ...


class FaceTrackerBase(ABC):
    '''人脸跟踪, 输入 BGR 帧, 输出带 track_id 的框'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def track(self, image: np.ndarray) -> List[FaceTrackResult]: ...

    def reset(self) -> None:
        return


class EmotionRecognizerBase(ABC):
    '''情绪识别'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def recognize(
        self,
        image: np.ndarray,
        tracks: Optional[List[FaceTrackResult]] = None,
    ) -> List[EmotionResult]: ...


class GazeEstimatorBase(ABC):
    '''视线估计'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def estimate(
        self,
        image: np.ndarray,
        tracks: Optional[List[FaceTrackResult]] = None,
    ) -> List[GazeResult]: ...


class HeadPoseEstimatorBase(ABC):
    '''头部角度估计(pitch/yaw/roll)'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def estimate(
        self,
        image: np.ndarray,
        tracks: Optional[List[FaceTrackResult]] = None,
    ) -> List[HeadPoseResult]: ...


class FaceRecognizerBase(ABC):
    '''人脸识别 + 身份库'''

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def recognize(
        self,
        image: np.ndarray,
        tracks: Optional[List[FaceTrackResult]] = None,
    ) -> List[FaceIdentityResult]: ...

    def enroll(
        self,
        image: np.ndarray,
        name: str,
        track: Optional[FaceTrackResult] = None,
    ) -> FaceIdentityResult:
        raise NotImplementedError

    def list_known(self) -> List[Dict[str, Any]]:
        return []


class RetargeterBase(ABC):
    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def retarget(self, motion: MotionResult) -> RobotMotion: ...

    def close(self) -> None:
        return
    

class PreviewBase(ABC):
    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def submit(self, motion: RobotMotion) -> None:
        '''主线程调用: 把一段机器人动作交给预览'''

    def wait(self) -> None:
        '''常开模式下阻塞到窗口被关掉, 默认无操作'''
        return

    def wait_played(self) -> None:
        '''等到当前这段播完, 默认无操作'''
        return

    def close(self) -> None:
        return