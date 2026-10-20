"""L4 训练发布：T7 增量训练 / T8 评测门禁 / T9 模型仓库与压缩适配 / T10 OTA 下发·影子·灰度·回滚。"""
from __future__ import annotations

import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..core.context import Context
from ..core.infra import Registry
from ..core.schemas import DatasetVersion, EvalReport, Manifest, ModelVersion, ReleasePlan, UpgradeReceipt
from ..core.states import OTA_SM, IllegalTransition, Tracker


def _models(ctx: Context) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in ctx.lake.list("models", "mv-*.json")]


def current_baseline(ctx: Context) -> Optional[ModelVersion]:
    """最近一次“已发布”的模型；没有则为 None（使用配置中的初始基线指标）。"""
    rels = [json.loads(p.read_text(encoding="utf-8")) for p in ctx.lake.list("releases", "rel-*.json")]
    rels = [r for r in rels if r.get("status") == "released"]
    if not rels:
        return None
    mv = rels[-1]["plan"]["model_version"]
    return ModelVersion.from_dict(ctx.lake.get_json("models", mv))


# ================================================================ T7 增量训练
class Trainer(ABC):
    @abstractmethod
    def train(self, ctx: Context, ds: DatasetVersion, base: Optional[ModelVersion]) -> ModelVersion: ...


@Registry.register("trainer", "demo")
class SimulatedTrainer(Trainer):
    """Demo：按数据量/类别覆盖模拟分切片指标（边际收益递减），不做真实训练。"""

    def train(self, ctx, ds, base):
        slices = list(ctx.c("eval.slices", ["day", "night", "far", "occluded"]))
        init = dict(ctx.c("eval.initial_baseline", {}))
        prev = base.metrics if base else {s: init.get(s, 0.6) for s in slices}
        n_syn, n_real = ds.stats.get("synthetic", 0), ds.stats.get("train", 0) - ds.stats.get("synthetic", 0)
        gain = 0.04 * (1 - min(1.0, (n_syn * 0.3 + n_real) / 400)) + 0.002   # 递减收益
        metrics = {}
        for s in slices:
            headroom = 0.98 - prev.get(s, 0.6)
            noise = ctx.rng.uniform(-0.015, 0.01)
            if s in ("night", "occluded") and n_syn == 0:
                noise -= 0.02                                         # 没有合成补样时难切片易退化
            metrics[s] = round(max(0.0, prev.get(s, 0.6) + headroom * gain * 4 + noise), 4)
        gpu = ctx.c("cost.gpu_hours_per_train", 2.0)
        ctx.ledger.charge("gpu_hours", gpu)
        mv = ModelVersion(model_version=f"mv-{len(_models(ctx)) + 1:03d}", base_model=base.model_version if base else "init",
                          dataset_version=ds.dataset_version,
                          train_config={"recipe": "incremental", "hard_case_weight": ctx.c("train.hard_case_weight", 2.0),
                                        "synthetic_ratio_cap": ctx.c("train.synthetic_ratio_cap", 0.5)},
                          metrics=metrics, cost={"gpu_hours": gpu})
        return mv


@Registry.register("trainer", "yolo", requires=("ultralytics",))
class YoloTrainer(Trainer):
    """真实小模型增量训练（Ultralytics）。数据来自 P6 导出的 yolo 目录。"""

    def train(self, ctx, ds, base):
        from ultralytics import YOLO
        root = ctx.lake.path("datasets") / ds.dataset_version / "train" / "yolo"
        weights = (base.train_config.get("weights") if base else None) or ctx.c("train.init_weights", "yolov8n.pt")
        model = YOLO(weights)
        # TODO: Ultralytics 需要 images/labels 目录结构与 data.yaml 中 train/val 路径，按实际存储补齐
        res = model.train(data=str(root / "data.yaml"), epochs=ctx.c("train.epochs", 20), imgsz=640, exist_ok=True)
        return ModelVersion(model_version=f"mv-{len(_models(ctx)) + 1:03d}", base_model=weights,
                            dataset_version=ds.dataset_version, train_config={"weights": str(res.save_dir)},
                            metrics={})


class Training:
    def __init__(self, ctx: Context):
        self.impl: Trainer = ctx.make("trainer")

    def run(self, ctx: Context, ds: DatasetVersion) -> ModelVersion:
        base = current_baseline(ctx)
        mv = self.impl.train(ctx, ds, base)
        ctx.lake.put_json("models", mv.model_version, mv)
        ctx.lineage.node(mv.model_version, "model", base=mv.base_model, **{f"m_{k}": v for k, v in mv.metrics.items()})
        ctx.lineage.link(ds.dataset_version, mv.model_version)
        return mv


# ================================================================ T8 评测门禁
class EvalGate:
    """真实设备金标回归集（Demo：P6 的 holdout 切分）上分切片不退化 + 污染检查。"""

    def run(self, ctx: Context, mv: ModelVersion, ds: DatasetVersion) -> tuple[EvalReport, Optional[ReleasePlan]]:
        base = current_baseline(ctx)
        init = dict(ctx.c("eval.initial_baseline", {}))
        base_metrics = base.metrics if base else {s: init.get(s, 0.6) for s in mv.metrics}
        thr = ctx.c("eval.regression_threshold", 0.01)                       # ⚠ 待定
        slices = {s: {"candidate": v, "baseline": base_metrics.get(s, 0.0)} for s, v in mv.metrics.items()}
        regressions = [s for s, d in slices.items() if d["candidate"] - d["baseline"] < -thr]
        # 污染检查：合成样本不得进入 holdout；holdout 与 train 不重叠
        anns = [ctx.lake.get_json("annotations", a) for a in ds.sample_list]
        contamination_ok = not any(a["synthetic"] and a["split"] != "train" for a in anns)
        improved = any(d["candidate"] - d["baseline"] > ctx.c("eval.min_improvement", 0.0) for d in slices.values())
        passed = not regressions and contamination_ok and improved
        rep = EvalReport(model_version=mv.model_version, baseline_version=base.model_version if base else "init",
                         eval_set=f"{ds.dataset_version}/holdout", slices=slices, passed=passed,
                         regressions=regressions + ([] if improved else ["no_improvement"]),
                         contamination_ok=contamination_ok, regression_threshold=thr)
        ctx.lake.put_json("reports", f"t8-{mv.model_version}", rep)
        ctx.audit.log("T8", "eval_gate", mv.model_version, passed=passed, regressions=rep.regressions)
        if not passed:
            ctx.lake.put_json("rejected", f"t8-{mv.model_version}", {"kind": "release_rejected", **rep.to_dict()})
            return rep, None
        plan = ReleasePlan(release_id=f"rel-{len(ctx.lake.list('releases', 'rel-*.json')) + 1:03d}", model_version=mv.model_version, eval_slices=list(slices),
                           regression_threshold=thr, rollout_plan=list(ctx.c("ota.rollout", [])),
                           rollback_threshold=dict(ctx.c("ota.rollback_threshold", {"fail_rate": 0.2})))
        plan.signature = sign(ctx, plan.to_dict())
        return rep, plan


def sign(ctx: Context, payload: dict[str, Any]) -> str:
    key = str(ctx.c("security.signing_key", "demo-key")).encode()   # 生产：KMS/HSM 私钥签名
    return hmac.new(key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()[:32]


# ================================================================ T9 模型仓库与压缩适配
class Compiler(ABC):
    @abstractmethod
    def compile(self, ctx: Context, mv: ModelVersion, target: dict[str, Any]) -> bytes: ...


@Registry.register("compiler", "demo")
class FakeCompiler(Compiler):
    """Demo：生成伪二进制包。生产：ONNX → TensorRT / RKNN / OpenVINO / TFLite 等按硬件编译+量化。"""

    def compile(self, ctx, mv, target):
        if target.get("simulate_fail"):
            raise RuntimeError(f"compile failed for {target['name']}")
        return json.dumps({"model": mv.model_version, "target": target["name"], "quant": target.get("quant", "int8")}).encode()


class Repository:
    def __init__(self, ctx: Context):
        self.compiler: Compiler = ctx.make("compiler")

    def run(self, ctx: Context, mv: ModelVersion, plan: ReleasePlan) -> list[Manifest]:
        out = []
        prev = current_baseline(ctx)
        for t in ctx.c("hardware.targets", [{"name": "npu-a", "runtime": "rt-1.0", "min_firmware": "1.0.0"}]):
            try:
                blob = self.compiler.compile(ctx, mv, t)
            except Exception as e:                                       # 编译失败该硬件不发布
                ctx.audit.log("T9", "compile_failed", mv.model_version, target=t["name"], err=str(e))
                continue
            build_id = f"build-{mv.model_version}-{t['name']}"
            pkg = ctx.lake.path("builds") / f"{build_id}.bin"
            pkg.write_bytes(blob)
            checksum = hashlib.sha256(blob).hexdigest()
            m = Manifest(model_id=ctx.c("run.model_id", "defect-detector"), version=mv.model_version, build_id=build_id,
                         dataset_version=mv.dataset_version, hardware_target=t["name"], runtime_version=t["runtime"],
                         min_firmware=t["min_firmware"], checksum=checksum, signature="", metrics_snapshot=mv.metrics,
                         package_uri=str(pkg), delta_from=prev.model_version if prev else None)
            m.signature = sign(ctx, {**m.to_dict(), "signature": ""})
            ctx.lake.put_json("builds", build_id, m)
            ctx.lineage.node(build_id, "build", target=t["name"], checksum=checksum)
            ctx.lineage.link(mv.model_version, build_id)
            ctx.lineage.node(plan.release_id, "release", model_version=mv.model_version)
            ctx.lineage.link(build_id, plan.release_id)
            out.append(m)
        return out


# ================================================================ T10 OTA（Demo：状态机模拟 + 离线回放）
class DeviceFleet:
    """设备清单（Demo 存在 lake/devices/fleet.json）。生产来自设备管理平台。"""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        p = ctx.lake.path("devices", "fleet.json")
        if p.exists():
            self.devices = json.loads(p.read_text(encoding="utf-8"))
        else:
            n = ctx.c("fleet.size", 20)
            targets = [t["name"] for t in ctx.c("hardware.targets", [{"name": "npu-a"}])]
            self.devices = [{"device_id": f"dev-{i:03d}", "hardware": targets[i % len(targets)],
                             "firmware": "1.2.0", "group": "internal" if i < 2 else "field",
                             "current_version": None, "previous_version": None, "online": True}
                            for i in range(n)]
            self.save()

    def save(self):
        self.ctx.lake.put_json("devices", "fleet", self.devices)


class OTA:
    def __init__(self, ctx: Context):
        self.fleet = DeviceFleet(ctx)

    def _upgrade_one(self, ctx: Context, dev: dict[str, Any], m: Manifest, plan: ReleasePlan) -> UpgradeReceipt:
        sm = Tracker(OTA_SM)
        fail = ctx.c("ota.failure_injection", {})
        reason = ""
        try:
            sm.to("downloading")
            if ctx.rng.random() < fail.get("power_loss", 0.0):              # 断电/超时 → 回稳定态重试
                sm.recover()
                sm.to("downloading")
            sm.to("downloaded").to("verifying")
            blob = open(m.package_uri, "rb").read()
            if hashlib.sha256(blob).hexdigest() != m.checksum or ctx.rng.random() < fail.get("verify", 0.0):
                sm.to("verify_failed")
                reason = "checksum/signature mismatch"
            else:
                sm.to("verified").to("installing")
                if ctx.rng.random() < fail.get("install", 0.0):
                    sm.to("install_failed")
                    reason = "install error"
                else:
                    sm.to("installed").to("switching")
                    if ctx.rng.random() < fail.get("switch", 0.0):
                        sm.to("switch_failed")
                        reason = "switch/boot health check failed"
                    else:
                        sm.to("active")
            if sm.state.endswith("_failed"):
                sm.to("rolling_back").to("rolled_back")                     # 保留旧版
        except IllegalTransition as e:
            reason = f"state error: {e}"
        ok = sm.state == "active"
        if ok:
            dev["previous_version"], dev["current_version"] = dev["current_version"], m.version
            dev["prev_release"], dev["current_release"] = dev.get("current_release"), plan.release_id
        return UpgradeReceipt(device_id=dev["device_id"], release_id=plan.release_id, build_id=m.build_id,
                              from_version=dev["previous_version"] if ok else dev["current_version"],
                              to_version=m.version, final_state=sm.state, success=ok, reason=reason,
                              duration_s=round(ctx.rng.uniform(20, 120), 1), history=sm.history)

    def run(self, ctx: Context, plan: ReleasePlan, manifests: list[Manifest]) -> dict[str, Any]:
        by_hw = {m.hardware_target: m for m in manifests}
        receipts: list[UpgradeReceipt] = []
        devices = [d for d in self.fleet.devices if d["hardware"] in by_hw and d.get("online", True)]
        done: set[str] = set()
        status = "released"
        stages_log = []
        for st in plan.rollout_plan or [{"stage": "all", "ratio": 1.0}]:
            pool = [d for d in devices if d["device_id"] not in done]
            if st["stage"] == "internal":
                batch = [d for d in pool if d["group"] == "internal"]
            elif st["stage"] == "shadow":
                # 影子模式：新模型旁路运行不切换，只收集分歧统计
                shadow = {"devices": len(pool[:max(1, len(pool) // 5)]),
                          "disagree_rate": round(ctx.rng.uniform(0.01, 0.08), 3)}
                stages_log.append({"stage": "shadow", **shadow})
                if shadow["disagree_rate"] > plan.rollback_threshold.get("shadow_disagree", 0.1):
                    status = "halted_shadow"
                    break
                continue
            else:
                target_n = int(round(len(devices) * st["ratio"]))
                batch = pool[:max(0, target_n - len(done))]
            rs = [self._upgrade_one(ctx, d, by_hw[d["hardware"]], plan) for d in batch]
            receipts += rs
            done |= {d["device_id"] for d in batch}
            fail_rate = sum(not r.success for r in rs) / max(1, len(rs))
            stages_log.append({"stage": st["stage"], "devices": len(rs), "fail_rate": round(fail_rate, 3)})
            if fail_rate > plan.rollback_threshold.get("fail_rate", 0.2):
                status = f"rolled_back_at_{st['stage']}"
                for d in self.fleet.devices:                                 # 全量回滚到上一版本
                    if d.get("current_release") == plan.release_id:
                        d["current_version"], d["previous_version"] = d["previous_version"], d["current_version"]
                        d["current_release"] = d.get("prev_release")
                ctx.audit.log("T10", "rollback", plan.release_id, stage=st["stage"], fail_rate=fail_rate)
                break
        for r in receipts:
            ctx.lake.put_json("devices", f"receipt-{r.device_id}-{r.release_id}", r)
        for d in self.fleet.devices:                                         # 含回滚后的设备，保持血缘与实际一致
            if d.get("current_version"):
                ctx.lineage.node(f"device:{d['device_id']}", "device", current_version=d["current_version"],
                                 previous_version=d["previous_version"], current_release=d.get("current_release"),
                                 hardware=d["hardware"])
            if d.get("current_release") == plan.release_id:
                ctx.lineage.link(plan.release_id, f"device:{d['device_id']}", "deployed")
        self.fleet.save()
        rec = {"plan": plan.to_dict(), "status": status, "stages": stages_log,
               "success": sum(r.success for r in receipts), "failed": sum(not r.success for r in receipts)}
        ctx.lake.put_json("releases", plan.release_id, rec)
        ctx.audit.log("T10", "release", plan.release_id, status=status)
        return rec
