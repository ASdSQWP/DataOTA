"""L2 缺陷发现分级：B1 用户报告 / B2 L1 小模型 / D1 路由 / B4 L2 大模型 / D2 路由 /
B6 人工仲裁 / B7 defect_event / B8 抽检校准 / N1 负样本池。"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.context import Context
from ..core.infra import Registry, new_id
from ..core.schemas import (BBox, DefectEvent, FinalDecision, HumanDecision, IngestEvent, L1Result,
                            L2Result, RouteStep, SceneQuality, SchemaError, Severity, SourceType, UserClaim)
from ..core.states import DEFECT_SM, Tracker
from ..models.detectors import build_with_fallback, iou as box_iou

SEV_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


def _sim(ev: IngestEvent) -> dict[str, Any]:
    return ev.source_meta.get("_sim", {})


# ================================================================ B1 L0 用户报告
class ClaimParser(ABC):
    @abstractmethod
    def parse(self, ctx: Context, ev: IngestEvent) -> Optional[UserClaim]: ...


@Registry.register("claim_parser", "demo")
class KeywordClaimParser(ClaimParser):
    """Demo：关键词解析。生产可换 LLM 结构化抽取。"""
    TYPES = {"裂": "crack", "crack": "crack", "划": "scratch", "scratch": "scratch", "凹": "dent",
             "dent": "dent", "锈": "rust", "rust": "rust", "漏": "leak", "leak": "leak"}
    SEV = {"严重": Severity.HIGH, "紧急": Severity.CRITICAL, "轻微": Severity.LOW}

    def parse(self, ctx, ev):
        text = (ev.user_description or "").lower()
        if not text:
            return None
        dtype = next((v for k, v in self.TYPES.items() if k in text), None)
        sev = next((v for k, v in self.SEV.items() if k in text), Severity.MEDIUM)
        loc = re.search(r"(左上|右上|左下|右下|中间|顶部|底部)", text)
        suspicious = len(text) < 2 or "test" in text
        return UserClaim(defect_type=dtype, location_hint=loc.group(1) if loc else None,
                         severity_subjective=sev, suspicious=suspicious)


# ================================================================ B2 L1 小模型筛查
class L1Screener(ABC):
    name = "l1"

    def __init__(self, ctx: Optional[Context] = None):
        pass

    @abstractmethod
    def screen(self, ctx: Context, ev: IngestEvent) -> L1Result: ...


@Registry.register("l1", "demo")
class MockDetector(L1Screener):
    """Demo：依据 _sim 真值加噪声模拟检测器。difficulty 越高越容易错、置信度越低。"""
    name = "mock-yolo"

    def screen(self, ctx, ev):
        s = _sim(ev)
        if s.get("l1_down"):
            return L1Result(model=self.name, defect_type=None, confidence=0.0, available=False)
        diff = float(s.get("difficulty", 0.2))
        truth = s.get("defect_type")
        conf = max(0.05, min(0.99, 0.98 - diff * 0.55 + ctx.rng.uniform(-0.06, 0.06)))
        wrong = ctx.rng.random() < diff * 0.5
        pred = truth
        if wrong:
            pred = None if truth else ctx.rng.choice(["crack", "scratch"])
        bbox = BBox(**s["bbox"]) if pred and s.get("bbox") else (BBox(0.4, 0.4, 0.2, 0.2) if pred else None)
        return L1Result(model=self.name, defect_type=pred, confidence=round(conf, 3), bbox=bbox,
                        category_uncertain=diff > 0.6, hard_slice=bool(s.get("hard_slice")))


HARD_FLAGS = {"blurry", "underexposed", "overexposed", "low_contrast"}


@Registry.register("l1", "detector")
class DetectorScreener(L1Screener):
    """B2 接检测模型库：[models.l1] detector = "<[models.detectors] 中的名字>"。

    score_threshold   最高候选分 ≥ 阈值 → 判有缺陷；否则无缺陷，置信度 = 1 - 最高候选分
    uncertain_margin  前两名不同类别候选分差 < margin，或集成成员不一致 → category_uncertain
    困难切片          场景质量标志（模糊 / 欠曝 / 过曝 / 低对比）或场景标签命中 hard_tags
    检测器不可用（缺依赖 / 未配置）时回退到 mock 并写审计；单次推理异常 → available=False（D1 直接升级）。
    """

    def __init__(self, ctx: Optional[Context] = None, detector: Optional[str] = None):
        name = detector or ctx.c("models.l1.detector", "mock")
        self.det, reason = build_with_fallback(ctx, name)
        if reason:
            ctx.fallbacks["l1.detector"] = {"selected": name, "effective": self.det.name, "reason": reason}
        else:
            ctx.fallbacks.setdefault("l1.detector", {"selected": name, "effective": name, "reason": ""})
        self.name = self.det.name
        self.thr = float(ctx.c("models.l1.score_threshold", 0.5))
        self.margin = float(ctx.c("models.l1.uncertain_margin", 0.1))
        self.hard_tags = dict(ctx.c("models.l1.hard_tags", {}) or {})

    def screen(self, ctx, ev):
        img = next((m.uri for m in ev.media if m.kind == "image"), None)
        if img is None:
            return L1Result(model=self.name, defect_type=None, confidence=0.0, available=False)
        t0 = time.perf_counter()
        try:
            dets = sorted(self.det.detect(ctx, img, {"_sim": _sim(ev)}), key=lambda d: -d.score)
            ok = True
        except Exception as e:
            dets, ok = [], False
            ctx.audit.log("B2", "detector_error", ev.ingest_id, model=self.name, err=str(e)[:200])
        ms = (time.perf_counter() - t0) * 1000
        ctx.model_stats.record("detector", self.det.name, ms, ok)
        q = ev.source_meta.get("scene_quality")
        quality = SceneQuality.from_dict(q) if q else None
        if not ok:
            return L1Result(model=self.name, defect_type=None, confidence=0.0, available=False,
                            scene_quality=quality, latency_ms=round(ms, 2))
        top = dets[0] if dets else None
        if top and top.score >= self.thr:
            pred, conf, bbox = top.label, top.score, top.bbox
        else:
            pred, conf, bbox = None, round(1 - (top.score if top else 0.05), 3), None
        rival = next((d for d in dets[1:] if top and d.label != top.label), None)
        uncertain = bool(top and ((rival and top.score - rival.score < self.margin) or top.support < 1.0))
        tags = ev.source_meta.get("scene_tags") or {}
        hard = (bool(_sim(ev).get("hard_slice")) or bool(quality and HARD_FLAGS & set(quality.flags))
                or any(tags.get(k) == v for k, v in self.hard_tags.items()))
        return L1Result(model=self.name, defect_type=pred, confidence=round(conf, 3), bbox=bbox,
                        category_uncertain=uncertain, hard_slice=hard, detections=dets[:8],
                        scene_quality=quality, latency_ms=round(ms, 2))


@Registry.register("l1", "yolo", requires=("ultralytics",))
class YoloScreener(DetectorScreener):
    """兼容旧配置 impl.l1 = "yolo"：等价于 l1 = "detector" 且 [models.l1] detector 指向 ultralytics 检测器。"""

    def __init__(self, ctx: Optional[Context] = None):
        super().__init__(ctx, detector=ctx.c("models.l1.detector", "yolov8n"))


# ================================================================ D1 / D2 路由决策
@dataclass
class RouteDecision:
    outcome: str                     # auto_accept / escalate / human
    reasons: list[str] = field(default_factory=list)


def route_d1(ctx: Context, l1: L1Result, claim: Optional[UserClaim], ev: IngestEvent) -> RouteDecision:
    """L1 → L2 升级规则（⚠ 阈值待定，见 configs [routing.d1]）。"""
    r = []
    if not l1.available:
        r.append("l1_unavailable")
    if l1.confidence < ctx.c("routing.d1.min_confidence", 0.85):
        r.append("low_confidence")
    if l1.category_uncertain:
        r.append("category_uncertain")
    if claim and claim.defect_type and claim.defect_type != l1.defect_type:
        r.append("claim_conflict")
    if any(a.urgency >= ctx.c("routing.d1.audio_urgency", 0.5) for a in ev.audio_events) and l1.defect_type is None:
        r.append("audio_anomaly_no_visual")
    if l1.hard_slice:
        r.append("hard_slice")
    return RouteDecision("escalate" if r else "auto_accept", r)


def route_d2(ctx: Context, l1: L1Result, l2: L2Result) -> RouteDecision:
    """L2 → L3 升级规则（⚠ 阈值待定，见 configs [routing.d2]）。"""
    r = []
    if l1.available and l1.defect_type != l2.defect_type:
        r.append("l1_l2_disagree")
    if l2.confidence < ctx.c("routing.d2.min_confidence", 0.8):
        r.append("low_confidence")
    if l2.self_uncertain:
        r.append("self_uncertain")
    if SEV_ORDER.index(l2.severity) >= SEV_ORDER.index(Severity(ctx.c("routing.d2.human_severity", "high"))):
        r.append("high_severity")
    if l2.long_tail:
        r.append("long_tail")
    if ctx.rng.random() < ctx.c("routing.d2.spot_check_rate", 0.05):
        r.append("spot_check")
    return RouteDecision("human" if r else "auto_accept", r)


# ================================================================ B4 L2 大模型分析
class L2Analyzer(ABC):
    name = "l2"

    @abstractmethod
    def analyze(self, ctx: Context, ev: IngestEvent, l1: L1Result, claim: Optional[UserClaim]) -> L2Result: ...


@Registry.register("l2", "demo")
class MockVLM(L2Analyzer):
    name = "mock-qwen2.5-vl-3b"

    def analyze(self, ctx, ev, l1, claim):
        s = _sim(ev)
        if "defect_type" not in s:                         # 真实数据（如控制台上传）：mock VLM 无从判断，交人工
            return L2Result(model=self.name, is_real=l1.defect_type is not None, defect_type=l1.defect_type,
                            severity=Severity.MEDIUM, confidence=0.5, self_uncertain=True,
                            evidence=[m.uri for m in ev.media], reasoning="mock VLM 无模拟真值，无法分析真实图像")
        diff = float(s.get("difficulty", 0.2))
        truth = s.get("defect_type")
        wrong = ctx.rng.random() < diff * 0.25
        pred = (None if truth else "crack") if wrong else truth
        conf = max(0.1, min(0.99, 0.98 - diff * 0.22 + ctx.rng.uniform(-0.04, 0.04)))
        sev = Severity(s.get("severity", "medium")) if pred else Severity.LOW
        return L2Result(model=self.name, is_real=pred is not None, defect_type=pred, severity=sev,
                        confidence=round(conf, 3), self_uncertain=diff > 0.7,
                        evidence=[m.uri for m in ev.media] + [f"audio:{a.label}" for a in ev.audio_events],
                        reasoning=f"mock: L1={l1.defect_type}, claim={claim.defect_type if claim else None}",
                        long_tail=bool(s.get("long_tail")))


@Registry.register("l2", "qwen_vl")
class QwenVLAnalyzer(L2Analyzer):
    """真实 Demo：Qwen2.5-VL-3B（transformers）。只给出提示词与解析，生成调用按部署方式补齐。"""
    name = "qwen2.5-vl-3b"
    PROMPT = (
        "你是缺陷质检专家。根据图像、用户描述、小模型结果和音频事件，输出 JSON："
        '{"is_real":bool,"defect_type":str|null,"severity":"low|medium|high|critical",'
        '"confidence":0-1,"self_uncertain":bool,"long_tail":bool,"evidence":[str],"reasoning":str}\n'
        "用户描述：{desc}\n小模型：{l1}\n音频：{audio}"
    )

    def __init__(self, model_path: str = "Qwen/Qwen2.5-VL-3B-Instruct"):
        self.model_path = model_path   # TODO: 加载 processor / model（transformers 或 vLLM 服务）

    def _generate(self, images: list[str], prompt: str) -> str:
        raise NotImplementedError("TODO: 接入 Qwen2.5-VL 推理（本地 transformers 或 vLLM HTTP）")

    def analyze(self, ctx, ev, l1, claim):
        prompt = self.PROMPT.format(desc=ev.user_description or "", l1=l1.to_dict(),
                                    audio=[a.to_dict() for a in ev.audio_events])
        raw = self._generate([m.uri for m in ev.media if m.kind == "image"], prompt)
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return L2Result(model=self.name, is_real=d["is_real"], defect_type=d.get("defect_type"),
                        severity=Severity(d.get("severity", "medium")), confidence=float(d["confidence"]),
                        self_uncertain=bool(d.get("self_uncertain")), evidence=d.get("evidence", []),
                        reasoning=d.get("reasoning", ""), long_tail=bool(d.get("long_tail")))


# ================================================================ B6 L3 人工仲裁
class HumanReviewer(ABC):
    @abstractmethod
    def review(self, ctx: Context, ev: IngestEvent, ctx_items: dict[str, Any]) -> Optional[HumanDecision]:
        """返回 None 表示已入审核队列、待人工处理（事件挂起）。"""


@Registry.register("reviewer", "oracle")
class OracleReviewer(HumanReviewer):
    """Demo 自动化：用 _sim 真值模拟审核员，便于一键跑通闭环。没有模拟真值的真实数据（控制台上传等）
    转入本地审核台（同 queue 模式），由人在 #/review 裁决，oracle 不代替人判断。"""

    def review(self, ctx, ev, ctx_items):
        s = _sim(ev)
        if "defect_type" not in s:
            ctx.count("b6.real_to_queue")
            return QueueReviewer().review(ctx, ev, ctx_items)
        truth = s.get("defect_type")
        if s.get("needs_more_data"):
            dec = FinalDecision.NEEDS_MORE_DATA
        else:
            dec = FinalDecision.CONFIRMED if truth else FinalDecision.REJECTED
        minutes = round(1 + 4 * float(s.get("difficulty", 0.2)), 1)
        if not ctx.ledger.charge("human_minutes", minutes):      # 人工预算用完 → 挂起排队
            return None
        return HumanDecision(reviewer="oracle", decision=dec, defect_type=truth,
                             severity=Severity(s.get("severity", "medium")) if truth else None,
                             reason="demo oracle", minutes_spent=minutes,
                             corrected_bbox=BBox(**s["bbox"]) if s.get("bbox") else None)


@Registry.register("reviewer", "queue")
class QueueReviewer(HumanReviewer):
    """本地审核台：写入 review_queue/<ingest_id>.json，人工填写 decision 字段后再次运行即可回写。"""

    def review(self, ctx, ev, ctx_items):
        name = f"{ev.ingest_id}.json"
        p = ctx.lake.path("review_queue", name)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("decision"):
                return HumanDecision.from_dict(d["decision"])
            return None
        sev_rank = max((SEV_ORDER.index(Severity(x)) for x in [ctx_items.get("severity", "low")]), default=0)
        ctx.lake.put_json("review_queue", name, {
            "priority": sev_rank * 10 + len(ctx_items.get("reasons", [])),   # 严重度 + 分歧度排序
            "ingest": ev.to_dict(), **ctx_items,
            "decision": None,
            "decision_template": {"reviewer": "", "decision": "confirmed|rejected|needs_more_data",
                                  "defect_type": None, "severity": None, "reason": "", "minutes_spent": 0},
        })
        return None


# ================================================================ B7 / N1 / B8
class DiscoveryLayer:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.claims: ClaimParser = ctx.make("claim_parser")
        self.l1: L1Screener = ctx.make("l1", ctx=ctx)
        self.l2: L2Analyzer = ctx.make("l2")
        self.reviewer: HumanReviewer = ctx.make("reviewer", default="oracle")
        self.auto_accepted: list[tuple[IngestEvent, DefectEvent]] = []
        self.eval_rows: list[dict[str, Any]] = []                 # 检测器评测：每个事件的预测与参考

    def process(self, ev: IngestEvent) -> Optional[DefectEvent]:
        ctx, sm = self.ctx, Tracker(DEFECT_SM)
        trace: list[RouteStep] = []
        level = "L1"
        claim = None
        if ev.source_type == SourceType.USER_UPLOAD and ev.user_description:     # A4 → B1
            claim = self.claims.parse(ctx, ev)
            level = "L0"
        l1 = self.l1.screen(ctx, ev)                                              # B2
        ctx.count("b2.screened")
        if l1.available:
            sm.to("l1_screened")
        d1 = route_d1(ctx, l1, claim, ev)                                         # D1
        trace.append(RouteStep("D1", d1.outcome, d1.reasons))
        l2: Optional[L2Result] = None
        human: Optional[HumanDecision] = None

        if d1.outcome == "auto_accept":
            ctx.count("d1.auto_accept")
            level = "L1"
            final = FinalDecision.CONFIRMED if l1.defect_type else FinalDecision.REJECTED
            dtype, conf, bbox, sev = l1.defect_type, l1.confidence, l1.bbox, None
        else:
            ctx.count("d1.escalate")
            if not ctx.ledger.charge("l2_calls", 1):                                # 成本超限 → 挂起排队
                sm.to("suspended")
                ctx.lake.put_json("review_queue", f"l2wait-{ev.ingest_id}", {"ingest_id": ev.ingest_id, "why": "l2 budget"})
                ctx.count("b4.budget_suspended")
                return None
            t0 = time.perf_counter()
            l2 = self.l2.analyze(ctx, ev, l1, claim)                              # B4
            ctx.model_stats.record("vlm", self.l2.name, (time.perf_counter() - t0) * 1000)
            sm.to("l2_analyzed")
            level = "L2"
            d2 = route_d2(ctx, l1, l2)                                            # D2
            trace.append(RouteStep("D2", d2.outcome, d2.reasons))
            dtype, conf, bbox, sev = l2.defect_type, l2.confidence, l1.bbox, l2.severity
            if d2.outcome == "auto_accept":
                ctx.count("d2.auto_accept")
                final = FinalDecision.CONFIRMED if l2.is_real else FinalDecision.REJECTED
            else:
                ctx.count("d2.human")
                human = self.reviewer.review(ctx, ev, {                          # B6
                    "l1": l1.to_dict(), "l2": l2.to_dict(), "claim": claim.to_dict() if claim else None,
                    "reasons": d2.reasons, "severity": l2.severity.value})
                if human is None:
                    sm.to("suspended")
                    ctx.count("b6.pending")
                    return None
                sm.to("human_reviewed")
                level = "L3"
                final = human.decision
                dtype = human.defect_type
                bbox = human.corrected_bbox or bbox
                sev = human.severity
                conf = 1.0
                ctx.audit.log(human.reviewer, "arbitrate", ev.ingest_id, decision=final.value, reasons=d2.reasons)

        return self._emit(ev, sm, level, dtype, bbox, sev, conf, trace, final, human, l1, l2)

    def _emit(self, ev: IngestEvent, sm: Tracker, level: str, dtype: Optional[str], bbox: Optional[BBox],
              sev: Optional[Severity], conf: float, trace: list[RouteStep], final: FinalDecision,
              human: Optional[HumanDecision], l1: Optional[L1Result], l2: Optional[L2Result]) -> DefectEvent:
        """B7 生成 defect_event → 按结论落到 defects(P2) / negatives(N1) / anchors(P3)。"""
        ctx = self.ctx
        sm.to(final.value)
        de = DefectEvent(                                                         # B7
            defect_id=new_id("def"), ingest_id=ev.ingest_id, level_reached=level, defect_type=dtype,
            location=bbox, severity=sev, confidence=conf,
            evidence=[m.uri for m in ev.media] + ([l2.reasoning] if l2 else []),
            routing_trace=trace, final_decision=final, human_decision=human, state=sm.state,
            l1_result=l1, l2_result=l2)
        de.validate()
        if l1 is not None:
            self._eval_row(ev, l1, de)
        ctx.lineage.node(de.defect_id, "defect", decision=final.value, level=level, defect_type=dtype)
        ctx.lineage.link(ev.ingest_id, de.defect_id)
        zone = {"confirmed": "defects", "rejected": "negatives", "needs_more_data": "anchors"}[final.value]
        ctx.lake.put_json(zone, de.defect_id, de)                                 # → P2 / N1 / P3
        ctx.count(f"b7.{final.value}")
        if not human:
            self.auto_accepted.append((ev, de))
        return de

    # ------------------------------------------------------------ B6 → B7 审核台裁决回写
    def apply_review_decisions(self) -> list[DefectEvent]:
        """审核台（#/review 或手工编辑 JSON）已裁决、尚未回写的条目 → defect_event（spec 连线 14）。
        挂起的事件不会被重新接入（A2 会按内容哈希去重），因此在每轮开始时由这里补写。"""
        ctx = self.ctx
        out = []
        for p in ctx.lake.list("review_queue", "ing-*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not d.get("decision") or d.get("applied_defect_id") or not d.get("ingest"):
                continue
            try:
                ev = IngestEvent.from_dict(d["ingest"])
                human = HumanDecision.from_dict(d["decision"])
                l1 = L1Result.from_dict(d["l1"]) if d.get("l1") else None
                l2 = L2Result.from_dict(d["l2"]) if d.get("l2") else None
            except (SchemaError, KeyError, TypeError, ValueError) as e:
                ctx.audit.log("B6", "review_apply_failed", p.stem, err=str(e)[:200])
                continue
            sm = Tracker(DEFECT_SM)
            if l1 is not None and l1.available:
                sm.to("l1_screened")
            sm.to("l2_analyzed").to("human_reviewed")
            trace = [RouteStep("D1", "escalate", []), RouteStep("D2", "human", list(d.get("reasons", [])))]
            de = self._emit(ev, sm, "L3", human.defect_type, human.corrected_bbox or (l1.bbox if l1 else None),
                            human.severity, 1.0, trace, human.decision, human, l1, l2)
            d["applied_defect_id"] = de.defect_id
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            ctx.audit.log(human.reviewer, "review_applied", ev.ingest_id, decision=human.decision.value,
                          defect_id=de.defect_id)
            ctx.count("b6.applied")
            out.append(de)
        return out

    # ------------------------------------------------------------ 检测器评测
    def _eval_row(self, ev: IngestEvent, l1: L1Result, de: DefectEvent) -> None:
        """参考标签：有模拟真值用真值；否则只取 L2/L3 独立裁决过的事件（D1 自动接受的结果就是 L1 自己，不能当参考）。"""
        s = _sim(ev)
        if "defect_type" in s:
            ref, src, ref_box = s.get("defect_type"), "sim_truth", (BBox(**s["bbox"]) if s.get("bbox") else None)
        elif de.level_reached in ("L2", "L3"):
            ref = de.defect_type if de.final_decision == FinalDecision.CONFIRMED else None
            src, ref_box = "downstream", de.location
        else:
            ref, src, ref_box = None, "skipped", None
        self.eval_rows.append({"ingest_id": ev.ingest_id, "defect_id": de.defect_id, "pred": l1.defect_type,
                               "ref": ref, "ref_source": src, "available": l1.available,
                               "confidence": l1.confidence, "latency_ms": l1.latency_ms,
                               "iou": round(box_iou(l1.bbox, ref_box), 4) if l1.bbox and ref_box else None,
                               "source_type": ev.source_type.value})

    def evaluate_detector(self) -> dict[str, Any]:
        rows = [r for r in self.eval_rows if r["ref_source"] != "skipped" and r["available"]]
        labels = sorted({str(r["ref"]) for r in rows} | {str(r["pred"]) for r in rows})
        confusion = {a: {b: 0 for b in labels} for a in labels}             # 参考 → 预测
        for r in rows:
            confusion[str(r["ref"])][str(r["pred"])] += 1
        per_class = {}
        for c in labels:
            if c == "None":
                continue
            tp = confusion[c][c]
            fp = sum(confusion[a][c] for a in labels if a != c)
            fn = sum(confusion[c][b] for b in labels if b != c)
            p = tp / (tp + fp) if tp + fp else 0.0
            r_ = tp / (tp + fn) if tp + fn else 0.0
            per_class[c] = {"precision": round(p, 3), "recall": round(r_, 3),
                            "f1": round(2 * p * r_ / (p + r_), 3) if p + r_ else 0.0, "support": tp + fn}
        ious = [r["iou"] for r in rows if r["iou"] is not None and r["pred"] == r["ref"] and r["pred"]]
        lat = sorted(r["latency_ms"] for r in self.eval_rows if r["latency_ms"])
        by_src: dict[str, list[int]] = {}
        for r in rows:
            by_src.setdefault(r["source_type"], [0, 0])
            by_src[r["source_type"]][0] += int(str(r["pred"]) == str(r["ref"]))
            by_src[r["source_type"]][1] += 1
        out = {"detector": self.l1.name, "events": len(self.eval_rows), "evaluated": len(rows),
               "reference_mix": dict(Counter(r["ref_source"] for r in self.eval_rows)),
               "unavailable": sum(not r["available"] for r in self.eval_rows),
               "accuracy": round(sum(str(r["pred"]) == str(r["ref"]) for r in rows) / len(rows), 4) if rows else None,
               "per_class": per_class, "confusion": confusion, "labels": labels,
               "mean_iou": round(sum(ious) / len(ious), 4) if ious else None,
               "latency_ms_p50": round(lat[len(lat) // 2], 2) if lat else None,
               "latency_ms_p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2) if lat else None,
               "accuracy_by_source": {k: round(a / n, 3) for k, (a, n) in by_src.items()}}
        self.ctx.lake.put_json("reports", "b2_detector_eval", out)
        return out

    # ------------------------------------------------------------ B8 抽检与校准
    def calibrate(self) -> dict[str, Any]:
        """对自动接受样本按比例抽检（Demo 用 oracle 真值），发现共谋错误回修并统计各级准确率。"""
        ctx = self.ctx
        rate = ctx.c("calibration.sample_rate", 0.2)
        sample = [x for x in self.auto_accepted if ctx.rng.random() < rate] or self.auto_accepted[:1]
        wrong = []
        for ev, de in sample:
            truth = _sim(ev).get("defect_type")
            if truth != de.defect_type:
                wrong.append({"defect_id": de.defect_id, "pred": de.defect_type, "truth": truth,
                              "level": de.level_reached})
                ctx.lake.put_json("rejected", f"collusion-{de.defect_id}", {"kind": "collusion_error", **wrong[-1]})
        rep = {"sampled": len(sample), "errors": len(wrong),
               "auto_accept_precision": round(1 - len(wrong) / max(1, len(sample)), 3), "error_cases": wrong}
        ctx.lake.put_json("reports", "b8_calibration", rep)
        ctx.audit.log("B8", "calibration", "auto_accept", **{k: v for k, v in rep.items() if k != "error_cases"})
        return rep
