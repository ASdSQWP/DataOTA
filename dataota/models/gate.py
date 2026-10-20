"""P4 几何门禁的度量实现 + 深度估计器。

gate 实现：
    demo     读取 demo 世界模型写入的模拟质量（零依赖）
    metrics  在真实帧像素上计算三项指标（numpy + Pillow）：
               depth_continuity  以目标框归一化裁剪后，相邻帧深度图（标准化）差异 → 越连续越高
               shape_stability   目标框内缺陷掩码在相邻帧的 IoU（尺度归一化）
               texture_flicker   相邻帧纹理能量的相对变化 + 扣除轨迹光照变化后的亮度跳变 → 越闪越高
             帧不可读（占位帧）时回退到模拟质量，并在 GateResult.source 标明。

depth 实现（[models.depth] impl）：
    proxy         低频亮度结构图，作为深度的代理（无深度模型时使用，平面目标上与深度单调相关）
    transformers  Depth Anything 等单目深度模型（transformers pipeline "depth-estimation"）
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any

from ..core.infra import Registry, cfg_get
from ..core.schemas import GeneratedClip
from . import imaging


class GeometryGate(ABC):
    @abstractmethod
    def measure(self, ctx: Any, clip: GeneratedClip) -> dict[str, Any]:
        """返回 depth_continuity / shape_stability / texture_flicker（0~1）与 source（pixels / sim）。"""


@Registry.register("gate", "demo")
class SelfConsistencyGate(GeometryGate):
    """Demo：三项自洽（读取模拟质量）。"""

    def measure(self, ctx, clip):
        q = clip.meta.get("_sim_quality", {})
        return {"depth_continuity": q.get("depth", 0.5), "shape_stability": q.get("shape", 0.5),
                "texture_flicker": q.get("flicker", 0.5), "source": "sim"}


# ================================================================ 深度估计
class DepthEstimator(ABC):
    name = "depth"

    @abstractmethod
    def estimate(self, rgb: Any) -> Any:
        """输入 float RGB [H,W,3]，输出与之同尺寸的 2D 深度（任意尺度，调用方会标准化）。"""


@Registry.register("depth", "proxy", requires=imaging.REQUIRES)
class ProxyDepth(DepthEstimator):
    name = "proxy-structure"

    def estimate(self, rgb):
        return imaging.blur(imaging.gray(rgb), 3.0)


@Registry.register("depth", "transformers", requires=("transformers", "torch", "PIL"))
class HFDepth(DepthEstimator):
    """pip install transformers torch；model 例如 depth-anything/Depth-Anything-V2-Small-hf。"""

    def __init__(self, model: str = "depth-anything/Depth-Anything-V2-Small-hf", device: int | str = -1, **_: Any):
        from transformers import pipeline
        self.pipe, self.name = pipeline("depth-estimation", model=model, device=device), model

    def estimate(self, rgb):
        np = imaging.np
        out = self.pipe(imaging.to_pil(rgb))["depth"]
        d = np.asarray(out, dtype=np.float32)
        return imaging.resize2d(d, rgb.shape[1], rgb.shape[0]) if d.shape != rgb.shape[:2] else d


# ================================================================ 像素度量门禁
@Registry.register("gate", "metrics", requires=imaging.REQUIRES)
class PixelMetricsGate(GeometryGate):
    """参数可在 [gate.metrics] 覆盖：work_width / depth_scale / flicker_scale / mask_z。"""

    def __init__(self, depth: DepthEstimator | None = None, work_width: int = 256, depth_scale: float = 0.9,
                 flicker_scale: float = 1.0, mask_z: float = 3.0, **_: Any):
        self.depth = depth or ProxyDepth()
        self.work_width, self.depth_scale = int(work_width), float(depth_scale)
        self.flicker_scale, self.mask_z = float(flicker_scale), float(mask_z)

    @classmethod
    def from_ctx(cls, ctx: Any) -> "PixelMetricsGate":
        impl = cfg_get(ctx.cfg, "models.depth.impl", "proxy")
        depth = None
        if impl != "proxy" and not Registry.missing("depth", impl):
            params = {k: v for k, v in (cfg_get(ctx.cfg, "models.depth", {}) or {}).items() if k != "impl"}
            try:
                depth = Registry.create("depth", impl, **params)
            except Exception as e:                                   # 深度模型加载失败 → 代理深度
                ctx.audit.log("system", "model_fallback", f"depth:{impl}", effective="proxy", reason=str(e))
        return cls(depth=depth, **(cfg_get(ctx.cfg, "gate.metrics", {}) or {}))

    def measure(self, ctx, clip):
        frames = [imaging.load_rgb(f, max_side=self.work_width) for f in clip.frames]
        if not frames or any(f is None for f in frames):
            out = SelfConsistencyGate().measure(ctx, clip)
            out["source"] = "sim" if clip.meta.get("_sim_quality") else "unreadable"
            return out
        if len(frames) == 1:
            return {"depth_continuity": 1.0, "shape_stability": 1.0, "texture_flicker": 0.0, "source": "pixels"}
        np = imaging.np
        boxes = list(clip.frame_boxes or [])
        lights = [p.light for p in clip.poses] if clip.poses else [1.0] * len(frames)
        # 以目标框为参照把每帧裁成同一尺度（补偿平移与远近缩放），再比较相邻帧
        crops, depths = [], []
        for i, f in enumerate(frames):
            box = boxes[i] if i < len(boxes) else None
            crops.append(self._crop(imaging.gray(f), box, 64))
            depths.append(self._z(self._crop(self.depth.estimate(f), box, 32)))
        d_err, flick, ious = [], [], []
        for t in range(1, len(frames)):
            b0 = boxes[t - 1] if t - 1 < len(boxes) else None
            b1 = boxes[t] if t < len(boxes) else None
            if (b0 is None) != (b1 is None):                         # 目标一帧在画内一帧出画
                d_err.append(2.0)
                flick.append(1.0)
                ious.append(0.0)
                continue
            d_err.append(float(np.abs(depths[t] - depths[t - 1]).mean()))
            ious.append(self._soft_iou(self._mask(crops[t - 1]), self._mask(crops[t])))
            e0, e1 = self._energy(crops[t - 1]), self._energy(crops[t])
            mu0, mu1 = float(crops[t - 1].mean()) + 1e-3, float(crops[t].mean()) + 1e-3
            expected = math.log(max(lights[t], 0.05) / max(lights[t - 1], 0.05)) if t < len(lights) else 0.0
            flick.append(abs(e1 - e0) / ((e0 + e1) / 2 + 1e-6) + abs(math.log(mu1 / mu0) - expected))
        depth_cont = max(0.0, 1.0 - (sum(d_err) / len(d_err)) / self.depth_scale)
        flicker = min(1.0, max(0.0, sum(flick) / len(flick) * self.flicker_scale))
        return {"depth_continuity": round(depth_cont, 4), "shape_stability": round(sum(ious) / len(ious), 4),
                "texture_flicker": round(flicker, 4), "source": "pixels"}

    @staticmethod
    def _crop(img2d, box, size: int, expand: float = 1.8):
        h, w = img2d.shape
        if box is None:
            return imaging.resize2d(img2d, size, size)
        cx, cy = (box.x + box.w / 2) * w, (box.y + box.h / 2) * h
        hw, hh = max(8.0, box.w * w * expand / 2), max(8.0, box.h * h * expand / 2)
        x0, x1 = int(max(0, cx - hw)), int(min(w, cx + hw))
        y0, y1 = int(max(0, cy - hh)), int(min(h, cy + hh))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return imaging.resize2d(img2d, size, size)
        return imaging.resize2d(img2d[y0:y1, x0:x1], size, size)

    @staticmethod
    def _z(d):
        np = imaging.np
        s = float(d.std())
        return (d - d.mean()) / s if s > 1e-6 else np.zeros_like(d)

    @staticmethod
    def _energy(crop) -> float:
        return float((imaging.blur(crop, 0.8) - imaging.blur(crop, 2.5)).std())

    def _mask(self, crop):
        """缺陷软掩码：带通残差 |z| 超阈值的区域，模糊后作软掩码（对 1–2 像素错位不敏感）。"""
        np = imaging.np
        bp = imaging.blur(crop, 0.8) - imaging.blur(crop, 5.0)
        z = np.abs(bp) / imaging.robust_sigma(bp)
        return imaging.blur((z > self.mask_z).astype(np.float32), 1.5)

    @staticmethod
    def _soft_iou(a, b) -> float:
        np = imaging.np
        union = float(np.maximum(a, b).sum())
        return float(np.minimum(a, b).sum()) / union if union > 1e-6 else 1.0
