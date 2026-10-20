"""P3 世界模型：轨迹规划 + 多后端（demo / geometric / http）+ 目录描述 + 初筛。

后端（configs [models.world.<名字>] 里用 impl 选择）：
    demo       受控变换占位：只写占位帧 + 模拟质量（零依赖）
    geometric  几何视角合成：平面单应（偏航/俯仰）、远近缩放、光照衰减与传感器噪声、遮挡物、手持抖动；
               在真实像素上生成帧，并精确给出每帧目标框（numpy + Pillow）。不是生成式模型，作为基线与回退。
    http       生成式世界模型服务（PPMem / InSpatio / Holo4D / DreamX / SANA-WM 等部署后按统一协议接入）

多模型路由在 layers.flywheel.Expansion：[flywheel.world_models] 视角 → 候选模型列表，按顺序回退；
N2 失败分析会把某视角上通过率过低的模型降级（“可用率低换模型”）。
"""
from __future__ import annotations

import base64
import json
import math
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from ..core.infra import Registry, missing_modules, new_id
from ..core.schemas import Anchor, BBox, CameraPose, GeneratedClip, Trajectory
from . import imaging

IFACE = "world_model"

# 视角 → 目标位姿（轨迹从锚点位姿插值到目标位姿）
VIEW_TARGETS: dict[str, dict[str, float]] = {
    "yaw-30": {"yaw": -30}, "yaw+30": {"yaw": 30}, "yaw-60": {"yaw": -60}, "yaw+60": {"yaw": 60},
    "pitch-15": {"pitch": -15}, "pitch+15": {"pitch": 15},
    "far": {"distance": 2.0}, "near": {"distance": 0.75},
    "night": {"light": 0.3}, "occluded": {"occlusion": 0.35},
    "orbit-360": {"yaw": 360},
}
# 视角难度（demo 后端模拟质量、P5 标注难度提示共用）
VIEW_HARDNESS = {"yaw-30": 0.1, "yaw+30": 0.1, "pitch-15": 0.15, "pitch+15": 0.15, "far": 0.2, "near": 0.1,
                 "night": 0.35, "occluded": 0.4, "yaw-60": 0.3, "yaw+60": 0.3, "orbit-360": 0.6}


class WorldModelUnavailable(RuntimeError):
    """后端暂不可用（缺依赖 / 未配置 / 锚点不可读），路由会尝试下一个候选模型。"""


def plan_trajectory(view: str, n_frames: int) -> Trajectory:
    """轨迹生成：从锚点位姿线性插值到视角目标位姿（第 1 帧已离开锚点，最后一帧到达目标）。"""
    tgt = VIEW_TARGETS.get(view, {})
    n = max(1, int(n_frames))
    poses = []
    for i in range(n):
        t = (i + 1) / n
        if view == "orbit-360":
            yaw = 50 * math.sin(2 * math.pi * t)                   # 平面目标无法真正绕到背面：±50° 摆动
            poses.append(CameraPose(yaw=round(yaw, 2), pitch=round(-10 * math.cos(2 * math.pi * t), 2)))
            continue
        poses.append(CameraPose(
            yaw=round(tgt.get("yaw", 0.0) * t, 2), pitch=round(tgt.get("pitch", 0.0) * t, 2),
            distance=round(1 + (tgt.get("distance", 1.0) - 1) * t, 3),
            light=round(1 + (tgt.get("light", 1.0) - 1) * t, 3),
            occlusion=round(tgt.get("occlusion", 0.0) * t, 3)))
    return Trajectory(view=view, poses=poses)


class WorldModel(ABC):
    impl = "base"

    def __init__(self, name: str = "", **params: Any):
        self.name = name or self.impl
        self.params = params

    @abstractmethod
    def generate(self, ctx: Any, anchor: Anchor, traj: Trajectory, out_dir: Path) -> GeneratedClip: ...


# ================================================================ demo（占位）
@Registry.register(IFACE, "demo")
class ControlledTransformWM(WorldModel):
    """Demo：受控变换占位（不生成像素，写占位帧文件 + 模拟质量）。"""
    impl = "demo"
    HARDNESS = VIEW_HARDNESS

    def generate(self, ctx, anchor, traj, out_dir):
        cid = new_id("gen")
        frames = []
        for i in range(len(traj.poses)):
            f = out_dir / f"{cid}_f{i}.jpg"
            f.write_bytes(b"\xff\xd8placeholder\xff\xd9")
            frames.append(str(f))
        h = VIEW_HARDNESS.get(traj.view, 0.3)
        q = {"depth": _clip(0.95 - h + ctx.rng.uniform(-0.15, 0.1)),
             "shape": _clip(0.95 - h * 0.8 + ctx.rng.uniform(-0.15, 0.1)),
             "flicker": _clip(0.05 + h * 0.6 + ctx.rng.uniform(-0.05, 0.15))}
        return GeneratedClip(clip_id=cid, anchor_id=anchor.anchor_id, generator=f"{self.name}:controlled-transform-v0",
                             trajectory=traj.view, frames=frames, meta={"_sim_quality": q}, poses=traj.poses,
                             frame_boxes=[anchor.bbox] * len(frames) if anchor.bbox else [])


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ================================================================ geometric（真实像素）
@Registry.register(IFACE, "geometric", requires=imaging.REQUIRES)
class GeometricWM(WorldModel):
    """几何视角合成基线。平面目标绕缺陷中心旋转（偏航/俯仰）→ 单应变换；距离 → 以缺陷为中心缩放；
    光照 → 伽马 + 衰减 + 噪声（每帧独立，夜间会自然产生闪烁）；遮挡 → 随帧平移的遮挡物；
    手持抖动 → 每帧微小旋转平移。目标框由同一单应精确映射，P5 标注可直接用作提示。"""
    impl = "geometric"

    def __init__(self, name="", focal: float = 1.25, jitter: float = 0.004, quality: int = 86, **params):
        super().__init__(name, **params)
        self.focal, self.jitter, self.quality = float(focal), float(jitter), int(quality)
        self._cache: tuple[str, Any] | None = None             # 同一锚点的多个视角复用外扩后的源图

    def _source(self, uri: str):
        if self._cache and self._cache[0] == uri:
            return self._cache[1]
        np = imaging.np
        src = imaging.load_rgb(uri)
        if src is None:
            raise WorldModelUnavailable(f"锚点不是可读图像：{uri}")
        h, w = src.shape[:2]
        # 镜像外扩源图：拉远 / 大角度时画面边缘露出的是延续的表面纹理，而不是纯色填充
        u8 = (np.clip(src, 0, 1) * 255 + 0.5).astype(np.uint8)
        pil = imaging.Image.fromarray(np.pad(u8, ((h, h), (w, w), (0, 0)), mode="symmetric"))
        fill = tuple(int(v) for v in u8.reshape(-1, 3).mean(0))
        self._cache = (uri, (pil, fill, w, h))
        return self._cache[1]

    def generate(self, ctx, anchor, traj, out_dir):
        np = imaging.np
        pil, fill, w, h = self._source(anchor.media_uri)
        ph, pw = h, w
        rng = np.random.default_rng(imaging.seed_int(anchor.anchor_id, traj.view, self.name))
        box = anchor.bbox or BBox(0.4, 0.4, 0.2, 0.2)
        cx, cy = (box.x + box.w / 2) * w, (box.y + box.h / 2) * h
        shift = np.array([[1, 0, pw], [0, 1, ph], [0, 0, 1.0]])
        cid = new_id("gen")
        frames, boxes = [], []
        for i, pose in enumerate(traj.poses):
            m = self._homography(pose, cx, cy, w, h, rng)
            inv = shift @ np.linalg.inv(m)
            inv = inv / inv[2, 2]
            im = pil.transform((w, h), imaging.Image.PERSPECTIVE, tuple(inv.flatten()[:8]),
                               resample=imaging.Image.BILINEAR, fillcolor=fill)
            arr = imaging.from_pil(im)
            arr = self._light(arr, pose.light, rng)
            fb = self._map_box(m, box, w, h)
            if pose.occlusion > 0:
                arr = self._occlude(arr, fb or box, pose.occlusion, i, rng)
            p = out_dir / f"{cid}_f{i}.jpg"
            imaging.save_jpg(arr, p, self.quality)
            frames.append(str(p))
            boxes.append(fb)
        return GeneratedClip(clip_id=cid, anchor_id=anchor.anchor_id, generator=f"{self.name}:geometric-v1",
                             trajectory=traj.view, frames=frames, poses=traj.poses,
                             frame_boxes=boxes if anchor.bbox else [],          # 锚点没有缺陷位置时不编造目标框
                             meta={"backend": self.impl, "size": [w, h], "focal": self.focal})

    def _homography(self, pose: CameraPose, cx: float, cy: float, w: int, h: int, rng) -> Any:
        np = imaging.np
        f = self.focal * w
        a, b = math.radians(pose.yaw), math.radians(pose.pitch)
        ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
        r = rx @ ry
        hn = np.stack([r[:, 0], r[:, 1], np.array([0.0, 0.0, 1.0])], axis=1)   # 平面 z=0 绕自身中心旋转
        k = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
        m = k @ hn @ np.linalg.inv(k)
        s = 1.0 / max(0.2, pose.distance)
        scale = np.array([[s, 0, cx * (1 - s)], [0, s, cy * (1 - s)], [0, 0, 1.0]])
        th = math.radians(rng.normal(0, 25 * self.jitter))
        tx, ty = rng.normal(0, self.jitter * w), rng.normal(0, self.jitter * h)
        shake = np.array([[math.cos(th), -math.sin(th), tx], [math.sin(th), math.cos(th), ty], [0, 0, 1.0]])
        return shake @ scale @ m

    @staticmethod
    def _map_box(m, box: BBox, w: int, h: int) -> Optional[BBox]:
        np = imaging.np
        pts = np.array([[box.x * w, box.y * h, 1], [(box.x + box.w) * w, box.y * h, 1],
                        [box.x * w, (box.y + box.h) * h, 1], [(box.x + box.w) * w, (box.y + box.h) * h, 1]]).T
        q = m @ pts
        q = q[:2] / q[2]
        x0, x1, y0, y1 = q[0].min() / w, q[0].max() / w, q[1].min() / h, q[1].max() / h
        full = max(1e-9, (x1 - x0) * (y1 - y0))
        cx0, cx1, cy0, cy1 = max(0, x0), min(1, x1), max(0, y0), min(1, y1)
        vis = max(0.0, cx1 - cx0) * max(0.0, cy1 - cy0) / full
        if vis < 0.4:                                               # 大半出画：该帧目标框记为 None
            return None
        return BBox(round(cx0, 4), round(cy0, 4), round(cx1 - cx0, 4), round(cy1 - cy0, 4))

    @staticmethod
    def _light(arr, light: float, rng):
        np = imaging.np
        if light >= 0.999:                                       # 正常光照：JPEG 重编码本身已带来逐帧细微差异
            return arr
        dark = 1 - light
        noise = rng.standard_normal(arr.shape, dtype=np.float32) * np.float32(0.004 + 0.03 * dark)
        out = arr ** np.float32(1 + 0.5 * dark) * np.float32(light) + np.array([0.0, 0.01, 0.035], dtype=np.float32) * dark
        return np.clip(out + noise, 0, 1)

    @staticmethod
    def _occlude(arr, box: BBox, ratio: float, i: int, rng):
        """遮挡物（立柱）从目标一侧滑入，覆盖目标宽度约 ratio。"""
        h, w = arr.shape[:2]
        pil = imaging.to_pil(arr)
        d = imaging.ImageDraw.Draw(pil)
        bw = max(4.0, box.w * w)
        pw = bw * ratio * 1.1
        x = box.x * w + bw * (0.55 - ratio) + i * 0.08 * bw
        shade = int(rng.integers(40, 70))
        d.rectangle([x, 0, x + pw, h], fill=(shade, shade + 4, shade + 8))
        return imaging.from_pil(pil)


# ================================================================ http（生成式世界模型服务）
@Registry.register(IFACE, "http")
class HttpWorldModel(WorldModel):
    """生成式世界模型服务的统一接入协议。请求（POST JSON）：
        {"anchor_id": "...", "image_b64": "<锚点图>", "bbox": {"x":..,"y":..,"w":..,"h":..},
         "trajectory": {"view": "yaw-30", "poses": [{"yaw":..,"pitch":..,"distance":..,"light":..,"occlusion":..}]},
         "num_frames": 3, "seed": 123, "model": "<名字>"}
    响应：
        {"frames": ["<jpeg base64>", ...], "frame_boxes": [[x, y, w, h] | null, ...], "model_version": "..."}
    frame_boxes 可省略（P5 将回退为锚点框，门禁会因此更严格）。
    """
    impl = "http"

    def __init__(self, name="", endpoint: str = "", timeout: float = 120.0, headers: Optional[dict[str, str]] = None,
                 **params):
        super().__init__(name, **params)
        if not endpoint:
            raise WorldModelUnavailable(f"世界模型 {self.name} 未配置 endpoint")
        self.endpoint, self.timeout, self.headers = endpoint, float(timeout), headers or {}

    def generate(self, ctx, anchor, traj, out_dir):
        body = {"anchor_id": anchor.anchor_id, "image_b64": base64.b64encode(Path(anchor.media_uri).read_bytes()).decode(),
                "bbox": anchor.bbox.to_dict() if anchor.bbox else None, "trajectory": traj.to_dict(),
                "num_frames": len(traj.poses), "seed": imaging.seed_int(anchor.anchor_id, traj.view), "model": self.name}
        req = urllib.request.Request(self.endpoint, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **self.headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:      # noqa: S310 - endpoint 来自本地配置
            data = json.loads(resp.read().decode())
        cid = new_id("gen")
        frames = []
        for i, b64 in enumerate(data.get("frames", [])):
            p = out_dir / f"{cid}_f{i}.jpg"
            p.write_bytes(base64.b64decode(b64))
            frames.append(str(p))
        boxes = [BBox(*b) if b else None for b in data.get("frame_boxes", [])]
        if not boxes and anchor.bbox:
            boxes = [anchor.bbox] * len(frames)
        return GeneratedClip(clip_id=cid, anchor_id=anchor.anchor_id,
                             generator=f"{self.name}:{data.get('model_version', 'remote')}", trajectory=traj.view,
                             frames=frames, poses=traj.poses, frame_boxes=boxes,
                             meta={"backend": self.impl, "endpoint": self.endpoint})


# ================================================================ 初筛（P3 内）
def prescreen(clip: GeneratedClip, expected_frames: int) -> dict[str, Any]:
    """空帧 / 帧数不符 / 冻结帧 / 目标出画。占位帧（demo 后端）只做存在性检查。"""
    issues = []
    if len(clip.frames) != expected_frames:
        issues.append("frame_count")
    arrays = []
    for f in clip.frames:
        p = Path(f)
        if not p.is_file() or p.stat().st_size == 0:
            issues.append("missing_frame")
            continue
        a = imaging.load_rgb(p, max_side=96) if imaging.AVAILABLE else None
        if a is not None:
            arrays.append(a)
    if arrays:
        np = imaging.np
        if any(float(a.std()) < 0.01 for a in arrays):
            issues.append("blank_frame")
        if any(float(np.abs(a - b).mean()) < 1e-4 for a, b in zip(arrays, arrays[1:])):
            issues.append("frozen_frames")
    if clip.frame_boxes and any(b is None for b in clip.frame_boxes):
        issues.append("target_out_of_frame")
    return {"ok": not issues, "issues": sorted(set(issues)), "pixels": bool(arrays)}


# ================================================================ 目录与构建
def world_specs(cfg_or_ctx: Any) -> dict[str, dict[str, Any]]:
    from ..core.infra import cfg_get
    cfg = getattr(cfg_or_ctx, "cfg", cfg_or_ctx)
    specs = dict(cfg_get(cfg, "models.world", {}) or {})
    specs.setdefault("demo", {"impl": "demo", "tier": "demo", "desc": "受控变换占位（零依赖）"})
    return specs


def routes(cfg_or_ctx: Any) -> dict[str, list[str]]:
    """视角 → 候选模型（按顺序回退）。未配置时用 [impl] world_model + demo。"""
    from ..core.infra import cfg_get
    cfg = getattr(cfg_or_ctx, "cfg", cfg_or_ctx)
    table = {k: list(v) for k, v in (cfg_get(cfg, "flywheel.world_models", {}) or {}).items()}
    if "default" not in table:
        first = cfg_get(cfg, "impl.world_model", "demo")
        table["default"] = list(dict.fromkeys([first, "demo"]))
    return table


def route_for(cfg_or_ctx: Any, view: str) -> list[str]:
    t = routes(cfg_or_ctx)
    return t.get(view) or t["default"]


def spec_status(spec: dict[str, Any]) -> tuple[str, str]:
    impl = spec.get("impl", "")
    if not Registry.has(IFACE, impl):
        return "unknown_impl", f"未注册实现 {IFACE}.{impl}"
    miss = Registry.missing(IFACE, impl)
    if miss:
        return "missing_deps", f"缺少依赖 {', '.join(miss)}"
    if impl == "http" and not spec.get("endpoint"):
        return "not_configured", "未配置 endpoint（部署生成服务后填写）"
    return "ready", ""


_PARAM_SKIP = {"impl", "tier", "desc", "views", "gpu_hours_per_clip", "paper", "headers"}


def build_world_model(ctx: Any, name: str) -> WorldModel:
    specs = world_specs(ctx)
    spec = specs.get(name) or ({"impl": name} if Registry.has(IFACE, name) else None)
    if spec is None:
        raise WorldModelUnavailable(f"未定义世界模型 {name}；已定义：{sorted(specs)}")
    status, why = spec_status(spec)
    if status != "ready":
        raise WorldModelUnavailable(f"世界模型 {name} 不可用：{why}")
    params = {k: v for k, v in spec.items() if k not in _PARAM_SKIP}
    return Registry.create(IFACE, spec["impl"], name=name, **params)


def supports(spec: dict[str, Any], view: str) -> bool:
    views = spec.get("views")
    return not views or view in views or "*" in views


def describe(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    table = routes(cfg)
    for name, spec in world_specs(cfg).items():
        status, why = spec_status(spec)
        impl = spec.get("impl", "")
        req = list(Registry.requires(IFACE, impl))
        out.append({"name": name, "impl": impl, "tier": spec.get("tier", "demo"), "desc": spec.get("desc", ""),
                    "status": status, "reason": why, "requires": req, "missing": missing_modules(req),
                    "views": spec.get("views") or ["*"], "endpoint": bool(spec.get("endpoint")),
                    "gpu_hours_per_clip": spec.get("gpu_hours_per_clip"),
                    "routed_views": sorted(v for v, chain in table.items() if name in chain)})
    return out
