"""L1 数据源与接入：A0 设备代理信号 / A1 多源接入 / A2 统一网关 / A3 音频事件 / A4 ingest_event。

约定：原始条目（raw item）是一个 dict：
    {"source_type": "crawl|user_upload|video_stream|audio|device",
     "media": [{"uri": "...", "kind": "image|video|audio"}],
     "user_description": "...", "has_audio": bool,
     "meta": {"license": "...", "contains_pii": bool, "redacted": bool, ...},
     "_sim": {...}}   # 仅 Demo：模拟真值，供 mock 模型使用；生产数据没有该字段
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..core.context import Context
from ..core.infra import Registry, content_hash, new_id
from ..core.schemas import AudioEvent, HardCase, IngestEvent, MediaRef, SchemaError, SourceType, TriggerReason


# ================================================================ A0 设备端推理与代理信号
class DeviceSignal(ABC):
    """设备端难例触发器：设备拿不到真值，只能用代理信号。"""

    @abstractmethod
    def select(self, ctx: Context, inferences: list[dict[str, Any]]) -> list[HardCase]: ...


@Registry.register("device_signal", "demo")
class RuleDeviceSignal(DeviceSignal):
    """规则版：低置信 / 影子分歧 / 场景异常 / 下游纠正，带每日上传预算。"""

    def select(self, ctx, inferences):
        thr = ctx.c("device.low_conf_threshold", 0.5)
        budget = ctx.c("device.daily_upload_budget", 50)
        out: list[HardCase] = []
        for inf in inferences:
            reason = None
            if inf.get("correction"):
                reason = TriggerReason.DOWNSTREAM_CORRECTION
            elif inf.get("shadow_pred") is not None and inf["shadow_pred"] != inf.get("pred"):
                reason = TriggerReason.SHADOW_DISAGREE
            elif inf.get("scene_quality", 1.0) < ctx.c("device.scene_quality_min", 0.4):
                reason = TriggerReason.SCENE_ANOMALY
            elif inf["confidence"] < thr:
                reason = TriggerReason.LOW_CONFIDENCE
            if reason is None:
                continue
            if len(out) >= budget:
                ctx.count("a0.budget_dropped")
                continue
            out.append(HardCase(
                device_id=inf["device_id"], model_version=inf["model_version"], trigger_reason=reason,
                sample_ref=inf["sample_ref"], confidence=inf["confidence"],
                scene_tags=inf.get("scene_tags", {}), privacy_flag=not ctx.privacy.allow_raw_media_off_device,
                timestamp=inf.get("ts", time.time()),
            ))
        ctx.count("a0.hard_cases", len(out))
        return out


def hard_case_to_raw(hc: HardCase, sim: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """A0 → A1：设备难例包转为统一原始条目。
    ⚠ 待定项 1：若图像不能离开设备，sample_ref 只是特征/摘要引用。"""
    return {
        "source_type": SourceType.DEVICE.value,
        "media": [{"uri": hc.sample_ref, "kind": "image"}],
        "has_audio": False,
        "meta": {"device_id": hc.device_id, "model_version": hc.model_version,
                 "trigger_reason": hc.trigger_reason.value, "scene_tags": hc.scene_tags,
                 "redacted": hc.privacy_flag, "license": "owned"},
        "_sim": sim or {},
    }


# ================================================================ A1 多源数据接入
class SourceAdapter(ABC):
    @abstractmethod
    def accept(self, ctx: Context, raw: dict[str, Any]) -> Optional[dict[str, Any]]:
        """认证、转码、元数据、来源标记。返回 None 表示已隔离。"""


@Registry.register("source_adapter", "demo")
class LocalSourceAdapter(SourceAdapter):
    def accept(self, ctx, raw):
        st = raw.get("source_type")
        if st not in {s.value for s in SourceType}:
            ctx.lake.put_json("quarantine", new_id("bad"), {"reason": "unknown source_type", "raw": raw})
            return None
        if raw.get("meta", {}).get("license") == "unknown":
            ctx.lake.put_json("quarantine", new_id("lic"), {"reason": "版权不明", "raw": raw})
            ctx.count("a1.quarantined")
            return None
        if st == SourceType.VIDEO_STREAM.value and raw.get("meta", {}).get("stream_broken"):
            ctx.audit.log("A1", "stream_break", raw["media"][0]["uri"])
        pkt = dict(raw)
        pkt["received_at"] = time.time()
        return pkt


# ================================================================ A2 统一接入网关
class Gateway(ABC):
    @abstractmethod
    def standardize(self, ctx: Context, pkt: dict[str, Any]) -> Optional[dict[str, Any]]: ...


@Registry.register("gateway", "demo")
class ScriptGateway(Gateway):
    def standardize(self, ctx, pkt):
        meta = dict(pkt.get("meta", {}))
        ok, why = ctx.privacy.check(meta)
        if not ok and why == "含 PII 未脱敏":
            meta["redacted"] = True            # Demo：模拟脱敏成功；生产接入真实脱敏服务
            ctx.audit.log("A2", "redact", pkt["media"][0]["uri"])
            ok = True
        if not ok:
            ctx.lake.put_json("quarantine", new_id("priv"), {"reason": why, "pkt": pkt})
            ctx.count("a2.quarantined")
            return None
        h = content_hash([m["uri"] for m in pkt["media"]], pkt.get("user_description"))
        if ctx.lake.exists_hash(h):
            ctx.count("a2.dedup_dropped")
            return None
        ctx.lake.mark_hash(h)
        pkt = dict(pkt, meta=meta, dedup_hash=h)
        return pkt


# ================================================================ A3 音频事件处理
class AudioProcessor(ABC):
    @abstractmethod
    def process(self, ctx: Context, pkt: dict[str, Any]) -> list[AudioEvent]: ...


@Registry.register("audio", "demo")
class MockAudioProcessor(AudioProcessor):
    """Demo：从 _sim.audio_label 生成事件；生产接 ASR + 声音事件分类。"""

    def process(self, ctx, pkt):
        sim = pkt.get("_sim", {})
        label = sim.get("audio_label")
        if not label:
            return []
        poor = sim.get("audio_quality", 1.0) < 0.3
        return [AudioEvent(label=label, t_start=0.0, t_end=2.0, confidence=0.8 if not poor else 0.5,
                           transcript=None if poor else sim.get("transcript"),
                           urgency=0.9 if label in ("crack_sound", "alarm", "impact") else 0.2)]


# ================================================================ A4 生成 ingest_event
def build_ingest_event(ctx: Context, pkt: dict[str, Any], audio: list[AudioEvent]) -> Optional[IngestEvent]:
    ev = IngestEvent(
        ingest_id=new_id("ing"),
        source_type=SourceType(pkt["source_type"]),
        media=[MediaRef(**m) for m in pkt["media"]],
        user_description=pkt.get("user_description"),
        audio_events=audio,
        privacy_flag=bool(pkt.get("meta", {}).get("redacted")),
        dedup_hash=pkt["dedup_hash"],
        source_meta={**pkt.get("meta", {}), "_sim": pkt.get("_sim", {}), "round": ctx.round},
    )
    try:
        ev.validate()
    except SchemaError as e:
        ctx.lake.put_json("quarantine", new_id("schema"), {"reason": str(e), "pkt": pkt})
        ctx.count("a4.schema_rejected")
        return None
    ctx.lake.put_json("ingest", ev.ingest_id, ev)
    ctx.lineage.node(ev.ingest_id, "ingest", source_type=ev.source_type.value)
    ctx.count("a4.ingest_events")
    ctx.count(f"a4.src.{ev.source_type.value}")
    return ev


class IngestLayer:
    """A1 → A2 →（场景质量检测）→ (A3) → A4 串联。"""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.adapter: SourceAdapter = ctx.make("source_adapter")
        self.gateway: Gateway = ctx.make("gateway")
        self.audio: AudioProcessor = ctx.make("audio")
        self.quality = ctx.make("scene_quality", default="mock") if ctx.impl("scene_quality", "") else None

    def assess_quality(self, pkt: dict[str, Any]) -> None:
        """A2 附加场景质量（模糊 / 曝光 / 对比度 / 噪声），供 D1 判困难切片与控制台展示。"""
        if self.quality is None:
            return
        img = next((m["uri"] for m in pkt["media"] if m.get("kind", "image") == "image"), None)
        if img is None:
            return
        t0 = time.perf_counter()
        try:
            q = self.quality.assess(self.ctx, img, {**pkt.get("meta", {}), "_sim": pkt.get("_sim", {})})
        except Exception:
            q = None
        self.ctx.model_stats.record("scene_quality", self.quality.name, (time.perf_counter() - t0) * 1000, q is not None)
        if q is not None:
            pkt["meta"] = {**pkt.get("meta", {}), "scene_quality": q.to_dict()}
            for f in q.flags:
                self.ctx.count(f"a2.quality.{f}")
        min_q = self.ctx.c("gateway.min_scene_quality", 0.0)
        if q is not None and q.score < min_q:
            self.ctx.audit.log("A2", "low_quality", img, score=q.score, flags=q.flags)

    def run(self, raws: list[dict[str, Any]]) -> list[IngestEvent]:
        out = []
        for raw in raws:
            pkt = self.adapter.accept(self.ctx, raw)
            if pkt is None:
                continue
            pkt = self.gateway.standardize(self.ctx, pkt)
            if pkt is None:
                continue
            self.assess_quality(pkt)
            audio = self.audio.process(self.ctx, pkt) if pkt.get("has_audio") else []
            ev = build_ingest_event(self.ctx, pkt, audio)
            if ev:
                out.append(ev)
        return out
