"""模型库测试：检测模型 / 集成融合 / 回退、世界模型轨迹与路由、像素门禁、N2 换模型、漂移检测、
控制台接口、上传收件箱 → 审核台 → B6→B7 回写。运行：python -m pytest -q"""
import base64
import json
import random
from pathlib import Path

import pytest

from dataota.core.context import Context
from dataota.core.infra import Registry
from dataota.core.schemas import (Anchor, BBox, DefectEvent, Detection, FinalDecision, GateResult, GateStatus,
                                  GeneratedClip)
from dataota.demo_data import load_inbox, make_seed
from dataota.layers.flywheel import Expansion, analyze_rejections
from dataota.models import detectors, gate, imaging, quality, world
from dataota.pipeline import Pipeline
from dataota.web.api import Dashboard

CFG = Path(__file__).resolve().parent.parent / "configs" / "demo.toml"
vision = pytest.mark.skipif(not imaging.AVAILABLE, reason="需要 numpy + Pillow（pip install -e .[vision]）")


class _Ctx:                                   # 检测器只用到 rng 的最小上下文
    rng = random.Random(0)


@pytest.fixture
def ctx(tmp_path):
    c = Context.from_config(CFG, tmp_path / "run")
    c.cfg["render"].update(width=320, height=180)
    return c


def _defect(defect_type="crack", bbox=None) -> DefectEvent:
    return DefectEvent("def-1", "ing-1", "L2", defect_type, bbox or BBox(0.4, 0.4, 0.2, 0.2), None, 0.9, [], [],
                       FinalDecision.CONFIRMED)


# ---------------------------------------------------------------- 检测模型
@vision
def test_classical_detector_on_rendered_defects(tmp_path):
    det = detectors.ClassicalDetector()
    bb = {"x": 0.3, "y": 0.3, "w": 0.25, "h": 0.25}
    hits = 0
    for i, dt in enumerate(["crack", "scratch", "rust", "crack", "scratch", "rust"]):
        p = imaging.render_to(tmp_path / f"{i}.jpg", i, (640, 360), {"defect_type": dt, "bbox": bb, "difficulty": 0.1})
        top = (det.detect(_Ctx, p) or [None])[0]
        hits += bool(top and top.label == dt and top.score >= 0.5 and detectors.iou(top.bbox, BBox(**bb)) > 0.2)
    assert hits >= 5
    clean = imaging.render_to(tmp_path / "clean.jpg", 99, (640, 360), {"defect_type": None, "difficulty": 0.1})
    assert all(d.score < 0.5 for d in det.detect(_Ctx, clean))
    with pytest.raises(FileNotFoundError):                      # 占位文件不是图像
        (tmp_path / "ph.jpg").write_bytes(b"placeholder")
        det.detect(_Ctx, str(tmp_path / "ph.jpg"))


def test_mock_detector_abstains_without_truth():
    assert detectors.MockDetector().detect(_Ctx, "x.jpg", {"_sim": {}}) == []
    out = detectors.MockDetector().detect(_Ctx, "x.jpg", {"_sim": {"defect_type": "crack", "difficulty": 0.0,
                                                                  "bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}})
    assert out and out[0].label == "crack"


class _Fixed(detectors.Detector):
    impl = "fixed"

    def __init__(self, name, dets):
        super().__init__(name)
        self.dets = dets

    def detect(self, ctx, image, hint=None):
        return self.dets


def test_ensemble_noisy_or_support_and_vote():
    b = BBox(0.1, 0.1, 0.2, 0.2)
    a = _Fixed("a", [Detection("crack", 0.7, b)])
    c = _Fixed("b", [Detection("crack", 0.8, BBox(0.11, 0.1, 0.2, 0.2)), Detection("dent", 0.6, BBox(0.6, 0.6, 0.1, 0.1))])
    out = {d.label: d for d in detectors.EnsembleDetector(name="e", members=[a, c]).detect(None, "x")}
    assert out["crack"].support == 1.0 and out["crack"].score == pytest.approx(1 - 0.3 * 0.2, abs=1e-3)
    assert out["dent"].support == 0.5 and out["dent"].score == pytest.approx(0.6, abs=1e-3)
    voted = detectors.EnsembleDetector(name="v", members=[a, c], fusion="vote", min_votes=2).detect(None, "x")
    assert [d.label for d in voted] == ["crack"]


def test_detector_catalog_and_fallback(ctx):
    det, reason = detectors.build_with_fallback(ctx, "remote")       # http 未配置 endpoint
    assert det.name == "mock" and "endpoint" in reason
    rows = {d["name"]: d for d in detectors.describe(ctx.cfg)}
    assert rows["remote"]["status"] == "not_configured" and rows["mock"]["status"] == "ready"
    assert any(r["action"] == "model_fallback" for r in ctx.audit.read())


def test_impl_falls_back_when_dependency_missing(ctx, monkeypatch):
    real = Registry.missing
    monkeypatch.setattr(Registry, "missing",
                        classmethod(lambda cls, i, n: ["numpy"] if (i, n) == ("gate", "metrics") else real(i, n)))
    g = ctx.make("gate")
    assert type(g).__name__ == "SelfConsistencyGate"
    assert ctx.fallbacks["gate"] == {"selected": "metrics", "effective": "demo", "reason": "缺少依赖 numpy"}
    assert any(r["action"] == "impl_fallback" for r in ctx.audit.read())


@vision
def test_scene_quality_flags(tmp_path):
    q = quality.ClassicalSceneQuality()
    sim = {"defect_type": None, "difficulty": 0.1}
    day = q.assess(None, imaging.render_to(tmp_path / "d.jpg", 1, (640, 360), sim, {"light": "day"}))
    night = q.assess(None, imaging.render_to(tmp_path / "n.jpg", 1, (640, 360), sim, {"light": "night"}))
    blurry = q.assess(None, imaging.render_to(tmp_path / "b.jpg", 1, (640, 360), {**sim, "difficulty": 0.85}))
    assert "underexposed" in night.flags and "underexposed" not in day.flags
    assert blurry.blur > day.blur and day.score > night.score


def test_drift_metrics():
    base = [0.1] * 10
    assert quality.psi(base, base) == pytest.approx(0, abs=1e-9)
    assert quality.js_divergence(base, base) == pytest.approx(0, abs=1e-9)
    shifted = [0.0] * 5 + [0.2] * 5
    assert quality.ks_distance(base, shifted) == pytest.approx(0.5)
    assert 0 < quality.js_divergence(base, shifted) <= 1


# ---------------------------------------------------------------- 世界模型
def test_trajectory_plan():
    assert [p.yaw for p in world.plan_trajectory("yaw-30", 3).poses] == [-10, -20, -30]
    assert world.plan_trajectory("far", 2).poses[-1].distance == 2.0
    assert world.plan_trajectory("night", 2).poses[-1].light == pytest.approx(0.3)


@vision
def test_geometric_world_model_and_pixel_gate(ctx, tmp_path):
    p = imaging.render_to(tmp_path / "a.jpg", 1, (320, 180),
                          {"defect_type": "crack", "bbox": {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}, "difficulty": 0.1})
    anc = Anchor("anc-1", "def-1", p, bbox=BBox(0.4, 0.4, 0.2, 0.2))
    far = world.GeometricWM(name="geometric").generate(ctx, anc, world.plan_trajectory("far", 3), tmp_path)
    assert len(far.frames) == 3 and all(imaging.is_image_file(f) for f in far.frames)
    assert far.frame_boxes[-1].w < 0.2 * 0.6                          # 拉远 2 倍：目标框缩到约一半
    assert world.prescreen(far, 3)["ok"]
    m = gate.PixelMetricsGate().measure(ctx, far)
    assert m["source"] == "pixels"
    assert all(0 <= m[k] <= 1 for k in ("depth_continuity", "shape_stability", "texture_flicker"))


def test_router_skips_unavailable_and_falls_back_on_failure(ctx, tmp_path):
    @Registry.register("world_model", "always_fail")
    class Boom(world.WorldModel):
        impl = "always_fail"

        def generate(self, ctx, anchor, traj, out_dir):
            raise RuntimeError("boom")

    ctx.cfg["models"]["world"]["boom"] = {"impl": "always_fail"}
    ctx.cfg["flywheel"]["world_models"] = {"default": ["ppmem", "boom", "demo"]}
    anc = Anchor("anc-1", "def-1", str(tmp_path / "x.jpg"), missing_views=["yaw-30"], bbox=BBox(0.4, 0.4, 0.2, 0.2))
    clips = Expansion(ctx).run(ctx, [(anc, _defect())])
    clip = clips[0][0]
    assert clip.meta["model"] == "demo" and clip.meta["fallback_from"] == ["boom:RuntimeError"]
    assert ctx.counters["p3.fallback"] == 1
    assert [r["target"] for r in ctx.audit.read() if r["action"] == "world_model_unavailable"] == ["ppmem"]
    assert ctx.fallbacks["world_model"]["effective"] == "boom"            # 默认链上第一个可构建的后端


def test_n2_switches_model_then_narrows_view(ctx):
    def clip(i, view):
        return GeneratedClip(f"gen-{i}", "anc", "geometric", view, [], meta={"model": "geometric"})

    def gr(i, ok):
        return GateResult(f"gen-{i}", 0.9, 0.9, 0.1, 0.9 if ok else 0.3,
                          GateStatus.PASSED if ok else GateStatus.REJECTED, [] if ok else ["score<0.7"])

    rejected = [(clip(i, "night"), gr(i, False)) for i in range(5)]
    passed = [(clip(10 + i, "yaw-30"), None, gr(10 + i, True)) for i in range(5)]
    ctx.cfg["flywheel"]["world_models"] = {"default": ["geometric", "demo"]}
    rep = analyze_rejections(ctx, rejected, 10, passed)
    assert rep["feedback_switch_models"] == [{"view": "night", "model": "geometric", "pass_rate": 0.0}]
    ctx.cfg["flywheel"]["world_models"] = {"default": ["geometric", "ppmem"]}   # 候选未部署 = 没得换 → 收窄视角
    rep2 = analyze_rejections(ctx, rejected, 10, passed)
    assert rep2["feedback_switch_models"] == [] and "night" in rep2["feedback_drop_views"]


def test_demoted_model_is_last_resort_not_removed(ctx, tmp_path):
    ctx.cfg["flywheel"]["world_models"] = {"default": ["demo", "ppmem"]}
    anc = Anchor("anc-1", "def-1", str(tmp_path / "x.jpg"), missing_views=["night"])
    clips = Expansion(ctx).run(ctx, [(anc, _defect(bbox=None))], demoted={("night", "demo")})
    assert clips and clips[0][0].meta["model"] == "demo"                  # ppmem 不可用，降级的 demo 兜底
    assert clips[0][0].frame_boxes == [] and clips[0][0].meta["prescreen"]["ok"]   # 无缺陷位置不编造目标框


# ---------------------------------------------------------------- 端到端 + 控制台接口
def test_round_report_and_console_api(ctx):
    ctx.cfg["flywheel"]["missing_views"] = ["yaw-30", "far"]
    seed = make_seed(ctx.lake.path("raw"), n=16, seed=3, size=(320, 180))
    rep, _ = Pipeline(ctx).run_round(1, seed)
    assert rep.detector_eval["events"] > 0 and rep.detector_eval["reference_mix"].get("sim_truth")
    assert rep.world["clips"] == rep.counters.get("p3.clips", 0)
    assert "detector" in {m["kind"] for m in rep.models}
    assert rep.fallbacks["l1"]["selected"] == "detector"
    d = Dashboard(ctx.workdir, CFG)
    for name in ("meta", "zoo", "world", "detectors", "sources", "overview", "system", "inbox"):
        assert isinstance(getattr(d, name)(), dict)
    assert d.meta()["thresholds"]["psi"] == ctx.c("monitor.psi_alert")
    clips = d.clips(size=200)
    assert clips["total"] == rep.world["clips"]
    if clips["items"]:
        detail = d.clip_detail(clips["items"][0]["clip_id"])
        assert len(detail["frames"]) == ctx.c("flywheel.frames_per_clip")
    dets = d.detections(size=200)
    assert dets["total"] == sum(rep.counters.get(f"b7.{k}", 0) for k in ("confirmed", "rejected", "needs_more_data"))
    assert d.ingest_events()["total"] == rep.counters["a4.ingest_events"]


def test_upload_goes_to_review_then_writes_back(ctx, tmp_path):
    d = Dashboard(ctx.workdir, CFG)
    with pytest.raises(ValueError):
        d.upload({"data": base64.b64encode(b"not an image").decode()})
    if imaging.AVAILABLE:
        img = imaging.render_to(tmp_path / "u.jpg", 7, (320, 180),
                                {"defect_type": "rust", "bbox": {"x": 0.5, "y": 0.4, "w": 0.2, "h": 0.2}, "difficulty": 0.1})
        blob = Path(img).read_bytes()
    else:
        blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    d.upload({"data": base64.b64encode(blob).decode(), "description": "顶部生锈了", "source_type": "user_upload"})
    raws = load_inbox(ctx.lake.path("inbox"))
    assert len(raws) == 1 and load_inbox(ctx.lake.path("inbox")) == []     # 读取即标记已消费
    pipe = Pipeline(ctx)
    rep1, _ = pipe.run_round(1, raws)
    assert rep1.counters.get("b6.real_to_queue") == 1                       # 没有模拟真值：oracle 不代判
    q = d.review()
    assert q["pending_count"] == 1
    d.submit_review(q["pending"][0]["queue_id"], {"decision": "confirmed", "defect_type": "rust", "severity": "medium"})
    rep2, _ = pipe.run_round(2, [])
    assert rep2.counters.get("b6.applied") == 1 and rep2.counters.get("b7.confirmed") == 1
    de = json.loads(next(ctx.lake.path("defects").glob("def-*.json")).read_text(encoding="utf-8"))
    assert de["level_reached"] == "L3" and de["human_decision"]["reviewer"] == "web-reviewer"
    rep3, _ = pipe.run_round(3, [])
    assert not rep3.counters.get("b6.applied")                              # 只回写一次
