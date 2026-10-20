"""命令行入口：python -m dataota <command>

  run      跑 N 轮 Demo 闭环（默认 3 轮）
  web      启动本地可视化控制台（浏览器访问）
  trace    血缘查询：trace device dev-003 / trace model mv-002 / trace annotation ann-xxx
  status   查看工作目录概况（数据集、模型、发布、告警）
  review   列出人工审核队列（impl.reviewer = "queue" 时使用）
  impls    列出已注册的可替换实现（含可选依赖缺失情况）
  models   列出检测模型 / 世界模型目录与可用状态
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .core.context import Context
from .core.infra import Registry
from .demo_data import load_inbox, make_seed
from .pipeline import Pipeline

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "configs" / "demo.toml"


def _ctx(a) -> Context:
    return Context.from_config(a.config, a.workdir)


def cmd_run(a):
    if a.fresh and Path(a.workdir or "runs/demo").exists():
        shutil.rmtree(a.workdir or "runs/demo")
    ctx = _ctx(a)
    size = (int(ctx.c("render.width", 640)), int(ctx.c("render.height", 360)))
    seed = make_seed(ctx.lake.path("raw"), n=a.seed_size, seed=ctx.c("run.seed", 0), size=size,
                     render=ctx.c("render.enabled", True))
    inbox = [] if a.no_inbox else load_inbox(ctx.lake.path("inbox"))
    if inbox:
        print(f"收件箱：{len(inbox)} 条控制台上传并入第 1 轮")
    reps = Pipeline(ctx).run(a.rounds, inbox + seed)
    for r in reps:
        c = r.counters
        print(f"\n=== Round {r.round} ===")
        print(f"  接入 ingest={c.get('a4.ingest_events', 0)}  隔离={c.get('a1.quarantined', 0) + c.get('a2.quarantined', 0)}"
              f"  去重={c.get('a2.dedup_dropped', 0)}")
        print(f"  发现 D1自动={c.get('d1.auto_accept', 0)} 升级L2={c.get('d1.escalate', 0)}"
              f"  D2自动={c.get('d2.auto_accept', 0)} 人工={c.get('d2.human', 0)}"
              f"  → 确认={c.get('b7.confirmed', 0)} 拒绝={c.get('b7.rejected', 0)} 补数据={c.get('b7.needs_more_data', 0)}")
        if r.calibration:
            print(f"  抽检 sampled={r.calibration['sampled']} 自动接受准确率={r.calibration['auto_accept_precision']}")
        de = r.detector_eval or {}
        if de:
            print(f"  检测 {de.get('detector')} 准确率={de.get('accuracy')} mIoU={de.get('mean_iou')}"
                  f" p50={de.get('latency_ms_p50')}ms 参考={de.get('reference_mix')}")
        w = r.world or {}
        if w:
            print("  世界模型 " + " ".join(f"{m}:{x['passed']}/{x['clips']}" for m, x in w.get('by_model', {}).items())
                  + f"  回退={w.get('fallbacks')} 初筛失败={w.get('prescreen_failed')} 降级={w.get('demoted')}")
        print(f"  飞轮 锚点={c.get('p2.anchors', 0)} 生成={c.get('p3.clips', 0)} 门禁通过={c.get('p4.passed', 0)}"
              f" 拒绝={c.get('p4.rejected', 0)}  标注={c.get('p5.annotations', 0)}"
              f"(自动 {c.get('p5.auto', 0)} / 仲裁 {c.get('p5.human', 0)})  N2收窄视角={r.n2.get('feedback_drop_views')}")
        print(f"  训练 {r.dataset} → {r.model}  门禁={'通过' if r.eval.get('passed') else '拒绝'}"
              f" Δ切片={r.eval.get('slices')} {r.eval.get('regressions') or ''}")
        rel = r.release
        print(f"  发布 status={rel.get('status')} 成功={rel.get('success', 0)} 失败={rel.get('failed', 0)}")
        print(f"  监控 PSI={r.monitor.get('psi')} 告警={[x['kind'] + '→' + x['action'] for x in r.monitor.get('alerts', [])]}")
        print(f"  治理 版本分布={r.governance.get('version_distribution')}  碎片率={r.governance.get('fragmentation')}")
        print(f"  成本 {r.cost}")
    print(f"\n工作目录: {ctx.workdir.resolve()}")


def cmd_trace(a):
    ctx = _ctx(a)
    lin = ctx.lineage
    q = {"device": lin.device_model, "model": lin.model_data, "annotation": lin.annotation_trust}[a.kind]
    print(json.dumps(q(a.id), ensure_ascii=False, indent=2))


def cmd_status(a):
    ctx = _ctx(a)
    lake = ctx.lake
    print("数据集:", [p.stem for p in lake.list("datasets", "ds-*.json")])
    print("模型  :", [p.stem for p in lake.list("models", "mv-*.json")])
    for p in lake.list("releases", "rel-*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        print("发布  :", p.stem, d["plan"]["model_version"], d["status"])
    fleet = lake.path("devices", "fleet.json")
    if fleet.exists():
        devs = json.loads(fleet.read_text(encoding="utf-8"))
        print("设备  :", {d["device_id"]: d["current_version"] for d in devs[:8]}, "...")
    print("审计条数:", len(ctx.audit.read()))


def cmd_review(a):
    ctx = _ctx(a)
    items = []
    for p in ctx.lake.list("review_queue", "*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("decision") is None and "priority" in d:
            items.append((d["priority"], p.name, d.get("reasons")))
    for pr, name, reasons in sorted(items, reverse=True):
        print(f"[P{pr:02d}] {name}  {reasons}")
    print(f"共 {len(items)} 条待审。编辑文件中的 decision 字段后重新运行即可回写。")


def cmd_web(a):
    from .core.infra import cfg_get, load_config
    from .web.server import serve

    workdir = a.workdir or cfg_get(load_config(a.config), "run.workdir", "runs/demo")
    serve(config_path=a.config, workdir=workdir, host=a.host, port=a.port,
          open_browser=not a.no_open)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dataota")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CFG))
    ap.add_argument("-w", "--workdir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--rounds", type=int, default=3)
    r.add_argument("--seed-size", type=int, default=60)
    r.add_argument("--fresh", action="store_true", help="清空工作目录后再跑")
    r.add_argument("--no-inbox", action="store_true", help="不并入控制台上传收件箱")
    w = sub.add_parser("web", help="启动本地可视化控制台")
    w.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    t = sub.add_parser("trace")
    t.add_argument("kind", choices=["device", "model", "annotation"])
    t.add_argument("id")
    sub.add_parser("status")
    sub.add_parser("review")
    sub.add_parser("impls")
    sub.add_parser("models", help="列出检测模型 / 世界模型目录与可用状态")
    a = ap.parse_args(argv)
    if a.cmd == "impls":
        from . import pipeline  # noqa: F401  确保所有实现已注册
        print(json.dumps(Registry.describe(), indent=2, ensure_ascii=False))
        return
    if a.cmd == "models":
        from .core.infra import load_config
        from .models import detectors, world
        cfg = load_config(a.config)
        for title, rows in (("检测模型", detectors.describe(cfg)), ("世界模型", world.describe(cfg))):
            print(f"== {title}")
            for x in rows:
                print(f"  {x['name']:<12} {x['impl']:<12} {x['tier']:<5} {x['status']:<15} {x['reason']}")
        return
    {"run": cmd_run, "web": cmd_web, "trace": cmd_trace,
     "status": cmd_status, "review": cmd_review}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
