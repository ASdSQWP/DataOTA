"""C1 版本血缘：ingest → defect → anchor → generated → gate → annotation
→ dataset → model → build → release → device。SQLite 单文件，Demo 足够，生产可换图数据库。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

KINDS = ("ingest", "defect", "anchor", "generated", "gate", "annotation",
         "dataset", "model", "build", "release", "device")


class LineageStore:
    def __init__(self, path: Path | str, check_same_thread: bool = True):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # 控制台会在 HTTP 线程里读同一个库：允许跨线程 + 等锁，避免并发写时直接报错。
        self.db = sqlite3.connect(self.path, check_same_thread=check_same_thread)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS node(id TEXT PRIMARY KEY, kind TEXT, attrs TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS edge(parent TEXT, child TEXT, rel TEXT, ts REAL,
                                            PRIMARY KEY(parent, child));
            CREATE INDEX IF NOT EXISTS edge_child ON edge(child);
            """
        )

    def close(self) -> None:
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    # ----------------------------------------------------------- 写
    def node(self, nid: str, kind: str, **attrs: Any) -> str:
        if kind not in KINDS:
            raise ValueError(f"未知血缘节点类型 {kind}")
        old = self.get(nid)
        merged = {**(old["attrs"] if old else {}), **attrs}
        self.db.execute("INSERT OR REPLACE INTO node VALUES (?,?,?,?)",
                        (nid, kind, json.dumps(merged, ensure_ascii=False), time.time()))
        self.db.commit()
        return nid

    def link(self, parent: str, child: str, rel: str = "derived") -> None:
        self.db.execute("INSERT OR IGNORE INTO edge VALUES (?,?,?,?)", (parent, child, rel, time.time()))
        self.db.commit()

    def links(self, parents: Iterable[str], child: str, rel: str = "derived") -> None:
        for p in parents:
            self.link(p, child, rel)

    # ----------------------------------------------------------- 读
    def get(self, nid: str) -> Optional[dict[str, Any]]:
        r = self.db.execute("SELECT id, kind, attrs FROM node WHERE id=?", (nid,)).fetchone()
        return {"id": r[0], "kind": r[1], "attrs": json.loads(r[2])} if r else None

    def parents(self, nid: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT parent FROM edge WHERE child=? ORDER BY ts", (nid,))]

    def children(self, nid: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT child FROM edge WHERE parent=? ORDER BY ts", (nid,))]

    def ancestors(self, nid: str, kinds: Optional[set[str]] = None) -> list[dict[str, Any]]:
        seen, out, stack = set(), [], [nid]
        while stack:
            cur = stack.pop()
            for p in self.parents(cur):
                if p in seen:
                    continue
                seen.add(p)
                n = self.get(p)
                if n and (kinds is None or n["kind"] in kinds):
                    out.append(n)
                stack.append(p)
        return out

    # ----------------------------------------------------------- 三个问题（spec 第 6 节）
    def device_model(self, device_id: str) -> dict[str, Any]:
        """Q1 设备跑哪版模型？device → release → build → model"""
        dev = self.get(f"device:{device_id}")
        if not dev:
            return {}
        a = dev["attrs"]
        rel = a.get("current_release")
        builds = [self.get(b) for b in self.parents(rel)] if rel else []
        build = next((b for b in builds if b and b["attrs"].get("target") == a.get("hardware")), None)
        model = self.parents(build["id"])[0] if build else None
        return {"device": device_id, "release": rel, "build": build["id"] if build else None, "model": model,
                "current_version": a.get("current_version"), "previous_version": a.get("previous_version")}

    def model_data(self, model_version: str) -> dict[str, list[str]]:
        """Q2 模型用哪批数据？model → dataset → annotation → generated → anchor → ingest"""
        out: dict[str, list[str]] = {}
        for n in self.ancestors(model_version):
            out.setdefault(n["kind"], []).append(n["id"])
        return out

    def annotation_trust(self, annotation_id: str) -> dict[str, Any]:
        """Q3 标注为什么可信？annotation → gate_status + agent_consensus + human_decision"""
        n = self.get(annotation_id)
        if not n:
            return {}
        gates = [g["attrs"] for g in self.ancestors(annotation_id, {"gate"})]
        return {
            "annotation": annotation_id,
            "agent_consensus": n["attrs"].get("agent_consensus"),
            "human_decision": n["attrs"].get("human_decision"),
            "gate": gates[0] if gates else None,
            "synthetic": n["attrs"].get("synthetic"),
        }
