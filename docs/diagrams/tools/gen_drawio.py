#!/usr/bin/env python3
"""Generate docs/diagrams/dataota-framework.drawio (multi-page, editable)."""
from xml.sax.saxutils import escape
import sys

OUT = sys.argv[1]

# ---------- palette ----------
LANE_COL = {
    "L1": ("#e7f1ff", "#4a7bd0"),
    "L2": ("#eef8ee", "#3f9142"),
    "L3": ("#f3edff", "#7a52c7"),
    "L4": ("#fff3e6", "#d9822b"),
    "L5": ("#ffeef0", "#c94a5a"),
}
LANE_TITLE = {
    "L1": "L1 数据源与接入",
    "L2": "L2 缺陷发现分级",
    "L3": "L3 云端飞轮",
    "L4": "L4 训练发布",
    "L5": "L5 设备与监控",
}
BLUE, ORANGE, RED, GREY = "#1f6feb", "#e8590c", "#d6336c", "#868e96"

# ---------- node data ----------
# id: label, output, kind, demo, prod, warn, tip(处理要点 | 异常)
N = {
 "SRC": ("数据源", "爬取 / 上传 / 视频流 / 音频", "src", None, None, False, "多源原始数据"),
 "A0": ("设备端推理与代理信号", "难例候选", "proc", "离线回放", "真实设备", False,
        "低置信/分歧/场景异常/下游纠正触发难例 | 影子崩溃不影响主推理"),
 "A1": ("多源数据接入", "原始数据包", "proc", "本地目录+一段离线流", "接入网关", False,
        "认证、转码、元数据、来源标记 | 版权不明隔离；流断流记录"),
 "A2": ("统一接入网关", "标准化媒体", "proc", "脚本", "转码集群", True,
        "标准化、合规脱敏、去重、元数据补全 | 隐私不合规先脱敏/隔离 | ⚠图像/音频能否离开设备"),
 "A3": ("音频事件处理", "audio_events[]", "proc", "单段音频", "音频管线", False,
        "ASR、声音事件分类、时间戳对齐、紧急度 | 质量差只保留事件分类"),
 "A4": ("生成 ingest_event", "ingest_event", "proc", None, None, False,
        "封装统一接入事件 | Schema 校验失败退回"),
 "B1": ("L0 用户报告", "user_claim", "proc", "手工构造", "上传接口+解析", False,
        "解析缺陷类型/位置/主观严重度 | 恶意报告标记"),
 "B2": ("L1 小模型筛查", "l1_result", "proc", "YOLO / RT-DETR", "多任务小模型", False,
        "快速检测疑似缺陷、类别、粗位置 | 不可用则跳 L2 并记录"),
 "D1": ("L1 路由", "", "dec", "脚本阈值", "规则引擎", True,
        "高置信且一致→自动接受；否则升级 | ⚠升级阈值待定"),
 "B4": ("L2 大模型分析", "l2_result", "proc", "Qwen2.5-VL-3B", "大模型集群", False,
        "判定真实性、类型、严重度、证据、reasoning | 成本超限按优先级排队"),
 "D2": ("L2 路由", "", "dec", "规则", "在线校准+AB", True,
        "高置信一致→自动接受；困难/高严重度→人工；抽检命中也升级 | ⚠阈值待定"),
 "B6": ("L3 人工仲裁", "human_decision", "proc", "本地审核台 10–20 条", "审核平台", True,
        "最终裁决、修正标注、仲裁理由、人工分钟 | 积压按严重度+分歧度排序 | ⚠人工比例分母"),
 "B7": ("生成 defect_event", "defect_event", "proc", None, None, False,
        "封装缺陷事件与证据 | 需更多数据→触发补锚点"),
 "B8": ("抽检与校准", "校准记录", "proc", "人工抽检", "持续校准", False,
        "高一致样本抽检、L1/L2 校准 | 发现共谋错误→回修"),
 "N1": ("负样本池", "", "pool", None, None, False, "拒绝/误报/无效报告；用于 L1/L2 校准和负样本训练；定期清理"),
 "P2": ("接入校验与入湖", "标准化记录+队列任务", "proc", "脚本入目录", "对象存储+队列", False,
        "认证、Schema 校验、幂等去重、隐私复核、入湖 | 校验失败隔离"),
 "P3": ("世界模型蜂窝扩充", "生成帧/片段+lineage", "proc", "PPMem/InSpatio/受控变换，单卡",
        "Holo4D/DreamX/SANA-WM，多模型路由", True,
        "锚点选择→轨迹生成→算力调度→视频生成→抽帧→lineage→初筛 | 可用率低收窄视角/换模型 | ⚠Demo 选型"),
 "P4": ("几何门禁", "gate_score / gate_status", "proc", "三项自洽+20 段抽检", "PDI-Bench/Orbia 标准化审计", True,
        "深度连续性、形状稳定性、纹理闪烁；加权评分 | 与人工不一致调阈值；通过率低回 P3 | ⚠权重与阈值"),
 "P5": ("多智能体标注与分歧路由", "统一 schema 标注", "proc", "2–3 实例+本地审核台", "多智能体集群+校准集", True,
        "切片分配→多实例标注→交叉一致性→分歧路由→人工仲裁→语义生成 | 分歧率过高换配置/降级"),
 "P6": ("统一 Schema 与双格式导出", "dataset_version", "proc", "四导出器+小样本回归", "数据集版本管理+审计", False,
        "Schema 校验→落库→COCO/YOLO→ShareGPT/messages→版本化→血缘 | 坐标越界回修；导出器回归测试"),
 "N2": ("拒绝池与失败分析", "", "pool", None, None, False, "门禁拒绝帧段、共谋错误、拒绝发布；失败归因并反馈生成模型/标注策略"),
 "T7": ("增量训练", "候选模型+训练血缘", "proc", "小模型三轮对比", "大模型微调+分布式", True,
        "数据配比、难例加权、训练、成本记录 | 不收敛回退基线；成本超限缩规模 | ⚠单轮成本上限"),
 "T8": ("评测门禁", "评测报告+发布计划", "proc", "小规模真实留出集", "金标回归+统计显著性", True,
        "分切片评测、不退化门禁、污染检查、代理指标对齐 | 某切片退化拒绝发布 | ⚠留出集规模与切片"),
 "T9": ("模型仓库与压缩适配", "模型包+manifest", "proc", "manifest+脚本模拟", "多硬件编译+CDN", False,
        "仓库登记、量化编译、manifest、差分 | 编译失败该硬件不发布"),
 "T10": ("OTA 下发/影子/灰度/回滚", "升级回执+影子统计", "proc", "状态机模拟+离线回放", "真实双分区+灰度平台", True,
         "下载→校验→安装→切换→影子→灰度→回滚→回执 | 失败自动回滚保留旧版 | ⚠是否只做模拟"),
 "M11": ("线上监控", "告警+补样任务+难例回流", "proc", "脚本统计", "实时遥测平台", False,
         "延迟/内存/置信度分布/难例率/版本分布/回滚率；漂移检测；精度停滞触发 | 遥测丢失本地补报；漂移误报用金标确认"),
 "M12": ("版本治理与数据生命周期", "治理决策+删除审计", "proc", "文档设计", "治理平台", False,
         "最低支持版本、强制升级、离线补升级、EOL、过期删除 | 碎片过高启动补升级"),
}

W, H = 168, 60          # proc node size
DW, DH = 128, 84        # diamond
LX = 200                # content start x (lane label 0..120, return corridor 120..200)

# lane-relative positions: id -> (lane, x, dy)
POS = {
 "A0": ("L1", LX, 24), "SRC": ("L1", LX, 104),
 "A1": ("L1", 430, 64), "A2": ("L1", 660, 64), "A3": ("L1", 890, 14), "A4": ("L1", 1120, 64),
 "B1": ("L2", LX, 30), "B2": ("L2", 430, 110), "D1": ("L2", 660, 98),
 "B4": ("L2", 830, 20), "D2": ("L2", 1060, 8), "B6": ("L2", 1250, 20),
 "B7": ("L2", 1430, 110), "N1": ("L2", 1650, 108), "B8": ("L2", 900, 220),
 "P2": ("L3", 1430, 50), "P3": ("L3", 1190, 50), "P4": ("L3", 950, 50),
 "P5": ("L3", 710, 50), "P6": ("L3", 470, 50), "N2": ("L3", 950, 160),
 "T7": ("L4", 470, 60), "T8": ("L4", 710, 60), "T9": ("L4", 950, 60), "T10": ("L4", 1190, 60),
 "M11": ("L5", 1190, 50), "M12": ("L5", 1430, 50),
}
LANE_H = {"L1": 200, "L2": 320, "L3": 260, "L4": 200, "L5": 170}
CONTENT_R = 1860   # lane right edge

# edges: (src, tgt, label, kind, opts)  kind: main / back / fail
E = [
 ("A0", "A1", "难例触发", "main", {}),
 ("SRC", "A1", "", "main", {}),
 ("A1", "A2", "", "main", {}),
 ("A2", "A3", "有音频", "main", {}),
 ("A2", "A4", "无音频", "main", {}),
 ("A3", "A4", "", "main", {}),
 ("A4", "B1", "user_upload+描述", "main", {"pts": [(1204, "L1", 186), (284, "L1", 186)], "entry": (0.5, 0)}),
 ("A4", "B2", "其他来源", "main", {"pts": [(1250, "L1", 186), (1250, "L2", 6), (514, "L2", 6)], "exit": (0.75, 1), "entry": (0.5, 0)}),
 ("B1", "B2", "", "main", {}),
 ("B2", "D1", "", "main", {}),
 ("D1", "B7", "高置信一致·自动接受", "main", {"exit": (1, 0.5), "entry": (0, 0.5)}),
 ("D1", "B4", "低置信/矛盾/困难", "main", {"exit": (0.5, 0), "entry": (0, 0.5)}),
 ("B4", "D2", "", "main", {}),
 ("D2", "B7", "自动接受·保留抽检", "main", {"exit": (0.5, 1), "entry": (0.25, 0)}),
 ("D2", "B6", "升级人工", "main", {}),
 ("B6", "B7", "仲裁回写", "back", {"exit": (1, 0.5), "entry": (0.75, 0)}),
 ("B7", "N1", "拒绝/误报", "fail", {}),
 ("B8", "B2", "抽检/校准", "back", {"exit": (0, 0.5), "entry": (0.5, 1), "both": True}),
 ("B8", "B4", "", "back", {"exit": (0.5, 0), "entry": (0.5, 1), "both": True}),
 ("B8", "N1", "", "back", {"exit": (1, 0.5), "entry": (0.5, 1), "both": True}),
 ("B7", "P2", "确认", "main", {"exit": (0.5, 1), "entry": (0.5, 0)}),
 ("B7", "P3", "needs_more_data", "back", {"exit": (0.15, 1), "entry": (0.5, 0), "pts": [(1455, "L2", 300), (1274, "L2", 300)]}),
 ("P2", "P3", "", "main", {}),
 ("P3", "P4", "", "main", {}),
 ("P4", "P5", "passed", "main", {}),
 ("P4", "N2", "gate_failed", "fail", {"exit": (0.5, 1), "entry": (0.5, 0)}),
 ("N2", "P3", "失败归因反馈", "back", {"exit": (1, 0.5), "entry": (0.5, 1)}),
 ("P5", "P6", "", "main", {}),
 ("P6", "T7", "dataset_version", "main", {"exit": (0.5, 1), "entry": (0.5, 0)}),
 ("T7", "T8", "", "main", {}),
 ("T8", "T9", "通过", "main", {}),
 ("T8", "T7", "拒绝·回修", "fail", {"exit": (0.5, 1), "entry": (0.5, 1), "pts": [(794, "L4", 172), (554, "L4", 172)]}),
 ("T8", "N2", "拒绝发布", "fail", {"exit": (0.75, 0), "entry": (0, 0.5), "pts": [(836, "L3", 190)]}),
 ("T9", "T10", "", "main", {}),
 ("T10", "M11", "回执+影子统计+遥测", "main", {"exit": (0.5, 1), "entry": (0.5, 0)}),
 ("M12", "T10", "强制/补升级", "back", {"exit": (0.5, 0), "entry": (1, 0.5), "pts": [(1514, "L4", 90)]}),
 ("M11", "A1", "难例回流", "back", {"exit": (0, 0.5), "entry": (0, 0.5),
     "pts": [(150, "L5", 80), (150, "L1", 94)], "skip_pts_if_missing": True}),
 ("M11", "B2", "重新筛查", "back", {"exit": (0, 0.75), "entry": (0, 0.5),
     "pts": [(170, "L5", 95), (170, "L2", 140)]}),
 ("M11", "P3", "精度停滞·补锚点", "back", {"exit": (1, 0.25), "entry": (0.75, 1),
     "pts": [(1395, "L5", 65), (1395, "L3", 235), (1316, "L3", 235)]}),
]

STYLE_EDGE = {
 "main": f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#343a40;strokeWidth=1.6;fontSize=11;labelBackgroundColor=#ffffff;",
 "back": f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=open;dashed=1;dashPattern=6 4;strokeColor={GREY};strokeWidth=1.4;fontSize=11;fontColor=#495057;labelBackgroundColor=#ffffff;",
 "fail": f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=open;dashed=1;dashPattern=6 4;strokeColor={RED};strokeWidth=1.6;fontSize=11;fontColor={RED};labelBackgroundColor=#ffffff;",
}


class Page:
    def __init__(self, name, pid):
        self.name, self.pid, self.cells, self.n = name, pid, [], 0

    def nid(self, p="c"):
        self.n += 1
        return f"{self.pid}-{p}{self.n}"

    def raw(self, cid, label, style, x, y, w, h, tip=None, vertex=True):
        lab = escape(label, {'"': "&quot;"})
        geo = f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
        cell = f'<mxCell style="{style}" vertex="1" parent="1">{geo}</mxCell>'
        if tip:
            self.cells.append(f'<UserObject id="{cid}" label="{lab}" tooltip="{escape(tip, {chr(34): "&quot;"})}">{cell}</UserObject>')
        else:
            self.cells.append(f'<mxCell id="{cid}" value="{lab}" style="{style}" vertex="1" parent="1">{geo}</mxCell>')
        return cid

    def box(self, label, x, y, w, h, style, tip=None):
        return self.raw(self.nid(), label, style, x, y, w, h, tip)

    def edge(self, s, t, label, kind, exit=None, entry=None, pts=(), both=False):
        st = STYLE_EDGE[kind]
        if exit: st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry: st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        if both: st += "startArrow=open;startFill=0;"
        p = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts)
        arr = f'<Array as="points">{p}</Array>' if pts else ""
        self.cells.append(
            f'<mxCell id="{self.nid("e")}" value="{escape(label)}" style="{st}" edge="1" parent="1" source="{s}" target="{t}">'
            f'<mxGeometry relative="1" as="geometry">{arr}</mxGeometry></mxCell>')

    def xml(self):
        body = "".join(self.cells)
        return (f'<diagram id="{self.pid}" name="{escape(self.name)}"><mxGraphModel dx="1600" dy="1000" grid="1" gridSize="10" '
                f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" math="0" shadow="0">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram>')


def node_style(kind, lane):
    fill, stroke = LANE_COL.get(lane, ("#ffffff", "#495057"))
    base = "html=1;whiteSpace=wrap;fontSize=12;fontFamily=Microsoft YaHei;"
    if kind == "proc":
        return base + f"rounded=1;arcSize=12;fillColor=#ffffff;strokeColor={stroke};strokeWidth=1.6;"
    if kind == "dec":
        return base + f"rhombus;fillColor=#fff4e5;strokeColor={ORANGE};strokeWidth=1.6;"
    if kind == "pool":
        return base + "shape=cylinder3;boundedLbl=1;size=8;fillColor=#f1f3f5;strokeColor=#868e96;"
    if kind == "src":
        return base + "rounded=1;fillColor=#f8f9fa;strokeColor=#adb5bd;dashed=1;"
    if kind == "ext":
        return base + "rounded=1;fillColor=#ffffff;strokeColor=#adb5bd;dashed=1;fontColor=#495057;fontSize=11;"
    return base


def build(page, lanes, ids, detail=False, stubs=(), extra_edges=(), crosscut=False, width=None):
    """Draw lanes, nodes, edges. Returns (map id->cellid, laneY)."""
    right = width or CONTENT_R
    laneY, y = {}, 0
    for ln in lanes:
        laneY[ln] = y
        fill, stroke = LANE_COL[ln]
        page.box(LANE_TITLE[ln], 0, y, right, LANE_H[ln],
                 f"swimlane;horizontal=0;startSize=120;html=1;fontSize=14;fontStyle=1;fillColor={fill};strokeColor={stroke};"
                 f"swimlaneFillColor={fill}66;fontFamily=Microsoft YaHei;collapsible=0;")
        y += LANE_H[ln]
    total_h = y
    cid = {}
    for i in ids:
        ln, x, dy = POS[i]
        if ln not in laneY:
            continue
        lab, out, kind, d, p, warn, tip = N[i]
        yy = laneY[ln] + dy
        if kind == "dec":
            w, h = DW, DH
            label = f"<b>{i}</b><br>{lab}" + (" ⚠" if warn else "")
        elif kind == "pool":
            w, h = 150, 70
            label = f"<b>{i}</b><br>{lab}"
        else:
            w, h = W, H if not detail else H + 8
            label = f"<b>{i}</b> {lab}" + (" ⚠" if warn else "") if i != "SRC" else f"<b>{lab}</b>"
            if detail and out:
                label += f'<br><font style="font-size:10px" color="#6c757d">→ {escape(out)}</font>'
            elif i == "SRC":
                label += f'<br><font style="font-size:10px" color="#6c757d">{out}</font>'
        cid[i] = page.raw(f"{page.pid}-{i}", label, node_style(kind, ln), x, yy, w, h, tip=tip)
        # D / P badges
        bx = x + w - (18 if kind != "dec" else 30)
        if p:
            page.box("P", bx, yy - 9, 18, 18,
                     f"ellipse;html=1;fillColor={ORANGE};strokeColor=none;fontColor=#ffffff;fontSize=10;fontStyle=1;",
                     tip=f"生产：{p}")
            bx -= 20
        if d:
            page.box("D", bx, yy - 9, 18, 18,
                     f"ellipse;html=1;fillColor={BLUE};strokeColor=none;fontColor=#ffffff;fontSize=10;fontStyle=1;",
                     tip=f"Demo：{d}")
    # stubs: (key, label, lane, x, dy, w)
    for key, lab, ln, x, dy, w in stubs:
        cid[key] = page.box(lab, x, laneY[ln] + dy, w, 44, node_style("ext", ln))
    for s, t, lab, kind, o in list(E) + list(extra_edges):
        if s not in cid or t not in cid:
            continue
        pts = []
        ok = True
        for px, pl, pdy in o.get("pts", []):
            if pl not in laneY:
                ok = False
                break
            pts.append((px, laneY[pl] + pdy))
        if not ok:
            pts = []
        page.edge(cid[s], cid[t], lab, kind, o.get("exit") if ok else None, o.get("entry") if ok else None, pts, o.get("both", False))
    return cid, laneY, total_h


def legend(page, x, y):
    page.box("<b>图例</b>", x, y, 330, 210,
             "rounded=1;html=1;fillColor=#ffffff;strokeColor=#ced4da;verticalAlign=top;align=left;spacingLeft=10;fontSize=13;")
    rows = [("main", "主流程"), ("back", "回流 / 降级 / 补样 / 仲裁回写"), ("fail", "异常 / 失败 / 拒绝")]
    for k, (kind, txt) in enumerate(rows):
        yy = y + 40 + k * 26
        a = page.box("", x + 14, yy, 1, 1, "shape=point;size=1;strokeColor=none;fillColor=none;")
        b = page.box("", x + 84, yy, 1, 1, "shape=point;size=1;strokeColor=none;fillColor=none;")
        page.edge(a, b, "", kind)
        page.box(txt, x + 96, yy - 10, 220, 20, "text;html=1;align=left;fontSize=12;")
    yy = y + 122
    page.box("", x + 18, yy, 30, 22, f"rhombus;fillColor=#fff4e5;strokeColor={ORANGE};")
    page.box("路由 / 门禁决策", x + 56, yy, 110, 22, "text;html=1;align=left;fontSize=12;")
    page.box("", x + 176, yy - 2, 26, 26, "shape=cylinder3;boundedLbl=1;size=5;fillColor=#f1f3f5;strokeColor=#868e96;")
    page.box("样本池 / 拒绝池", x + 210, yy, 110, 22, "text;html=1;align=left;fontSize=12;")
    yy = y + 160
    page.box("D", x + 22, yy, 18, 18, f"ellipse;fillColor={BLUE};strokeColor=none;fontColor=#ffffff;fontSize=10;fontStyle=1;")
    page.box("Demo 方案", x + 46, yy - 2, 80, 22, "text;html=1;align=left;fontSize=12;")
    page.box("P", x + 130, yy, 18, 18, f"ellipse;fillColor={ORANGE};strokeColor=none;fontColor=#ffffff;fontSize=10;fontStyle=1;")
    page.box("生产方案", x + 154, yy - 2, 70, 22, "text;html=1;align=left;fontSize=12;")
    page.box("⚠ 待定项", x + 230, yy - 2, 90, 22, "text;html=1;align=left;fontSize=12;")
    page.box("悬停节点 / 角标可看处理要点与方案", x + 14, y + 184, 300, 20,
             "text;html=1;align=left;fontSize=11;fontColor=#868e96;")


def note(page, title, lines, x, y, w, h=None):
    body = "<br>".join(lines)
    h = h or 36 + 18 * (len(lines) + body.count("<br>") - len(lines) + 1)
    page.box(f"<b>{title}</b><br>{body}", x, y, w, h,
             "rounded=1;html=1;whiteSpace=wrap;fillColor=#fffdf5;strokeColor=#e9d8a6;align=left;verticalAlign=top;"
             "spacingLeft=10;spacingTop=6;fontSize=12;fontFamily=Microsoft YaHei;")


def title(page, t, sub, y=-80):
    page.box(f'<b style="font-size:20px">{t}</b><br><font color="#6c757d" style="font-size:12px">{sub}</font>',
             0, y, 1200, 56, "text;html=1;align=left;verticalAlign=middle;fontFamily=Microsoft YaHei;")


pages = []

# ---------------- 图1 五层总览 ----------------
p = Page("图1 五层总览", "f1")
title(p, "图 1 · 五层总览主流程（一页全景）", "蛇形主流程：L1/L2 左→右，L3 右→左，L4 左→右，L5 闭环；右侧为横切带；虚线为回流")
cid, laneY, th = build(p, ["L1", "L2", "L3", "L4", "L5"], list(POS.keys()), width=1860)
# crosscut bar
cx = 1880
p.box("<b>横切带</b>", cx, 0, 230, th,
      "rounded=0;html=1;fillColor=#f8f9fa;strokeColor=#adb5bd;verticalAlign=top;fontSize=14;fontFamily=Microsoft YaHei;")
p.box("<b>C1 版本血缘</b><br><font style='font-size:10px'>ingest → defect → anchor → generated → gate → annotation → dataset → model → build → release → device</font>",
      cx + 12, 36, 32, th - 48,
      "rounded=1;html=1;horizontal=0;fillColor=#d0ebff;strokeColor=#4a7bd0;fontSize=12;whiteSpace=wrap;")
cc = [("C2 隐私合规 ⚠", "A2 · P2 · P6 · M12<br>脱敏 / 隔离 / 保留期限"),
      ("C3 算力调度与成本 ⚠", "P3 · P5 · T7 · T9<br>预算 / 队列 / 优先级"),
      ("C4 审计日志 🔒", "全流程<br>门禁 / 仲裁 / 发布 / 回滚"),
      ("C5 指标字典", "B 系列 · P4 · T8 · M11<br>指标口径注释")]
for k, (a, b) in enumerate(cc):
    p.box(f"<b>{a}</b><br><font style='font-size:11px' color='#495057'>{b}</font>", cx + 56, 40 + k * 280, 162, 90,
          "rounded=1;html=1;whiteSpace=wrap;fillColor=#ffffff;strokeColor=#adb5bd;fontSize=12;fontFamily=Microsoft YaHei;")
legend(p, 0, th + 30)
note(p, "Demo 阶段明确不做", ["真实 OTA 双分区 · 大规模多机调度 · 全开放 360° 生成",
                          "多硬件编译 · 生产级灰度回滚 · VLM 从头 SFT"], 360, th + 30, 470, 90)
note(p, "回流通道", ["左侧通道：M11 → A1 难例回流；M11 → B2 重新筛查",
                  "右侧：M11 → P3 精度停滞补锚点；M12 → T10 强制/补升级",
                  "P4 拒绝 → N2 → P3；T8 拒绝 → T7 / N2"], 860, th + 30, 470, 110)
pages.append(p)

# ---------------- 图2 入口+发现 ----------------
p = Page("图2 入口+发现", "f2")
title(p, "图 2 · 入口层 + 缺陷发现分级泳道", "A0–A4 多源接入 → L0/L1/L2/L3 逐级升级 → defect_event；节点下方灰字为输出")
stubs2 = [("XP2", "→ <b>P2</b> 接入校验与入湖（图3）", "L2", 1500, 268, 190),
          ("XP3", "→ <b>P3</b> 补锚点（图3）", "L2", 1240, 268, 170),
          ("XM1", "↩ <b>M11</b> 难例回流（图4）", "L1", 430, 150, 168),
          ("XM2", "↩ <b>M11</b> 重新筛查（图4）", "L2", LX, 250, 168)]
ext2 = [("B7", "XP2", "确认", "main", {"exit": (0.7, 1), "entry": (0.5, 0)}),
        ("B7", "XP3", "needs_more_data", "back", {"exit": (0.2, 1), "entry": (0.5, 0)}),
        ("XM1", "A1", "", "back", {"exit": (0.5, 0), "entry": (0.5, 1)}),
        ("XM2", "B2", "", "back", {"exit": (1, 0.5), "entry": (0.25, 1)})]
cid, laneY, th = build(p, ["L1", "L2"], [k for k, v in POS.items() if v[0] in ("L1", "L2")],
                       detail=True, stubs=stubs2, extra_edges=ext2)
note(p, "ingest_event 关键字段（A4）", ["ingest_id · source_type · media · user_description",
                                      "audio_events[] · privacy_flag · dedup_hash · source_meta"], 0, th + 30, 420, 80)
note(p, "defect_event 关键字段（B7）", ["defect_id · level_reached · defect_type · location · severity",
                                      "confidence · evidence（帧/片段/音频/L1·L2 reasoning）",
                                      "routing_trace · human_decision · final_decision"], 440, th + 30, 470, 98)
note(p, "升级规则", ["<b>L1→L2（D1）</b>：低置信 · 类别不确定 · 描述与 L1 矛盾 · 音频异常但视觉未检出 · 困难切片",
                 "<b>L2→L3（D2）</b>：L1/L2 分歧 · L2 低置信/自评不确定 · 高严重度 · 长尾新场景 · 抽检命中"],
     930, th + 30, 640, 80)
legend(p, 1590, th + 30)
pages.append(p)

# ---------------- 图3 云端飞轮 ----------------
p = Page("图3 云端飞轮", "f3")
title(p, "图 3 · 云端飞轮泳道（P2–P6）", "defect_event / 难例 → 入湖 → 世界模型扩充 → 几何门禁 → 多智能体标注 → 双格式导出 → dataset_version")
LANE_H_BAK = LANE_H["L3"]
LANE_H["L3"] = 300
stubs3 = [("XB7", "<b>B7</b> defect_event / 难例包（图2）", "L3", 1650, 58, 190),
          ("XB7n", "<b>B7</b> needs_more_data（图2）", "L3", 1190, 236, 168),
          ("XM11", "<b>M11</b> 精度停滞（图4）", "L3", 1430, 236, 168),
          ("XT7", "→ <b>T7</b> 增量训练（图4）", "L3", LX, 58, 168),
          ("XT8", "<b>T8</b> 拒绝发布（图4）", "L3", 710, 236, 168)]
ext3 = [("XB7", "P2", "", "main", {}),
        ("XB7n", "P3", "", "back", {"exit": (0.3, 0), "entry": (0.3, 1)}),
        ("XM11", "P3", "", "back", {"exit": (0.5, 0), "entry": (0.9, 1)}),
        ("P6", "XT7", "dataset_version", "main", {"exit": (0, 0.5), "entry": (1, 0.5)}),
        ("XT8", "N2", "", "fail", {"exit": (1, 0.5), "entry": (0, 0.7)})]
cid, laneY, th = build(p, ["L3"], ["P2", "P3", "P4", "P5", "P6", "N2"], detail=True, stubs=stubs3, extra_edges=ext3)
LANE_H["L3"] = LANE_H_BAK
steps = [("P3 内部", "锚点选择 → 轨迹生成 → 算力调度 → 视频生成 → 抽帧 → lineage → 初筛"),
         ("P4 内部", "深度连续性 + 形状稳定性 + 纹理闪烁（可选 PDI-Bench）→ 加权评分 → gate_status ⚠"),
         ("P5 内部", "视角切片分配 → 多实例标注 → 交叉一致性 → 分歧路由 → 人工仲裁 → 语义生成"),
         ("P6 内部", "Schema 校验 → 落库 → COCO/YOLO → ShareGPT/messages → 数据集版本化 → 血缘")]
note(p, "节点内部步骤", [f"<b>{a}</b>：{b}" for a, b in steps], 0, th + 30, 760, 110)
note(p, "硬约束", ["① 合成数据只进训练集", "② 门禁通过才进标注", "③ 分歧与人工仲裁保留为 VLM 高价值信号",
                "④ 同一份标注事实派生两种格式"], 780, th + 30, 330, 110)
note(p, "dataset_version 关键字段（P6）", ["dataset_version · format · export_config · sample_list",
                                         "schema_version · exporter_version · lineage"], 1130, th + 30, 400, 80)
legend(p, 1550, th + 30)
pages.append(p)

# ---------------- 图4 训练发布+闭环 ----------------
p = Page("图4 训练发布+闭环", "f4")
title(p, "图 4 · 训练发布 + 线上闭环泳道", "dataset_version → 训练 → 评测门禁 → 仓库/压缩 → OTA → 线上监控 → 回流")
stubs4 = [("XP6", "<b>P6</b> dataset_version（图3）", "L4", LX, 68, 180),
          ("XN2", "→ <b>N2</b> 拒绝池（图3）", "L4", 820, 160, 168),
          ("XA1", "↩ <b>A1</b> 难例回流（图2）", "L5", 800, 4, 168),
          ("XB2", "↩ <b>B2</b> 重新筛查（图2）", "L5", 800, 62, 168),
          ("XP3b", "↩ <b>P3</b> 补锚点（图3）", "L5", 800, 120, 168)]
ext4 = [("XP6", "T7", "", "main", {}),
        ("T8", "XN2", "拒绝发布", "fail", {"exit": (0.8, 1), "entry": (0.15, 0)}),
        ("M11", "XA1", "难例回流", "back", {"exit": (0, 0.2), "entry": (1, 0.5)}),
        ("M11", "XB2", "重新筛查", "back", {"exit": (0, 0.5), "entry": (1, 0.5)}),
        ("M11", "XP3b", "精度停滞", "back", {"exit": (0, 0.8), "entry": (1, 0.5)})]
LANE_H["L4"], LANE_H["L5"] = 230, 170
cid, laneY, th = build(p, ["L4", "L5"], ["T7", "T8", "T9", "T10", "M11", "M12"], detail=True,
                       stubs=stubs4, extra_edges=ext4)
# re-route T8->T7 reject below lane bottom handled by E pts (L4,172) ok
note(p, "发布单关键字段（T8 输出）", ["release_id · model_version · eval_slices",
                                  "regression_threshold · rollout_plan · rollback_threshold · signature"], 0, th + 30, 470, 80)
note(p, "T10 下发链路", ["下载 → 校验 → 安装 → 切换 → 影子 → 灰度 → 回滚 → 回执（状态机见图 5）"], 490, th + 30, 480, 62)
note(p, "M11 监控项", ["延迟 · 内存 · 置信度分布 · 难例率 · 版本分布 · 回滚率",
                    "漂移检测 · 精度停滞触发；遥测丢失本地补报；漂移误报用金标确认"], 990, th + 30, 500, 80)
legend(p, 1510, th + 30)
pages.append(p)

# ---------------- 图5 状态机 ----------------
p = Page("图5 状态机", "f5")
title(p, "图 5 · 三类状态机", "缺陷事件 / 标注记录 / OTA 升级；红虚线为失败分支，灰虚线为捷径或回修")
SS = "rounded=1;arcSize=40;html=1;fillColor=#ffffff;strokeColor=#495057;fontSize=12;fontFamily=Consolas;"
END = "rounded=1;arcSize=40;html=1;fillColor=#d3f9d8;strokeColor=#2f9e44;fontSize=12;fontStyle=1;fontFamily=Consolas;"
BAD = "rounded=1;arcSize=40;html=1;fillColor=#ffe3e3;strokeColor=#e03131;fontSize=12;fontFamily=Consolas;"
MID = "rounded=1;arcSize=40;html=1;fillColor=#fff4e5;strokeColor=#e8590c;fontSize=12;fontFamily=Consolas;"


def sm_title(t, y):
    p.box(f"<b>{t}</b>", 0, y, 400, 26, "text;html=1;align=left;fontSize=15;fontFamily=Microsoft YaHei;")


def chain(names, x0, y, gap=200, w=150, styles=None):
    ids = []
    for k, n in enumerate(names):
        st = (styles or {}).get(n, SS)
        ids.append(p.box(n, x0 + k * gap, y, w, 40, st))
    for a, b in zip(ids, ids[1:]):
        p.edge(a, b, "", "main")
    return dict(zip(names, ids))

# 5.1 defect
sm_title("5.1 缺陷事件 defect_event", 0)
d = chain(["reported", "l1_screened", "l2_analyzed", "human_reviewed"], 0, 50)
d["confirmed"] = p.box("confirmed", 860, 0, 150, 40, END)
d["rejected"] = p.box("rejected", 860, 60, 150, 40, BAD)
d["nmd"] = p.box("needs_more_data", 860, 120, 150, 40, MID)
d["susp"] = p.box("suspended（降级/挂起）", 330, 150, 190, 40, BAD)
for k in ("confirmed", "rejected", "nmd"):
    p.edge(d["human_reviewed"], d[k], "", "main")
p.edge(d["l1_screened"], d["confirmed"], "D1 自动接受", "back", (0.5, 0), (0, 0.25), [(275, 10)])
p.edge(d["l2_analyzed"], d["confirmed"], "D2 自动接受", "back", (0.5, 0), (0, 0.5), [(475, 20), (800, 20)])
p.edge(d["l2_analyzed"], d["susp"], "任一级超时/失败", "fail", (0.5, 1), (0.5, 0))
xn1 = p.box("→ N1 负样本池", 1050, 60, 130, 40, node_style("ext", ""))
xp3 = p.box("→ P3 补锚点", 1050, 120, 130, 40, node_style("ext", ""))
p.edge(d["rejected"], xn1, "", "fail")
p.edge(d["nmd"], xp3, "", "back")

# 5.2 annotation
y0 = 240
sm_title("5.2 标注记录 annotation", y0)
a = chain(["draft", "multi_agent_labeled", "consensus_checked"], 0, y0 + 60, w=170, gap=220)
a["auto"] = p.box("auto_accepted", 700, y0 + 20, 150, 40, SS)
a["hr"] = p.box("human_review", 700, y0 + 100, 150, 40, SS)
a["arb"] = p.box("arbitrated", 900, y0 + 100, 150, 40, SS)
a["frozen"] = p.box("frozen", 1100, y0 + 60, 150, 40, END)
a["hang"] = p.box("分歧未决·挂起", 900, y0 + 180, 150, 40, BAD)
p.edge(a["consensus_checked"], a["auto"], "一致", "main")
p.edge(a["consensus_checked"], a["hr"], "分歧", "main")
p.edge(a["hr"], a["arb"], "", "main")
p.edge(a["auto"], a["frozen"], "", "main")
p.edge(a["arb"], a["frozen"], "", "main")
p.edge(a["hr"], a["hang"], "未决", "fail", (0.5, 1), (0, 0.5))
p.edge(a["consensus_checked"], a["draft"], "Schema 校验失败回修", "fail", (0.5, 1), (0.5, 1), [(525, y0 + 150), (75, y0 + 150)])
p.box("人工积压按 严重度 + 分歧度 排序", 1080, y0 + 180, 230, 40,
      "text;html=1;align=left;fontSize=11;fontColor=#868e96;")

# 5.3 OTA
y1 = 530
sm_title("5.3 OTA 升级 device update", y1)
names = ["pending", "downloading", "downloaded", "verifying", "verified", "installing", "installed", "switching", "active"]
u = chain(names, 0, y1 + 50, gap=165, w=130, styles={"active": END})
u["rb"] = p.box("rolling_back", 660, y1 + 170, 150, 40, BAD)
u["rbd"] = p.box("rolled_back", 900, y1 + 170, 150, 40, BAD)
p.edge(u["verifying"], u["rb"], "verify_failed", "fail", (0.5, 1), (0, 0.5))
p.edge(u["installing"], u["rb"], "install_failed", "fail", (0.5, 1), (0.5, 0))
p.edge(u["switching"], u["rb"], "switch_failed", "fail", (0.5, 1), (1, 0.5), [(1220, y1 + 140), (840, y1 + 140)])
p.edge(u["rb"], u["rbd"], "", "fail")
p.box("断电 / 超时：恢复回上一稳定态（downloaded / verified / installed / 旧版 active）", 0, y1 + 240, 640, 26,
      "text;html=1;align=left;fontSize=12;fontColor=#495057;")
pages.append(p)

# ---------------- 图6 版本血缘 ----------------
p = Page("图6 版本血缘", "f6")
title(p, "图 6 · 版本血缘主线", "每一跳都是可追溯的 ID；三个问题沿链路回溯即可回答")
chainv = [("ingest_event", "L1"), ("defect_event", "L2"), ("anchor", "L3"), ("generated<br><font style='font-size:10px'>synthetic=true</font>", "L3"),
          ("gate<br><font style='font-size:10px'>passed / rejected</font>", "L3"),
          ("annotation<br><font style='font-size:10px'>agent_consensus + human_decision</font>", "L3"),
          ("dataset_version", "L3"), ("model_version", "L4"), ("build_id", "L4"), ("release_id", "L4"),
          ("device<br><font style='font-size:10px'>当前 / 上一版本 / 回滚记录</font>", "L5")]
ids6 = []
for k, (t, ln) in enumerate(chainv):
    fill, stroke = LANE_COL[ln]
    ids6.append(p.box(t, 300, k * 78, 220, 50,
                      f"rounded=1;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.6;fontSize=13;fontFamily=Consolas;"))
for a1, b1 in zip(ids6, ids6[1:]):
    p.edge(a1, b1, "", "main")
for ln, rng in [("L1", (0, 0)), ("L2", (1, 1)), ("L3", (2, 6)), ("L4", (7, 9)), ("L5", (10, 10))]:
    fill, stroke = LANE_COL[ln]
    p.box(LANE_TITLE[ln], 60, rng[0] * 78, 200, (rng[1] - rng[0]) * 78 + 50,
          f"rounded=1;html=1;fillColor={fill};strokeColor={stroke};dashed=1;fontSize=12;fontColor={stroke};fontStyle=1;")
qs = [("Q1 设备跑哪版模型？（从 device 回溯）", "device → release_id → build_id → model_version", 10, 7),
      ("Q2 模型用哪批数据？（从 model_version 回溯）", "model_version → dataset_version → annotation → generated → anchor → ingest_event", 7, 0),
      ("Q3 标注为什么可信？（从 annotation 回溯）", "annotation → gate_status + agent_consensus + human_decision", 5, 4)]
for k, (q, path, frm, to) in enumerate(qs):
    qid = p.box(f"<b>{q}</b><br><font style='font-size:11px' color='#495057'>{path}</font>", 640, 60 + k * 260, 380, 70,
                "rounded=1;html=1;whiteSpace=wrap;fillColor=#fffdf5;strokeColor=#e9d8a6;align=left;spacingLeft=10;fontSize=13;fontFamily=Microsoft YaHei;")
    tx_ = 560 + k * 26
    p.edge(qid, ids6[frm], "", "back", (0, 0.5), (1, 0.5), [(tx_, 60 + k * 260 + 35), (tx_, frm * 78 + 25)])
pages.append(p)

# ---------------- 附 图例与待定项 ----------------
p = Page("附 图例与待定项", "f7")
title(p, "附 · 图例 / Demo-生产对照 / ⚠ 待定项", "画其他图时可从本页复制图例与角标")
legend(p, 0, 0)
rows = [("A1–A4", "本地目录+一段离线流+少量上传", "接入网关+转码+音频管线+合规"),
        ("B2/B4/B6", "YOLO + 小型 VLM + 本地审核台", "多任务小模型 + 大模型集群 + 审核平台"),
        ("P3", "PPMem/InSpatio/受控变换，单卡", "Holo4D/DreamX/SANA-WM，多模型路由"),
        ("P4", "三项自洽 + 20 段抽检", "PDI-Bench/Orbia 标准化审计"),
        ("P5", "2–3 实例 + 本地审核台", "多智能体集群 + 校准集"),
        ("P6", "四导出器 + 小样本回归", "数据集版本管理 + 审计"),
        ("T7–T8", "小模型三轮对比 + 小规模真实留出集", "大模型微调 + 金标回归 + 统计显著性"),
        ("T9–T10", "manifest + 脚本模拟 + 离线回放", "多硬件编译 + 双分区 + 灰度平台"),
        ("M11–M12", "脚本统计 + 报告", "实时遥测 + 治理平台")]
tx, ty = 380, 0
hdr = "html=1;fontStyle=1;fillColor=#343a40;fontColor=#ffffff;strokeColor=#343a40;fontSize=12;fontFamily=Microsoft YaHei;"
cell = "html=1;whiteSpace=wrap;fillColor=#ffffff;strokeColor=#dee2e6;fontSize=12;align=left;spacingLeft=8;fontFamily=Microsoft YaHei;"
p.box("位置", tx, ty, 100, 30, hdr)
p.box("<font color='#9ec5fe'>D</font> Demo", tx + 100, ty, 280, 30, hdr)
p.box("<font color='#ffc078'>P</font> 生产", tx + 380, ty, 300, 30, hdr)
for k, (a1, b1, c1) in enumerate(rows):
    yy = ty + 30 + k * 30
    p.box(a1, tx, yy, 100, 30, cell + "fontStyle=1;")
    p.box(b1, tx + 100, yy, 280, 30, cell)
    p.box(c1, tx + 380, yy, 300, 30, cell)
note(p, "Demo 阶段明确不做", ["真实 OTA 双分区 · 大规模多机调度 · 全开放 360° 生成",
                          "多硬件编译 · 生产级灰度回滚 · VLM 从头 SFT"], 0, 240, 360, 90)
warn = [("1", "图像/音频能否离开设备", "A2、C2"), ("2", "L1/L2 升级阈值", "D1、D2"),
        ("3", "几何门禁指标权重与阈值", "P4"), ("4", "真实留出集规模与切片口径", "T8"),
        ("5", "人工介入比例分母定义", "B6、P5"), ("6", "Demo 生成模型最终选型", "P3"),
        ("7", "OTA 是否只做模拟", "T10"), ("8", "单轮闭环成本上限", "C3、T7")]
wy = 340
p.box("⚠", tx, wy, 50, 30, hdr)
p.box("待定项", tx + 50, wy, 400, 30, hdr)
p.box("标注位置", tx + 450, wy, 230, 30, hdr)
for k, (a1, b1, c1) in enumerate(warn):
    yy = wy + 30 + k * 30
    p.box(a1, tx, yy, 50, 30, cell + "align=center;spacingLeft=0;fillColor=#fff4e5;")
    p.box(b1, tx + 50, yy, 400, 30, cell)
    p.box(c1, tx + 450, yy, 230, 30, cell)
pages.append(p)

xml = '<mxfile host="app.diagrams.net" type="device">' + "".join(pg.xml() for pg in pages) + "</mxfile>"
open(OUT, "w", encoding="utf-8").write(xml)
print("pages:", len(pages), "bytes:", len(xml))
