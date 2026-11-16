'''
项目统一文件读写(文本 / JSON / YAML / wav / npy / npz / 图片 / 拷贝移动 / 建目录)
'''

from __future__ import annotations

import wave
import json
import shutil

import numpy as np

from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, Union


PathLike = Union[str, Path]


def to_path(path: PathLike) -> Path:
    return path if isinstance(path, Path) else Path(path)


def ensure_dir(path: PathLike) -> Path:
    p = to_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_parent(path: PathLike) -> Path:
    p = to_path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_text(path: PathLike, *, encoding: str = "utf-8") -> str:
    return to_path(path).read_text(encoding=encoding)


def write_text(path: PathLike, text: str, *, encoding: str = "utf-8", newline: bool = True) -> str:
    p = ensure_parent(path)
    body = text if (not newline or text.endswith("\n")) else (text + "\n")
    p.write_text(body, encoding=encoding)
    return str(p.resolve())


def read_json(path: PathLike, *, encoding: str = "utf-8") -> Any:
    return json.loads(read_text(path, encoding=encoding))


def write_json(path: PathLike, obj: Any, *, encoding: str = "utf-8", indent: int = 2, ensure_ascii: bool = False,) -> str:
    p = ensure_parent(path)
    p.write_text(
        json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent) + "\n",
        encoding=encoding,
    )
    return str(p.resolve())


def write_wav(path: PathLike, wav: np.ndarray, sample_rate: int) -> str:
    '''
    float32 mono → wav, 优先 soundfile, 失败则 stdlib wave
    '''
    p = ensure_parent(path)
    x = np.asarray(wav, dtype=np.float32).reshape(-1)
    sr = int(sample_rate)
    try:
        import soundfile as sf

        sf.write(str(p), x, sr)
    except Exception:
        pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
    return str(p.resolve())


def read_wav(path: PathLike) -> tuple:
    '''
    返回 (float32 mono wav, sample_rate)
    '''
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("读取 WAV 需要安装 soundfile") from exc
    wav, sr = sf.read(str(to_path(path)), always_2d=False)
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    return wav, int(sr)


def save_npy(path: PathLike, arr: Any) -> str:
    p = ensure_parent(path)
    np.save(str(p), np.asarray(arr))
    return str(p.resolve())


def load_npy(path: PathLike) -> np.ndarray:
    return np.load(str(to_path(path)), allow_pickle=False)


def save_npz(path: PathLike, **arrays: Any) -> str:
    '''np.savez 多数组打包落盘'''
    p = ensure_parent(path)
    np.savez(str(p), **arrays)
    return str(p.resolve())


def load_npz(path: PathLike) -> Any:
    return np.load(str(to_path(path)), allow_pickle=True)


def write_yaml(path: PathLike, obj: Any, *, encoding: str = "utf-8") -> str:
    import yaml

    p = ensure_parent(path)
    with open(p, "w", encoding=encoding) as f:
        yaml.safe_dump(obj, f, allow_unicode=True)
    return str(p.resolve())


def read_yaml(path: PathLike, *, encoding: str = "utf-8") -> Any:
    import yaml

    with open(to_path(path), "r", encoding=encoding) as f:
        return yaml.safe_load(f)


def copy_file(src: PathLike, dst: PathLike) -> str:
    dst_p = ensure_parent(dst)
    shutil.copy2(str(to_path(src)), str(dst_p))
    return str(dst_p.resolve())


def move_file(src: PathLike, dst: PathLike) -> str:
    dst_p = ensure_parent(dst)
    shutil.move(str(to_path(src)), str(dst_p))
    return str(dst_p.resolve())


def remove_file(path: PathLike) -> bool:
    p = to_path(path)
    try:
        if p.is_file():
            p.unlink()
            return True
    except Exception:
        pass
    return False


def resolve_under(root: PathLike, path: PathLike) -> Path:
    p = to_path(path)
    if p.is_absolute():
        return p
    return (to_path(root) / p).resolve()


def write_image(path: PathLike, image: Any, *, params: Optional[list] = None) -> str:
    '''
    写图片(OpenCV 约定: BGR / 灰度 ndarray)
    params 可传 cv2.IMWRITE_* 列表, 例如 [cv2.IMWRITE_JPEG_QUALITY, 95]
    '''
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("读写图片需要安装 opencv-python") from exc
    p = ensure_parent(path)
    arr = np.asarray(image)
    ok = cv2.imwrite(str(p), arr, params if params is not None else [])
    if not ok:
        raise RuntimeError(f"写图片失败: {p}")
    return str(p.resolve())


def read_image(path: PathLike, *, color: bool = True) -> np.ndarray:
    '''
    读图片, color=True → BGR 三通道; False → 灰度
    '''
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("读写图片需要安装 opencv-python") from exc
    p = to_path(path)
    flags = cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE
    img = cv2.imread(str(p), flags)
    if img is None:
        raise FileNotFoundError(f"无法读取图片: {p}")
    return img


def make_timed_dir(parent: PathLike, *, stamp: Optional[str] = None) -> Path:
    '''
    按时间建目录: YYYYMMDD_HHMMSS, 同秒冲突则追加 _2/_3
    '''
    parent_p = ensure_dir(parent)
    base = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = parent_p / base
    if not path.exists():
        path.mkdir(parents=True, exist_ok=False)
        return path
    n = 2
    while True:
        cand = parent_p / f"{base}_{n}"
        if not cand.exists():
            cand.mkdir(parents=True, exist_ok=False)
            return cand
        n += 1
