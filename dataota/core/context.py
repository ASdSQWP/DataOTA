"""运行上下文：把配置与横切能力打包传给每个节点。"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .infra import AuditLog, CostLedger, DataLake, ModelStats, PrivacyPolicy, Registry, cfg_get, load_config
from .lineage import LineageStore


@dataclass
class Context:
    cfg: dict[str, Any]
    workdir: Path
    lake: DataLake
    lineage: LineageStore
    audit: AuditLog
    ledger: CostLedger
    privacy: PrivacyPolicy
    rng: random.Random
    counters: dict[str, int] = field(default_factory=dict)
    round: int = 0                                                      # 当前闭环轮次（写入各记录，便于控制台按轮筛选）
    model_stats: ModelStats = field(default_factory=ModelStats)
    fallbacks: dict[str, dict[str, Any]] = field(default_factory=dict)   # 接口 → 实际生效实现与回退原因

    @classmethod
    def from_config(cls, cfg_path: Path | str, workdir: Path | str | None = None) -> "Context":
        cfg = load_config(cfg_path)
        wd = Path(workdir or cfg_get(cfg, "run.workdir", "runs/demo"))
        wd.mkdir(parents=True, exist_ok=True)
        return cls(
            cfg=cfg,
            workdir=wd,
            lake=DataLake(wd / "lake"),
            lineage=LineageStore(wd / "lineage.sqlite"),
            audit=AuditLog(wd / "audit.jsonl"),
            ledger=CostLedger(budget=dict(cfg_get(cfg, "cost.budget", {}))),
            privacy=PrivacyPolicy(
                allow_raw_media_off_device=cfg_get(cfg, "privacy.allow_raw_media_off_device", False),
                retention_days=dict(cfg_get(cfg, "privacy.retention_days", {"raw": 30})),
            ),
            rng=random.Random(cfg_get(cfg, "run.seed", 0)),
        )

    def c(self, dotted: str, default: Any = None) -> Any:
        return cfg_get(self.cfg, dotted, default)

    def impl(self, node: str, default: str = "demo") -> str:
        """configs 中 [impl] 段选择实现，例如 impl.l1 = "yolo"。"""
        return cfg_get(self.cfg, f"impl.{node}", default)

    def make(self, iface: str, default: str = "demo", **kw: Any) -> Any:
        """按 [impl] 创建实现；所选实现缺少可选依赖时回退到 default，并记审计（不静默降级）。"""
        chosen = self.impl(iface, default)
        missing = Registry.missing(iface, chosen) if Registry.has(iface, chosen) else []
        if missing and chosen != default:
            self.fallbacks[iface] = {"selected": chosen, "effective": default,
                                     "reason": f"缺少依赖 {', '.join(missing)}"}
            self.audit.log("system", "impl_fallback", iface, selected=chosen, effective=default, missing=missing)
            chosen = default
        else:
            self.fallbacks.setdefault(iface, {"selected": chosen, "effective": chosen, "reason": ""})
        return Registry.create(iface, chosen, **kw)

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n
