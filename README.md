# DataOTA

增量视觉数据飞轮 → 设备端算法更新与维护的工程框架（Demo 版骨架）。

- 设计依据：`docs/process-diagram-spec.md`（流程图设计稿 v0.2）、`设备端算法更新与维护技术框架 v0.1.md`
- 框架图：`docs/diagrams/dataota-framework.drawio`

## 快速开始

需要 **Python 3.11+**。核心流水线只用标准库；装上 **numpy + Pillow** 后，Demo 会渲染真实 JPEG，
检测模型、世界模型、几何门禁、场景质量检测都在真实像素上运行（没装时自动回退 mock，并写审计）。

```bash
cd D:\DataOTA
pip install -e .[vision,dev]                  # 可选：numpy + Pillow + pytest
python -m dataota run --rounds 4 --fresh      # 跑 4 轮完整闭环（每轮约 20–40 秒）
python -m dataota status                      # 数据集 / 模型 / 发布 / 设备版本
python -m dataota models                      # 检测模型 / 世界模型目录与可用状态
python -m dataota trace device dev-005        # Q1 设备跑哪版模型
python -m dataota trace model mv-001          # Q2 模型用哪批数据
python -m dataota trace annotation ann-xxxx   # Q3 标注为什么可信
python -m dataota impls                       # 已注册的可替换实现（含缺失的可选依赖）
python -m pytest -q                           # 测试
```

运行产物在 `runs/demo/`：`lake/`（各阶段数据，按 zone 分目录）、`lineage.sqlite`（血缘）、`audit.jsonl`（审计）。

## Web 控制台

```bash
python -m dataota web --port 8000        # 启动后自动打开 http://127.0.0.1:8000
python -m dataota web --port 8000 --no-open
```

零依赖单页控制台（标准库 HTTP + 原生 ES Modules），包含 14 个视图。阈值、类别 / 切片名称都从
`/api/meta`（即 `configs/demo.toml`）读取，前端不再硬编码；图像来自数据湖里的真实帧（`/media`），
旧产物里的占位帧才回退为合成示意图。

| 路由 | 内容 |
| --- | --- |
| `#/overview` | 最近一轮状态带、9 项 KPI、接入/门禁/检测器准确率/世界模型通过率/PSI/成本/碎片率趋势、T8 切片、车队分布 |
| `#/rounds` | L1–L5 五层泳道图，逐节点下钻 23 个流程节点（B2 / P3 显示实际生效的模型），轮次切换与计数器明细 |
| `#/sources` | **数据接入**：逐轮来源构成、隔离 / 去重、设备难例触发原因、A2 场景质量标志、接入事件浏览（真实缩略图）、**上传收件箱** |
| `#/detectors` | **检测模型**：模型库目录与状态、L1 配置、逐轮准确率 / mIoU / 延迟、混淆矩阵、分类别 P/R/F1、D1 升级原因、检测结果回放（检测框 vs 参考框） |
| `#/world` | **世界模型**：后端目录、视角路由链（✓ 为实际生效）、模型 × 视角可用率矩阵、门禁三项度量分布、N2 换模型 / 收窄视角、生成片段逐帧回放（随视角变换的目标框、位姿、门禁明细） |
| `#/datasets` | 版本演进、COCO/YOLO/ShareGPT/messages 导出配置、类别直方图、样本预览 |
| `#/models` | T8 分切片回归、模型版本演进、训练超参、双硬件构建与校验和 |
| `#/fleet` | 20 台设备清单/遥测/OTA 发布单，设备与发布单状态机时间线 |
| `#/monitor` | M11 PSI 漂移、基线分布对比、P95 SLO 趋势、告警回流动作、M12 治理 |
| `#/annotations` | 画廊/表格、分面筛选、bbox 叠加与多智能体一致性（占位帧由标注数据合成示意） |
| `#/lineage` | Q1/Q2/Q3 多列 DAG，深度裁剪、节点搜索、上下游高亮、全链路抽屉 |
| `#/review` | 人工审核台：媒体与 L1 检测框、证据链、裁决；下一轮开始时回写为 defect_event（B6 → B7） |
| `#/audit` | append-only 审计检索、详情抽屉、CSV 导出 |
| `#/system` | 实现注册表（配置选择 vs 实际生效、缺失依赖）、数据分区占用、待定项、指标字典、实时配置树 |

顶部「运行闭环」可直接触发 pipeline：已有归档时自动从下一轮续跑，勾选“清空工作目录”则从 R1 重建；
“并入收件箱上传”把数据接入页上传的图像作为第 1 轮的 A1 用户上传源。

**真实上传的数据流**：上传图像没有模拟真值，mock 的 L2 VLM / oracle 审核员不会代替人判断，
事件会进入 `#/review`；在审核台裁决后，下一次运行开始时写回为 L3 defect_event，再进入世界模型扩充、标注与训练。
人工裁决同时作为检测器评测的参考标签。

## 目录与流程图节点对应

```
dataota/
  models/          模型库（可选依赖 numpy + Pillow，缺失时回退并写审计）
    detectors.py   检测模型：mock / classical / ultralytics(YOLO, RT-DETR) / onnx / torchvision / http / ensemble
    world.py       世界模型：轨迹规划、demo / geometric / http 后端、目录、初筛
    gate.py        P4 几何门禁度量（demo / metrics）与深度估计（proxy / transformers）
    quality.py     A2 场景质量检测（mock / classical）、M11 漂移检测（PSI / JS / KS）
    imaging.py     图像读写、Demo 场景渲染、numpy 图像原语
  core/
    schemas.py     全部数据契约：ingest_event / defect_event / annotation / dataset_version / manifest / receipt ...
    states.py      三类状态机：缺陷事件 / 标注记录 / OTA 升级（含失败与断电恢复）
    lineage.py     C1 版本血缘（SQLite）+ 三个查询问题
    infra.py       配置、DataLake、C4 审计、C3 成本账本、C2 隐私策略、C5 指标字典、插件 Registry
    context.py     运行上下文
  layers/
    ingest.py      L1  A0 设备代理信号 · A1 多源接入 · A2 统一网关 · A3 音频事件 · A4 ingest_event
    discovery.py   L2  B1 用户报告 · B2 L1 小模型 · D1 路由 · B4 L2 大模型 · D2 路由 · B6 人工仲裁 · B7 · B8 · N1
    flywheel.py    L3  P2 入湖 · P3 世界模型扩充 · P4 几何门禁 · N2 失败分析 · P5 多智能体标注 · P6 双格式导出
    release.py     L4  T7 增量训练 · T8 评测门禁 · T9 仓库/压缩/签名 · T10 OTA 灰度/影子/回滚
    monitor.py     L5  M11 遥测/漂移(PSI)/精度停滞/回滚率 → 回流 · M12 版本治理与数据生命周期
  pipeline.py      端到端编排（一轮闭环 + 回流到下一轮），每轮报告含模型调用统计 / 检测器评测 / 世界模型汇总
  demo_data.py     Demo 种子数据（渲染真实 JPEG，带模拟真值 _sim 供 mock 与评测使用）+ 上传收件箱读取
  web/             控制台：api.py（运行 / 资产 / 血缘）、api_models.py（元数据 / 模型库 / 数据源 / 上传）
configs/demo.toml  所有阈值与实现选择；⚠ 标注的是设计文档第 8 节待定项
tests/             契约、状态机、路由、导出、端到端、OTA 回滚；test_models.py：检测 / 世界模型 / 门禁 / 回退 / 控制台接口 / 上传回写
```

## 换成真实实现

每个节点都有抽象接口 + `demo` 实现，用 `@Registry.register("<接口>", "<名字>", requires=(...))` 注册新实现，
再在 `configs/demo.toml` 的 `[impl]` 段切换，流水线代码不用改。所选实现缺少可选依赖时回退到默认实现，
回退写入审计（`impl_fallback`）并显示在 `#/system` 的“实际生效”列。

| 节点 | 接口 | 已有实现 | 下一步 |
| --- | --- | --- | --- |
| A2 | `scene_quality` | `mock`、`classical`（模糊 / 曝光 / 对比度 / 噪声） | 接专用 IQA 模型 |
| B2 | `l1` | `demo`、`detector`（接检测模型库，默认 ensemble）、`yolo`（兼容旧配置） | 在 `[models.detectors]` 登记自训练权重，改 `[models.l1] detector` |
| B4 | `l2` | `demo`、`qwen_vl`（提示词+解析已写，推理调用 TODO） | 补 `_generate`：transformers 或 vLLM |
| B6 | `reviewer` | `oracle`（有模拟真值时自动，否则转审核台）、`queue`（审核台） | UI 审核台已内置（`#/review`），可继续接审核平台 |
| P3 | `world_model` | `demo`、`geometric`（几何视角合成基线）、`http`（生成式模型服务） | 部署 PPMem / InSpatio / Holo4D / DreamX / SANA-WM 服务后填 `endpoint` |
| P4 | `gate` | `demo`（读模拟质量）、`metrics`（真实帧像素度量） | `[models.depth] impl = "transformers"` 换 Depth Anything；加跟踪分割 |
| P5 | `label_agent` | `demo`、`detector`（任一检测器作为标注智能体） | 2–3 个真实 VLM 实例 |
| T7 | `trainer` | `demo`（模拟指标）、`yolo`（目录结构 TODO） | 补齐 Ultralytics 数据目录 |
| T9 | `compiler` | `demo` | ONNX → 目标硬件编译与量化 |
| M11 | `telemetry` | `demo`（PSI / JS / KS 三种漂移检测，`monitor.drift_method` 选告警口径） | 真实设备遥测上报 |

### 检测模型库（`[models.detectors.<名字>]`）

| impl | 依赖 | 说明 |
| --- | --- | --- |
| `mock` | 无 | 读模拟真值加噪声；真实数据（无模拟真值）时弃权 |
| `classical` | numpy、Pillow | 背景差分 + 极性 / 形状 / 颜色规则，冷启动基线，也作为 P5 的异构标注智能体 |
| `ensemble` | 视成员 | WBF / NMS / 投票融合，noisy-OR 计分；成员不一致 → L1 类别不确定；不可用成员自动跳过 |
| `ultralytics` | ultralytics | YOLO / RT-DETR，`weights` + `class_map` |
| `onnx` | onnxruntime | YOLOv8 导出格式，可直接回放 T9 的设备端产物 |
| `torchvision` | torch、torchvision | Faster R-CNN / RetinaNet / FCOS，`weights` 为自训练 state_dict |
| `http` | 无 | 远程推理服务；协议见 `dataota/models/detectors.py` 的 `HttpDetector` |

例：换成自训练 YOLO —— 在 `[models.detectors.yolov8n]` 改 `weights = "path/to/best.pt"`，`pip install -e .[yolo]`，
再把 `[models.l1] detector` 设为 `"yolov8n"`（或放进 ensemble 的 `members`）。

### 世界模型（`[models.world.<名字>]` + `[flywheel.world_models]` 路由）

P3 按视角查路由链，依次尝试候选：未部署 / 缺依赖的跳过（审计 `world_model_unavailable`），生成失败的换下一个（片段记 `fallback_from`）；
N2 发现某模型在某视角上可用率低于 `gate.min_pass_rate`、且有其它可用候选时把它降到链尾（“换模型”），没有可用候选则收窄视角。
生成式模型按 `HttpWorldModel` 的 JSON 协议部署成服务（锚点图 + 轨迹位姿 → 帧 + 每帧目标框），填上 `endpoint` 即接入。
`geometric` 是几何变换基线，不是生成式模型：它在平面目标上很稳定，用来跑通闭环和兜底。

## 已实现的硬约束

- 合成数据只进训练集；门禁没通过不能进标注（`Annotation.validate`）
- T8 分切片不退化 + 污染检查，不通过就进 N2 拒绝池，不会发布
- OTA 每个阶段都按失败率判断，超阈值整批回滚，设备保留上一版本；也会模拟断电后的恢复
- 门禁、仲裁、发布、回滚、过期删除都写入审计日志
- 单轮闭环有成本预算（GPU 时、L2 调用、人工分钟），超了就挂起排队；世界模型按后端的 `gpu_hours_per_clip` 记账
- mock 模型不对真实数据编造结论：无模拟真值的样本由检测器 / 人工判断，检测器评测只用模拟真值或 L2/L3 独立裁决作参考
