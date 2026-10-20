"""控制台数据接口：把 workdir 下的 Demo 产物聚合成前端可直接渲染的 JSON。

设计原则
--------
* **只读**：除「审核台回写」会修改 ``review_queue`` 的 decision 字段并追加一条审计外，
  不触碰流水线数据。
* **容错**：工作目录可能为空、可能正在被 ``run`` 写入；所有读取都必须能优雅降级。
* **分页**：标注 / 审计动辄上千条，接口一律只返回当前页，列表页不整包下发。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from ..core.infra import METRICS, AuditLog, cfg_get, load_config
from ..core.lineage import LineageStore
from .api_models import ModelViewsMixin

# 血缘节点的展示顺序（与设计文档第 6 节一致）
KIND_ORDER = ["ingest", "defect", "anchor", "generated", "gate", "annotation",
              "dataset", "model", "build", "release", "device"]
KIND_LABEL = {
    "ingest": "接入事件", "defect": "缺陷事件", "anchor": "锚点", "generated": "生成片段",
    "gate": "几何门禁", "annotation": "标注", "dataset": "数据集", "model": "模型",
    "build": "构建包", "release": "发布单", "device": "设备",
}

# 设计文档第 8 节的 ⚠ 待定项（当前 config 里是占位值）
OPEN_ITEMS = [
    {"id": 1, "title": "图像 / 音频能否离开设备", "where": "A2、C2",
     "impact": "决定 A0→A1 回流是传原始数据还是特征 / 摘要"},
    {"id": 2, "title": "L1 / L2 升级阈值", "where": "D1、D2",
     "impact": "决定 L2 调用量与人工量"},
    {"id": 3, "title": "几何门禁指标权重与阈值", "where": "P4",
     "impact": "决定合成数据通过率"},
    {"id": 4, "title": "真实留出集规模与切片口径", "where": "T8",
     "impact": "决定「不退化」判定的可信度"},
    {"id": 5, "title": "人工介入比例分母定义", "where": "B6、P5",
     "impact": "决定人工成本指标口径"},
    {"id": 6, "title": "Demo 生成模型最终选型", "where": "P3",
     "impact": "决定单卡算力与生成质量"},
    {"id": 7, "title": "OTA 是否只做模拟", "where": "T10",
     "impact": "决定 Demo 范围"},
    {"id": 8, "title": "单轮闭环成本上限", "where": "C3、T7",
     "impact": "决定生成 / 训练规模"},
]

DECISIONS = ("confirmed", "rejected", "needs_more_data")
SEVERITIES = ("low", "medium", "high", "critical")


def _num(value: Any) -> int:
    """从 id 中提取最后一个数字，用于按版本号排序（mv-004 → 4）。"""
    nums = re.findall(r"\d+", str(value))
    return int(nums[-1]) if nums else -1


def _ratio(a: float, b: float) -> float:
    return round(a / b, 4) if b else 0.0


def _iso(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _norm_hist(hist: list[float]) -> list[float]:
    s = sum(hist) or 1.0
    return [round(x / s, 4) for x in hist]


class Dashboard(ModelViewsMixin):
    """一个工作目录对应一个 Dashboard 实例；内部带短 TTL 缓存。模型库 / 数据源相关接口见 api_models.ModelViewsMixin。"""

    def __init__(self, workdir: Path | str, config_path: Path | str):
        self.workdir = Path(workdir).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve()
        self.lake = self.workdir / "lake"
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._lin: Optional[LineageStore] = None
        self._lin_lock = threading.RLock()

    # ------------------------------------------------------------------ 基础工具
    @property
    def cfg(self) -> dict[str, Any]:
        try:
            return load_config(self.config_path)
        except Exception:
            return {}

    def c(self, dotted: str, default: Any = None) -> Any:
        return cfg_get(self.cfg, dotted, default)

    def _cached(self, key: str, ttl: float, builder: Callable[[], Any]) -> Any:
        now = time.time()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        val = builder()
        with self._cache_lock:
            self._cache[key] = (now, val)
        return val

    def _read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _media_rel(self, uri: Any) -> str:
        """把数据里的 frame_uri 映射成 /media 地址（仅允许 workdir 内的媒体文件）。"""
        s = str(uri or "").replace("\\", "/")
        idx = s.find("/lake/")
        if idx >= 0:
            s = s[idx + 1:]
        elif s.startswith("lake/"):
            pass
        else:
            return ""
        target = (self.workdir / s).resolve()
        try:
            target.relative_to(self.workdir)
        except ValueError:
            return ""
        if target.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return ""
        return "/media?" + urlencode({"path": s})

    def _zone_files(self, zone: str, pattern: str = "*.json") -> list[Path]:
        d = self.lake / zone
        if not d.is_dir():
            return []
        return sorted(d.glob(pattern))

    def _lineage(self) -> Optional[LineageStore]:
        """复用一条只读连接；sqlite 连接跨线程需要 check_same_thread=False。"""
        if self._lin is None:
            db = self.workdir / "lineage.sqlite"
            if not db.exists():
                return None
            try:
                with self._lin_lock:
                    if self._lin is None:
                        self._lin = LineageStore(db, check_same_thread=False)
            except Exception:
                return None
        return self._lin

    def close(self) -> None:
        with self._lin_lock:
            if self._lin is not None:
                try:
                    self._lin.close()
                finally:
                    self._lin = None

    # ------------------------------------------------------------------ 轮次
    def rounds(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            out = []
            for p in self._zone_files("reports", "round-*.json"):
                d = self._read_json(p)
                if isinstance(d, dict):
                    d["_file"] = p.name
                    try:
                        d["_mtime"] = p.stat().st_mtime
                    except OSError:
                        d["_mtime"] = None
                    out.append(d)
            out.sort(key=lambda r: int(r.get("round") or _num(r.get("_file"))))
            return out

        return self._cached("rounds", 1.0, build)

    def round_report(self, n: int) -> Optional[dict[str, Any]]:
        return next((r for r in self.rounds() if int(r.get("round", -1)) == int(n)), None)

    # ------------------------------------------------------------------ 数据集 / 模型 / 发布
    def datasets(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            items = []
            for p in self._zone_files("datasets", "ds-v*.json"):
                d = self._read_json(p, {}) or {}
                cfg = d.get("export_config") or {}
                stats = d.get("stats") or {}
                items.append({
                    "dataset_version": d.get("dataset_version", p.stem),
                    "formats": d.get("formats", []),
                    "schema_version": d.get("schema_version"),
                    "exporter_version": d.get("exporter_version"),
                    "classes": cfg.get("classes", []),
                    "parent": cfg.get("parent"),
                    "image_width": cfg.get("image_width"),
                    "image_height": cfg.get("image_height"),
                    "sample_count": len(d.get("sample_list") or []),
                    "stats": stats,
                    "class_hist": stats.get("class_hist", {}),
                })
            items.sort(key=lambda x: _num(x["dataset_version"]))
            return {"items": items, "total": len(items)}

        return self._cached("datasets", 1.5, build)

    def dataset_detail(self, version: str) -> Optional[dict[str, Any]]:
        p = self.lake / "datasets" / f"{version}.json"
        d = self._read_json(p)
        if not isinstance(d, dict):
            return None
        samples = list(d.get("sample_list") or [])
        exported = []
        root = self.lake / "datasets" / version
        for split in ("train", "holdout"):
            for fmt in d.get("formats", []):
                files = sorted((root / split).rglob(f"*{self._fmt_glob(fmt)}"))
                exported.append({
                    "split": split, "format": fmt,
                    "files": [f.name for f in files[:8]],
                    "count": len(files),
                })
        cfg = d.get("export_config") or {}
        return {
            "dataset_version": d.get("dataset_version", version),
            "formats": d.get("formats", []),
            "schema_version": d.get("schema_version"),
            "exporter_version": d.get("exporter_version"),
            "export_config": cfg,
            "stats": d.get("stats", {}),
            "class_hist": (d.get("stats") or {}).get("class_hist", {}),
            "sample_count": len(samples),
            "sample_preview": samples[:60],
            "exported_files": exported,
            "parent": cfg.get("parent"),
            "lineage_annotation_count": len((d.get("lineage") or {}).get("annotations") or []),
        }

    @staticmethod
    def _fmt_glob(fmt: str) -> str:
        return {"coco": "coco.json", "sharegpt": "sharegpt.json",
                "messages": "messages.jsonl", "yolo": "data.yaml"}.get(fmt, "*")

    def models(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            releases = self._release_index()
            items = []
            for p in self._zone_files("models", "mv-*.json"):
                m = self._read_json(p, {}) or {}
                mv = m.get("model_version", p.stem)
                ev = self._read_json(self.lake / "reports" / f"t8-{mv}.json")
                rel = releases.get(mv)
                builds = []
                for b in self._zone_files("builds", f"build-{mv}-*.json"):
                    bd = self._read_json(b, {}) or {}
                    builds.append({"build_id": bd.get("build_id", b.stem),
                                   "target": bd.get("hardware_target"),
                                   "checksum": bd.get("checksum"),
                                   "runtime": bd.get("runtime_version")})
                items.append({
                    "model_version": mv,
                    "base_model": m.get("base_model"),
                    "dataset_version": m.get("dataset_version"),
                    "train_config": m.get("train_config", {}),
                    "metrics": m.get("metrics", {}),
                    "cost": m.get("cost", {}),
                    "eval": ev,
                    "builds": builds,
                    "release": rel,
                })
            items.sort(key=lambda x: _num(x["model_version"]))
            return {"items": items, "total": len(items)}

        return self._cached("models", 1.5, build)

    def _release_index(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for p in self._zone_files("releases", "rel-*.json"):
            d = self._read_json(p, {}) or {}
            mv = (d.get("plan") or {}).get("model_version")
            if mv:
                out[mv] = {"release_id": (d.get("plan") or {}).get("release_id", p.stem),
                           "status": d.get("status"),
                           "success": d.get("success", 0), "failed": d.get("failed", 0)}
        return out

    def releases(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            items = []
            for p in self._zone_files("releases", "rel-*.json"):
                d = self._read_json(p, {}) or {}
                plan = d.get("plan") or {}
                rid = plan.get("release_id", p.stem)
                receipts = []
                for rp in self._zone_files("devices", f"receipt-*-{rid}.json"):
                    r = self._read_json(rp, {}) or {}
                    receipts.append({
                        "device_id": r.get("device_id"),
                        "from_version": r.get("from_version"),
                        "to_version": r.get("to_version"),
                        "final_state": r.get("final_state"),
                        "success": bool(r.get("success")),
                        "reason": r.get("reason", ""),
                        "duration_s": r.get("duration_s", 0),
                        "history": r.get("history", []),
                    })
                status = d.get("status", "unknown")
                items.append({
                    "release_id": rid,
                    "model_version": plan.get("model_version"),
                    "status": status,
                    "rolled_back": str(status).startswith("rolled_back") or "halted" in str(status),
                    "success": d.get("success", 0),
                    "failed": d.get("failed", 0),
                    "stages": d.get("stages", []),
                    "plan": plan,
                    "receipts": receipts,
                })
            items.sort(key=lambda x: _num(x["release_id"]))
            return {"items": items, "total": len(items)}

        return self._cached("releases", 1.5, build)

    # ------------------------------------------------------------------ 设备 / 遥测 / 监控
    def _latest_telemetry_round(self) -> int:
        rounds = {_num(p.stem.split("-", 1)[0].lstrip("r")) for p in self._zone_files("telemetry", "r*-*.json")}
        rounds = {r for r in rounds if r >= 0}
        return max(rounds) if rounds else 0

    def telemetry(self, round_no: Optional[int] = None) -> dict[str, Any]:
        latest = self._latest_telemetry_round()

        def build() -> dict[str, Any]:
            r = int(round_no or latest or 0)
            files = self._zone_files("telemetry", f"r{r:02d}-*.json") if r else []
            devices = [self._read_json(p, {}) or {} for p in files]
            devices = [d for d in devices if d.get("device_id")]
            hists = [_norm_hist(d.get("confidence_hist") or [0] * 10) for d in devices]
            hist_avg = [round(sum(h[i] for h in hists) / len(hists), 4) for i in range(10)] if hists else []
            p50 = sorted(d.get("latency_ms_p50", 0) for d in devices)
            p95 = sorted(d.get("latency_ms_p95", 0) for d in devices)
            return {
                "round": r,
                "available_rounds": sorted({_num(p.stem.split("-", 1)[0].lstrip("r"))
                                            for p in self._zone_files("telemetry", "r*-*.json")}),
                "devices": devices,
                "hist_avg": hist_avg,
                "latency_p50_avg": round(sum(p50) / len(p50), 1) if p50 else 0,
                "latency_p95_avg": round(sum(p95) / len(p95), 1) if p95 else 0,
                "latency_p95_max": max(p95) if p95 else 0,
                "hard_case_rate_avg": round(sum(d.get("hard_case_rate", 0) for d in devices) / len(devices), 4) if devices else 0,
                "devices_reporting": len(devices),
            }

        return self._cached(f"telemetry-{round_no}", 1.5, build)

    def fleet(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            devices = self._read_json(self.lake / "devices" / "fleet.json", []) or []
            tel = self.telemetry()
            by_dev = {d.get("device_id"): d for d in tel.get("devices", [])}
            dist = Counter((d.get("current_version") or "none") for d in devices)
            latest = max((v for v in dist if v != "none"), key=_num, default=None)
            frag = round(1 - dist.get(latest, 0) / max(1, len(devices)), 4) if devices else 0.0
            out = []
            for d in devices:
                t = by_dev.get(d.get("device_id"), {})
                out.append({**d, "telemetry": {
                    "latency_ms_p50": t.get("latency_ms_p50"),
                    "latency_ms_p95": t.get("latency_ms_p95"),
                    "hard_case_rate": t.get("hard_case_rate"),
                    "model_version": t.get("model_version"),
                } if t else None})
            return {
                "devices": out,
                "total": len(out),
                "online": sum(1 for d in out if d.get("online", True)),
                "distribution": dict(dist),
                "latest_version": latest,
                "fragmentation": frag,
                "telemetry_round": tel.get("round"),
            }

        return self._cached("fleet", 1.5, build)

    def monitor(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            reports = []
            for p in self._zone_files("reports", "m11-r*.json"):
                d = self._read_json(p, {}) or {}
                if d:
                    reports.append(d)
            reports.sort(key=lambda r: int(r.get("round", 0)))
            baseline = self._read_json(self.lake / "reports" / "m11_baseline_hist.json", []) or []
            latest_tel = self.telemetry()
            hist = latest_tel.get("hist_avg") or []
            alerts = []
            for r in reports:
                for a in r.get("alerts", []) or []:
                    alerts.append({"round": r.get("round"), **a})
            psi_series = [{"round": r.get("round"), "psi": r.get("psi", 0)} for r in reports]
            latency_series = []
            for r in reports:
                t = self.telemetry(int(r.get("round") or 0))
                latency_series.append({"round": r.get("round"),
                                       "p95": t.get("latency_p95_avg", 0),
                                       "p50": t.get("latency_p50_avg", 0)})
            gov = self._read_json(self.lake / "reports" / "m12_governance.json", {}) or {}
            return {
                "reports": reports,
                "psi_series": psi_series,
                "psi_threshold": self.c("monitor.psi_alert", 0.2),
                "latency_series": latency_series,
                "latency_threshold": self.c("monitor.latency_p95_ms", 60),
                "baseline_hist": _norm_hist(baseline) if baseline else [],
                "current_hist": hist,
                "alerts": alerts,
                "governance": gov,
                "hard_case_rate": latest_tel.get("hard_case_rate_avg", 0),
                "devices_reporting": latest_tel.get("devices_reporting", 0),
            }

        return self._cached("monitor", 1.5, build)

    # ------------------------------------------------------------------ 标注
    def annotations_index(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            out = []
            for p in self._zone_files("annotations", "ann-*.json"):
                d = self._read_json(p, {}) or {}
                if not d:
                    continue
                out.append({
                    "annotation_id": d.get("annotation_id", p.stem),
                    "defect_type": d.get("defect_type"),
                    "split": d.get("split"),
                    "synthetic": bool(d.get("synthetic")),
                    "agent_consensus": d.get("agent_consensus", 0),
                    "gate_status": d.get("gate_status"),
                    "human_decision": bool(d.get("human_decision")),
                    "state": d.get("state"),
                    "frame_uri": d.get("frame_uri"),
                    "frame_url": self._media_rel(d.get("frame_uri")),
                    "bbox": d.get("bbox") or None,
                    "defect_id": d.get("defect_id"),
                })
            out.sort(key=lambda x: x["annotation_id"], reverse=True)
            return out

        return self._cached("annotations-index", 4.0, build)

    def annotations(self, page: int = 1, size: int = 25, split: str = "",
                    synthetic: str = "", defect_type: str = "", gate: str = "",
                    human: str = "", q: str = "") -> dict[str, Any]:
        rows = self.annotations_index()
        facets = {
            "split": dict(Counter(r["split"] or "unknown" for r in rows)),
            "synthetic": {"synthetic": sum(r["synthetic"] for r in rows),
                          "real": sum(not r["synthetic"] for r in rows)},
            "defect_type": dict(Counter(r["defect_type"] or "none" for r in rows)),
            "gate": dict(Counter(r["gate_status"] or "none" for r in rows)),
            "human": {"human": sum(r["human_decision"] for r in rows),
                      "auto": sum(not r["human_decision"] for r in rows)},
            "total": len(rows),
        }
        sel = rows
        if split:
            sel = [r for r in sel if (r["split"] or "") == split]
        if synthetic in ("true", "false"):
            want = synthetic == "true"
            sel = [r for r in sel if r["synthetic"] is want]
        if defect_type:
            sel = [r for r in sel if (r["defect_type"] or "none") == defect_type]
        if gate:
            sel = [r for r in sel if (r["gate_status"] or "none") == gate]
        if human in ("true", "false"):
            want = human == "true"
            sel = [r for r in sel if r["human_decision"] is want]
        if q:
            needle = q.lower()
            sel = [r for r in sel if needle in (r["annotation_id"] + " " + (r["defect_type"] or "") + " "
                                                + (r["defect_id"] or "")).lower()]
        size = max(1, min(200, int(size)))
        pages = max(1, (len(sel) + size - 1) // size)
        page = max(1, min(pages, int(page)))
        start = (page - 1) * size
        return {"total": len(sel), "page": page, "size": size, "pages": pages,
                "items": sel[start:start + size], "facets": facets}

    def annotation_detail(self, nid: str) -> Optional[dict[str, Any]]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", nid or ""):
            return None
        d = self._read_json(self.lake / "annotations" / f"{nid}.json")
        if not isinstance(d, dict):
            return None
        trust: Any = {}
        lin = self._lineage()
        if lin is not None:
            try:
                with self._lin_lock:
                    trust = lin.annotation_trust(nid) or {}
            except Exception:
                trust = {}
        return {"annotation": d, "trust": trust,
                "frame_url": self._media_rel(d.get("frame_uri"))}

    # ------------------------------------------------------------------ 审计
    def _audit_rows(self) -> list[dict[str, Any]]:
        path = self.workdir / "audit.jsonl"
        if not path.exists():
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []

        def build() -> list[dict[str, Any]]:
            rows = []
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            continue
            except OSError:
                return []
            for r in rows:
                r["ts_iso"] = _iso(r.get("ts"))
            return rows

        now = time.time()
        key = f"audit-{mtime}"
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < 2.0:
                return hit[1]
        rows = build()
        with self._cache_lock:
            # 只保留最近两条审计快照，避免缓存无限增长
            for k in [k for k in self._cache if k.startswith("audit-") and k != key]:
                self._cache.pop(k, None)
            self._cache[key] = (now, rows)
        return rows

    def audit(self, page: int = 1, size: int = 30, action: str = "",
              actor: str = "", q: str = "") -> dict[str, Any]:
        rows = self._audit_rows()
        facets = {"actions": dict(Counter(r.get("action") for r in rows)),
                  "actors": dict(Counter(r.get("actor") for r in rows)),
                  "total": len(rows)}
        sel = rows
        if action:
            sel = [r for r in sel if r.get("action") == action]
        if actor:
            sel = [r for r in sel if r.get("actor") == actor]
        if q:
            needle = q.lower()

            def hit(r: dict[str, Any]) -> bool:
                blob = f"{r.get('target','')} {r.get('action','')} {r.get('actor','')} " \
                       f"{json.dumps(r.get('detail', {}), ensure_ascii=False)}"
                return needle in blob.lower()

            sel = [r for r in sel if hit(r)]
        sel = sorted(sel, key=lambda r: float(r.get("ts") or 0), reverse=True)
        size = max(1, min(300, int(size)))
        pages = max(1, (len(sel) + size - 1) // size)
        page = max(1, min(pages, int(page)))
        start = (page - 1) * size
        return {"total": len(sel), "page": page, "size": size, "pages": pages,
                "items": sel[start:start + size], "facets": facets}

    def audit_csv(self, action: str = "", actor: str = "", q: str = "") -> str:
        data = self.audit(page=1, size=10 ** 9, action=action, actor=actor, q=q)
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "time", "actor", "action", "target", "detail"])
        for r in reversed(data["items"]):
            w.writerow([r.get("ts", ""), r.get("ts_iso", ""), r.get("actor", ""),
                        r.get("action", ""), r.get("target", ""),
                        json.dumps(r.get("detail", {}), ensure_ascii=False)])
        return buf.getvalue()

    # ------------------------------------------------------------------ 审核台
    def _queue_files(self) -> list[Path]:
        return self._zone_files("review_queue", "*.json")

    def review(self, include_resolved: bool = False) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            pending, resolved = [], []
            for p in self._queue_files():
                d = self._read_json(p, {}) or {}
                item = {
                    "queue_id": p.stem,
                    "priority": d.get("priority", 0),
                    "ingest_id": (d.get("ingest") or {}).get("ingest_id") or d.get("ingest_id"),
                    "reasons": d.get("reasons", []),
                    "claim": d.get("claim"),
                    "l1": d.get("l1"),
                    "l2": d.get("l2"),
                    "ingest": d.get("ingest"),
                    "decision": d.get("decision"),
                    "decision_template": d.get("decision_template"),
                    "why": d.get("why"),
                    "frame_url": self._media_rel(next((m.get("uri") for m in (d.get("ingest") or {}).get("media") or []
                                                       if m.get("kind") == "image"), "")),
                    "applied_defect_id": d.get("applied_defect_id"),
                }
                if d.get("decision") is None:
                    pending.append(item)
                else:
                    resolved.append(item)
            pending.sort(key=lambda x: (-int(x.get("priority") or 0), x["queue_id"]))
            resolved.sort(key=lambda x: x["queue_id"], reverse=True)
            out = {"pending": pending, "resolved": resolved,
                   "pending_count": len(pending), "resolved_count": len(resolved),
                   "reviewer_impl": self.c("impl.reviewer", "oracle")}
            if not include_resolved:
                out["resolved"] = resolved[:40]
            return out

        return self._cached("review", 1.0, build)

    def submit_review(self, queue_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", queue_id or ""):
            raise ValueError("非法的待审条目 ID")
        path = self.lake / "review_queue" / f"{queue_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"待审条目不存在：{queue_id}")
        d = self._read_json(path, None)
        if not isinstance(d, dict) or "decision" not in d:
            raise ValueError("该文件不是待审条目")
        decision = str(payload.get("decision") or "").strip()
        if decision not in DECISIONS:
            raise ValueError(f"decision 必须是 {DECISIONS} 之一")
        severity = payload.get("severity") or None
        if severity not in (*SEVERITIES, None):
            raise ValueError(f"severity 必须是 {SEVERITIES} 之一")
        try:
            minutes = float(payload.get("minutes_spent") or 0)
        except (TypeError, ValueError):
            minutes = 0.0
        rec = {
            "reviewer": str(payload.get("reviewer") or "web-reviewer")[:64],
            "decision": decision,
            "defect_type": payload.get("defect_type") or None,
            "severity": severity,
            "reason": str(payload.get("reason") or "web console")[:500],
            "minutes_spent": round(minutes, 1),
            "corrected_bbox": payload.get("corrected_bbox") or None,
        }
        d["decision"] = rec
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        AuditLog(self.workdir / "audit.jsonl").log(
            rec["reviewer"], "review_decision", queue_id, decision=decision,
            defect_type=rec["defect_type"], minutes_spent=rec["minutes_spent"], source="web")
        with self._cache_lock:
            self._cache.pop("review", None)
            self._cache.pop("rounds", None)
        return {"ok": True, "queue_id": queue_id, "decision": rec}

    # ------------------------------------------------------------------ 血缘
    def trace(self, kind: str, ident: str) -> dict[str, Any]:
        lin = self._lineage()
        if lin is None:
            return {"kind": kind, "id": ident, "error": "尚未生成血缘库，请先运行一次闭环"}
        if kind not in ("device", "model", "annotation"):
            raise ValueError("kind 必须是 device / model / annotation")
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", ident or ""):
            raise ValueError("非法的查询 ID")
        with self._lin_lock:
            if kind == "device":
                result: Any = lin.device_model(ident)
                node_id = ident if ident.startswith("device:") else f"device:{ident}"
            elif kind == "model":
                raw = lin.model_data(ident)
                result = {k: {"count": len(v), "sample": v[:12]} for k, v in raw.items()}
                result["_summary"] = {k: len(v) for k, v in raw.items()}
                node_id = ident
            else:
                result = lin.annotation_trust(ident)
                node_id = ident
            node = lin.get(node_id)
            parents = [{"id": x, "kind": (lin.get(x) or {}).get("kind")} for x in lin.parents(node_id)] if node_id else []
            children = [{"id": x, "kind": (lin.get(x) or {}).get("kind")} for x in lin.children(node_id)] if node_id else []
        return {"kind": kind, "id": ident, "node_id": node_id, "node": node,
                "parents": parents[:30], "children": children[:30],
                "result": result}

    def _release_nodes(self) -> list[str]:
        lin = self._lineage()
        if lin is None:
            return []
        with self._lin_lock:
            rows = [r[0] for r in lin.db.execute("SELECT id FROM node WHERE kind='release'")]
        return sorted(rows, key=_num)

    def lineage_counts(self) -> dict[str, int]:
        lin = self._lineage()
        if lin is None:
            return {}
        with self._lin_lock:
            return dict(lin.db.execute("SELECT kind, COUNT(*) FROM node GROUP BY kind").fetchall())

    def lineage_graph(self, focus: str = "", depth: int = 2, max_nodes: int = 90) -> dict[str, Any]:
        lin = self._lineage()
        if lin is None:
            return {"nodes": [], "edges": [], "counts": {}, "truncated": False,
                    "message": "尚未生成血缘库，请先运行一次闭环"}
        depth = max(1, min(4, int(depth)))
        max_nodes = max(10, min(200, int(max_nodes)))
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str], dict[str, Any]] = {}
        truncated = False

        def add(nid: str) -> Optional[dict[str, Any]]:
            nonlocal truncated
            if not nid:
                return None
            if nid in nodes:
                return nodes[nid]
            if len(nodes) >= max_nodes:
                truncated = True
                return None
            with self._lin_lock:
                n = lin.get(nid)
            if not n:
                return None
            label = nid.split(":", 1)[-1] if n["kind"] == "device" else nid
            nodes[nid] = {"id": nid, "kind": n["kind"], "label": label,
                          "label_kind": KIND_LABEL.get(n["kind"], n["kind"]),
                          "attrs": n.get("attrs", {})}
            return nodes[nid]

        def link(parent: str, child: str, rel: str = "derived") -> None:
            if parent in nodes and child in nodes:
                edges[(parent, child)] = {"parent": parent, "child": child, "rel": rel}

        def parents(nid: str) -> list[str]:
            with self._lin_lock:
                return lin.parents(nid)

        def children(nid: str) -> list[str]:
            with self._lin_lock:
                return lin.children(nid)

        if focus:
            with self._lin_lock:
                seed = lin.get(focus)
            if seed is None and not focus.startswith("device:"):
                with self._lin_lock:
                    seed = lin.get(f"device:{focus}")
                if seed is not None:
                    focus = f"device:{focus}"
            if seed is None:
                return {"nodes": [], "edges": [], "counts": self.lineage_counts(),
                        "truncated": False, "message": f"未找到节点 {focus}"}
            add(focus)
            frontier = [focus]
            for _ in range(depth):
                nxt: list[str] = []
                for nid in frontier:
                    for p in parents(nid):
                        if add(p):
                            link(p, nid)
                            nxt.append(p)
                    for c in children(nid):
                        if add(c):
                            link(nid, c, "deployed" if nid.startswith("rel-") else "derived")
                            nxt.append(c)
                frontier = nxt
                if not frontier:
                    break
        else:
            # 默认视图：最近一次发布的「模型 → 数据」主链 + 少量样本，避免上千个标注塞满画布
            rels = self._release_nodes()
            if not rels:
                return {"nodes": [], "edges": [], "counts": self.lineage_counts(),
                        "truncated": False, "message": "血缘库中还没有发布记录"}
            release = rels[-1]
            add(release)
            build_ids = [b for b in parents(release) if b.startswith("build-")]
            build = build_ids[-1] if build_ids else ""
            if build:
                add(build)
                link(build, release)
            model = (parents(build) or [""])[0] if build else ""
            if not model:
                with self._lin_lock:
                    row = lin.db.execute(
                        "SELECT parent FROM edge WHERE child=? AND parent LIKE 'mv-%'", (release,)
                    ).fetchone()
                model = row[0] if row else ""
                if model:
                    add(model)
                    link(model, release, "deployed")
            elif build:
                add(model)
                link(model, build)
            dataset = (parents(model) or [""])[0] if model else ""
            if dataset:
                add(dataset)
                link(dataset, model)
            # 发布设备
            devs = [d for d in children(release) if d.startswith("device:")]
            for d in devs[:8]:
                add(d)
                link(release, d, "deployed")
            # 从数据集抽样标注，再沿链路回溯到 ingest（血缘方向：annotation → dataset，故取 parents）
            anns = [a for a in parents(dataset) if a.startswith("ann-")] if dataset else []
            if anns:
                stride = max(1, len(anns) // 6)
                sample = anns[::stride][:6]
                for ann in sample:
                    if not add(ann):
                        break
                    link(ann, dataset)
                    for p in parents(ann):
                        if not add(p):
                            continue
                        link(p, ann)
                        for gp in parents(p):
                            if not add(gp):
                                continue
                            link(gp, p)
                            for ggp in parents(gp):
                                if not add(ggp):
                                    continue
                                link(ggp, gp)
                                for gggp in parents(ggp):
                                    if not add(gggp):
                                        continue
                                    link(gggp, ggp)
                                    for ggggp in parents(gggp):
                                        if not add(ggggp):
                                            continue
                                        link(ggggp, gggp)
        ordered = sorted(nodes.values(), key=lambda n: (KIND_ORDER.index(n["kind"])
                                                        if n["kind"] in KIND_ORDER else 99, n["id"]))
        return {"nodes": ordered, "edges": list(edges.values()),
                "counts": self.lineage_counts(), "truncated": truncated, "focus": focus}

    def search_nodes(self, q: str, limit: int = 20) -> dict[str, Any]:
        q = (q or "").strip()
        lin = self._lineage()
        out: list[dict[str, Any]] = []
        if lin is not None and q:
            with self._lin_lock:
                rows = lin.db.execute(
                    "SELECT id, kind FROM node WHERE id LIKE ? ORDER BY kind LIMIT ?",
                    (f"%{q}%", max(1, min(100, int(limit)))),).fetchall()
            out = [{"id": i, "kind": k, "label": i.split(":", 1)[-1] if k == "device" else i}
                   for i, k in rows]
            out.sort(key=lambda n: KIND_ORDER.index(n["kind"]) if n["kind"] in KIND_ORDER else 99)
        return {"items": out}

    # ------------------------------------------------------------------ 概况
    def zone_stats(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            out = []
            if not self.lake.is_dir():
                return out
            for z in sorted(self.lake.iterdir()):
                if not z.is_dir():
                    continue
                count = size = 0
                for root, _dirs, files in os.walk(z):
                    for f in files:
                        try:
                            count += 1
                            size += (Path(root) / f).stat().st_size
                        except OSError:
                            continue
                out.append({"zone": z.name, "files": count, "bytes": size})
            return out

        return self._cached("zone-stats", 3.0, build)

    def overview(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            rounds = self.rounds()
            latest = rounds[-1] if rounds else None
            models = self.models()["items"]
            datasets = self.datasets()["items"]
            releases = self.releases()["items"]
            fleet = self.fleet()
            monitor = self.monitor()
            review = self.review()
            audit_total = len(self._audit_rows())
            ann_total = len(self.annotations_index())

            def rc(key: str) -> int:
                return int(((latest or {}).get("counters") or {}).get(key, 0))

            trend = {
                "rounds": [r.get("round") for r in rounds],
                "ingest": [int((r.get("counters") or {}).get("a4.ingest_events", 0)) for r in rounds],
                "confirmed": [int((r.get("counters") or {}).get("b7.confirmed", 0)) for r in rounds],
                "rejected": [int((r.get("counters") or {}).get("b7.rejected", 0)) for r in rounds],
                "d1_auto": [int((r.get("counters") or {}).get("d1.auto_accept", 0)) for r in rounds],
                "d2_human": [int((r.get("counters") or {}).get("d2.human", 0)) for r in rounds],
                "gate_pass_rate": [
                    _ratio(int((r.get("counters") or {}).get("p4.passed", 0)),
                           int((r.get("counters") or {}).get("p4.passed", 0))
                           + int((r.get("counters") or {}).get("p4.rejected", 0)))
                    for r in rounds],
                "psi": [float((r.get("monitor") or {}).get("psi") or 0) for r in rounds],
                "fragmentation": [float((r.get("governance") or {}).get("fragmentation") or 0) for r in rounds],
                "gpu_hours": [float((r.get("cost") or {}).get("gpu_hours") or 0) for r in rounds],
                "human_minutes": [float((r.get("cost") or {}).get("human_minutes") or 0) for r in rounds],
                "l2_calls": [float((r.get("cost") or {}).get("l2_calls") or 0) for r in rounds],
                "detector_accuracy": [(r.get("detector_eval") or {}).get("accuracy") for r in rounds],
                "world_pass_rate": [_ratio((r.get("world") or {}).get("passed", 0), (r.get("world") or {}).get("clips", 0))
                                    if (r.get("world") or {}).get("clips") else None for r in rounds],   # 没生成 = 无数据，不是 0%
            }
            funnel = []
            if latest:
                funnel = [
                    {"label": "接入 ingest", "value": rc("a4.ingest_events")},
                    {"label": "确认缺陷", "value": rc("b7.confirmed")},
                    {"label": "锚点", "value": rc("p2.anchors")},
                    {"label": "生成片段", "value": rc("p3.clips")},
                    {"label": "门禁通过", "value": rc("p4.passed")},
                    {"label": "标注", "value": rc("p5.annotations")},
                ]
            models_sorted = sorted(models, key=lambda m: _num(m["model_version"]), reverse=True)
            latest_model = models_sorted[0] if models_sorted else None
            latest_ds = max((d["dataset_version"] for d in datasets), key=_num, default=None)
            latest_release = releases[-1] if releases else None
            alerts = [a for a in monitor["alerts"]][-12:]
            budget = dict(self.c("cost.budget", {}) or {})
            spent = {
                "gpu_hours": round(sum(trend["gpu_hours"]), 2),
                "human_minutes": round(sum(trend["human_minutes"]), 2),
                "l2_calls": round(sum(trend["l2_calls"]), 2),
            }
            return {
                "workdir": str(self.workdir),
                "config": str(self.config_path),
                "generated_at": time.time(),
                "has_data": bool(rounds or datasets or models),
                "kpi": {
                    "rounds": len(rounds),
                    "datasets": len(datasets),
                    "models": len(models),
                    "releases": len(releases),
                    "devices": fleet["total"],
                    "annotations": ann_total,
                    "audit_events": audit_total,
                    "open_reviews": review["pending_count"],
                    "pipeline_version": "0.1.0",
                },
                "latest_round": latest,
                "trend": trend,
                "funnel": funnel,
                "latest_dataset": latest_ds,
                "latest_model": latest_model,
                "latest_release": latest_release,
                "models": models_sorted[:6],
                "datasets": sorted(datasets, key=lambda d: _num(d["dataset_version"]), reverse=True)[:6],
                "releases": list(reversed(releases))[:6],
                "alerts": alerts,
                "fleet": {
                    "distribution": fleet["distribution"],
                    "fragmentation": fleet["fragmentation"],
                    "latest_version": fleet["latest_version"],
                    "total": fleet["total"],
                    "online": fleet["online"],
                },
                "cost": {"spent": spent, "budget": budget},
                "model_zoo": {
                    "detector": ((latest or {}).get("detector_eval") or {}).get("detector"),
                    "detector_accuracy": ((latest or {}).get("detector_eval") or {}).get("accuracy"),
                    "world": {m: x.get("pass_rate") for m, x in (((latest or {}).get("world") or {}).get("by_model") or {}).items()},
                    "fallbacks": {k: v for k, v in ((latest or {}).get("fallbacks") or {}).items()
                                  if v.get("selected") != v.get("effective")},
                },
                "inbox_pending": self.inbox()["pending"],
                "health": {
                    "eval_passed": bool(((latest_model or {}).get("eval") or {}).get("passed")),
                    "eval_regressions": ((latest_model or {}).get("eval") or {}).get("regressions", []),
                    "drift_alerts": sum(1 for a in alerts if a.get("kind") == "drift"),
                    "rolled_back": sum(1 for r in releases if r["rolled_back"]),
                    "open_reviews": review["pending_count"],
                    "psi": (monitor["psi_series"][-1]["psi"] if monitor["psi_series"] else 0),
                    "fragmentation": fleet["fragmentation"],
                },
            }

        return self._cached("overview", 1.0, build)

    # ------------------------------------------------------------------ 配置 / 系统
    def system(self) -> dict[str, Any]:
        cfg = self.cfg
        try:
            from .. import pipeline as _pipeline  # noqa: F401  注册所有实现
            from ..core.infra import Registry
            impls = Registry.available()
        except Exception:
            impls = {}
        selected = dict(cfg.get("impl", {}) or {})
        # 不在 [impl] 段里选择的接口：检测器 / 深度估计由 [models.*] 决定
        l1_det = cfg_get(cfg, "models.l1.detector")
        if l1_det:
            selected.setdefault("detector", cfg_get(cfg, f"models.detectors.{l1_det}.impl", l1_det))
        selected.setdefault("depth", cfg_get(cfg, "models.depth.impl", "proxy"))
        try:
            described = Registry.describe()
        except Exception:
            described = {}
        rounds = self.rounds()
        effective = (rounds[-1].get("fallbacks") if rounds else None) or {}
        impl_rows = []
        for iface in sorted(impls):
            info = {d["name"]: d for d in described.get(iface, [])}
            impl_rows.append({
                "iface": iface,
                "selected": selected.get(iface),
                "available": impls[iface],
                "missing": {n: d["missing"] for n, d in info.items() if d["missing"]},
                "effective": (effective.get(iface) or {}).get("effective"),
                "fallback_reason": (effective.get(iface) or {}).get("reason", ""),
            })
        try:
            from .. import __version__ as pkg_version
        except Exception:
            pkg_version = "0.1.0"
        return {
            "workdir": str(self.workdir),
            "config": str(self.config_path),
            "version": pkg_version,
            "impl": impl_rows,
            "config_tree": cfg,
            "metrics": METRICS,
            "open_items": OPEN_ITEMS,
            "zones": self.zone_stats(),
            "lineage_counts": self.lineage_counts(),
        }


class RunManager:
    """在后台线程跑闭环；同一个工作目录同时只允许一个任务。"""

    def __init__(self, config_path: Path | str, workdir: Path | str):
        self.config_path = Path(config_path).expanduser().resolve()
        self.workdir = Path(workdir).expanduser().resolve()
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False, "started_at": None, "finished_at": None,
            "rounds": 0, "done": 0, "log": [], "error": None, "last_report": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = dict(self._state)
        st["log"] = list(st.get("log") or [])[-60:]
        if st.get("started_at"):
            end = st.get("finished_at") or time.time()
            st["elapsed"] = round(end - st["started_at"], 1)
        else:
            st["elapsed"] = 0
        st["started_at_iso"] = _iso(st.get("started_at")) if st.get("started_at") else ""
        st["finished_at_iso"] = _iso(st.get("finished_at")) if st.get("finished_at") else ""
        return st

    def start(self, rounds: int = 3, seed_size: int = 60, fresh: bool = False, include_inbox: bool = True) -> bool:
        rounds = max(1, min(30, int(rounds)))
        seed_size = max(0, min(500, int(seed_size)))
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {"running": True, "started_at": time.time(), "finished_at": None,
                           "rounds": rounds, "done": 0, "log": [], "error": None,
                           "last_report": None}
        threading.Thread(target=self._run, args=(rounds, seed_size, bool(fresh), bool(include_inbox)),
                         name="dataota-run", daemon=True).start()
        return True

    def _append(self, line: str) -> None:
        with self._lock:
            self._state["log"].append(f"[{time.strftime('%H:%M:%S')}] {line}")

    def _next_round(self) -> int:
        """已有工作目录时从最后一轮之后续跑，避免 round-01 被覆盖、旧轮次残留。"""
        nums = []
        for p in self._zone_files("reports", "round-*.json"):
            try:
                nums.append(int(p.stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return max(nums) + 1 if nums else 1

    def _zone_files(self, zone: str, pattern: str = "*.json") -> list[Path]:
        base = self.workdir / "lake" / zone
        return sorted(base.glob(pattern)) if base.is_dir() else []

    def _pending_writebacks(self) -> int:
        n = 0
        for p in self._zone_files("review_queue", "ing-*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            n += bool(d.get("decision") and not d.get("applied_defect_id"))
        return n

    def _run(self, rounds: int, seed_size: int, fresh: bool, include_inbox: bool = True) -> None:
        try:
            from ..core.context import Context
            from ..demo_data import load_inbox, make_seed
            from ..pipeline import Pipeline

            if fresh and self.workdir.exists():
                self._append(f"清空工作目录 {self.workdir}")
                inbox = self.workdir / "lake" / "inbox"
                keep = None
                if include_inbox and inbox.is_dir():                     # 清空时保留未消费的上传
                    keep = self.workdir.parent / f".inbox-keep-{int(time.time())}"
                    shutil.move(str(inbox), str(keep))
                shutil.rmtree(self.workdir)
                if keep is not None:
                    (self.workdir / "lake").mkdir(parents=True, exist_ok=True)
                    shutil.move(str(keep), str(inbox))
            ctx = Context.from_config(self.config_path, self.workdir)
            start = self._next_round()
            if start > 1:
                self._append(f"检测到已有 {start - 1} 轮归档，从 R{start} 续跑")
            raws = load_inbox(ctx.lake.path("inbox")) if include_inbox else []
            if raws:
                self._append(f"并入收件箱上传 {len(raws)} 条（user_upload 等真实图像，无模拟真值）")
            if seed_size:
                self._append(f"生成种子数据 {seed_size} 条（渲染 {ctx.c('render.width', 640)}×{ctx.c('render.height', 360)}）")
                tag = f"r{start:02d}-{time.strftime('%H%M%S')}"
                raws += make_seed(ctx.lake.path("raw"), n=seed_size, seed=ctx.c("run.seed", 0), tag=tag,
                                  size=(int(ctx.c("render.width", 640)), int(ctx.c("render.height", 360))),
                                  render=ctx.c("render.enabled", True))
            writebacks = self._pending_writebacks()
            if writebacks:
                self._append(f"审核台有 {writebacks} 条裁决待回写（B6 → B7）")
            if not raws and not writebacks:
                raise ValueError("没有输入数据：种子数据量为 0、收件箱为空，且没有待回写的审核裁决")
            pipe = Pipeline(ctx)
            changed = {k: v for k, v in ctx.fallbacks.items() if v.get("selected") != v.get("effective")}
            for k, v in changed.items():
                self._append(f"实现回退 {k}: {v['selected']} → {v['effective']}（{v['reason']}）")
            for i in range(rounds):
                r = start + i
                self._append(f"开始第 {r} 轮（本次 {i + 1}/{rounds}）")
                rep, nxt = pipe.run_round(r, raws)
                c = rep.counters
                de = rep.detector_eval or {}
                self._append(
                    f"第 {r} 轮完成：接入 {c.get('a4.ingest_events', 0)}，确认 {c.get('b7.confirmed', 0)}，"
                    f"检测器 {de.get('detector', '-')} 准确率 {de.get('accuracy', '-')}，"
                    f"生成 {c.get('p3.clips', 0)} 段 / 门禁通过 {c.get('p4.passed', 0)}，标注 {c.get('p5.annotations', 0)}，"
                    f"模型 {rep.model or '-'}，发布 {(rep.release or {}).get('status', '-')}")
                with self._lock:
                    self._state["done"] = i + 1
                    self._state["last_report"] = {
                        "round": r, "counters": c, "dataset": rep.dataset, "model": rep.model,
                        "eval": rep.eval, "release": rep.release,
                    }
                raws = nxt
            self._append("闭环完成")
        except Exception as exc:  # 后台任务：记录失败原因供前端展示
            with self._lock:
                self._state["error"] = f"{type(exc).__name__}: {exc}"
            self._append(f"失败：{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            with self._lock:
                self._state["running"] = False
                self._state["finished_at"] = time.time()
