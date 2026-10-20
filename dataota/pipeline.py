"""端到端编排：一轮闭环 = 接入 → 发现分级 → 云端飞轮 → 训练发布 → 监控治理 → 回流下一轮。

节点之间通过 DataLake 的 zone 交接（defects / anchors / annotations / datasets ...），
任何一段都可以单独重跑或替换为异步队列。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from . import models as _models  # noqa: F401  注册模型库实现（检测器 / 世界模型 / 门禁 / 场景质量）
from .core.context import Context
from .core.infra import CostLedger, ModelStats
from .core.schemas import Annotation, DefectEvent, FinalDecision, GeneratedClip, GateResult
from .layers.discovery import DiscoveryLayer
from .layers.flywheel import Annotator, DatasetExport, Expansion, Gate, Intake, analyze_rejections
from .layers.ingest import IngestLayer, hard_case_to_raw
from .layers.monitor import Governance, Monitor
from .layers.release import OTA, EvalGate, Repository, Training


@dataclass
class RoundReport:
    round: int
    counters: dict[str, int] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    n2: dict[str, Any] = field(default_factory=dict)
    dataset: Optional[str] = None
    model: Optional[str] = None
    eval: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)
    monitor: dict[str, Any] = field(default_factory=dict)
    governance: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)
    models: list[dict[str, Any]] = field(default_factory=list)        # 本轮各模型调用统计
    detector_eval: dict[str, Any] = field(default_factory=dict)       # B2 检测器评测
    world: dict[str, Any] = field(default_factory=dict)               # P3 世界模型生成 / 门禁汇总
    fallbacks: dict[str, Any] = field(default_factory=dict)           # 接口 → 实际生效实现（含回退原因）


class Pipeline:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.ingest = IngestLayer(ctx)
        self.discovery = DiscoveryLayer(ctx)
        self.intake, self.expansion, self.gate = Intake(), Expansion(ctx), Gate(ctx)
        self.annotator, self.exporter = Annotator(ctx), DatasetExport()
        self.training, self.eval_gate, self.repo, self.ota = Training(ctx), EvalGate(), Repository(ctx), OTA(ctx)
        self.monitor, self.governance = Monitor(ctx), Governance()
        self.device_signal = ctx.make("device_signal")
        self.dropped_views: set[str] = set()
        self.demoted: set[tuple[str, str]] = set()                  # N2 → P3：(视角, 模型) 降级
        self.extra_views: list[str] = []

    # ------------------------------------------------------------------ 一轮
    def run_round(self, r: int, raws: list[dict[str, Any]]) -> tuple[RoundReport, list[dict[str, Any]]]:
        ctx = self.ctx
        ctx.counters = {}
        ctx.ledger = CostLedger(budget=dict(ctx.c("cost.budget", {})))       # ⚠ 单轮闭环成本上限
        ctx.model_stats = ModelStats()
        ctx.round = r
        rep = RoundReport(round=r)

        # L1 接入 ---------------------------------------------------------
        events = self.ingest.run(raws)
        # L2 发现分级 -----------------------------------------------------
        self.discovery.auto_accepted = []
        self.discovery.eval_rows = []
        self.discovery.apply_review_decisions()                               # B6 → B7：审核台裁决回写
        for ev in events:
            self.discovery.process(ev)
        rep.calibration = self.discovery.calibrate() if self.discovery.auto_accepted else {}
        rep.detector_eval = self.discovery.evaluate_detector() if self.discovery.eval_rows else {}
        # L3 云端飞轮 -----------------------------------------------------
        anchors = self.intake.run(ctx)
        if self.extra_views:                                                   # M11 精度停滞 → 补锚点
            for a, _ in anchors:
                a.missing_views = list(dict.fromkeys(a.missing_views + self.extra_views))
        all_views = {v for a, _ in anchors for v in a.missing_views}
        clips = self.expansion.run(ctx, anchors, all_views - self.dropped_views, self.demoted)
        passed, rejected = self.gate.run(ctx, clips)
        rep.n2 = analyze_rejections(ctx, rejected, len(clips), passed)
        self.dropped_views = set(rep.n2.get("feedback_drop_views", []))       # N2 → P3 反馈：收窄视角
        self.demoted |= {(x["view"], x["model"]) for x in rep.n2.get("feedback_switch_models", [])}  # 换模型
        rep.world = self._world_summary(clips, passed, rejected)
        real = [de for _, de in anchors if de.final_decision == FinalDecision.CONFIRMED]
        self.annotator.run(ctx, passed, real)
        all_anns = [Annotation.from_dict(json.loads(p.read_text(encoding="utf-8")))
                    for p in ctx.lake.list("annotations", "ann-*.json")]
        prev_ds = ctx.lake.list("datasets", "ds-*.json")
        if not all_anns:
            self._finish(rep)
            return rep, []
        ds = self.exporter.run(ctx, all_anns, parent=prev_ds[-1].stem if prev_ds else None)   # 累积式数据集
        rep.dataset = ds.dataset_version
        # L4 训练发布 -----------------------------------------------------
        mv = self.training.run(ctx, ds)
        rep.model = mv.model_version
        ev_rep, plan = self.eval_gate.run(ctx, mv, ds)
        rep.eval = {"passed": ev_rep.passed, "regressions": ev_rep.regressions,
                    "slices": {k: round(v["candidate"] - v["baseline"], 4) for k, v in ev_rep.slices.items()}}
        if plan:
            manifests = self.repo.run(ctx, mv, plan)
            rep.release = self.ota.run(ctx, plan, manifests) if manifests else {"status": "no_build"}
        else:
            rep.release = {"status": "gate_rejected"}
        # L5 监控与治理 ---------------------------------------------------
        fleet = self.ota.fleet.devices
        rep.monitor = self.monitor.run(ctx, fleet, r)
        rep.governance = self.governance.run(ctx, fleet)
        actions = {a["action"] for a in rep.monitor["alerts"]}
        self.extra_views = ["night", "occluded"] if "add_anchors" in actions else []
        # 回流：设备推理 → A0 难例 → 下一轮 A1
        drift = "reflow_hard_cases" in actions or r + 1 >= ctx.c("monitor.drift_from_round", 99)
        infs = Monitor.device_inferences(ctx, fleet, ctx.c("device.inferences_per_round", 10), drift)
        hard = self.device_signal.select(ctx, infs)
        Monitor.materialize(infs, {h.sample_ref for h in hard})             # 只有选中上传的难例才落盘图像
        sims = {i["sample_ref"]: i.pop("_sim") for i in infs}
        next_raws = [hard_case_to_raw(h, sims.get(h.sample_ref)) for h in hard]
        self._finish(rep)
        return rep, next_raws

    def _finish(self, rep: RoundReport) -> None:
        ctx = self.ctx
        rep.counters = dict(ctx.counters)
        rep.cost = {k: round(v, 2) for k, v in ctx.ledger.spent.items()}
        rep.models = ctx.model_stats.snapshot()
        rep.fallbacks = dict(ctx.fallbacks)
        ctx.lake.put_json("reports", f"round-{rep.round:02d}", rep.__dict__)

    def _world_summary(self, clips: list[tuple[GeneratedClip, DefectEvent]],
                       passed: list[tuple[GeneratedClip, DefectEvent, GateResult]],
                       rejected: list[tuple[GeneratedClip, GateResult]]) -> dict[str, Any]:
        ok = {c.clip_id for c, _, _ in passed}
        by_model: dict[str, dict[str, Any]] = {}
        by_view: dict[str, dict[str, int]] = {}
        for c, _ in clips:
            m = c.meta.get("model", c.generator)
            row = by_model.setdefault(m, {"clips": 0, "passed": 0, "latency_ms": 0.0, "gpu_hours": 0.0})
            row["clips"] += 1
            row["passed"] += c.clip_id in ok
            row["latency_ms"] += float(c.meta.get("latency_ms", 0))
            row["gpu_hours"] += float(c.meta.get("gpu_hours", 0))
            v = by_view.setdefault(c.trajectory, {"clips": 0, "passed": 0})
            v["clips"] += 1
            v["passed"] += c.clip_id in ok
        for row in by_model.values():
            row["pass_rate"] = round(row["passed"] / max(1, row["clips"]), 3)
            row["latency_ms_avg"] = round(row.pop("latency_ms") / max(1, row["clips"]), 1)
            row["gpu_hours"] = round(row["gpu_hours"], 3)
        sources = Counter(g.source for _, g in rejected) + Counter(g.source for _, _, g in passed)
        return {"clips": len(clips), "passed": len(passed), "rejected": len(rejected),
                "by_model": by_model, "by_view": by_view, "metric_source": dict(sources),
                "fallbacks": self.ctx.counters.get("p3.fallback", 0),
                "prescreen_failed": self.ctx.counters.get("p3.prescreen_failed", 0),
                "demoted": sorted([{"view": v, "model": m} for v, m in self.demoted], key=lambda x: (x["view"], x["model"])),
                "dropped_views": sorted(self.dropped_views)}

    def run(self, rounds: int, seed_raws: list[dict[str, Any]]) -> list[RoundReport]:
        out, raws = [], seed_raws
        for r in range(1, rounds + 1):
            rep, nxt = self.run_round(r, raws)
            out.append(rep)
            raws = nxt                     # 第 2 轮起：设备难例回流（可再拼接新的外部数据）
        return out
