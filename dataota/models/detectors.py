"""检测模型库：统一的 Detector 接口 + 多种实现，B2 L1 筛查与 P5 检测型标注智能体共用。

实现（configs [models.detectors.<名字>] 里用 impl 选择）：
    mock         读模拟真值加噪声（Demo，零依赖）
    classical    传统视觉：背景差分 + 极性/形状/颜色规则（numpy + Pillow，真实像素上运行）
    ultralytics  YOLO / RT-DETR（pip install ultralytics）
    onnx         ONNX Runtime 加载 YOLOv8 格式导出模型（T9 编译产物可直接回放）
    torchvision  Faster R-CNN / RetinaNet / FCOS / SSD 等（pip install torch torchvision）
    http         远程检测服务（JSON 协议，见 HttpDetector 文档）
    ensemble     多个检测器加权框融合（WBF）/ NMS / 投票

所有实现返回 list[Detection]，分数为 0~1，并保留低分候选（由调用方按阈值判定），这样“无缺陷”
也能给出置信度 = 1 - 最高候选分。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from ..core.infra import Registry, missing_modules
from ..core.schemas import BBox, Detection
from . import imaging

IFACE = "detector"


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def make_bbox(x: float, y: float, w: float, h: float) -> BBox:
    x, y = _clip01(x), _clip01(y)
    return BBox(round(x, 4), round(y, 4), round(max(0.0, min(w, 1 - x)), 4), round(max(0.0, min(h, 1 - y)), 4))


def iou(a: BBox, b: BBox) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def nms(dets: list[Detection], thr: float = 0.5) -> list[Detection]:
    keep: list[Detection] = []
    for d in sorted(dets, key=lambda x: -x.score):
        if all(k.label != d.label or iou(k.bbox, d.bbox) < thr for k in keep):
            keep.append(d)
    return keep


class Detector(ABC):
    """检测器接口。hint 只给 mock 用（模拟真值），真实实现不得读取 hint 里的 _sim。"""
    impl = "base"
    requires: tuple[str, ...] = ()

    def __init__(self, name: str = "", class_map: Optional[dict[Any, str]] = None, **params: Any):
        self.name = name or self.impl
        self.class_map = {str(k): v for k, v in (class_map or {}).items()}
        self.params = params

    def label_of(self, cls: Any) -> str:
        return self.class_map.get(str(cls), str(cls))

    @abstractmethod
    def detect(self, ctx: Any, image: str, hint: Optional[dict[str, Any]] = None) -> list[Detection]: ...


# ================================================================ mock
@Registry.register(IFACE, "mock")
class MockDetector(Detector):
    """Demo：依据模拟真值加噪声。difficulty 越高越容易错、置信度越低。"""
    impl = "mock"

    def detect(self, ctx, image, hint=None):
        s = (hint or {}).get("_sim") or {}
        if "defect_type" not in s:                         # 真实数据没有模拟真值：mock 弃权，不编造结果
            return []
        diff = float(s.get("difficulty", 0.2))
        truth = s.get("defect_type")
        conf = max(0.05, min(0.99, 0.98 - diff * 0.55 + ctx.rng.uniform(-0.06, 0.06)))
        pred = truth
        if ctx.rng.random() < diff * 0.5:
            pred = None if truth else ctx.rng.choice(["crack", "scratch"])
        if pred is None:                                   # 判为无缺陷：只留一个低分候选
            box = s.get("bbox") or {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
            return [Detection(truth or "crack", round(1 - conf, 3), make_bbox(**box), self.name)]
        box = s.get("bbox") if s.get("bbox") else {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
        return [Detection(pred, round(conf, 3), make_bbox(**box), self.name)]


# ================================================================ classical（真实像素）
@Registry.register(IFACE, "classical", requires=imaging.REQUIRES)
class ClassicalDetector(Detector):
    """传统视觉基线：背景差分得到残差 z 分数 → 暗/亮/锈色/明暗对三类掩码 → 连通域 → 规则分类。

    暗细长 = crack，亮细长 = scratch，橙褐色 = rust，大尺度明暗相对的平滑区域 = dent。
    不需要训练，适合作为 L1 冷启动基线与 P5 的异构标注智能体；准确率受光照 / 模糊影响，这正是 D1 要升级的场景。
    """
    impl = "classical"
    requires = imaging.REQUIRES

    def __init__(self, name="", class_map=None, work_width: int = 320, z_thresh: float = 4.0,
                 min_area: int = 10, **params):
        super().__init__(name, class_map, **params)
        self.work_width, self.z, self.min_area = int(work_width), float(z_thresh), int(min_area)

    def detect(self, ctx, image, hint=None):
        np = imaging.np
        arr = imaging.load_rgb(image, max_side=self.work_width)
        if arr is None:
            raise FileNotFoundError(f"不是可读图像：{image}")
        h, w = arr.shape[:2]
        g = imaging.gray(arr)
        bg = imaging.blur(g, 6.0)
        resid = g - bg
        sig = imaging.robust_sigma(resid)
        z = resid / sig
        r, gg, b = arr[..., 0], arr[..., 1], arr[..., 2]
        chroma = (r - b) / (r + gg + b + 0.03)                    # 偏红程度，对亮度不敏感（夜间也可用）
        cres = chroma - imaging.blur(chroma, 6.0)
        rust = (cres / imaging.robust_sigma(cres) > 4.5) & (r > b) & (chroma > 0.08)
        dog = imaging.blur(g, 2.5) - imaging.blur(g, 12.0)
        dsig = imaging.robust_sigma(dog)
        cands: list[Detection] = []
        for label, mask, polarity in (("rust", rust, 0), ("dark", z < -self.z, -1), ("bright", z > self.z, 1)):
            for x0, y0, x1, y1, n in imaging.components(mask, cell=3):
                d = self._classify(label, mask, z, x0, y0, x1, y1, n, w, h)
                if d:
                    cands.append(d)
        rusty = [c.bbox for c in cands if c.label == "rust"]
        for x0, y0, x1, y1, n in imaging.components(np.abs(dog) > 3.2 * dsig, cell=6):
            d = self._dent(dog, dsig, x0, y0, x1, y1, n, w, h)
            if d and all(iou(d.bbox, rb) < 0.2 for rb in rusty):     # 锈斑本身也有明暗，优先判锈
                cands.append(d)
        merged = self._merge(cands)
        keep: list[Detection] = []                                    # 跨类别 NMS：同一区域只保留最高分
        for d in sorted(merged, key=lambda x: -x.score):
            if all(iou(k.bbox, d.bbox) < 0.5 for k in keep):
                keep.append(d)
        return keep

    def _classify(self, kind, mask, z, x0, y0, x1, y1, n, w, h) -> Optional[Detection]:
        bw, bh = x1 - x0, y1 - y0
        if n < self.min_area or bw > 0.9 * w or bh > 0.9 * h:     # 太小或贯穿全图（接缝）
            return None
        fill = n / max(1, bw * bh)
        elong = max(bw, bh) / max(1, min(bw, bh))
        strength = float(abs(z[y0:y1, x0:x1][mask[y0:y1, x0:x1]]).mean()) if kind != "rust" else 8.0
        thin = fill < 0.35 or elong > 3.0
        if kind == "rust":
            if n < self.min_area * 3:
                return None
            label, base = "rust", 0.55 + 0.4 * min(1.0, fill * 2)
        elif kind == "dark" and thin:
            label, base = "crack", 0.5
        elif kind == "bright" and thin:
            label, base = "scratch", 0.5
        else:
            return None
        s = min(1.0, max(0.0, (strength - self.z) / 6.0))
        size = min(1.0, max(0.0, (n ** 0.5) / 12.0))
        score = _clip01(0.15 + base * (0.45 * s + 0.55 * size) + 0.2 * min(1.0, elong / 6))
        return Detection(label, round(score, 3), make_bbox(x0 / w, y0 / h, bw / w, bh / h), self.name)

    def _dent(self, dog, dsig, x0, y0, x1, y1, n, w, h) -> Optional[Detection]:
        np = imaging.np
        bw, bh = x1 - x0, y1 - y0
        if bw * bh < 200 or bw > 0.6 * w or bh > 0.6 * h:
            return None
        patch = dog[y0:y1, x0:x1] / dsig
        pos, neg = float((patch > 2.5).mean()), float((patch < -2.5).mean())
        if min(pos, neg) < 0.08:                                   # 凹陷：明暗两瓣同时存在
            return None
        # 两瓣质心要分开（光照方向），否则是普通斑点
        yy, xx = np.mgrid[0:bh, 0:bw]
        cp = (xx[patch > 2.5].mean(), yy[patch > 2.5].mean())
        cn = (xx[patch < -2.5].mean(), yy[patch < -2.5].mean())
        sep = ((cp[0] - cn[0]) ** 2 + (cp[1] - cn[1]) ** 2) ** 0.5 / max(bw, bh)
        if sep < 0.18:
            return None
        score = _clip01(0.3 + 0.9 * min(pos, neg) + 0.4 * min(1.0, sep))
        return Detection("dent", round(score, 3), make_bbox(x0 / w, y0 / h, bw / w, bh / h), self.name)

    @staticmethod
    def _merge(cands: list[Detection], gap: float = 0.03) -> list[Detection]:
        """同类且相邻的连通域合并（裂缝分叉、多条平行划痕）。"""
        out: list[Detection] = []
        for d in sorted(cands, key=lambda x: -x.score):
            for k, o in enumerate(out):
                a, b = o.bbox, d.bbox
                near = (a.x - gap < b.x + b.w and b.x - gap < a.x + a.w and a.y - gap < b.y + b.h and b.y - gap < a.y + a.h)
                if o.label == d.label and near:
                    x0, y0 = min(a.x, b.x), min(a.y, b.y)
                    x1, y1 = max(a.x + a.w, b.x + b.w), max(a.y + a.h, b.y + b.h)
                    out[k] = Detection(o.label, round(min(0.99, max(o.score, d.score) + 0.05), 3),
                                       make_bbox(x0, y0, x1 - x0, y1 - y0), o.model)
                    break
            else:
                out.append(d)
        return out


# ================================================================ ultralytics（YOLO / RT-DETR）
@Registry.register(IFACE, "ultralytics", requires=("ultralytics",))
class UltralyticsDetector(Detector):
    """Ultralytics YOLOv5/v8/v11、RT-DETR。model_type = "yolo" | "rtdetr"。"""
    impl = "ultralytics"
    requires = ("ultralytics",)

    def __init__(self, name="", class_map=None, weights: str = "yolov8n.pt", model_type: str = "yolo",
                 conf: float = 0.05, imgsz: int = 640, device: Optional[str] = None, **params):
        super().__init__(name, class_map, **params)
        import ultralytics
        cls = ultralytics.RTDETR if model_type == "rtdetr" else ultralytics.YOLO
        self.model, self.conf, self.imgsz, self.device = cls(weights), float(conf), int(imgsz), device

    def detect(self, ctx, image, hint=None):
        r = self.model.predict(image, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        out = []
        for (x, y, w, h), c, k in zip(r.boxes.xywhn.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
            out.append(Detection(self.label_of(int(k)), round(float(c), 4), make_bbox(x - w / 2, y - h / 2, w, h), self.name))
        return out


# ================================================================ onnx（YOLOv8 导出格式）
@Registry.register(IFACE, "onnx", requires=("onnxruntime", "numpy", "PIL"))
class OnnxDetector(Detector):
    """ONNX Runtime 推理 YOLOv8/YOLO11 导出模型：输入 1×3×S×S，输出 1×(4+类别数)×N（cx,cy,w,h + 各类分数）。

    与 T9 压缩适配产物同格式，可用来离线回放“设备上实际跑的模型”。providers 默认 CPU。
    """
    impl = "onnx"
    requires = ("onnxruntime", "numpy", "PIL")

    def __init__(self, name="", class_map=None, path: str = "", imgsz: int = 640, conf: float = 0.05,
                 iou_thr: float = 0.5, providers: Optional[list[str]] = None, **params):
        super().__init__(name, class_map, **params)
        import onnxruntime as ort
        if not path or not Path(path).is_file():
            raise FileNotFoundError(f"ONNX 模型不存在：{path!r}")
        self.sess = ort.InferenceSession(path, providers=providers or ["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.imgsz, self.conf, self.iou_thr = int(imgsz), float(conf), float(iou_thr)

    def detect(self, ctx, image, hint=None):
        np = imaging.np
        arr = imaging.load_rgb(image)
        if arr is None:
            raise FileNotFoundError(f"不是可读图像：{image}")
        h, w = arr.shape[:2]
        s = self.imgsz / max(h, w)
        nh, nw = round(h * s), round(w * s)
        canvas = np.full((self.imgsz, self.imgsz, 3), 114 / 255, dtype=np.float32)    # letterbox
        top, left = (self.imgsz - nh) // 2, (self.imgsz - nw) // 2
        canvas[top:top + nh, left:left + nw] = imaging.from_pil(imaging.to_pil(arr).resize((nw, nh)))
        out = self.sess.run(None, {self.inp: canvas.transpose(2, 0, 1)[None]})[0][0]   # (4+nc, N)
        if out.shape[0] > out.shape[1]:
            out = out.T
        boxes, scores = out[:4].T, out[4:].T
        cls = scores.argmax(1)
        conf = scores.max(1)
        dets = []
        for (cx, cy, bw, bh), c, k in zip(boxes, conf, cls):
            if c < self.conf:
                continue
            x0, y0 = (cx - bw / 2 - left) / s / w, (cy - bh / 2 - top) / s / h
            dets.append(Detection(self.label_of(int(k)), round(float(c), 4), make_bbox(x0, y0, bw / s / w, bh / s / h), self.name))
        return nms(dets, self.iou_thr)


# ================================================================ torchvision
@Registry.register(IFACE, "torchvision", requires=("torch", "torchvision", "PIL"))
class TorchvisionDetector(Detector):
    """torchvision.models.detection 下的检测器，arch 如 fasterrcnn_resnet50_fpn_v2 / retinanet_resnet50_fpn / fcos_resnet50_fpn。
    weights 为本地 state_dict 路径（自训练缺陷模型），留空则用官方 COCO 权重（仅冒烟测试，类别不是缺陷）。"""
    impl = "torchvision"
    requires = ("torch", "torchvision", "PIL")

    def __init__(self, name="", class_map=None, arch: str = "fasterrcnn_resnet50_fpn_v2", weights: str = "",
                 num_classes: Optional[int] = None, conf: float = 0.05, device: str = "cpu", **params):
        super().__init__(name, class_map, **params)
        import torch
        import torchvision
        builder = getattr(torchvision.models.detection, arch)
        if weights:
            self.model = builder(weights=None, weights_backbone=None, num_classes=num_classes or len(self.class_map) + 1)
            self.model.load_state_dict(torch.load(weights, map_location=device))
        else:
            self.model = builder(weights="DEFAULT")
        self.model.eval().to(device)
        self.torch, self.device, self.conf = torch, device, float(conf)

    def detect(self, ctx, image, hint=None):
        arr = imaging.load_rgb(image)
        if arr is None:
            raise FileNotFoundError(f"不是可读图像：{image}")
        h, w = arr.shape[:2]
        with self.torch.no_grad():
            t = self.torch.from_numpy(arr.transpose(2, 0, 1).copy()).to(self.device)
            r = self.model([t])[0]
        out = []
        for (x0, y0, x1, y1), c, k in zip(r["boxes"].tolist(), r["scores"].tolist(), r["labels"].tolist()):
            if c >= self.conf:
                out.append(Detection(self.label_of(k), round(c, 4), make_bbox(x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h), self.name))
        return out


# ================================================================ http（远程推理服务）
@Registry.register(IFACE, "http")
class HttpDetector(Detector):
    """远程检测服务。请求（POST JSON）：
        {"image_b64": "<jpeg/png base64>", "image_uri": "...", "model": "<name>"}
    响应：
        {"detections": [{"label": "crack", "score": 0.91, "bbox": [x, y, w, h]}]}   # bbox 归一化，左上角 + 宽高
    适用于 Triton / TorchServe / 自建 FastAPI 等部署方式；send_image=false 时只发 image_uri（服务端可访问同一存储）。
    """
    impl = "http"

    def __init__(self, name="", class_map=None, endpoint: str = "", timeout: float = 10.0, send_image: bool = True,
                 headers: Optional[dict[str, str]] = None, **params):
        super().__init__(name, class_map, **params)
        if not endpoint:
            raise ValueError(f"检测服务 {self.name} 未配置 endpoint")
        self.endpoint, self.timeout, self.send_image, self.headers = endpoint, float(timeout), bool(send_image), headers or {}

    def detect(self, ctx, image, hint=None):
        body: dict[str, Any] = {"image_uri": image, "model": self.name}
        if self.send_image:
            body["image_b64"] = base64.b64encode(Path(image).read_bytes()).decode()
        req = urllib.request.Request(self.endpoint, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **self.headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:      # noqa: S310 - endpoint 来自本地配置
            data = json.loads(resp.read().decode())
        out = []
        for d in data.get("detections", []):
            b = d["bbox"]
            x, y, w, h = (b["x"], b["y"], b["w"], b["h"]) if isinstance(b, dict) else b
            out.append(Detection(self.label_of(d["label"]), round(float(d["score"]), 4), make_bbox(x, y, w, h), self.name))
        return out


# ================================================================ ensemble（多模型融合）
@Registry.register(IFACE, "ensemble")
class EnsembleDetector(Detector):
    """多个检测器融合。fusion = "wbf"（加权框融合）| "nms" | "vote"（同类 IoU≥阈值的框至少 min_votes 个模型支持）。
    score = "noisy_or"（默认，独立检测器同时命中时置信度上升：1 - Π(1-s_i)^w_i）| "mean"（按权重平均，未投票成员计 0）。
    融合框的 support = 支持该框的成员比例，L1 用它判断类别不确定。不可用的成员会被跳过并记入 skipped。"""
    impl = "ensemble"

    def __init__(self, name="", class_map=None, members: Optional[list[Detector]] = None, fusion: str = "wbf",
                 iou_thr: float = 0.4, weights: Optional[list[float]] = None, min_votes: int = 2,
                 skipped: Optional[list[str]] = None, score: str = "noisy_or", **params):
        super().__init__(name, class_map, **params)
        if not members:
            raise ValueError("ensemble 至少需要一个可用成员")
        self.members, self.fusion, self.iou_thr, self.score_mode = members, fusion, float(iou_thr), score
        self.weights = weights or [1.0] * len(members)
        self.min_votes, self.skipped = int(min_votes), skipped or []

    def detect(self, ctx, image, hint=None):
        per: list[list[Detection]] = []
        for m in self.members:
            t0 = time.perf_counter()
            try:
                per.append(m.detect(ctx, image, hint))
                ok = True
            except Exception:                                      # 单个成员失败不影响整体
                per.append([])
                ok = False
            if ctx is not None and hasattr(ctx, "model_stats"):
                ctx.model_stats.record("detector", m.name, (time.perf_counter() - t0) * 1000, ok)
        if self.fusion == "nms" or len(self.members) == 1:
            fused = nms([d for ds in per for d in ds], self.iou_thr)
            return [Detection(d.label, d.score, d.bbox, self.name, self._support(d, per)) for d in fused]
        return self._wbf(per)

    def _support(self, d: Detection, per: list[list[Detection]]) -> float:
        n = sum(any(x.label == d.label and iou(x.bbox, d.bbox) >= self.iou_thr for x in ds) for ds in per)
        return round(n / len(per), 3)

    def _wbf(self, per: list[list[Detection]]) -> list[Detection]:
        clusters: list[dict[str, Any]] = []
        for mi, ds in enumerate(per):
            for d in ds:
                wgt = self.weights[mi] * d.score
                for c in clusters:
                    if c["label"] == d.label and iou(c["box"], d.bbox) >= self.iou_thr:
                        c["items"].append((mi, d, wgt))
                        tw = sum(x[2] for x in c["items"]) or 1e-9
                        c["box"] = make_bbox(*(sum(getattr(x[1].bbox, k) * x[2] for x in c["items"]) / tw for k in "xywh"))
                        break
                else:
                    clusters.append({"label": d.label, "box": d.bbox, "items": [(mi, d, wgt)]})
        out = []
        total_w = sum(self.weights)
        for c in clusters:
            voters = {mi for mi, _, _ in c["items"]}
            if self.fusion == "vote" and len(voters) < min(self.min_votes, len(per)):
                continue
            best = {mi: max(d.score for m2, d, _ in c["items"] if m2 == mi) for mi in voters}
            if self.score_mode == "mean":
                score = sum(self.weights[mi] * s for mi, s in best.items()) / total_w   # 未投票成员按 0 计
            else:
                miss = 1.0
                for mi, s in best.items():
                    miss *= (1 - min(s, 0.999)) ** self.weights[mi]
                score = 1 - miss
            out.append(Detection(c["label"], round(score, 4), c["box"], self.name, round(len(voters) / len(per), 3)))
        return sorted(out, key=lambda d: -d.score)


# ================================================================ 构建（配置 → 实例）
def detector_specs(cfg_or_ctx: Any) -> dict[str, dict[str, Any]]:
    from ..core.infra import cfg_get
    cfg = getattr(cfg_or_ctx, "cfg", cfg_or_ctx)
    specs = dict(cfg_get(cfg, "models.detectors", {}) or {})
    specs.setdefault("mock", {"impl": "mock", "task": "defect_detection", "tier": "demo"})
    return specs


def spec_status(spec: dict[str, Any], iface: str = IFACE) -> tuple[str, str]:
    """(状态, 说明)：ready / missing_deps / not_configured / unknown_impl。不实例化模型。"""
    impl = spec.get("impl", "")
    if not Registry.has(iface, impl):
        return "unknown_impl", f"未注册实现 {iface}.{impl}"
    miss = Registry.missing(iface, impl)
    if miss:
        return "missing_deps", f"缺少依赖 {', '.join(miss)}"
    if impl == "http" and not spec.get("endpoint"):
        return "not_configured", "未配置 endpoint（部署服务后填写）"
    for key in ("path", "weights"):
        v = spec.get(key)
        if impl in ("onnx", "torchvision") and key in spec and v and not Path(v).is_file():
            return "not_configured", f"{key} 文件不存在：{v}"
    if impl == "onnx" and not spec.get("path"):
        return "not_configured", "未配置 path（T9 导出的 .onnx）"
    return "ready", ""


_PARAM_SKIP = {"impl", "task", "tier", "desc", "members"}


def build_detector(ctx: Any, name: str, _depth: int = 0) -> Detector:
    """按名字构建检测器；不可用时抛 RuntimeError（调用方决定是否回退）。"""
    specs = detector_specs(ctx)
    if name not in specs:
        raise RuntimeError(f"未定义检测器 {name}；已定义：{sorted(specs)}")
    spec = specs[name]
    impl = spec.get("impl", "mock")
    if impl == "ensemble":
        if _depth > 2:
            raise RuntimeError("ensemble 嵌套过深")
        members, skipped = [], []
        for m in spec.get("members", []):
            try:
                members.append(build_detector(ctx, m, _depth + 1))
            except Exception as e:                                   # 成员不可用：跳过并记录
                skipped.append(f"{m}: {e}")
        params = {k: v for k, v in spec.items() if k not in _PARAM_SKIP}
        return Registry.create(IFACE, "ensemble", name=name, members=members, skipped=skipped, **params)
    status, why = spec_status(spec)
    if status != "ready":
        raise RuntimeError(f"检测器 {name} 不可用：{why}")
    params = {k: v for k, v in spec.items() if k not in _PARAM_SKIP}
    return Registry.create(IFACE, impl, name=name, **params)


def build_with_fallback(ctx: Any, name: str, fallback: str = "mock") -> tuple[Detector, Optional[str]]:
    """构建失败时回退到 fallback，并写审计。返回 (检测器, 回退原因)。"""
    try:
        return build_detector(ctx, name), None
    except Exception as e:
        reason = str(e)
        if hasattr(ctx, "audit"):
            ctx.audit.log("system", "model_fallback", f"detector:{name}", effective=fallback, reason=reason)
        return build_detector(ctx, fallback), reason


def describe(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, spec in detector_specs(cfg).items():
        impl = spec.get("impl", "")
        if impl == "ensemble":
            sub = [spec_status(detector_specs(cfg).get(m, {"impl": "?"}))[0] for m in spec.get("members", [])]
            status = "ready" if "ready" in sub else "not_configured"
            why = "" if all(s == "ready" for s in sub) else f"可用成员 {sub.count('ready')}/{len(sub)}"
            requires: list[str] = []
        else:
            status, why = spec_status(spec)
            requires = list(Registry.requires(IFACE, impl))
        out.append({"name": name, "impl": impl, "task": spec.get("task", "defect_detection"),
                    "tier": spec.get("tier", "demo"), "desc": spec.get("desc", ""), "status": status, "reason": why,
                    "requires": requires, "missing": missing_modules(requires),
                    "members": spec.get("members", []),
                    "params": {k: v for k, v in spec.items() if k not in _PARAM_SKIP and "key" not in k.lower()
                               and "token" not in k.lower() and k != "headers"}})
    return out
