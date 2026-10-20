"""L3 云端飞轮：P2 接入校验与入湖 / P3 世界模型蜂窝扩充 / P4 几何门禁 /
P5 多智能体标注与分歧路由 / P6 统一 Schema 与双格式导出 / N2 拒绝池与失败分析。"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from ..core.context import Context
from ..core.infra import Registry, content_hash, new_id
from ..core.schemas import (AgentLabel, Anchor, Annotation, BBox, DatasetVersion, DefectEvent, FinalDecision,
                            GateResult, GateStatus, GeneratedClip, HumanDecision, SchemaError)
from ..core.states import ANNOTATION_SM, Tracker
from ..models.detectors import build_with_fallback
from ..models.gate import GeometryGate, PixelMetricsGate, SelfConsistencyGate  # noqa: F401  兼容旧导入路径
from ..models.world import (VIEW_HARDNESS, ControlledTransformWM, WorldModel, build_world_model,  # noqa: F401
                            plan_trajectory, prescreen, route_for, spec_status, supports, world_specs)

SCHEMA_VERSION = "1.0"
EXPORTER_VERSION = "0.1.0"


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ================================================================ P2 接入校验与入湖
class Intake:
    """读取 B7 确认缺陷与 needs_more_data 事件 → Schema 校验、幂等去重、隐私复核 → 锚点任务。"""

    def run(self, ctx: Context) -> list[tuple[Anchor, DefectEvent]]:
        out = []
        views = list(ctx.c("flywheel.missing_views", ["yaw-30", "yaw+30", "pitch-15", "far"]))
        for zone, priority in (("anchors", "high"), ("defects", "normal")):
            for p in ctx.lake.list(zone, "def-*.json"):
                done = p.with_suffix(".p2done")
                if done.exists():                                  # 幂等
                    continue
                try:
                    de = DefectEvent.from_dict(json.loads(p.read_text(encoding="utf-8")))
                    de.validate()
                except (SchemaError, KeyError, TypeError) as e:
                    ctx.lake.put_json("quarantine", f"p2-{p.stem}", {"reason": str(e)})
                    ctx.count("p2.quarantined")
                    continue
                ing = ctx.lake.get_json("ingest", de.ingest_id)
                if not ing.get("privacy_flag") and ing["source_meta"].get("contains_pii"):
                    ctx.lake.put_json("quarantine", f"p2priv-{p.stem}", {"reason": "隐私复核未过"})
                    continue
                anc = Anchor(anchor_id=new_id("anc"), defect_id=de.defect_id, media_uri=first_image(ing["media"]),
                             scene_tags=ing["source_meta"].get("scene_tags", {}),
                             missing_views=views if priority == "normal" else views + ["night", "occluded"],
                             bbox=de.location)
                ctx.lake.put_json("anchors", anc.anchor_id, anc)
                ctx.lineage.node(anc.anchor_id, "anchor", priority=priority)
                ctx.lineage.link(de.defect_id, anc.anchor_id)
                done.touch()
                out.append((anc, de))
        ctx.count("p2.anchors", len(out))
        return out


def first_image(media: list[dict[str, Any]]) -> str:
    """优先取图像媒体（视频源由 A2 抽出关键帧）。"""
    return next((m["uri"] for m in media if m.get("kind") == "image"), media[0]["uri"])


# ================================================================ P3 世界模型蜂窝扩充
class Expansion:
    """锚点 → 轨迹生成 → 按视角路由到世界模型（失败/不可用按顺序回退）→ 算力记账 → 生成 → 初筛 → lineage。

    demoted：N2 反馈的 {(视角, 模型)}，该模型在该视角上可用率过低，有其它候选时跳过（“换模型”）。
    """

    def __init__(self, ctx: Context):
        self._backends: dict[str, Any] = {}                    # 名字 → WorldModel 实例或不可用原因(str)

    def backend(self, ctx: Context, name: str) -> Optional[WorldModel]:
        if name not in self._backends:
            try:
                self._backends[name] = build_world_model(ctx, name)
            except Exception as e:                              # 缺依赖 / 未配置：记一次审计，后续直接跳过
                self._backends[name] = str(e)
                ctx.audit.log("P3", "world_model_unavailable", name, reason=str(e))
        b = self._backends[name]
        return None if isinstance(b, str) else b

    def run(self, ctx: Context, anchors: list[tuple[Anchor, DefectEvent]], allowed_views: Optional[set[str]] = None,
            demoted: Optional[set[tuple[str, str]]] = None) -> list[tuple[GeneratedClip, DefectEvent]]:
        out = []
        out_dir = ctx.lake.path("generated")
        n_frames = int(ctx.c("flywheel.frames_per_clip", 4))
        specs = world_specs(ctx)
        demoted = demoted or set()
        for anc, de in anchors:
            for view in anc.missing_views:
                if allowed_views is not None and view not in allowed_views:
                    continue
                traj = plan_trajectory(view, n_frames)
                chain = route_for(ctx, view)                            # N2 降级只调整顺序，不删除候选
                chain = [m for m in chain if (view, m) not in demoted] + [m for m in chain if (view, m) in demoted]
                clip, tried = None, []
                for name in chain:
                    spec = specs.get(name, {"impl": name})
                    wm = self.backend(ctx, name)
                    if wm is None or not supports(spec, view):
                        continue
                    gpu = float(spec.get("gpu_hours_per_clip", ctx.c("cost.gpu_hours_per_clip", 0.05)))
                    if not ctx.ledger.charge("gpu_hours", gpu):          # C3 算力预算
                        ctx.count("p3.budget_skipped")
                        break
                    t0 = time.perf_counter()
                    try:
                        clip = wm.generate(ctx, anc, traj, out_dir)
                        ok = True
                    except Exception as e:                              # 生成失败 → 换下一个候选模型
                        ok = False
                        tried.append(f"{name}:{type(e).__name__}")
                        ctx.audit.log("P3", "generate_failed", anc.anchor_id, model=name, view=view, err=str(e)[:200])
                    ms = (time.perf_counter() - t0) * 1000
                    ctx.model_stats.record("world_model", name, ms, ok, fallback=bool(tried) and ok)
                    if ok:
                        clip.meta.update({"model": name, "fallback_from": tried, "gpu_hours": gpu,
                                          "latency_ms": round(ms, 1), "round": ctx.round, "defect_type": de.defect_type})
                        break
                if clip is None:
                    ctx.count("p3.generation_failed")
                    continue
                if clip.meta["fallback_from"]:
                    ctx.count("p3.fallback")
                clip.meta["prescreen"] = prescreen(clip, n_frames)        # 初筛
                if not clip.meta["prescreen"]["ok"]:
                    ctx.count("p3.prescreen_failed")
                ctx.count(f"p3.model.{clip.meta['model']}")
                ctx.lake.put_json("generated", clip.clip_id, clip)
                ctx.lineage.node(clip.clip_id, "generated", generator=clip.generator, view=view, synthetic=True,
                                 model=clip.meta["model"])
                ctx.lineage.link(anc.anchor_id, clip.clip_id)
                out.append((clip, de))
        ctx.count("p3.clips", len(out))
        self._record_effective(ctx)
        return out

    def _record_effective(self, ctx: Context) -> None:
        """默认路由链上实际生效的世界模型（前面的候选不可用时写明原因），供控制台系统页展示。"""
        chain = route_for(ctx, "default")
        skipped = [f"{n}: {self._backends[n]}" for n in chain if isinstance(self._backends.get(n), str)]
        eff = next((n for n in chain if n in self._backends and not isinstance(self._backends[n], str)), None)
        if eff:
            ctx.fallbacks["world_model"] = {"selected": chain[0], "effective": eff, "reason": "；".join(skipped)}


# ================================================================ P4 几何门禁
class Gate:
    def __init__(self, ctx: Context):
        self.impl: GeometryGate = ctx.make("gate")
        if isinstance(self.impl, PixelMetricsGate):
            self.impl = PixelMetricsGate.from_ctx(ctx)

    def run(self, ctx: Context, clips: list[tuple[GeneratedClip, DefectEvent]]):
        w = ctx.c("gate.weights", {"depth": 0.4, "shape": 0.4, "flicker": 0.2})   # ⚠ 权重待定
        thr = ctx.c("gate.pass_score", 0.7)
        floor = ctx.c("gate.per_metric_floor", 0.5)
        passed, rejected = [], []
        for clip, de in clips:
            pre = clip.meta.get("prescreen") or {"ok": True, "issues": []}
            m = self.impl.measure(ctx, clip)
            source = m.pop("source", "")
            parts = {"depth": m["depth_continuity"], "shape": m["shape_stability"], "flicker": 1 - m["texture_flicker"]}
            score = sum(w[k] * parts[k] for k in parts)
            reasons = [f"prescreen:{x}" for x in pre.get("issues", [])]
            reasons += [f"{k}<{floor}" for k, v in parts.items() if v < floor]
            if score < thr:
                reasons.append(f"score<{thr}")
            st = GateStatus.PASSED if not reasons else GateStatus.REJECTED
            gr = GateResult(clip_id=clip.clip_id, gate_score=round(score, 3), gate_status=st, reasons=reasons,
                            generator=clip.meta.get("model", clip.generator), view=clip.trajectory, source=source,
                            **{k: round(float(v), 4) for k, v in m.items()})
            gid = f"gate-{clip.clip_id}"
            ctx.lake.put_json("gates", gid, gr)
            ctx.lineage.node(gid, "gate", status=st.value, score=gr.gate_score, reasons=reasons)
            ctx.lineage.link(clip.clip_id, gid)
            if st == GateStatus.PASSED:
                passed.append((clip, de, gr))
            else:
                ctx.lake.put_json("rejected", gid, {"kind": "gate_failed", "clip": clip.to_dict(), "gate": gr.to_dict()})
                rejected.append((clip, gr))
        ctx.audit.log("P4", "gate", "batch", passed=len(passed), rejected=len(rejected))
        ctx.count("p4.passed", len(passed))
        ctx.count("p4.rejected", len(rejected))
        return passed, rejected


# ================================================================ N2 拒绝池与失败分析
def analyze_rejections(ctx: Context, rejected: list[tuple[GeneratedClip, GateResult]], total: int,
                       passed: Optional[list[tuple[GeneratedClip, Any, GateResult]]] = None) -> dict[str, Any]:
    by_view = Counter(c.trajectory for c, _ in rejected)
    by_reason = Counter(r for _, g in rejected for r in g.reasons)
    pass_rate = 1 - len(rejected) / max(1, total)
    # 按 模型 × 视角 统计可用率：低于阈值且该视角还有其它候选模型 → 换模型；没有候选 → 收窄视角
    combo: dict[tuple[str, str], list[int]] = {}
    for c, _ in rejected:
        combo.setdefault((c.meta.get("model", c.generator), c.trajectory), [0, 0])[1] += 1
    for c, _, _ in passed or []:
        r = combo.setdefault((c.meta.get("model", c.generator), c.trajectory), [0, 0])
        r[0] += 1
        r[1] += 1
    min_rate = ctx.c("gate.min_pass_rate", 0.6)
    min_n = int(ctx.c("gate.min_samples_for_feedback", 4))
    specs = world_specs(ctx)
    ready = {m for m in specs if spec_status(specs[m])[0] == "ready"}
    switch, narrow = [], set()
    for (model, view), (ok, n) in combo.items():
        if n >= min_n and ok / n < min_rate:
            if any(m != model and m in ready for m in route_for(ctx, view)):   # 只有真正可用的候选才算“能换”
                switch.append({"view": view, "model": model, "pass_rate": round(ok / n, 3)})
            else:
                narrow.add(view)
    if pass_rate < min_rate:                                    # 整体可用率过低：去掉拒绝最多的视角
        narrow |= {v for v, _ in by_view.most_common(2)}
    rep = {"total": total, "rejected": len(rejected), "pass_rate": round(pass_rate, 3),
           "by_view": dict(by_view), "by_reason": dict(by_reason),
           "by_model_view": [{"model": m, "view": v, "passed": ok, "total": n, "pass_rate": round(ok / n, 3)}
                             for (m, v), (ok, n) in sorted(combo.items())],
           "feedback_switch_models": switch,
           "feedback_drop_views": sorted(narrow)}
    ctx.lake.put_json("reports", "n2_failure_analysis", rep)
    return rep


# ================================================================ P5 多智能体标注与分歧路由
class LabelAgent(ABC):
    name = "agent"

    @abstractmethod
    def label(self, ctx: Context, frame: str, hint: dict[str, Any]) -> AgentLabel: ...


@Registry.register("label_agent", "demo")
class MockLabelAgent(LabelAgent):
    def __init__(self, name: str = "agent", noise: float = 0.15):
        self.name, self.noise = name, noise

    def label(self, ctx, frame, hint):
        truth, bb = hint.get("defect_type"), hint.get("bbox")
        hard = hint.get("hardness", 0.2)
        dtype = truth if ctx.rng.random() > self.noise * 0.5 + hard * 0.25 else ctx.rng.choice(["crack", "scratch", "dent", None])
        box = None
        if dtype and bb:
            x = _clip(bb.x + bb.w * ctx.rng.uniform(-0.08, 0.08))
            y = _clip(bb.y + bb.h * ctx.rng.uniform(-0.08, 0.08))
            box = BBox(x, y, min(bb.w, 1 - x), min(bb.h, 1 - y))
        return AgentLabel(agent=self.name, defect_type=dtype, bbox=box,
                          caption=f"图中{'存在' + dtype if dtype else '未见'}缺陷")


@Registry.register("label_agent", "detector")
class DetectorLabelAgent(LabelAgent):
    """把检测模型库里的任意检测器当作一个标注智能体（异构多智能体：错误模式与 VLM 实例不同）。"""

    def __init__(self, name: str = "agent", detector: str = "classical", ctx: Optional[Context] = None,
                 score_threshold: float = 0.5):
        self.thr = float(score_threshold)
        self.det, self.fallback_reason = build_with_fallback(ctx, detector)
        self.name = f"{name}:{self.det.name}"

    def label(self, ctx, frame, hint):
        t0 = time.perf_counter()
        try:
            dets = self.det.detect(ctx, frame, {"_sim": {"defect_type": hint.get("defect_type"),
                                                          "bbox": hint["bbox"].to_dict() if hint.get("bbox") else None,
                                                          "difficulty": hint.get("hardness", 0.2)}})
            ok = True
        except Exception:
            dets, ok = [], False
        ctx.model_stats.record("detector", self.det.name, (time.perf_counter() - t0) * 1000, ok)
        top = dets[0] if dets and dets[0].score >= self.thr else None
        return AgentLabel(agent=self.name, defect_type=top.label if top else None, bbox=top.bbox if top else None,
                          caption=f"检测器 {self.det.name}：{'发现 ' + top.label if top else '未检出缺陷'}")


def iou(a: Optional[BBox], b: Optional[BBox]) -> float:
    if not a or not b:
        return 1.0 if a is b else 0.0
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def consensus(labels: list[AgentLabel], iou_thr: float) -> tuple[float, AgentLabel]:
    """多数类别 + 与多数代表框 IoU≥阈值 的比例作为一致度。"""
    top, _ = Counter(l.defect_type for l in labels).most_common(1)[0]
    group = [l for l in labels if l.defect_type == top]
    rep = group[0]
    agree = sum(1 for l in group if top is None or iou(l.bbox, rep.bbox) >= iou_thr)
    return agree / len(labels), rep


def build_agents(ctx: Context) -> list[LabelAgent]:
    """[annotate] agents = ["demo", "demo", "detector:classical"] 显式列出每个智能体；
    未配置时为 num_agents 个 [impl] label_agent。"""
    specs = ctx.c("annotate.agents")
    if not specs:
        extra = {"ctx": ctx} if ctx.impl("label_agent") == "detector" else {}
        return [ctx.make("label_agent", name=f"agent{i}", **extra) for i in range(ctx.c("annotate.num_agents", 3))]
    agents: list[LabelAgent] = []
    for i, s in enumerate(specs):
        impl, _, arg = str(s).partition(":")
        if impl == "detector":
            agents.append(DetectorLabelAgent(name=f"agent{i}", detector=arg or "classical", ctx=ctx,
                                             score_threshold=ctx.c("models.l1.score_threshold", 0.5)))
        else:
            agents.append(Registry.create("label_agent", impl, name=f"agent{i}"))
    return agents


class Annotator:
    def __init__(self, ctx: Context):
        self.agents: list[LabelAgent] = build_agents(ctx)

    def _finalize(self, ctx: Context, ann: Annotation, sm: Tracker, rep: AgentLabel, truth: dict[str, Any]):
        thr = ctx.c("annotate.consensus_threshold", 0.67)
        ann.defect_type, ann.bbox, ann.caption = rep.defect_type, rep.bbox, rep.caption
        try:
            ann.validate()
        except SchemaError:
            sm.to("draft")                                     # Schema 失败回修
            ctx.count("p5.schema_fix")
            if ann.bbox:
                b = ann.bbox
                ann.bbox = BBox(_clip(b.x), _clip(b.y), min(b.w, 1 - _clip(b.x)), min(b.h, 1 - _clip(b.y)))
            sm.to("multi_agent_labeled").to("consensus_checked")
        if ann.agent_consensus >= thr:
            sm.to("auto_accepted")
            ctx.count("p5.auto")
        else:
            sm.to("human_review")
            ctx.count("p5.human")
            # Demo：oracle 仲裁；生产接审核平台（同 B6 QueueReviewer 的模式）
            hd = HumanDecision(reviewer="oracle", decision=FinalDecision.CONFIRMED, defect_type=truth.get("defect_type"),
                               severity=None, reason="p5 disagreement", minutes_spent=1.5,
                               corrected_bbox=truth.get("bbox"))
            ctx.ledger.charge("human_minutes", hd.minutes_spent)
            ann.human_decision = hd
            ann.defect_type, ann.bbox = hd.defect_type, hd.corrected_bbox or ann.bbox
            sm.to("arbitrated")
            ctx.audit.log("oracle", "arbitrate_annotation", ann.annotation_id, consensus=ann.agent_consensus)
        sm.to("frozen")
        ann.state = sm.state
        ann.qa = [{"q": "图中是否有缺陷？是什么类型？",
                   "a": f"有，{ann.defect_type}" if ann.defect_type else "没有明显缺陷"}]
        if ann.agent_consensus < 1.0:   # 硬约束：保留分歧信号供 VLM 训练
            ann.qa.append({"q": "这个判断难在哪里？", "a": f"多智能体一致度 {ann.agent_consensus:.2f}，存在歧义"})

    def run(self, ctx: Context, passed, real_defects: list[DefectEvent]) -> list[Annotation]:
        iou_thr = ctx.c("annotate.iou_threshold", 0.5)
        anns: list[Annotation] = []
        items = []
        for clip, de, gr in passed:                                  # 合成帧：目标框随视角变换（世界模型给出）
            for k, f in enumerate(clip.frames):
                fb = clip.frame_boxes[k] if k < len(clip.frame_boxes) and clip.frame_boxes[k] else de.location
                items.append((f, clip.clip_id, de, True, gr.gate_status, VIEW_HARDNESS.get(clip.trajectory, 0.3), fb))
        for de in real_defects:                                      # 真实确认样本
            ing = ctx.lake.get_json("ingest", de.ingest_id)
            items.append((first_image(ing["media"]), None, de, False, None, 0.1, de.location))
        holdout = ctx.c("dataset.real_holdout_ratio", 0.3)
        for frame, clip_id, de, synthetic, gst, hard, box in items:
            truth = {"defect_type": de.defect_type, "bbox": box, "hardness": hard}
            sm = Tracker(ANNOTATION_SM)
            labels = [a.label(ctx, frame, truth) for a in self.agents]
            sm.to("multi_agent_labeled")
            score, rep = consensus(labels, iou_thr)
            sm.to("consensus_checked")
            split = "train" if synthetic else ("holdout" if int(content_hash(frame), 16) % 100 < holdout * 100 else "train")
            ann = Annotation(annotation_id=new_id("ann"), frame_uri=frame, source_clip_id=clip_id,
                             defect_id=de.defect_id, defect_type=None, bbox=None, caption="",
                             synthetic=synthetic, agent_labels=labels, agent_consensus=round(score, 3),
                             gate_status=gst, split=split)
            self._finalize(ctx, ann, sm, rep, truth)
            ctx.lake.put_json("annotations", ann.annotation_id, ann)
            ctx.lineage.node(ann.annotation_id, "annotation", agent_consensus=ann.agent_consensus,
                             human_decision=ann.human_decision.reviewer if ann.human_decision else None,
                             synthetic=synthetic, split=split)
            ctx.lineage.link(f"gate-{clip_id}" if clip_id else de.defect_id, ann.annotation_id)
            anns.append(ann)
        ctx.count("p5.annotations", len(anns))
        return anns


# ================================================================ P6 统一 Schema 与双格式导出
class Exporter(ABC):
    fmt = ""

    @abstractmethod
    def export(self, anns: list[Annotation], classes: list[str], out: Path, cfg: dict[str, Any]) -> None: ...


class CocoExporter(Exporter):
    fmt = "coco"

    def export(self, anns, classes, out, cfg):
        W, H = cfg["image_width"], cfg["image_height"]
        images, annotations = [], []
        for i, a in enumerate(anns, 1):
            images.append({"id": i, "file_name": a.frame_uri, "width": W, "height": H})
            if a.bbox and a.defect_type:
                b = a.bbox
                annotations.append({"id": len(annotations) + 1, "image_id": i,
                                    "category_id": classes.index(a.defect_type) + 1,
                                    "bbox": [b.x * W, b.y * H, b.w * W, b.h * H], "area": b.w * W * b.h * H,
                                    "iscrowd": 0})
        data = {"images": images, "annotations": annotations,
                "categories": [{"id": i + 1, "name": c} for i, c in enumerate(classes)]}
        (out / "coco.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class YoloExporter(Exporter):
    fmt = "yolo"

    def export(self, anns, classes, out, cfg):
        d = out / "yolo" / "labels"
        d.mkdir(parents=True, exist_ok=True)
        lines_index = []
        for a in anns:
            stem = Path(a.frame_uri).stem
            rows = []
            if a.bbox and a.defect_type:
                b = a.bbox
                rows.append(f"{classes.index(a.defect_type)} {b.x + b.w / 2:.6f} {b.y + b.h / 2:.6f} {b.w:.6f} {b.h:.6f}")
            (d / f"{stem}.txt").write_text("\n".join(rows), encoding="utf-8")
            lines_index.append(a.frame_uri)
        (out / "yolo" / "images.txt").write_text("\n".join(lines_index), encoding="utf-8")
        (out / "yolo" / "data.yaml").write_text(
            "names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(classes)), encoding="utf-8")


class ShareGPTExporter(Exporter):
    fmt = "sharegpt"

    def export(self, anns, classes, out, cfg):
        rows = []
        for a in anns:
            conv = []
            for k, qa in enumerate(a.qa):
                conv.append({"from": "human", "value": ("<image>\n" if k == 0 else "") + qa["q"]})
                conv.append({"from": "gpt", "value": qa["a"]})
            rows.append({"id": a.annotation_id, "image": a.frame_uri, "conversations": conv})
        (out / "sharegpt.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


class MessagesExporter(Exporter):
    fmt = "messages"

    def export(self, anns, classes, out, cfg):
        with (out / "messages.jsonl").open("w", encoding="utf-8") as f:
            for a in anns:
                msgs = []
                for k, qa in enumerate(a.qa):
                    content = ([{"type": "image", "image": a.frame_uri}] if k == 0 else []) + [{"type": "text", "text": qa["q"]}]
                    msgs.append({"role": "user", "content": content})
                    msgs.append({"role": "assistant", "content": [{"type": "text", "text": qa["a"]}]})
                f.write(json.dumps({"id": a.annotation_id, "messages": msgs}, ensure_ascii=False) + "\n")


EXPORTERS: dict[str, type[Exporter]] = {e.fmt: e for e in (CocoExporter, YoloExporter, ShareGPTExporter, MessagesExporter)}


class DatasetExport:
    def run(self, ctx: Context, anns: list[Annotation], parent: Optional[str] = None) -> DatasetVersion:
        good = []
        for a in anns:
            try:
                a.validate()
                good.append(a)
            except SchemaError as e:                                   # 坐标越界等 → 回修
                ctx.lake.put_json("rejected", f"schema-{a.annotation_id}", {"kind": "schema", "err": str(e)})
        classes = sorted({a.defect_type for a in good if a.defect_type} | set(ctx.c("dataset.classes", [])))
        ver = f"ds-v{len(ctx.lake.list('datasets', 'ds-*.json')) + 1:03d}"
        out = ctx.lake.path("datasets") / ver
        out.mkdir(parents=True, exist_ok=True)
        cfg = {"image_width": ctx.c("dataset.image_width", 1280), "image_height": ctx.c("dataset.image_height", 720)}
        formats = list(ctx.c("dataset.formats", list(EXPORTERS)))
        for split in ("train", "holdout"):
            part = [a for a in good if a.split == split]
            sd = out / split
            sd.mkdir(exist_ok=True)
            for fmt in formats:
                EXPORTERS[fmt]().export(part, classes, sd, cfg)
        dv = DatasetVersion(
            dataset_version=ver, formats=formats, export_config={**cfg, "classes": classes, "parent": parent},
            sample_list=[a.annotation_id for a in good], schema_version=SCHEMA_VERSION, exporter_version=EXPORTER_VERSION,
            lineage={"annotations": [a.annotation_id for a in good]},
            stats={"total": len(good), "synthetic": sum(a.synthetic for a in good),
                   "train": sum(a.split == "train" for a in good), "holdout": sum(a.split == "holdout" for a in good),
                   "human_arbitrated": sum(a.human_decision is not None for a in good),
                   "class_hist": dict(Counter(a.defect_type for a in good))})
        ctx.lake.put_json("datasets", ver, dv)
        ctx.lineage.node(ver, "dataset", **dv.stats)
        ctx.lineage.links([a.annotation_id for a in good], ver)
        ctx.audit.log("P6", "dataset_version", ver, **dv.stats)
        return dv
