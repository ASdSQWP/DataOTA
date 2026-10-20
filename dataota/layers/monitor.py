"""L5 设备与监控：M11 线上监控（遥测/漂移/精度停滞/回滚率 → 回流）/ M12 版本治理与数据生命周期。"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

from ..core.context import Context
from ..core.infra import Registry
from ..core.schemas import Alert, Telemetry
from ..models import imaging
from ..models.quality import DRIFT_METRICS, psi  # noqa: F401  psi 保留旧导入路径


def _norm(h: list[float]) -> list[float]:
    s = sum(h) or 1.0
    return [x / s for x in h]


# ================================================================ 遥测采集
class TelemetrySource(ABC):
    @abstractmethod
    def collect(self, ctx: Context, fleet: list[dict[str, Any]], round_idx: int) -> list[Telemetry]: ...


@Registry.register("telemetry", "demo")
class SimulatedTelemetry(TelemetrySource):
    """Demo：按轮次模拟遥测；monitor.drift_from_round 起注入置信度左移（镜头老化/季节光照）。"""

    def collect(self, ctx, fleet, round_idx):
        drift = round_idx >= ctx.c("monitor.drift_from_round", 99)
        out = []
        for d in fleet:
            if not d.get("current_version"):
                continue
            shift = 2 if drift else 0
            hist = [0.02] * 10
            for k in range(6 - shift, 10 - shift):
                hist[k] += 0.2 + ctx.rng.uniform(-0.03, 0.03)
            lat = 18 + (8 if d["hardware"].endswith("b") else 0) + ctx.rng.uniform(-2, 2)
            out.append(Telemetry(device_id=d["device_id"], model_version=d["current_version"],
                                 latency_ms_p50=round(lat, 1), latency_ms_p95=round(lat * 1.6, 1),
                                 mem_mb=round(120 + ctx.rng.uniform(-5, 5), 1), confidence_hist=_norm(hist),
                                 hard_case_rate=round(0.03 + (0.05 if drift else 0) + ctx.rng.uniform(0, 0.01), 4),
                                 shadow_disagree_rate=round(ctx.rng.uniform(0.0, 0.05), 3)))
        return out


# ================================================================ M11 线上监控
class Monitor:
    def __init__(self, ctx: Context):
        self.src: TelemetrySource = ctx.make("telemetry")

    def run(self, ctx: Context, fleet: list[dict[str, Any]], round_idx: int) -> dict[str, Any]:
        tel = self.src.collect(ctx, fleet, round_idx)
        for t in tel:
            ctx.lake.put_json("telemetry", f"r{round_idx:02d}-{t.device_id}", t)
        alerts: list[Alert] = []
        if tel:
            cur = _norm([sum(t.confidence_hist[i] for t in tel) for i in range(10)])
            bp = ctx.lake.path("reports", "m11_baseline_hist.json")
            if not bp.exists():
                bp.write_text(json.dumps(cur), encoding="utf-8")
            base = json.loads(bp.read_text(encoding="utf-8"))
            scores = {k: round(f(base, cur), 4) for k, f in DRIFT_METRICS.items()}      # 漂移检测：PSI / JS / KS
            drift = scores["psi"]
            method = ctx.c("monitor.drift_method", "psi")
            limit = ctx.c(f"monitor.drift_thresholds.{method}", ctx.c("monitor.psi_alert", 0.2))
            if scores.get(method, drift) > limit:
                alerts.append(Alert("drift", f"置信度分布漂移 {method.upper()}={scores[method]:.3f}", "reflow_hard_cases",
                                    {"psi": round(drift, 3), "method": method, "value": scores[method]}))
                alerts.append(Alert("drift", "漂移确认前用金标集复核，避免误报", "rescreen", {}))
            p95 = sorted(t.latency_ms_p95 for t in tel)[int(len(tel) * 0.95) - 1]
            if p95 > ctx.c("monitor.latency_p95_ms", 60):
                alerts.append(Alert("latency", f"p95 延迟 {p95}ms 超限", "investigate", {"p95": p95}))
        else:
            drift = 0.0
            scores = {k: 0.0 for k in DRIFT_METRICS}
        # 精度停滞：连续 N 个候选模型无显著提升 → 补锚点（沿用“连续 3 轮无提升”规则）
        models = [json.loads(p.read_text(encoding="utf-8")) for p in ctx.lake.list("models", "mv-*.json")]
        n = ctx.c("monitor.plateau_rounds", 3)
        if len(models) > n:
            means = [sum(m["metrics"].values()) / max(1, len(m["metrics"])) for m in models]
            best_before = max(means[:-n])
            if max(means[-n:]) - best_before < ctx.c("monitor.plateau_eps", 0.005):
                weak = min(models[-1]["metrics"], key=models[-1]["metrics"].get)
                alerts.append(Alert("plateau", f"连续 {n} 轮无提升，最弱切片 {weak}", "add_anchors",
                                    {"slice": weak}))
        # 回滚率
        rels = [json.loads(p.read_text(encoding="utf-8")) for p in ctx.lake.list("releases", "rel-*.json")]
        if rels and rels[-1]["status"].startswith("rolled_back"):
            alerts.append(Alert("rollback_rate", f"{rels[-1]['plan']['release_id']} 发生回滚", "investigate", {}))
        rep = {"round": round_idx, "devices_reporting": len(tel), "psi": round(drift, 4), "drift": scores,
               "drift_method": ctx.c("monitor.drift_method", "psi"),
               "hard_case_rate": round(sum(t.hard_case_rate for t in tel) / max(1, len(tel)), 4),
               "alerts": [a.to_dict() for a in alerts]}
        ctx.lake.put_json("reports", f"m11-r{round_idx:02d}", rep)
        for a in alerts:
            ctx.audit.log("M11", "alert", a.kind, message=a.message, next_action=a.action)
        return rep

    @staticmethod
    def device_inferences(ctx: Context, fleet: list[dict[str, Any]], n_per_device: int, drift: bool) -> list[dict[str, Any]]:
        """Demo：模拟设备端推理记录（供下一轮 A0 选难例）。附 _sim 真值仅供 mock 模型使用。
        装了 numpy + Pillow 时为难例渲染真实图像（设备离线回放），否则 sample_ref 只是引用。"""
        out = []
        size = (int(ctx.c("render.width", 640)), int(ctx.c("render.height", 360)))
        render = imaging.AVAILABLE and ctx.c("render.enabled", True)
        for d in fleet:
            if not d.get("current_version"):
                continue
            for k in range(n_per_device):
                hard = ctx.rng.random() < (0.35 if drift else 0.15)
                truth = ctx.rng.choice(["crack", "scratch", "dent", None]) if hard else ctx.rng.choice(["crack", None])
                pred = truth if not hard or ctx.rng.random() < 0.5 else None
                ref = f"{ctx.lake.path('raw')}/{d['device_id']}-{int(time.time()*1000)%10**8}-{k}.jpg"
                tags = {"light": "night" if hard and ctx.rng.random() < 0.5 else "day"}
                sim = {"defect_type": truth, "difficulty": 0.65 if hard else 0.15, "severity": "medium", "hard_slice": hard,
                       "bbox": {"x": round(ctx.rng.uniform(0.1, 0.6), 3), "y": round(ctx.rng.uniform(0.1, 0.6), 3),
                                "w": round(ctx.rng.uniform(0.1, 0.25), 3), "h": round(ctx.rng.uniform(0.1, 0.25), 3)}
                       if truth else None}
                out.append({"device_id": d["device_id"], "model_version": d["current_version"], "sample_ref": ref,
                            "confidence": round(ctx.rng.uniform(0.2, 0.6) if hard else ctx.rng.uniform(0.75, 0.99), 3),
                            "pred": pred, "shadow_pred": pred if ctx.rng.random() < 0.9 else truth,
                            "scene_quality": ctx.rng.uniform(0.2, 1.0) if hard else 0.9,
                            "scene_tags": tags, "_sim": sim, "_render": render, "_size": size})
        return out

    @staticmethod
    def materialize(infs: list[dict[str, Any]], selected_refs: set[str]) -> None:
        """只为被 A0 选中上传的难例渲染图像（其余推理记录留在设备端，不落盘）。"""
        for inf in infs:
            if inf.pop("_render", False) and inf["sample_ref"] in selected_refs:
                imaging.render_to(inf["sample_ref"], inf["sample_ref"], inf.pop("_size"), inf["_sim"], inf["scene_tags"])
            inf.pop("_size", None)


# ================================================================ M12 版本治理与数据生命周期
class Governance:
    def run(self, ctx: Context, fleet: list[dict[str, Any]]) -> dict[str, Any]:
        dist = Counter(d.get("current_version") or "none" for d in fleet)
        latest = max((v for v in dist if v != "none"), default=None)
        on_latest = dist.get(latest, 0) if latest else 0
        frag = 1 - on_latest / max(1, len(fleet))
        min_ver = ctx.c("governance.min_supported_version", None)
        force = [d["device_id"] for d in fleet
                 if d.get("current_version") is None or (min_ver and d["current_version"] < min_ver)]
        # 数据生命周期：按保留期限删除（Demo 只处理 raw / quarantine）
        deleted = 0
        now = time.time()
        for zone in ("raw", "quarantine"):
            days = ctx.privacy.retention_days.get(zone, ctx.privacy.retention_days.get("raw", 30))
            for p in ctx.lake.path(zone).glob("*"):
                if p.is_file() and now - p.stat().st_mtime > days * 86400:
                    p.unlink()
                    deleted += 1
                    ctx.audit.log("M12", "delete_expired", str(p), zone=zone, retention_days=days)
        rep = {"version_distribution": dict(dist), "latest": latest, "fragmentation": round(frag, 3),
               "force_upgrade": force if frag > ctx.c("governance.max_fragmentation", 0.3) else [],
               "expired_deleted": deleted}
        ctx.lake.put_json("reports", "m12_governance", rep)
        return rep
