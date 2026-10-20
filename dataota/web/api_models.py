"""控制台数据接口（模型与数据源部分）：元数据、模型库、世界模型片段、检测模型评测 / 检测结果、
数据接入与上传收件箱。作为 Dashboard 的 mixin，复用其缓存与只读工具方法。

与 api.py 相同的原则：只读（上传收件箱除外）、容错、分页。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections import Counter
from typing import Any, Optional

from ..core.infra import new_id

UPLOAD_TYPES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}
UPLOAD_MAX_BYTES = 1_500_000
UPLOAD_SOURCES = ("user_upload", "crawl", "device")
SCENE_FLAGS = ("blurry", "underexposed", "overexposed", "low_contrast", "noisy")
_ID = re.compile(r"[A-Za-z0-9._:-]+")


def _page(rows: list[Any], page: int, size: int, cap: int = 200) -> dict[str, Any]:
    size = max(1, min(cap, int(size)))
    pages = max(1, (len(rows) + size - 1) // size)
    page = max(1, min(pages, int(page)))
    start = (page - 1) * size
    return {"total": len(rows), "page": page, "size": size, "pages": pages, "items": rows[start:start + size]}


def _hist(values: list[float], bins: int = 10, lo: float = 0.0, hi: float = 1.0) -> list[int]:
    out = [0] * bins
    for v in values:
        try:
            k = int((float(v) - lo) / (hi - lo) * bins)
        except (TypeError, ValueError):
            continue
        out[max(0, min(bins - 1, k))] += 1
    return out


class ModelViewsMixin:
    # 由 Dashboard 提供：workdir / lake / cfg / c / _cached / _read_json / _zone_files / _media_rel / rounds

    # ------------------------------------------------------------------ 元数据（前端阈值 / 标签不再硬编码）
    def meta(self) -> dict[str, Any]:
        from ..models.world import VIEW_HARDNESS, VIEW_TARGETS
        c = self.c
        classes = list(c("dataset.classes", []) or [])
        return {
            "thresholds": {
                "gate_min_pass_rate": c("gate.min_pass_rate", 0.6),
                "gate_pass_score": c("gate.pass_score", 0.7),
                "gate_metric_floor": c("gate.per_metric_floor", 0.5),
                "psi": c("monitor.psi_alert", 0.2),
                "drift_method": c("monitor.drift_method", "psi"),
                "drift": c("monitor.drift_thresholds", {"psi": c("monitor.psi_alert", 0.2)}),
                "latency_p95_ms": c("monitor.latency_p95_ms", 60),
                "fragmentation": c("governance.max_fragmentation", 0.3),
                "regression": c("eval.regression_threshold", 0.01),
                "rollback_fail_rate": c("ota.rollback_threshold.fail_rate", 0.25),
                "shadow_disagree": c("ota.rollback_threshold.shadow_disagree", 0.1),
                "d1_min_confidence": c("routing.d1.min_confidence", 0.85),
                "d2_min_confidence": c("routing.d2.min_confidence", 0.8),
                "l1_score": c("models.l1.score_threshold", 0.5),
                "consensus": c("annotate.consensus_threshold", 0.67),
            },
            "classes": classes,
            "class_labels": dict(c("dataset.class_labels", {}) or {}),
            "slices": list(c("eval.slices", []) or []),
            "slice_labels": dict(c("eval.slice_labels", {}) or {}),
            "views": list(dict.fromkeys(list(c("flywheel.missing_views", []) or []) + ["night", "occluded"])),
            "all_views": list(VIEW_TARGETS),
            "view_hardness": VIEW_HARDNESS,
            "image_size": [c("dataset.image_width", 640), c("dataset.image_height", 360)],
            "impl": dict(c("impl", {}) or {}),
            "budget": dict(c("cost.budget", {}) or {}),
            "upload": {"max_bytes": UPLOAD_MAX_BYTES, "sources": list(UPLOAD_SOURCES), "types": ["jpg", "png"]},
        }

    # ------------------------------------------------------------------ 模型库
    def _round_reports(self) -> list[dict[str, Any]]:
        return [r for r in self.rounds() if isinstance(r, dict)]

    def zoo(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            from ..core.infra import Registry
            from ..models import detectors, world
            cfg = self.cfg
            reps = self._round_reports()
            usage: dict[tuple[str, str], dict[str, Any]] = {}
            for r in reps:
                for m in r.get("models") or []:
                    u = usage.setdefault((m.get("kind"), m.get("model")),
                                         {"kind": m.get("kind"), "model": m.get("model"), "calls": 0, "failures": 0,
                                          "fallbacks": 0, "latency_ms_total": 0.0, "rounds": []})
                    u["calls"] += int(m.get("calls", 0))
                    u["failures"] += int(m.get("failures", 0))
                    u["fallbacks"] += int(m.get("fallbacks", 0))
                    u["latency_ms_total"] += float(m.get("latency_ms_total", 0))
                    u["rounds"].append({"round": r.get("round"), "calls": m.get("calls", 0),
                                        "p50": m.get("latency_ms_p50"), "p95": m.get("latency_ms_p95")})
            for u in usage.values():
                u["latency_ms_avg"] = round(u["latency_ms_total"] / max(1, u["calls"]), 2)
            latest = reps[-1] if reps else {}
            depth_impl = self.c("models.depth.impl", "proxy")
            return {
                "detectors": detectors.describe(cfg),
                "world": world.describe(cfg),
                "routes": world.routes(cfg),
                "l1": dict(self.c("models.l1", {}) or {}),
                "l1_impl": self.c("impl.l1", "demo"),
                "agents": list(self.c("annotate.agents", []) or []),
                "scene_quality": {"impl": self.c("impl.scene_quality", ""),
                                  "missing": Registry.missing("scene_quality", self.c("impl.scene_quality", "mock"))
                                  if Registry.has("scene_quality", self.c("impl.scene_quality", "mock")) else []},
                "gate": {"impl": self.c("impl.gate", "demo"), "depth": depth_impl,
                         "missing": Registry.missing("gate", self.c("impl.gate", "demo"))
                         if Registry.has("gate", self.c("impl.gate", "demo")) else []},
                "usage": sorted(usage.values(), key=lambda x: (x["kind"] or "", -x["calls"])),
                "fallbacks": latest.get("fallbacks") or {},
                "rounds": len(reps),
            }

        return self._cached("zoo", 2.0, build)

    # ------------------------------------------------------------------ 世界模型
    def _clip_index(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            rows = []
            lin = self._lineage()
            for p in self._zone_files("generated", "gen-*.json"):
                d = self._read_json(p, {}) or {}
                if not d.get("clip_id"):
                    continue
                g = self._read_json(self.lake / "gates" / f"gate-{d['clip_id']}.json", {}) or {}
                if not g:                                   # 旧产物没有 gates 分区：门禁失败的在 rejected，结论都在血缘
                    g = (self._read_json(self.lake / "rejected" / f"gate-{d['clip_id']}.json", {}) or {}).get("gate") or {}
                if not g and lin is not None:
                    with self._lin_lock:
                        n = lin.get(f"gate-{d['clip_id']}")
                    if n:
                        a = n.get("attrs") or {}
                        g = {"gate_status": a.get("status"), "gate_score": a.get("score"), "reasons": a.get("reasons", [])}
                meta = d.get("meta") or {}
                pre = meta.get("prescreen") or {}
                frames = d.get("frames") or []
                rows.append({
                    "clip_id": d["clip_id"], "anchor_id": d.get("anchor_id"), "view": d.get("trajectory"),
                    "model": meta.get("model") or str(d.get("generator", "")).split(":")[0],
                    "generator": d.get("generator"), "round": meta.get("round"),
                    "defect_type": meta.get("defect_type"),
                    "frames": len(frames), "thumb": self._media_rel(frames[0]) if frames else "",
                    "frame_urls": [self._media_rel(f) for f in frames],
                    "frame_boxes": d.get("frame_boxes") or [],
                    "status": g.get("gate_status") or "pending", "score": g.get("gate_score"),
                    "depth": g.get("depth_continuity"), "shape": g.get("shape_stability"),
                    "flicker": g.get("texture_flicker"), "reasons": g.get("reasons", []),
                    "metric_source": g.get("source") or ("sim" if meta.get("_sim_quality") else ""),
                    "prescreen_ok": pre.get("ok", True), "prescreen": pre.get("issues", []),
                    "latency_ms": meta.get("latency_ms"), "fallback_from": meta.get("fallback_from", []),
                })
            rows.sort(key=lambda x: x["clip_id"], reverse=True)
            return rows

        return self._cached("clip-index", 4.0, build)

    def world(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            from ..models import world as wm
            clips = self._clip_index()
            reps = self._round_reports()
            combo: dict[tuple[str, str], list[int]] = {}
            for c in clips:
                k = combo.setdefault((c["model"], c["view"]), [0, 0])
                k[0] += c["status"] == "passed"
                k[1] += 1
            n2 = self._read_json(self.lake / "reports" / "n2_failure_analysis.json", {}) or {}
            scored = [c for c in clips if c["score"] is not None]
            return {
                "catalog": wm.describe(self.cfg),
                "routes": wm.routes(self.cfg),
                "missing_views": list(self.c("flywheel.missing_views", []) or []),
                "frames_per_clip": self.c("flywheel.frames_per_clip", 3),
                "totals": {"clips": len(clips), "passed": sum(c["status"] == "passed" for c in clips),
                           "prescreen_failed": sum(not c["prescreen_ok"] for c in clips),
                           "fallback": sum(bool(c["fallback_from"]) for c in clips),
                           "pixels": sum(c["metric_source"] == "pixels" for c in clips)},
                "matrix": [{"model": m, "view": v, "passed": ok, "total": n, "pass_rate": round(ok / n, 3)}
                           for (m, v), (ok, n) in sorted(combo.items())],
                "rounds": [{"round": r.get("round"), **(r.get("world") or {})} for r in reps],
                "n2": n2,
                "reasons": dict(Counter(x for c in clips for x in c["reasons"])),
                "metric_hist": {k: _hist([c[k] for c in scored if c[k] is not None]) for k in ("depth", "shape", "flicker")},
                "score_hist": _hist([c["score"] for c in scored]),
                "thresholds": {"pass_score": self.c("gate.pass_score", 0.7), "floor": self.c("gate.per_metric_floor", 0.5),
                               "min_pass_rate": self.c("gate.min_pass_rate", 0.6),
                               "weights": self.c("gate.weights", {})},
            }

        return self._cached("world", 2.0, build)

    def clips(self, page: int = 1, size: int = 24, view: str = "", model: str = "", status: str = "",
              round_no: str = "", q: str = "") -> dict[str, Any]:
        rows = self._clip_index()
        facets = {"view": dict(Counter(r["view"] for r in rows)), "model": dict(Counter(r["model"] for r in rows)),
                  "status": dict(Counter(r["status"] for r in rows)),
                  "round": dict(Counter(str(r["round"]) for r in rows if r["round"] is not None))}
        sel = rows
        if view:
            sel = [r for r in sel if r["view"] == view]
        if model:
            sel = [r for r in sel if r["model"] == model]
        if status:
            sel = [r for r in sel if r["status"] == status]
        if round_no:
            sel = [r for r in sel if str(r["round"]) == str(round_no)]
        if q:
            needle = q.lower()
            sel = [r for r in sel if needle in f"{r['clip_id']} {r['anchor_id']} {r['defect_type']}".lower()]
        out = _page([{k: v for k, v in r.items() if k not in ("frame_urls", "frame_boxes")} for r in sel], page, size)
        out["facets"] = facets
        return out

    def clip_detail(self, clip_id: str) -> Optional[dict[str, Any]]:
        if not _ID.fullmatch(clip_id or ""):
            return None
        d = self._read_json(self.lake / "generated" / f"{clip_id}.json")
        if not isinstance(d, dict):
            return None
        row = next((r for r in self._clip_index() if r["clip_id"] == clip_id), {})
        anchor = self._read_json(self.lake / "anchors" / f"{d.get('anchor_id')}.json", {}) or {}
        gate = self._read_json(self.lake / "gates" / f"gate-{clip_id}.json", {}) or {}
        anns = []
        lin = self._lineage()
        if lin is not None:
            try:
                with self._lin_lock:
                    anns = [a for a in lin.children(f"gate-{clip_id}") if a.startswith("ann-")]
            except Exception:
                anns = []
        frames = d.get("frames") or []
        boxes = d.get("frame_boxes") or []
        return {
            "clip": {k: v for k, v in d.items() if k != "meta"}, "meta": {k: v for k, v in (d.get("meta") or {}).items()
                                                                         if not k.startswith("_")},
            "summary": row, "gate": gate,
            "frames": [{"url": self._media_rel(f), "path": f, "bbox": boxes[i] if i < len(boxes) else None,
                        "pose": (d.get("poses") or [{}] * len(frames))[i] if i < len(d.get("poses") or []) else None}
                       for i, f in enumerate(frames)],
            "anchor": {**anchor, "url": self._media_rel(anchor.get("media_uri"))} if anchor else None,
            "annotations": anns[:30],
        }

    # ------------------------------------------------------------------ 检测模型
    def _ingest_index(self) -> dict[str, dict[str, Any]]:
        def build() -> dict[str, dict[str, Any]]:
            out = {}
            for p in self._zone_files("ingest", "ing-*.json"):
                d = self._read_json(p, {}) or {}
                if not d.get("ingest_id"):
                    continue
                meta = d.get("source_meta") or {}
                media = d.get("media") or []
                img = next((m.get("uri") for m in media if m.get("kind") == "image"), None)
                sim = meta.get("_sim") or {}
                out[d["ingest_id"]] = {
                    "ingest_id": d["ingest_id"], "source_type": d.get("source_type"), "round": meta.get("round"),
                    "image": img, "thumb": self._media_rel(img) if img else "",
                    "media_kinds": [m.get("kind") for m in media],
                    "description": d.get("user_description"), "privacy_flag": d.get("privacy_flag"),
                    "audio": [a.get("label") for a in d.get("audio_events") or []],
                    "scene_tags": meta.get("scene_tags") or {}, "scene_quality": meta.get("scene_quality"),
                    "device_id": meta.get("device_id"), "trigger_reason": meta.get("trigger_reason"),
                    "upload_id": meta.get("upload_id"),
                    "truth": sim.get("defect_type") if "defect_type" in sim else "__none__",
                    "truth_bbox": sim.get("bbox"),
                }
            return out

        return self._cached("ingest-index", 4.0, build)

    def _defect_index(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            ing = self._ingest_index()
            rows = []
            for zone in ("defects", "negatives", "anchors"):
                for p in self._zone_files(zone, "def-*.json"):
                    d = self._read_json(p, {}) or {}
                    if not d.get("defect_id"):
                        continue
                    l1 = d.get("l1_result") or {}
                    i = ing.get(d.get("ingest_id"), {})
                    pred, final = l1.get("defect_type"), d.get("final_decision")
                    final_type = d.get("defect_type") if final == "confirmed" else None
                    truth = i.get("truth", "__none__")
                    ref = truth if truth != "__none__" else (final_type if d.get("level_reached") in ("L2", "L3") else "__none__")
                    if ref == "__none__" or not l1:
                        outcome = "unknown"
                    elif pred == ref:
                        outcome = "correct" if pred else "true_negative"
                    elif pred is None:
                        outcome = "missed"
                    elif ref is None:
                        outcome = "false_alarm"
                    else:
                        outcome = "wrong_class"
                    d1 = next((s for s in d.get("routing_trace") or [] if s.get("router") == "D1"), {})
                    rows.append({
                        "defect_id": d["defect_id"], "ingest_id": d.get("ingest_id"), "round": i.get("round"),
                        "source_type": i.get("source_type"), "thumb": i.get("thumb", ""),
                        "model": l1.get("model"), "pred": pred, "confidence": l1.get("confidence"),
                        "bbox": l1.get("bbox"), "available": l1.get("available", True),
                        "detections": (l1.get("detections") or [])[:5], "latency_ms": l1.get("latency_ms"),
                        "category_uncertain": l1.get("category_uncertain"), "hard_slice": l1.get("hard_slice"),
                        "scene_quality": l1.get("scene_quality") or i.get("scene_quality"),
                        "final_decision": final, "final_type": d.get("defect_type"), "level": d.get("level_reached"),
                        "location": d.get("location"),
                        "reference": None if ref == "__none__" else ref, "ref_source": "sim_truth" if truth != "__none__"
                        else ("downstream" if ref != "__none__" else "none"),
                        "truth_bbox": i.get("truth_bbox"), "outcome": outcome,
                        "d1": d1.get("outcome"), "d1_reasons": d1.get("reasons", []),
                    })
            rows.sort(key=lambda r: r["defect_id"], reverse=True)
            return rows

        return self._cached("defect-index", 4.0, build)

    def detectors(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            from ..models import detectors as det
            reps = self._round_reports()
            evals = [{"round": r.get("round"), **(r.get("detector_eval") or {})} for r in reps if r.get("detector_eval")]
            latest = evals[-1] if evals else {}
            rows = self._defect_index()
            ing = list(self._ingest_index().values())
            quality = [i["scene_quality"] for i in ing if i.get("scene_quality")]
            stats = [m for r in reps[-1:] for m in r.get("models") or [] if m.get("kind") in ("detector", "scene_quality", "vlm")]
            return {
                "catalog": det.describe(self.cfg),
                "l1": dict(self.c("models.l1", {}) or {}), "l1_impl": self.c("impl.l1", "demo"),
                "agents": list(self.c("annotate.agents", []) or []),
                "rounds": [{"round": e.get("round"), "accuracy": e.get("accuracy"), "mean_iou": e.get("mean_iou"),
                            "latency_ms_p50": e.get("latency_ms_p50"), "latency_ms_p95": e.get("latency_ms_p95"),
                            "evaluated": e.get("evaluated"), "events": e.get("events"), "detector": e.get("detector"),
                            "unavailable": e.get("unavailable"), "reference_mix": e.get("reference_mix")} for e in evals],
                "latest": latest,
                "outcomes": dict(Counter(r["outcome"] for r in rows)),
                "d1": dict(Counter(r["d1"] or "none" for r in rows)),
                "d1_reasons": dict(Counter(x for r in rows for x in r["d1_reasons"])),
                "confidence_hist": _hist([r["confidence"] for r in rows if r["confidence"] is not None]),
                "quality": {"count": len(quality),
                            "flags": {f: sum(f in (q.get("flags") or []) for q in quality) for f in SCENE_FLAGS},
                            "score_hist": _hist([q.get("score") for q in quality]),
                            "blur_hist": _hist([q.get("blur") for q in quality]),
                            "exposure_hist": _hist([q.get("exposure") for q in quality], lo=-1.0, hi=1.0),
                            "model": next((q.get("model") for q in quality if q.get("model")), None)},
                "model_stats": stats,
                "thresholds": {"score": self.c("models.l1.score_threshold", 0.5),
                               "d1_min_confidence": self.c("routing.d1.min_confidence", 0.85),
                               "uncertain_margin": self.c("models.l1.uncertain_margin", 0.1)},
            }

        return self._cached("detectors", 2.0, build)

    def detections(self, page: int = 1, size: int = 24, outcome: str = "", label: str = "", source: str = "",
                   round_no: str = "", q: str = "") -> dict[str, Any]:
        rows = self._defect_index()
        facets = {"outcome": dict(Counter(r["outcome"] for r in rows)),
                  "label": dict(Counter(str(r["pred"]) for r in rows)),
                  "source": dict(Counter(r["source_type"] or "unknown" for r in rows)),
                  "round": dict(Counter(str(r["round"]) for r in rows if r["round"] is not None))}
        sel = rows
        if outcome:
            sel = [r for r in sel if r["outcome"] == outcome]
        if label:
            sel = [r for r in sel if str(r["pred"]) == label]
        if source:
            sel = [r for r in sel if r["source_type"] == source]
        if round_no:
            sel = [r for r in sel if str(r["round"]) == str(round_no)]
        if q:
            needle = q.lower()
            sel = [r for r in sel if needle in f"{r['defect_id']} {r['ingest_id']} {r['pred']} {r['final_type']}".lower()]
        out = _page(sel, page, size)
        out["facets"] = facets
        return out

    # ------------------------------------------------------------------ 数据接入
    def sources(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            reps = self._round_reports()
            per_round = []
            for r in reps:
                c = r.get("counters") or {}
                per_round.append({
                    "round": r.get("round"),
                    "by_source": {k.split(".", 2)[2]: v for k, v in c.items() if k.startswith("a4.src.")},
                    "ingest": c.get("a4.ingest_events", 0), "hard_cases": c.get("a0.hard_cases", 0),
                    "budget_dropped": c.get("a0.budget_dropped", 0),
                    "quarantined": c.get("a1.quarantined", 0) + c.get("a2.quarantined", 0),
                    "dedup_dropped": c.get("a2.dedup_dropped", 0), "schema_rejected": c.get("a4.schema_rejected", 0),
                    "quality_flags": {k.split(".", 2)[2]: v for k, v in c.items() if k.startswith("a2.quality.")},
                })
            quarantine = []
            for p in self._zone_files("quarantine", "*.json"):
                d = self._read_json(p, {}) or {}
                raw = d.get("raw") or d.get("pkt") or {}
                quarantine.append({"id": p.stem, "reason": d.get("reason", "unknown"),
                                   "source_type": raw.get("source_type"),
                                   "uri": ((raw.get("media") or [{}])[0] or {}).get("uri")})
            ing = list(self._ingest_index().values())
            inbox = self.inbox()
            return {
                "per_round": per_round,
                "totals": {"ingest": len(ing), "by_source": dict(Counter(i["source_type"] for i in ing)),
                           "with_image": sum(bool(i["thumb"]) for i in ing),
                           "with_audio": sum(bool(i["audio"]) for i in ing),
                           "device": sum(i["source_type"] == "device" for i in ing),
                           "quarantined": len(quarantine)},
                "quarantine_reasons": dict(Counter(q["reason"] for q in quarantine)),
                "quarantine": quarantine[-40:],
                "triggers": dict(Counter(i["trigger_reason"] for i in ing if i.get("trigger_reason"))),
                "scene_tags": {k: dict(Counter((i["scene_tags"] or {}).get(k) for i in ing if (i["scene_tags"] or {}).get(k)))
                               for k in ("light", "distance")},
                "inbox": inbox,
                "privacy": {"allow_raw_media_off_device": self.c("privacy.allow_raw_media_off_device", False),
                            "retention_days": self.c("privacy.retention_days", {})},
                "device_budget": self.c("device.daily_upload_budget", 0),
            }

        return self._cached("sources", 2.0, build)

    def ingest_events(self, page: int = 1, size: int = 24, source: str = "", round_no: str = "", flag: str = "",
                      q: str = "") -> dict[str, Any]:
        decisions = {r["ingest_id"]: r for r in self._defect_index()}
        rows = []
        for i in self._ingest_index().values():
            d = decisions.get(i["ingest_id"]) or {}
            rows.append({**{k: v for k, v in i.items() if k not in ("truth", "truth_bbox")},
                         "defect_id": d.get("defect_id"), "final_decision": d.get("final_decision"),
                         "final_type": d.get("final_type"), "level": d.get("level"), "pred": d.get("pred")})
        rows.sort(key=lambda r: r["ingest_id"], reverse=True)
        sel = rows
        if source:
            sel = [r for r in sel if r["source_type"] == source]
        if round_no:
            sel = [r for r in sel if str(r["round"]) == str(round_no)]
        if flag:
            sel = [r for r in sel if flag in ((r.get("scene_quality") or {}).get("flags") or [])]
        if q:
            needle = q.lower()
            sel = [r for r in sel if needle in f"{r['ingest_id']} {r['description'] or ''} {r['device_id'] or ''}".lower()]
        return _page(sel, page, size)

    # ------------------------------------------------------------------ 上传收件箱（A1 用户上传源）
    def inbox(self) -> dict[str, Any]:
        items = []
        for p in self._zone_files("inbox", "up-*.json"):
            d = self._read_json(p, {}) or {}
            if d.get("upload_id"):
                items.append({**d, "url": self._media_rel(d.get("image"))})
        items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return {"pending": sum(not x.get("consumed") for x in items), "consumed": sum(bool(x.get("consumed")) for x in items),
                "items": items[:60]}

    def upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = str(payload.get("data") or "")
        if "," in raw[:80] and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("data 必须是 base64 编码的图像") from None
        if not blob or len(blob) > UPLOAD_MAX_BYTES:
            raise ValueError(f"图像大小需在 1B–{UPLOAD_MAX_BYTES // 1000}KB 之间")
        ext = next((e for magic, e in UPLOAD_TYPES.items() if blob.startswith(magic)), None)
        if ext is None:
            raise ValueError("只支持 JPEG / PNG")
        source = str(payload.get("source_type") or "user_upload")
        if source not in UPLOAD_SOURCES:
            raise ValueError(f"source_type 必须是 {UPLOAD_SOURCES} 之一")
        tags = payload.get("scene_tags") or {}
        if not isinstance(tags, dict):
            raise ValueError("scene_tags 必须是对象")
        tags = {str(k)[:24]: str(v)[:24] for k, v in list(tags.items())[:6]}
        uid = new_id("up")
        inbox = self.lake / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        img = inbox / f"{uid}{ext}"
        img.write_bytes(blob)
        rec = {"upload_id": uid, "image": str(img), "source_type": source,
               "description": str(payload.get("description") or "")[:300] or None,
               "filename": re.sub(r"[^\w.\-]+", "_", str(payload.get("filename") or ""))[:80],
               "scene_tags": tags, "license": "ok", "contains_pii": bool(payload.get("contains_pii")),
               "bytes": len(blob), "created_at": time.time(), "consumed": False}
        (inbox / f"{uid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        from ..core.infra import AuditLog
        AuditLog(self.workdir / "audit.jsonl").log("web-upload", "inbox_upload", uid, source_type=source,
                                                   bytes=len(blob), filename=rec["filename"])
        with self._cache_lock:
            for k in ("sources", "overview"):
                self._cache.pop(k, None)
        return {"ok": True, "upload": {**rec, "url": self._media_rel(str(img))}}
