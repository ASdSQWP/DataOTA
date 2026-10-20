"""三类状态机（spec 第 5 节）。非法迁移直接抛错，迁移历史写入 history 便于审计。"""
from __future__ import annotations

from dataclasses import dataclass, field


class IllegalTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class StateMachineDef:
    name: str
    initial: str
    transitions: dict[str, frozenset[str]]
    terminal: frozenset[str]

    def can(self, src: str, dst: str) -> bool:
        return dst in self.transitions.get(src, frozenset())


def _sm(name, initial, edges, terminal):
    t: dict[str, set[str]] = {}
    for a, b in edges:
        t.setdefault(a, set()).add(b)
    return StateMachineDef(name, initial, {k: frozenset(v) for k, v in t.items()}, frozenset(terminal))


# 5.1 缺陷事件
DEFECT_SM = _sm(
    "defect_event", "reported",
    [
        ("reported", "l1_screened"),
        ("reported", "l2_analyzed"),        # L1 不可用 → 跳 L2
        ("l1_screened", "l2_analyzed"),
        ("l1_screened", "confirmed"),       # D1 自动接受
        ("l1_screened", "rejected"),        # D1 高置信无缺陷
        ("l2_analyzed", "human_reviewed"),
        ("l2_analyzed", "confirmed"),       # D2 自动接受
        ("l2_analyzed", "rejected"),        # L2 判定非真实缺陷
        ("human_reviewed", "confirmed"),
        ("human_reviewed", "rejected"),
        ("human_reviewed", "needs_more_data"),
        *[(s, "suspended") for s in ("reported", "l1_screened", "l2_analyzed", "human_reviewed")],
        ("suspended", "l2_analyzed"),
        ("suspended", "human_reviewed"),
    ],
    {"confirmed", "rejected", "needs_more_data"},
)

# 5.2 标注记录
ANNOTATION_SM = _sm(
    "annotation", "draft",
    [
        ("draft", "multi_agent_labeled"),
        ("multi_agent_labeled", "consensus_checked"),
        ("consensus_checked", "auto_accepted"),
        ("consensus_checked", "human_review"),
        ("consensus_checked", "draft"),     # Schema 校验失败回修
        ("auto_accepted", "frozen"),
        ("human_review", "arbitrated"),
        ("human_review", "suspended"),      # 分歧未决挂起
        ("suspended", "human_review"),
        ("arbitrated", "frozen"),
    ],
    {"frozen"},
)

# 5.3 OTA 升级
_OTA_MAIN = ["pending", "downloading", "downloaded", "verifying", "verified",
             "installing", "installed", "switching", "active"]
OTA_SM = _sm(
    "ota_upgrade", "pending",
    [
        *zip(_OTA_MAIN, _OTA_MAIN[1:]),
        ("downloading", "pending"),          # 断点/超时：回到上一稳定态重试
        ("verifying", "verify_failed"),
        ("installing", "install_failed"),
        ("switching", "switch_failed"),
        ("verify_failed", "rolling_back"),
        ("install_failed", "rolling_back"),
        ("switch_failed", "rolling_back"),
        ("active", "rolling_back"),          # 灰度观察期指标跌破阈值
        ("rolling_back", "rolled_back"),
    ],
    {"active", "rolled_back"},
)

# 断电/超时恢复：回到最近的稳定态
OTA_STABLE = ["pending", "downloaded", "verified", "installed", "active"]


@dataclass
class Tracker:
    """绑定到某个对象的状态跟踪器。"""
    sm: StateMachineDef
    state: str = ""
    history: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.state:
            self.state = self.sm.initial
        if not self.history:
            self.history = [self.state]

    def to(self, dst: str) -> "Tracker":
        if not self.sm.can(self.state, dst):
            raise IllegalTransition(f"[{self.sm.name}] {self.state} -> {dst} 不允许")
        self.state = dst
        self.history.append(dst)
        return self

    def recover(self) -> str:
        """仅 OTA：断电后回退到最近稳定态。"""
        for s in reversed(self.history):
            if s in OTA_STABLE:
                self.state = s
                self.history.append(f"recover:{s}")
                return s
        self.state = self.sm.initial
        return self.state

    @property
    def done(self) -> bool:
        return self.state in self.sm.terminal
