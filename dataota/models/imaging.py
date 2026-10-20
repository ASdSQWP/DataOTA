"""图像工具：读写、Demo 场景渲染、numpy 图像原语（模糊 / 连通域 / 平移估计）。

依赖 numpy + Pillow（pip install -e .[vision]）。未安装时 AVAILABLE=False，调用方回退到占位文件 / mock 实现。
"""
from __future__ import annotations

import hashlib
import math
from collections import deque
from pathlib import Path
from typing import Any, Optional

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    AVAILABLE = True
except ImportError:  # pragma: no cover - 由 missing_modules 检查覆盖
    np = None  # type: ignore[assignment]
    AVAILABLE = False

REQUIRES = ("numpy", "PIL")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")


def seed_int(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def is_image_file(path: str | Path) -> bool:
    """按文件头判断是否为真实图像（Demo 早期产物是 b"placeholder" 占位文件）。"""
    p = Path(path)
    if p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
        return False
    try:
        with p.open("rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return any(head.startswith(m) for m in _MAGIC) and (not head.startswith(b"RIFF") or head[8:12] == b"WEBP")


def load_rgb(path: str | Path, max_side: Optional[int] = None) -> Optional["np.ndarray"]:
    """读成 float32 RGB [H,W,3]，取值 0~1；不是真实图像（占位文件 / 不存在）时返回 None。
    只打开一次文件；JPEG 缩小读取时用 draft 模式按 1/2、1/4 解码，省掉大部分解码时间。"""
    if not AVAILABLE or Path(path).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    try:
        with Image.open(path) as im:
            if max_side and im.format == "JPEG" and max(im.size) > 2 * max_side:
                s = max_side / max(im.size)
                im.draft("RGB", (max(1, int(im.width * s)), max(1, int(im.height * s))))
            im = im.convert("RGB")
            if max_side and max(im.size) > max_side:
                s = max_side / max(im.size)
                im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.BILINEAR)
            return np.asarray(im, dtype=np.float32) / 255.0
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def save_jpg(arr: "np.ndarray", path: str | Path, quality: int = 86) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).save(p, "JPEG", quality=quality)
    return str(p)


def to_pil(arr: "np.ndarray") -> "Image.Image":
    return Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8))


def from_pil(im: "Image.Image") -> "np.ndarray":
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


# ================================================================ numpy 原语
def gray(arr: "np.ndarray") -> "np.ndarray":
    return arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114


def _box1d(x: "np.ndarray", r: int, axis: int) -> "np.ndarray":
    """沿 axis 的盒式均值（边缘复制），累加和 + 切片视图，O(n)。"""
    if r <= 0:
        return x
    a = np.moveaxis(x, axis, 0)
    n = a.shape[0]
    padded = np.concatenate([np.repeat(a[:1], r + 1, axis=0), a, np.repeat(a[-1:], r, axis=0)], axis=0)
    c = np.cumsum(padded, axis=0, dtype=np.float64)
    out = (c[2 * r + 1:2 * r + 1 + n] - c[:n]) / (2 * r + 1)
    return np.moveaxis(out.astype(np.float32), 0, axis)


def blur(img2d: "np.ndarray", radius: float) -> "np.ndarray":
    """近似高斯模糊：三次盒式滤波（sigma≈radius），纯 numpy，免 scipy / cv2。"""
    if radius <= 0:
        return img2d.astype(np.float32)
    n = 3
    w_ideal = math.sqrt(12 * radius * radius / n + 1)
    wl = int(w_ideal) - (int(w_ideal) % 2 == 0)
    m = round((12 * radius * radius - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4))
    out = img2d.astype(np.float32)
    for i in range(n):
        r = ((wl if i < m else wl + 2) - 1) // 2
        out = _box1d(_box1d(out, r, 0), r, 1)
    return out


def resize2d(img2d: "np.ndarray", w: int, h: int) -> "np.ndarray":
    im = Image.fromarray(np.ascontiguousarray(img2d, dtype=np.float32))
    return np.asarray(im.resize((w, h), Image.BILINEAR), dtype=np.float32)


def robust_sigma(x: "np.ndarray") -> float:
    med = float(np.median(x))
    return 1.4826 * float(np.median(np.abs(x - med))) + 1e-5


def components(mask: "np.ndarray", cell: int = 4) -> list[tuple[int, int, int, int, int]]:
    """粗网格 8 邻域连通域：返回 [(x0, y0, x1, y1, 像素数)]，坐标为像素、右下开区间。纯 Python BFS，网格很小。"""
    h, w = mask.shape
    gh, gw = math.ceil(h / cell), math.ceil(w / cell)
    pad = np.zeros((gh * cell, gw * cell), dtype=bool)
    pad[:h, :w] = mask
    cells = pad.reshape(gh, cell, gw, cell)
    counts = cells.sum(axis=(1, 3))
    occ = counts > 0
    seen = np.zeros_like(occ)
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if not occ[gy, gx] or seen[gy, gx]:
                continue
            q = deque([(gy, gx)])
            seen[gy, gx] = True
            y0, x0, y1, x1, n = gy, gx, gy, gx, 0
            while q:
                cy, cx = q.popleft()
                n += int(counts[cy, cx])
                y0, x0, y1, x1 = min(y0, cy), min(x0, cx), max(y1, cy), max(x1, cx)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < gh and 0 <= nx < gw and occ[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
            out.append((x0 * cell, y0 * cell, min(w, (x1 + 1) * cell), min(h, (y1 + 1) * cell), n))
    return out


def phase_shift(a: "np.ndarray", b: "np.ndarray") -> tuple[int, int]:
    """相位相关估计 b 相对 a 的整数平移 (dy, dx)。用于门禁里对齐相邻帧。"""
    fa, fb = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.abs(np.fft.ifft2(r))
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = a.shape
    return (int(dy - h) if dy > h // 2 else int(dy), int(dx - w) if dx > w // 2 else int(dx))


def shift2d(x: "np.ndarray", dy: int, dx: int) -> "np.ndarray":
    return np.roll(np.roll(x, dy, axis=0), dx, axis=1)


# ================================================================ Demo 场景渲染
SURFACES = [(0.56, 0.56, 0.54), (0.47, 0.51, 0.56), (0.62, 0.6, 0.55), (0.42, 0.45, 0.44)]


def _low_freq(rng, h: int, w: int, gh: int, gw: int, amp: float) -> "np.ndarray":
    small = rng.normal(0, 1, (gh, gw)).astype(np.float32)
    return resize2d(small, w, h) * amp


def render_scene(seed: Any, size: tuple[int, int] = (640, 360), defect_type: Optional[str] = None,
                 bbox: Optional[dict[str, float]] = None, difficulty: float = 0.2,
                 light: str = "day", distance: str = "near") -> "np.ndarray":
    """按模拟真值渲染一张“表面缺陷”示意照片（Demo 数据源，生产环境为真实采集图像）。

    裂缝=暗色折线、划痕=亮色细直线、凹陷=明暗相对的椭圆、锈蚀=橙褐色斑块；difficulty 越高对比越低、越模糊。
    """
    w, h = size
    rng = np.random.default_rng(seed_int("scene", seed))
    base = np.array(SURFACES[int(rng.integers(len(SURFACES)))], dtype=np.float32)
    img = np.ones((h, w, 3), dtype=np.float32) * base
    img += _low_freq(rng, h, w, 6, 10, 0.05)[..., None]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img += ((xx / w - 0.5) * rng.uniform(-0.12, 0.12) + (yy / h - 0.5) * rng.uniform(-0.1, 0.1))[..., None]
    img += rng.normal(0, 0.018, (h, w, 1)).astype(np.float32)

    pil = to_pil(img)
    draw = ImageDraw.Draw(pil)
    for _ in range(int(rng.integers(0, 3))):                      # 面板接缝：贯穿全图的弱干扰线
        if rng.random() < 0.5:
            x = int(rng.uniform(0.1, 0.9) * w)
            draw.line([(x, 0), (x, h)], fill=(70, 70, 70), width=1)
        else:
            y = int(rng.uniform(0.1, 0.9) * h)
            draw.line([(0, y), (w, y)], fill=(70, 70, 70), width=1)
    img = img * 0.82 + from_pil(pil) * 0.18

    if defect_type and bbox:
        img = _draw_defect(img, rng, defect_type, bbox, float(difficulty))

    radius = 2.2 * max(0.0, float(difficulty) - 0.2) + (0.6 if distance == "far" else 0.0)
    if radius > 0.05:
        img = from_pil(to_pil(img).filter(ImageFilter.GaussianBlur(radius)))
    if light == "night":
        img = img ** 1.25 * 0.32 + np.array([0.0, 0.01, 0.04], dtype=np.float32)
        img += rng.normal(0, 0.025, img.shape).astype(np.float32)
    return np.clip(img, 0, 1)


def _draw_defect(img: "np.ndarray", rng, dtype: str, bbox: dict[str, float], difficulty: float) -> "np.ndarray":
    h, w = img.shape[:2]
    x0, y0 = bbox["x"] * w, bbox["y"] * h
    bw, bh = max(6.0, bbox["w"] * w), max(6.0, bbox["h"] * h)
    alpha = 1.0 - 0.55 * min(1.0, difficulty)
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    if dtype == "crack":
        n = 9
        pts = []
        sx, sy = x0 + rng.uniform(0, 0.25) * bw, y0 + rng.uniform(0, 0.25) * bh
        ex, ey = x0 + rng.uniform(0.75, 1.0) * bw, y0 + rng.uniform(0.75, 1.0) * bh
        for i in range(n + 1):
            t = i / n
            pts.append((sx + (ex - sx) * t + rng.normal(0, bw * 0.06), sy + (ey - sy) * t + rng.normal(0, bh * 0.06)))
        d.line(pts, fill=255, width=int(rng.integers(2, 4)))
        k = int(rng.integers(2, n - 2))
        bx, by = pts[k]
        d.line([(bx, by), (bx + rng.uniform(-0.3, 0.3) * bw, by + rng.uniform(0.15, 0.35) * bh)], fill=200, width=2)
        m = np.asarray(mask, dtype=np.float32)[..., None] / 255.0 * alpha
        return img * (1 - m) + np.array([0.06, 0.055, 0.05], dtype=np.float32) * m
    if dtype == "scratch":
        ang = rng.uniform(-0.35, 0.35)
        for _ in range(int(rng.integers(2, 5))):
            off = rng.uniform(0.15, 0.85) * bh
            ys = y0 + off - math.tan(ang) * bw / 2
            d.line([(x0, ys), (x0 + bw, ys + math.tan(ang) * bw)], fill=255, width=1)
        m = np.asarray(mask, dtype=np.float32)[..., None] / 255.0 * alpha
        return img * (1 - m) + np.array([0.95, 0.95, 0.93], dtype=np.float32) * m
    if dtype == "dent":
        cx, cy, rx, ry = x0 + bw / 2, y0 + bh / 2, bw * 0.45, bh * 0.45
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        u, v = (xx - cx) / rx, (yy - cy) / ry
        r2 = u * u + v * v
        inside = np.clip(1 - r2, 0, 1)
        shade = (u * 0.7 + v * 0.7) * inside * 0.32 * alpha
        return img + shade[..., None]
    if dtype == "rust":
        for _ in range(int(rng.integers(3, 7))):
            cx, cy = x0 + rng.uniform(0.15, 0.85) * bw, y0 + rng.uniform(0.15, 0.85) * bh
            r = rng.uniform(0.12, 0.3) * min(bw, bh)
            d.ellipse([cx - r, cy - r * rng.uniform(0.6, 1.2), cx + r, cy + r], fill=int(rng.integers(170, 255)))
        m = np.asarray(mask.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)[..., None] / 255.0 * alpha * 0.85
        tex = np.array([0.58, 0.28, 0.1], dtype=np.float32) + rng.normal(0, 0.04, img.shape).astype(np.float32)
        return img * (1 - m) + tex * m
    return img


def render_to(path: str | Path, seed: Any, size: tuple[int, int], sim: dict[str, Any],
              scene_tags: Optional[dict[str, str]] = None) -> str:
    tags = scene_tags or {}
    arr = render_scene(seed, size, sim.get("defect_type"), sim.get("bbox"), float(sim.get("difficulty", 0.2)),
                       tags.get("light", "day"), tags.get("distance", "near"))
    return save_jpg(arr, path)
