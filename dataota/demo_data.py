"""Demo 种子数据：多源原始条目（含模拟真值 _sim）+ 控制台上传收件箱。生产环境不需要 make_seed。

装了 numpy + Pillow 时为每条样本渲染真实 JPEG（视频源额外渲染一张关键帧），检测器 / 世界模型 / 门禁都在真实像素上运行；
否则写占位文件，流水线回退到 mock 实现。
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Optional

from .models import imaging

CLASSES = ["crack", "scratch", "dent", "rust"]
DESC = {"crack": "这里有一条裂缝，比较严重", "scratch": "表面有划痕，轻微", "dent": "右下角有凹陷",
        "rust": "顶部生锈了", None: "感觉有点问题"}


def make_seed(raw_dir: Path, n: int = 60, seed: int = 0, tag: str = "",
              size: tuple[int, int] = (640, 360), render: Optional[bool] = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    raw_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"seed-{tag}" if tag else "seed"
    render = imaging.AVAILABLE if render is None else (render and imaging.AVAILABLE)
    items = []
    for i in range(n):
        src = rng.choices(["crawl", "user_upload", "video_stream", "audio", "device"], [3, 3, 2, 1, 1])[0]
        truth = rng.choice(CLASSES + [None, None])
        diff = round(rng.choice([0.1, 0.2, 0.3, 0.5, 0.7, 0.85]), 2)
        kind = "video" if src == "video_stream" else "image"
        uri = raw_dir / f"{prefix}-{i:03d}.{'mp4' if kind == 'video' else 'jpg'}"
        sim = {"defect_type": truth, "difficulty": diff,
               "severity": rng.choice(["low", "medium", "medium", "high", "critical"]) if truth else "low",
               "hard_slice": diff >= 0.7, "long_tail": rng.random() < 0.08,
               "needs_more_data": truth is not None and rng.random() < 0.08,
               "bbox": {"x": round(rng.uniform(0.05, 0.6), 3), "y": round(rng.uniform(0.05, 0.6), 3),
                        "w": round(rng.uniform(0.05, 0.3), 3), "h": round(rng.uniform(0.05, 0.3), 3)} if truth else None}
        tags = {"light": rng.choice(["day", "night"]), "distance": rng.choice(["near", "far"])}
        media = [{"uri": str(uri), "kind": kind}]
        if kind == "video":
            uri.write_bytes(b"placeholder")                       # Demo 不生成视频流本身
            if render:                                            # A2 抽出的关键帧
                kf = raw_dir / f"{prefix}-{i:03d}_kf0.jpg"
                imaging.render_to(kf, f"{prefix}-{i}", size, sim, tags)
                media.append({"uri": str(kf), "kind": "image"})
        elif render:
            imaging.render_to(uri, f"{prefix}-{i}", size, sim, tags)
        else:
            uri.write_bytes(b"placeholder")
        item: dict[str, Any] = {
            "source_type": src, "media": media, "has_audio": src in ("audio", "video_stream"),
            "meta": {"license": "unknown" if (src == "crawl" and rng.random() < 0.1) else "ok",
                     "contains_pii": rng.random() < 0.1, "redacted": False, "scene_tags": tags},
            "_sim": sim,
        }
        if src == "user_upload":
            # 部分用户描述与真值不一致（触发 claim_conflict）
            item["user_description"] = DESC[truth if rng.random() > 0.2 else rng.choice(CLASSES)]
        if item["has_audio"]:
            sim["audio_label"] = rng.choice(["crack_sound", "normal", "impact"]) if truth else "normal"
            sim["audio_quality"] = rng.random()
        items.append(item)
    return items


# ================================================================ 控制台上传收件箱（A1 用户上传源）
def load_inbox(inbox_dir: Path, consume: bool = True) -> list[dict[str, Any]]:
    """读取 lake/inbox/*.json（控制台上传）转为原始条目。真实上传没有模拟真值：
    mock 的 L2 / oracle 会按无缺陷处理，建议配合 reviewer = "queue" 人工裁决。consume=True 时标记为已消费。"""
    items = []
    for p in sorted(Path(inbox_dir).glob("up-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("consumed"):
            continue
        raw = {"source_type": d.get("source_type", "user_upload"),
               "media": [{"uri": d["image"], "kind": "image"}], "has_audio": False,
               "meta": {"license": d.get("license", "ok"), "contains_pii": bool(d.get("contains_pii")),
                        "redacted": False, "scene_tags": d.get("scene_tags", {}), "upload_id": d["upload_id"]},
               "_sim": {}}
        if d.get("description"):
            raw["user_description"] = d["description"]
        items.append(raw)
        if consume:
            d["consumed"] = True
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return items
