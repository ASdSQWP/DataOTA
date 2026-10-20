"""统一数据契约（对应 docs/process-diagram-spec.md 各节点的关键字段）。

只依赖标准库 dataclasses；每个记录都可 to_dict()/from_dict() 落盘为 JSON。
字段命名与设计文档保持一致，改字段时请同步更新文档。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Optional, get_args, get_origin, get_type_hints


class SchemaError(ValueError):
    """Schema 校验失败（A4 / P2 / P6 的退回、隔离、回修分支都基于它）。"""


# ---------------------------------------------------------------- 枚举
class SourceType(str, Enum):
    CRAWL = "crawl"
    USER_UPLOAD = "user_upload"
    VIDEO_STREAM = "video_stream"
    AUDIO = "audio"
    DEVICE = "device"          # A0 设备回流难例


class TriggerReason(str, Enum):  # A0 代理信号
    LOW_CONFIDENCE = "low_confidence"
    SHADOW_DISAGREE = "shadow_disagree"
    SCENE_ANOMALY = "scene_anomaly"
    DOWNSTREAM_CORRECTION = "downstream_correction"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FinalDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NEEDS_MORE_DATA = "needs_more_data"


class GateStatus(str, Enum):
    PASSED = "passed"
    REJECTED = "rejected"


# ---------------------------------------------------------------- 基类
class Record:
    """所有契约记录的基类。"""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(asdict(self))  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        return _from_dict(cls, d)

    def validate(self) -> None:  # 子类按需覆盖
        return None


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


def _from_dict(tp: Any, v: Any) -> Any:
    if v is None:
        return None
    origin = get_origin(tp)
    if origin is list:
        (inner,) = get_args(tp) or (Any,)
        return [_from_dict(inner, x) for x in v]
    if origin is dict:
        return dict(v)
    if origin is not None:  # Optional[X] / Union
        args = [a for a in get_args(tp) if a is not type(None)]
        return _from_dict(args[0], v) if len(args) == 1 else v
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(v)
    if is_dataclass(tp):
        hints = get_type_hints(tp)
        known = {f.name for f in fields(tp)}
        unknown = set(v) - known
        if unknown:
            raise SchemaError(f"{tp.__name__}: 未知字段 {sorted(unknown)}")
        return tp(**{k: _from_dict(hints[k], x) for k, x in v.items()})
    return v


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


# ---------------------------------------------------------------- L1 入口层
@dataclass
class MediaRef(Record):
    uri: str                         # 本地路径 / 对象存储 key
    kind: str = "image"              # image | video | audio
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None


@dataclass
class AudioEvent(Record):            # A3 输出
    label: str                       # 声音事件类别
    t_start: float
    t_end: float
    confidence: float
    transcript: Optional[str] = None  # ASR 文本（质量差时为空）
    urgency: float = 0.0


@dataclass
class HardCase(Record):              # A0 输出：设备难例候选（对应 v0.1 文档 3.1 难例上传）
    device_id: str
    model_version: str
    trigger_reason: TriggerReason
    sample_ref: str
    confidence: float
    scene_tags: dict[str, str] = field(default_factory=dict)
    privacy_flag: bool = False
    timestamp: float = 0.0


@dataclass
class IngestEvent(Record):           # A4 输出
    ingest_id: str
    source_type: SourceType
    media: list[MediaRef]
    user_description: Optional[str] = None
    audio_events: list[AudioEvent] = field(default_factory=list)
    privacy_flag: bool = False       # True = 已脱敏
    dedup_hash: str = ""
    source_meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _require(bool(self.ingest_id), "ingest_id 为空")
        _require(len(self.media) > 0, "media 为空")
        _require(bool(self.dedup_hash), "dedup_hash 为空")


# ---------------------------------------------------------------- L2 发现层
@dataclass
class BBox(Record):
    x: float                          # 归一化 [0,1]，左上角
    y: float
    w: float
    h: float

    def validate(self) -> None:
        for v in (self.x, self.y, self.w, self.h):
            _require(0.0 <= v <= 1.0, f"bbox 越界: {self}")
        _require(self.x + self.w <= 1.0 + 1e-6 and self.y + self.h <= 1.0 + 1e-6, f"bbox 越界: {self}")


@dataclass
class Detection(Record):             # 检测模型统一输出（B2 / P5 检测型智能体共用）
    label: str
    score: float                     # 0~1，已校准的置信度
    bbox: BBox
    model: str = ""
    support: float = 1.0             # 集成检测时支持该框的成员比例


@dataclass
class SceneQuality(Record):          # 场景质量检测（A2 / A0 场景异常 / D1 困难切片）
    blur: float                      # 0~1，越高越模糊
    exposure: float                  # -1~1，负为欠曝、正为过曝
    contrast: float                  # 0~1
    noise: float                     # 0~1
    score: float                     # 0~1 综合质量
    flags: list[str] = field(default_factory=list)   # blurry / underexposed / overexposed / low_contrast / noisy
    model: str = ""


@dataclass
class UserClaim(Record):             # B1 输出
    defect_type: Optional[str]
    location_hint: Optional[str]
    severity_subjective: Optional[Severity]
    suspicious: bool = False         # 恶意报告标记


@dataclass
class L1Result(Record):              # B2 输出
    model: str
    defect_type: Optional[str]
    confidence: float
    bbox: Optional[BBox] = None
    category_uncertain: bool = False
    hard_slice: bool = False          # 困难切片（夜间/遮挡/远距等）
    available: bool = True            # L1 不可用时 False，直接升级 L2
    detections: list[Detection] = field(default_factory=list)   # 检测器全部候选框（含低分）
    scene_quality: Optional[SceneQuality] = None
    latency_ms: float = 0.0


@dataclass
class L2Result(Record):              # B4 输出
    model: str
    is_real: bool
    defect_type: Optional[str]
    severity: Severity
    confidence: float
    self_uncertain: bool
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""
    long_tail: bool = False


@dataclass
class HumanDecision(Record):         # B6 / P5 人工仲裁输出
    reviewer: str
    decision: FinalDecision
    defect_type: Optional[str]
    severity: Optional[Severity]
    reason: str
    minutes_spent: float
    corrected_bbox: Optional[BBox] = None


@dataclass
class RouteStep(Record):
    router: str                      # D1 / D2 / sampling
    outcome: str                     # auto_accept / escalate / human
    reasons: list[str] = field(default_factory=list)


@dataclass
class DefectEvent(Record):           # B7 输出
    defect_id: str
    ingest_id: str
    level_reached: str               # L0 / L1 / L2 / L3
    defect_type: Optional[str]
    location: Optional[BBox]
    severity: Optional[Severity]
    confidence: float
    evidence: list[str]
    routing_trace: list[RouteStep]
    final_decision: FinalDecision
    human_decision: Optional[HumanDecision] = None
    state: str = "reported"          # 见 core.states.DEFECT_SM
    l1_result: Optional[L1Result] = None   # 留存检测器原始输出，供检测模型评测与前端回放
    l2_result: Optional[L2Result] = None

    def validate(self) -> None:
        _require(bool(self.defect_id), "defect_id 为空")
        if self.location:
            self.location.validate()


# ---------------------------------------------------------------- L3 云端飞轮
@dataclass
class Anchor(Record):                # P3 输入：扩充锚点
    anchor_id: str
    defect_id: str
    media_uri: str
    scene_tags: dict[str, str] = field(default_factory=dict)
    missing_views: list[str] = field(default_factory=list)
    bbox: Optional[BBox] = None      # 缺陷在锚点图上的位置，世界模型据此计算各帧目标框


@dataclass
class CameraPose(Record):            # P3 轨迹上的一个相机位姿（相对锚点）
    yaw: float = 0.0                 # 度
    pitch: float = 0.0               # 度
    distance: float = 1.0            # 相对锚点的距离倍数
    light: float = 1.0               # 光照系数，1 为锚点光照
    occlusion: float = 0.0           # 遮挡比例 0~1


@dataclass
class Trajectory(Record):            # P3 轨迹生成输出
    view: str
    poses: list[CameraPose]


@dataclass
class GeneratedClip(Record):         # P3 输出
    clip_id: str
    anchor_id: str
    generator: str                   # 世界模型名称/版本
    trajectory: str                  # 视角轨迹描述
    frames: list[str]
    synthetic: bool = True           # 硬约束：合成数据只进训练集
    meta: dict[str, Any] = field(default_factory=dict)
    poses: list[CameraPose] = field(default_factory=list)
    frame_boxes: list[Optional[BBox]] = field(default_factory=list)   # 目标框随视角变换后的位置；None = 出画


@dataclass
class GateResult(Record):            # P4 输出
    clip_id: str
    depth_continuity: float
    shape_stability: float
    texture_flicker: float           # 越低越好，评分时取 1 - flicker
    gate_score: float
    gate_status: GateStatus
    reasons: list[str] = field(default_factory=list)
    generator: str = ""
    view: str = ""
    source: str = ""                 # pixels = 基于真实帧像素计算；sim = 读取模拟质量


@dataclass
class AgentLabel(Record):
    agent: str
    defect_type: Optional[str]
    bbox: Optional[BBox]
    caption: str = ""


@dataclass
class Annotation(Record):            # P5 输出：统一 schema 标注（一份事实，两种导出）
    annotation_id: str
    frame_uri: str
    source_clip_id: Optional[str]    # 真实样本为 None
    defect_id: Optional[str]
    defect_type: Optional[str]
    bbox: Optional[BBox]
    caption: str                     # VLM 语义描述
    qa: list[dict[str, str]] = field(default_factory=list)   # [{"q":..,"a":..}]
    synthetic: bool = False
    agent_labels: list[AgentLabel] = field(default_factory=list)
    agent_consensus: float = 0.0     # 0~1
    human_decision: Optional[HumanDecision] = None
    gate_status: Optional[GateStatus] = None
    state: str = "draft"             # 见 core.states.ANNOTATION_SM
    split: str = "train"             # train / holdout；synthetic 必须是 train

    def validate(self) -> None:
        _require(bool(self.annotation_id), "annotation_id 为空")
        if self.bbox:
            self.bbox.validate()
        _require(not (self.synthetic and self.split != "train"), "合成数据只能进训练集")
        _require(not self.synthetic or self.gate_status == GateStatus.PASSED, "合成数据未通过几何门禁")


@dataclass
class DatasetVersion(Record):        # P6 输出
    dataset_version: str
    formats: list[str]
    export_config: dict[str, Any]
    sample_list: list[str]           # annotation_id 列表
    schema_version: str
    exporter_version: str
    lineage: dict[str, list[str]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- L4 训练发布
@dataclass
class ModelVersion(Record):          # T7 输出
    model_version: str
    base_model: str
    dataset_version: str
    train_config: dict[str, Any]
    metrics: dict[str, float]
    cost: dict[str, float] = field(default_factory=dict)


@dataclass
class EvalReport(Record):            # T8 输出
    model_version: str
    baseline_version: str
    eval_set: str
    slices: dict[str, dict[str, float]]    # slice -> {"candidate":..,"baseline":..}
    passed: bool
    regressions: list[str]
    contamination_ok: bool
    regression_threshold: float = 0.0


@dataclass
class ReleasePlan(Record):           # T8 输出：发布单
    release_id: str
    model_version: str
    eval_slices: list[str]
    regression_threshold: float
    rollout_plan: list[dict[str, Any]]      # [{"stage":"internal","ratio":..,"observe_h":..}]
    rollback_threshold: dict[str, float]
    signature: str = ""


@dataclass
class Manifest(Record):              # T9 输出：模型包清单（对应 v0.1 文档 3.2）
    model_id: str
    version: str
    build_id: str
    dataset_version: str
    hardware_target: str
    runtime_version: str
    min_firmware: str
    checksum: str
    signature: str
    metrics_snapshot: dict[str, float]
    package_uri: str
    delta_from: Optional[str] = None


@dataclass
class UpgradeReceipt(Record):        # T10 输出
    device_id: str
    release_id: str
    build_id: str
    from_version: Optional[str]
    to_version: str
    final_state: str
    success: bool
    reason: str = ""
    duration_s: float = 0.0
    history: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- L5 监控
@dataclass
class Telemetry(Record):             # 设备遥测（对应 v0.1 文档 3.4）
    device_id: str
    model_version: str
    latency_ms_p50: float
    latency_ms_p95: float
    mem_mb: float
    confidence_hist: list[float]     # 10 桶归一化直方图
    hard_case_rate: float
    offline_hours: float = 0.0
    shadow_disagree_rate: float = 0.0


@dataclass
class Alert(Record):                 # M11 输出
    kind: str                        # drift / plateau / latency / rollback_rate / fragmentation
    message: str
    action: str                      # reflow_hard_cases / rescreen / add_anchors / force_upgrade
    payload: dict[str, Any] = field(default_factory=dict)
