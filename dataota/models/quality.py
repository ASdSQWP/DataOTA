"""场景质量检测（A2 网关标注 / A0 场景异常 / D1 困难切片）与漂移检测（M11）。

scene_quality 实现：
    mock       由场景标签 + 模拟难度推断（零依赖）
    classical  拉普拉斯方差（模糊）、亮度均值与截断（曝光）、RMS 对比度、高频残差 MAD（噪声）
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..core.infra import Registry
from ..core.schemas import SceneQuality
from . import imaging


class SceneQualityDetector(ABC):
    name = "scene_quality"

    @abstractmethod
    def assess(self, ctx: Any, image: str, meta: Optional[dict[str, Any]] = None) -> Optional[SceneQuality]:
        """返回 None 表示无法评估（例如媒体不是图像）。"""


def _flags(blur: float, exposure: float, contrast: float, noise: float) -> list[str]:
    f = []
    if blur > 0.6:
        f.append("blurry")
    if exposure < -0.45:
        f.append("underexposed")
    if exposure > 0.6:
        f.append("overexposed")
    if contrast < 0.15:
        f.append("low_contrast")
    if noise > 0.5:
        f.append("noisy")
    return f


def _score(blur: float, exposure: float, contrast: float, noise: float) -> float:
    pen = max(blur, abs(exposure) * 0.9, (0.3 - min(contrast, 0.3)) / 0.3 * 0.7, noise * 0.8)
    return round(max(0.0, 1.0 - pen), 3)


@Registry.register("scene_quality", "mock")
class MockSceneQuality(SceneQualityDetector):
    name = "mock-quality"

    def assess(self, ctx, image, meta=None):
        meta = meta or {}
        sim = meta.get("_sim") or {}
        tags = meta.get("scene_tags") or {}
        diff = float(sim.get("difficulty", 0.2))
        blur = round(min(1.0, max(0.0, (diff - 0.2) * 1.1 + (0.15 if tags.get("distance") == "far" else 0))), 3)
        exposure = -0.7 if tags.get("light") == "night" else 0.0
        contrast = 0.45 if tags.get("light") != "night" else 0.18
        noise = 0.55 if tags.get("light") == "night" else 0.15
        return SceneQuality(blur, exposure, contrast, noise, _score(blur, exposure, contrast, noise),
                            _flags(blur, exposure, contrast, noise), self.name)


@Registry.register("scene_quality", "classical", requires=imaging.REQUIRES)
class ClassicalSceneQuality(SceneQualityDetector):
    """纯 numpy 图像统计。参考值按 320 宽缩略图标定，可在 [models.scene_quality] 中覆盖。"""
    name = "classical-quality"

    def __init__(self, work_width: int = 320, lap_sharp: float = 4.5e-4, lap_blurry: float = 0.4e-4,
                 noise_ref: float = 0.04, contrast_ref: float = 0.12, **_: Any):
        self.work_width = int(work_width)
        self.lap_sharp, self.lap_blurry = float(lap_sharp), float(lap_blurry)
        self.noise_ref, self.contrast_ref = float(noise_ref), float(contrast_ref)

    def assess(self, ctx, image, meta=None):
        np = imaging.np
        arr = imaging.load_rgb(image, max_side=self.work_width)
        if arr is None:
            return None
        g = imaging.gray(arr)
        lap = (np.roll(g, 1, 0) + np.roll(g, -1, 0) + np.roll(g, 1, 1) + np.roll(g, -1, 1) - 4 * g)[1:-1, 1:-1]
        sharp = max(float(lap.var()), 1e-9)
        # 对数刻度：拉普拉斯方差 ≥ lap_sharp 记 0，≤ lap_blurry 记 1
        blur = math.log(self.lap_sharp / sharp) / math.log(self.lap_sharp / self.lap_blurry)
        blur = round(min(1.0, max(0.0, blur)), 3)
        m = float(g.mean())
        clipped = float(((g < 0.02) | (g > 0.98)).mean())
        exposure = round(max(-1.0, min(1.0, (m - 0.45) / 0.3 + (clipped if m > 0.5 else -clipped))), 3)
        contrast = round(min(1.0, float(g.std()) / self.contrast_ref), 3)
        noise_rel = imaging.robust_sigma(g - imaging.blur(g, 1.0)) / max(m, 0.05)   # 相对噪声：暗图更显噪
        noise = round(min(1.0, noise_rel / self.noise_ref), 3)
        return SceneQuality(blur, exposure, contrast, noise, _score(blur, exposure, contrast, noise),
                            _flags(blur, exposure, contrast, noise), self.name)


# ================================================================ M11 漂移检测
def _norm(h: list[float]) -> list[float]:
    s = sum(h) or 1.0
    return [x / s for x in h]


def psi(base: list[float], cur: list[float], eps: float = 1e-4) -> float:
    return sum((c - b) * math.log((c + eps) / (b + eps)) for b, c in zip(base, cur))


def js_divergence(base: list[float], cur: list[float], eps: float = 1e-12) -> float:
    p, q = _norm(base), _norm(cur)
    m = [(a + b) / 2 for a, b in zip(p, q)]
    kl = lambda x, y: sum(a * math.log2((a + eps) / (b + eps)) for a, b in zip(x, y) if a > 0)  # noqa: E731
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def ks_distance(base: list[float], cur: list[float]) -> float:
    p, q = _norm(base), _norm(cur)
    cp = cq = best = 0.0
    for a, b in zip(p, q):
        cp, cq = cp + a, cq + b
        best = max(best, abs(cp - cq))
    return best


DRIFT_METRICS = {"psi": psi, "js": js_divergence, "ks": ks_distance}
