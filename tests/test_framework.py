"""框架级测试：契约、状态机、路由、导出、血缘、OTA 回滚、端到端闭环。运行：python -m pytest -q"""
import json
from pathlib import Path

import pytest

from dataota.core.context import Context
from dataota.core.schemas import (Annotation, AudioEvent, BBox, GateStatus, IngestEvent, L1Result, L2Result,
                                  MediaRef, SchemaError, Severity, SourceType, UserClaim)
from dataota.core.states import ANNOTATION_SM, DEFECT_SM, OTA_SM, IllegalTransition, Tracker
from dataota.demo_data import make_seed
from dataota.layers.discovery import route_d1, route_d2
from dataota.layers.flywheel import EXPORTERS, consensus, iou
from dataota.pipeline import Pipeline

CFG = Path(__file__).resolve().parent.parent / "configs" / "demo.toml"


@pytest.fixture
def ctx(tmp_path):
    return Context.from_config(CFG, tmp_path / "run")


def _ev(**kw):
    base = dict(ingest_id="ing-1", source_type=SourceType.CRAWL, media=[MediaRef("a.jpg")], dedup_hash="h")
    base.update(kw)
    return IngestEvent(**base)


# ---------------------------------------------------------------- 契约
def test_schema_roundtrip_and_unknown_field():
    ev = _ev(audio_events=[AudioEvent("alarm", 0, 1, 0.9)])
    assert IngestEvent.from_dict(ev.to_dict()) == ev
    d = ev.to_dict()
    d["oops"] = 1
    with pytest.raises(SchemaError):
        IngestEvent.from_dict(d)


def test_synthetic_hard_constraints():
    a = Annotation("ann-1", "f.jpg", "gen-1", None, "crack", BBox(0.1, 0.1, 0.2, 0.2), "c",
                   synthetic=True, gate_status=GateStatus.PASSED, split="holdout")
    with pytest.raises(SchemaError):          # 合成数据只进训练集
        a.validate()
    a.split, a.gate_status = "train", GateStatus.REJECTED
    with pytest.raises(SchemaError):          # 门禁未过不能进标注
        a.validate()


def test_bbox_out_of_range():
    with pytest.raises(SchemaError):
        BBox(0.9, 0.1, 0.2, 0.1).validate()


# ---------------------------------------------------------------- 状态机
def test_state_machines():
    t = Tracker(DEFECT_SM).to("l1_screened").to("confirmed")
    assert t.done
    with pytest.raises(IllegalTransition):
        Tracker(DEFECT_SM).to("confirmed")
    a = Tracker(ANNOTATION_SM).to("multi_agent_labeled").to("consensus_checked").to("human_review")
    a.to("arbitrated").to("frozen")
    assert a.done
    o = Tracker(OTA_SM)
    for s in ["downloading", "downloaded", "verifying", "verified", "installing", "install_failed",
              "rolling_back", "rolled_back"]:
        o.to(s)
    assert o.state == "rolled_back"
    o2 = Tracker(OTA_SM).to("downloading").to("downloaded").to("verifying")
    assert o2.recover() == "downloaded"


# ---------------------------------------------------------------- 路由
def test_d1_rules(ctx):
    ev = _ev()
    hi = L1Result("m", "crack", 0.95)
    assert route_d1(ctx, hi, None, ev).outcome == "auto_accept"
    assert "claim_conflict" in route_d1(ctx, hi, UserClaim("dent", None, Severity.LOW), ev).reasons
    assert "low_confidence" in route_d1(ctx, L1Result("m", "crack", 0.4), None, ev).reasons
    ev2 = _ev(audio_events=[AudioEvent("alarm", 0, 1, 0.9, urgency=0.9)])
    assert "audio_anomaly_no_visual" in route_d1(ctx, L1Result("m", None, 0.95), None, ev2).reasons


def test_d2_rules(ctx):
    ctx.cfg["routing"]["d2"]["spot_check_rate"] = 0.0
    l1 = L1Result("m", "crack", 0.9)
    ok = L2Result("v", True, "crack", Severity.MEDIUM, 0.95, False)
    assert route_d2(ctx, l1, ok).outcome == "auto_accept"
    hi = L2Result("v", True, "crack", Severity.CRITICAL, 0.95, False)
    assert "high_severity" in route_d2(ctx, l1, hi).reasons
    dis = L2Result("v", True, "dent", Severity.LOW, 0.95, False)
    assert "l1_l2_disagree" in route_d2(ctx, l1, dis).reasons


# ---------------------------------------------------------------- 标注一致性 & 导出
def test_consensus_and_iou():
    from dataota.core.schemas import AgentLabel
    b = BBox(0.1, 0.1, 0.2, 0.2)
    assert iou(b, b) == pytest.approx(1.0)
    labels = [AgentLabel("a", "crack", b), AgentLabel("b", "crack", b), AgentLabel("c", "dent", b)]
    score, rep = consensus(labels, 0.5)
    assert rep.defect_type == "crack" and score == pytest.approx(2 / 3)


def test_dual_format_export(tmp_path):
    a = Annotation("ann-1", "img/x1.jpg", None, "def-1", "crack", BBox(0.1, 0.2, 0.3, 0.4), "图中存在crack缺陷",
                   qa=[{"q": "有缺陷吗？", "a": "有，crack"}])
    cfg = {"image_width": 100, "image_height": 100}
    for fmt, cls in EXPORTERS.items():
        cls().export([a], ["crack"], tmp_path, cfg)
    coco = json.loads((tmp_path / "coco.json").read_text(encoding="utf-8"))
    assert coco["annotations"][0]["bbox"] == pytest.approx([10, 20, 30, 40])
    yolo = (tmp_path / "yolo" / "labels" / "x1.txt").read_text().split()
    assert yolo[0] == "0" and float(yolo[1]) == pytest.approx(0.25)
    sg = json.loads((tmp_path / "sharegpt.json").read_text(encoding="utf-8"))
    assert sg[0]["conversations"][1]["value"] == "有，crack"
    msg = json.loads((tmp_path / "messages.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert msg["messages"][0]["content"][0]["type"] == "image"


# ---------------------------------------------------------------- 端到端
def test_end_to_end_two_rounds(ctx):
    # 本用例验证飞轮与血缘；OTA 故障注入会让首轮在 internal 阶段（2 台设备）偶发整批回滚，
    # 第 2 轮就没有设备回流 —— 回滚路径由 test_ota_rollback_on_high_failure 单独覆盖。
    ctx.cfg["ota"]["failure_injection"] = {}
    seed = make_seed(ctx.lake.path("raw"), n=40, seed=1, size=(320, 180))
    reps = Pipeline(ctx).run(2, seed)
    assert reps[0].dataset == "ds-v001" and reps[0].model == "mv-001"
    assert reps[1].counters.get("a4.ingest_events", 0) > 0            # 第 2 轮来自设备难例回流
    # 血缘三问都能回答
    lin = ctx.lineage
    fleet = json.loads(ctx.lake.path("devices", "fleet.json").read_text(encoding="utf-8"))
    dev = next(d for d in fleet if d["current_version"])
    q1 = lin.device_model(dev["device_id"])
    assert q1["model"] == dev["current_version"]
    q2 = lin.model_data("mv-001")
    assert {"dataset", "annotation", "ingest"} <= set(q2)
    ann = q2["annotation"][0]
    assert "agent_consensus" in lin.annotation_trust(ann)
    # 审计里有门禁和发布记录
    acts = {r["action"] for r in ctx.audit.read()}
    assert {"gate", "eval_gate"} <= acts


def test_ota_rollback_on_high_failure(ctx):
    ctx.cfg["ota"]["failure_injection"] = {"install": 1.0}
    seed = make_seed(ctx.lake.path("raw"), n=30, seed=2, size=(320, 180))
    rep, _ = Pipeline(ctx).run_round(1, seed)
    if rep.release.get("status") not in ("gate_rejected", "no_build"):
        assert rep.release["status"].startswith("rolled_back")
        fleet = json.loads(ctx.lake.path("devices", "fleet.json").read_text(encoding="utf-8"))
        assert all(d["current_version"] is None for d in fleet)       # 首版失败 → 无设备被切换
