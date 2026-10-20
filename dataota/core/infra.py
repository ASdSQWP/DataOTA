"""横切基础设施：配置、ID、存储、审计(C4)、成本(C3)、隐私(C2)、指标字典(C5)、插件注册。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# ================================================================ 配置
def load_config(path: Path | str) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def cfg_get(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ================================================================ ID
def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


def content_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
    return h.hexdigest()[:16]


# ================================================================ 本地数据湖
class DataLake:
    """Demo 用本地目录模拟对象存储 + 队列。生产替换为 S3/OSS + MQ。"""

    ZONES = ("inbox", "raw", "quarantine", "ingest", "defects", "negatives", "anchors", "generated",
             "gates", "rejected", "annotations", "review_queue", "datasets", "models", "builds",
             "releases", "devices", "telemetry", "reports")

    def __init__(self, root: Path | str):
        self.root = Path(root)
        for z in self.ZONES:
            (self.root / z).mkdir(parents=True, exist_ok=True)

    def path(self, zone: str, name: str = "") -> Path:
        assert zone in self.ZONES, zone
        return self.root / zone / name if name else self.root / zone

    def put_json(self, zone: str, name: str, obj: Any) -> Path:
        p = self.path(zone, name if name.endswith(".json") else f"{name}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        data = obj.to_dict() if hasattr(obj, "to_dict") else obj
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def get_json(self, zone: str, name: str) -> Any:
        p = self.path(zone, name if name.endswith(".json") else f"{name}.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self, zone: str, pattern: str = "*.json") -> list[Path]:
        return sorted(self.path(zone).glob(pattern))

    def exists_hash(self, dedup_hash: str) -> bool:
        return (self.path("ingest") / f".hash-{dedup_hash}").exists()

    def mark_hash(self, dedup_hash: str) -> None:
        (self.path("ingest") / f".hash-{dedup_hash}").touch()


# ================================================================ C4 审计日志
class AuditLog:
    """追加写 JSONL。门禁 / 仲裁 / 发布 / 回滚 / 删除必须记录。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, actor: str, action: str, target: str, **detail: Any) -> None:
        rec = {"ts": time.time(), "actor": actor, "action": action, "target": target, "detail": detail}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def read(self, action: Optional[str] = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l]
        return [r for r in rows if action is None or r["action"] == action]


# ================================================================ C3 算力与成本
class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostLedger:
    """按“单轮闭环”记账。budget 为 0 表示不限（⚠ 单轮成本上限待定）。"""
    budget: dict[str, float] = field(default_factory=dict)   # {"gpu_hours":.., "l2_calls":.., "human_minutes":..}
    spent: dict[str, float] = field(default_factory=dict)

    def remaining(self, k: str) -> float:
        b = self.budget.get(k, 0)
        return float("inf") if not b else b - self.spent.get(k, 0)

    def charge(self, k: str, amount: float, strict: bool = False) -> bool:
        if self.remaining(k) < amount:
            if strict:
                raise BudgetExceeded(f"{k} 超预算: 已用 {self.spent.get(k, 0)} / {self.budget.get(k)}")
            return False
        self.spent[k] = self.spent.get(k, 0) + amount
        return True


# ================================================================ 模型调用统计
class ModelStats:
    """按“单轮闭环”统计每个模型的调用次数、失败、回退与耗时，写入轮次报告供控制台展示。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, kind: str, name: str, ms: float, ok: bool = True, fallback: bool = False) -> None:
        r = self.rows.setdefault((kind, name), {"kind": kind, "model": name, "calls": 0, "failures": 0,
                                                "fallbacks": 0, "latencies": []})
        r["calls"] += 1
        r["failures"] += 0 if ok else 1
        r["fallbacks"] += 1 if fallback else 0
        r["latencies"].append(float(ms))

    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        for r in self.rows.values():
            lat = sorted(r["latencies"])
            p = (lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 2) if lat else 0.0)
            out.append({k: v for k, v in r.items() if k != "latencies"}
                       | {"latency_ms_p50": p(0.5), "latency_ms_p95": p(0.95),
                          "latency_ms_total": round(sum(lat), 1)})
        return sorted(out, key=lambda x: (x["kind"], x["model"]))


# ================================================================ C2 隐私合规
@dataclass
class PrivacyPolicy:
    allow_raw_media_off_device: bool = False   # ⚠ 待定项 1：图像/音频能否离开设备
    retention_days: dict[str, int] = field(default_factory=lambda: {"raw": 30, "ingest": 180, "negatives": 90})

    def check(self, meta: dict[str, Any]) -> tuple[bool, str]:
        """返回 (是否可入湖, 原因)。Demo 规则：含人脸/车牌且未脱敏 → 隔离。"""
        if meta.get("contains_pii") and not meta.get("redacted"):
            return False, "含 PII 未脱敏"
        if meta.get("license") == "unknown":
            return False, "版权不明"
        return True, ""


# ================================================================ C5 指标字典
METRICS: dict[str, str] = {
    "l1_auto_accept_rate": "D1 自动接受数 / 进入 L1 的事件数",
    "l2_escalation_rate": "进入 L2 的事件数 / 进入 L1 的事件数",
    "human_rate": "进入 L3 的事件数 / 进入 L1 的事件数（⚠ 分母待定：也可按样本帧计）",
    "gate_pass_rate": "P4 passed 片段数 / P3 生成片段数",
    "agent_disagreement_rate": "P5 进入人工仲裁的帧数 / 标注帧数",
    "slice_regression": "candidate - baseline，按切片计算；任一切片 < -regression_threshold 即拒绝发布",
    "rollback_rate": "rolled_back 回执数 / 下发设备数",
    "hard_case_rate": "设备端触发难例数 / 推理次数",
    "drift_psi": "置信度直方图相对基线的 PSI",
    "drift_js": "置信度直方图相对基线的 Jensen-Shannon 散度（以 2 为底，0~1）",
    "drift_ks": "置信度直方图累积分布的最大差（KS 距离，0~1）",
    "detector_accuracy": "B2 检测器预测类别 == 参考标签的事件数 / 有参考标签的事件数（参考：模拟真值或 L2/L3 独立裁决）",
    "detector_recall": "按类别：检测器命中该类的事件数 / 参考标签为该类的事件数",
    "world_model_pass_rate": "按世界模型 × 视角：P4 passed 片段数 / 该组合生成片段数",
    "prescreen_fail_rate": "P3 初筛失败片段数（空帧 / 帧数不符 / 目标出画）/ 生成片段数",
}


# ================================================================ 插件注册
def missing_modules(modules: Iterable[str]) -> list[str]:
    """返回未安装的可选依赖（只检查能否找到模块，不真正导入）。"""
    out = []
    for m in modules:
        try:
            if importlib.util.find_spec(m) is None:
                out.append(m)
        except (ImportError, ValueError):
            out.append(m)
    return out


class Registry:
    """接口名 → {实现名: 工厂}。配置里写实现名即可替换 Demo 为生产实现。

    register(..., requires=("numpy", "PIL")) 声明可选依赖；缺依赖时 Context.make 会回退到默认实现并写审计。
    """
    _impls: dict[str, dict[str, Callable[..., Any]]] = {}
    _requires: dict[tuple[str, str], tuple[str, ...]] = {}

    @classmethod
    def register(cls, iface: str, name: str, requires: Iterable[str] = ()):
        def deco(factory):
            cls._impls.setdefault(iface, {})[name] = factory
            cls._requires[(iface, name)] = tuple(requires)
            return factory
        return deco

    @classmethod
    def create(cls, iface: str, impl: str, **kw: Any) -> Any:
        try:
            factory = cls._impls[iface][impl]
        except KeyError:
            raise KeyError(f"未注册的实现 {iface}.{impl}；已注册: {list(cls._impls.get(iface, {}))}") from None
        return factory(**kw)

    @classmethod
    def requires(cls, iface: str, impl: str) -> tuple[str, ...]:
        return cls._requires.get((iface, impl), ())

    @classmethod
    def missing(cls, iface: str, impl: str) -> list[str]:
        return missing_modules(cls.requires(iface, impl))

    @classmethod
    def has(cls, iface: str, impl: str) -> bool:
        return impl in cls._impls.get(iface, {})

    @classmethod
    def available(cls) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in cls._impls.items()}

    @classmethod
    def describe(cls) -> dict[str, list[dict[str, Any]]]:
        """每个实现的依赖与缺失情况，供控制台展示。"""
        return {iface: [{"name": n, "requires": list(cls.requires(iface, n)), "missing": cls.missing(iface, n)}
                        for n in sorted(impls)]
                for iface, impls in cls._impls.items()}
