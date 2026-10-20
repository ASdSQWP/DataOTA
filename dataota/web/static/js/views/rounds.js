/* 飞轮轮次：L1–L5 泳道、单轮指标、历史对比与原始报告 */

import { api } from '../api.js';
import {
  esc, num, fixed, pct, ts, badge, statusBadge, alertTone, icon, alertTone as toneOfAlert,
} from '../util.js';
import {
  pageHead, kpi, card, emptyState, callout, kv, miniStat, stageBadge, barRow,
} from '../ui.js';
import { lineChart, barChart, palette } from '../charts.js';
import { thr } from '../meta.js';

const NODE_INFO = {
  A0: { desc: '端侧推理与代理信号。低置信、影子分歧、场景异常都会触发难例回流。', keys: ['a0.hard_cases'], cfg: ['device.low_conf_threshold', 'device.scene_quality_min'] },
  A2: { desc: '统一网关执行隐私脱敏、配额控制与 Schema 校验；超预算样本在此丢弃。', keys: ['a0.budget_dropped'], cfg: ['privacy.allow_raw_media_off_device', 'device.daily_upload_budget'] },
  A4: { desc: '封装统一的 ingest_event，进入数据湖并写入血缘。', keys: ['a4.ingest_events'], cfg: ['privacy.retention_days'] },
  B2: { desc: 'L1 检测模型先做低成本筛查（检测模型库：classical / ensemble / YOLO / RT-DETR / ONNX…），输出全部候选框、置信度与场景质量；每轮按参考标签评测。', keys: ['b2.screened'], cfg: ['impl.l1', 'models.l1.detector', 'models.l1.score_threshold', 'routing.d1.min_confidence'] },
  D1: { desc: 'L1 路由：高置信且一致则自动接受，否则升级到 L2 大模型。', keys: ['d1.escalate', 'd2.auto_accept'], cfg: ['routing.d1.min_confidence'] },
  D2: { desc: 'L2 路由：分歧 / 低置信 / 高严重度 / 长尾样本转人工仲裁。', keys: ['d2.human'], cfg: ['routing.d2.min_confidence', 'routing.d2.human_severity'] },
  B7: { desc: '封装 defect_event，确认的缺陷事件进入 P2 入湖与后续飞轮。', keys: ['b7.confirmed', 'b7.rejected'], cfg: ['calibration.sample_rate'] },
  N1: { desc: '负样本池：保留被拒绝的候选，供后续校准与误报分析。', keys: ['b7.rejected'], cfg: [] },
  P2: { desc: '接入校验、幂等去重与锚点选择，确定需要扩充的视角。', keys: ['p2.anchors'], cfg: ['flywheel.missing_views'] },
  P3: { desc: '轨迹规划 → 按视角路由到世界模型（不可用 / 失败 / 被 N2 降级则换下一个）→ 生成多视角片段 → 初筛 → lineage。', keys: ['p3.clips', 'p3.fallback', 'p3.prescreen_failed', 'p3.generation_failed'], cfg: ['impl.world_model', 'flywheel.world_models', 'flywheel.frames_per_clip', 'cost.gpu_hours_per_clip'] },
  P4: { desc: '几何门禁：在真实帧上计算深度连续性、形状稳定性、纹理闪烁并加权评分（帧不可读时读模拟质量）。', keys: ['p4.passed', 'p4.rejected'], cfg: ['impl.gate', 'gate.pass_score', 'gate.per_metric_floor', 'gate.min_pass_rate', 'gate.weights'] },
  P5: { desc: '多智能体标注 + 交叉一致性；分歧样本进入人工仲裁。', keys: ['p5.annotations', 'p5.human', 'p5.auto'], cfg: ['annotate.num_agents', 'annotate.consensus_threshold'] },
  P6: { desc: '导出 COCO / YOLO / ShareGPT / messages 四种格式，并写入数据集血缘。', keys: [], cfg: ['dataset.formats', 'dataset.real_holdout_ratio'] },
  N2: { desc: '拒绝池与失败分析：门禁拒绝、共谋错误与 T8 拒绝发布回流到生成侧。', keys: [], cfg: [] },
  T7: { desc: '增量训练：难例加权、合成比例上限，产出候选模型。', keys: [], cfg: ['train.hard_case_weight', 'train.synthetic_ratio_cap'] },
  T8: { desc: '分切片评测门禁 + 污染检查：任一真实切片退化即拒绝发布。', keys: [], cfg: ['eval.slices', 'eval.regression_threshold'] },
  T9: { desc: '模型仓库登记、量化编译、manifest 与签名。', keys: [], cfg: ['hardware.targets'] },
  T10: { desc: 'OTA 下载 → 校验 → 安装 → 切换 → 影子 → 灰度 → 回滚，全程回执。', keys: [], cfg: ['ota.rollout', 'ota.rollback_threshold'] },
  M11: { desc: '遥测聚合：延迟、内存、置信度分布、难例率、漂移检测与回流。', keys: [], cfg: ['monitor.psi_alert', 'monitor.latency_p95_ms'] },
  M12: { desc: '版本治理：最低支持版本、强制升级、离线补升级与过期删除。', keys: [], cfg: ['governance.max_fragmentation'] },
  C3: { desc: '算力与成本：GPU 小时、人工分钟、L2 调用与预算对照。', keys: [], cfg: ['cost.budget'] },
  C4: { desc: '审计：所有关键动作写入 audit.jsonl，可回放、可导出。', keys: [], cfg: [] },
};

function node(def, id) {
  const info = NODE_INFO[id] || {};
  def.info = info;
  return `<button type="button" class="node" data-node="${esc(id)}">
    <div class="node-top"><span class="node-id">${esc(id)}</span>${def.flag ? '<span class="node-flag">⚠</span>' : ''}</div>
    <div class="node-name">${esc(def.name)}</div>
    <div class="node-metric">${def.metric}${def.unit ? `<small>${esc(def.unit)}</small>` : ''}</div>
    <div class="node-metric-label">${esc(def.label || '')}</div>
    ${def.branches?.length ? `<div class="node-branches">${def.branches.map((b) => `<span>${esc(b)}</span>`).join('')}</div>` : ''}
  </button>`;
}

function flow(defs, map) {
  return defs.map((d) => {
    map.set(d.id, d);
    return node(d, d.id);
  }).join(`<span class="flow-arrow">${icon('chevronRight')}</span>`);
}

function lane(id, name, sub, cls, body) {
  return `<div class="lane ${cls}">
    <div class="lane-head"><span class="lane-id">${esc(id)}</span><span class="lane-name">${esc(name)}</span><span class="lane-sub">${esc(sub)}</span></div>
    <div class="lane-flow">${body}</div>
  </div>`;
}

function groupCounters(counters) {
  const groups = [
    { prefix: 'a', label: 'L1 接入' },
    { prefix: 'b', label: 'L2 发现' },
    { prefix: 'd', label: 'L2 路由' },
    { prefix: 'p', label: 'L3 飞轮' },
    { prefix: 't', label: 'L4 发布' },
  ];
  const rows = Object.entries(counters || {}).map(([k, v]) => {
    const [g, ...rest] = k.split('.');
    const group = groups.find((x) => x.prefix === g);
    return { key: k, value: v, group: group?.label || '其它', name: rest.join('.') || k };
  });
  rows.sort((a, b) => (a.group + a.key).localeCompare(b.group + b.key));
  return rows;
}

export async function render(root, ctx) {
  const { items } = await api.rounds();
  if (!items.length) {
    root.innerHTML = pageHead({ title: '飞轮轮次', sub: '尚无轮次报告', actions: `<button class="btn btn-primary btn-sm" data-act="run">${icon('play')}<span>运行闭环</span></button>` })
      + card({ body: emptyState({ iconName: 'flow', title: '还没有闭环轮次', text: '运行 2–3 轮后，这里会展示 L1→L5 的完整链路与每轮计数。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const latest = items[items.length - 1];
  const pick = Number(ctx.params.get('round')) || Number(latest.round);
  const rep = items.find((x) => Number(x.round) === pick) || latest;
  const c = rep.counters || {};
  const cost = rep.cost || {};
  const mon = rep.monitor || {};
  const gov = rep.governance || {};
  const ev = rep.eval || {};
  const rel = rep.release || {};
  const n2 = rep.n2 || {};
  const cal = rep.calibration || {};
  const gatePass = (Number(c['p4.passed']) || 0) + (Number(c['p4.rejected']) || 0);
  const gateRate = gatePass ? (Number(c['p4.passed']) || 0) / gatePass : 0;
  const psiLimit = thr('psi');
  const gateMin = thr('gate_min_pass_rate');
  const fragMax = thr('fragmentation');

  const idx = items.indexOf(rep);
  const prev = idx > 0 ? items[idx - 1] : null;
  const next = idx < items.length - 1 ? items[idx + 1] : null;

  const nodeMap = new Map();
  const L1 = flow([
    { id: 'A0', name: '设备代理信号', metric: num(c['a0.hard_cases']), unit: '条', label: '难例回流' },
    { id: 'A2', name: '隐私网关', metric: num(c['a0.budget_dropped']), unit: '条', label: '超配额丢弃' },
    { id: 'A4', name: 'ingest_event', metric: num(c['a4.ingest_events']), unit: '条', label: '统一接入' },
  ], nodeMap);
  const L2 = flow([
    { id: 'B2', name: 'L1 小模型筛查', metric: num(c['b2.screened']), unit: '条', label: rep.detector_eval?.detector ? `${rep.detector_eval.detector} · 准确率 ${pct(rep.detector_eval.accuracy, 0)}` : '低成本初筛' },
    { id: 'D1', name: 'L1 路由', metric: num(c['d1.escalate']), unit: '条', label: '升级至 L2', branches: [`自动 ${num(c['d2.auto_accept'])}`] },
    { id: 'D2', name: 'L2 路由', metric: num(c['d2.human']), unit: '条', label: '转人工仲裁' },
    { id: 'B7', name: 'defect_event', metric: num(c['b7.confirmed']), unit: '条', label: '确认缺陷', branches: [`拒绝 ${num(c['b7.rejected'])}`] },
    { id: 'N1', name: '负样本池', metric: num(c['b7.rejected']), unit: '条', label: '误报 / 拒绝' },
  ], nodeMap);
  const L3 = flow([
    { id: 'P2', name: '入湖 & 锚点', metric: num(c['p2.anchors']), unit: '个', label: '待扩充锚点' },
    { id: 'P3', name: '世界模型扩充', metric: num(c['p3.clips']), unit: '段', label: Object.keys(rep.world?.by_model || {}).join(' / ') || '生成片段', flag: Number(c['p3.generation_failed'] || 0) > 0 },
    { id: 'P4', name: '几何门禁', metric: num(c['p4.passed']), unit: '段', label: `通过率 ${pct(gateRate, 1)}`, flag: gateRate < gateMin, branches: [`拒绝 ${num(c['p4.rejected'])}`] },
    { id: 'P5', name: '多智能体标注', metric: num(c['p5.annotations']), unit: '条', label: `人工 ${num(c['p5.human'])} · 自动 ${num(c['p5.auto'])}` },
    { id: 'P6', name: '数据集导出', metric: esc(rep.dataset || '–'), unit: '', label: 'COCO / YOLO / 对话' },
    { id: 'N2', name: '拒绝池', metric: num(n2.rejected), unit: '段', label: '失败归因回流' },
  ], nodeMap);
  const L4 = flow([
    { id: 'T7', name: '增量训练', metric: fixed(cost.gpu_hours, 1), unit: 'h', label: 'GPU 训练成本' },
    { id: 'T8', name: '评测门禁', metric: ev.passed ? '通过' : '拒绝', unit: '', label: (ev.regressions || []).join(', ') || '无回归', flag: !ev.passed },
    { id: 'T9', name: '编译 & 签名', metric: esc(rep.model || '–'), unit: '', label: '多硬件产物' },
    { id: 'T10', name: 'OTA 灰度', metric: esc(rel.status || '–'), unit: '', label: 'internal → full', flag: String(rel.status || '').includes('roll') },
  ], nodeMap);
  const L5 = flow([
    { id: 'M11', name: '遥测 & 漂移', metric: fixed(mon.psi, 2), unit: 'PSI', label: mon.psi > psiLimit ? `超过 ${psiLimit} 阈值` : '分布稳定', flag: mon.psi > psiLimit },
    { id: 'M12', name: '版本治理', metric: pct(gov.fragmentation, 1), unit: '', label: `最新 ${esc(gov.latest || '–')}`, flag: Number(gov.fragmentation) > fragMax },
  ], nodeMap);
  const CX = flow([
    { id: 'C3', name: '成本核算', metric: fixed(cost.human_minutes, 0), unit: 'min', label: `GPU ${fixed(cost.gpu_hours, 1)}h · L2 ${num(cost.l2_calls)} 次` },
    { id: 'C4', name: '审计留痕', metric: 'JSONL', unit: '', label: '关键动作可回放' },
  ], nodeMap);

  const chips = items.map((x) => {
    const gx = Number((x.counters || {})['p4.passed']) || 0;
    const gr = Number((x.counters || {})['p4.rejected']) || 0;
    const rate = gx + gr ? gx / (gx + gr) : 0;
    return `<button type="button" class="chip ${Number(x.round) === Number(rep.round) ? 'active' : ''}" data-round="${x.round}">
      第 ${x.round} 轮 ${rate < gateMin ? '<span class="count" style="color:var(--amber)">⚠</span>' : `<span class="count">${pct(rate, 0)}</span>`}
    </button>`;
  }).join('');

  const kpis = `<div class="grid g-kpi mt-14">
    ${kpi({ label: '接入事件', value: num(c['a4.ingest_events']), foot: `难例 ${num(c['a0.hard_cases'])} · 丢弃 ${num(c['a0.budget_dropped'])}`, iconName: 'download', spark: '' })}
    ${kpi({ label: '确认缺陷', value: num(c['b7.confirmed']), foot: `拒绝 ${num(c['b7.rejected'])} · 人工 ${num(c['d2.human'])}`, iconName: 'target', tone: 'violet' })}
    ${kpi({ label: '门禁通过率', value: pct(gateRate, 1), foot: `${num(c['p4.passed'])} 通过 / ${num(c['p4.rejected'])} 拒绝`, iconName: 'shield', tone: gateRate < gateMin ? 'amber' : 'green' })}
    ${kpi({ label: '新增标注', value: num(c['p5.annotations']), foot: `自动 ${num(c['p5.auto'])} · 人工 ${num(c['p5.human'])}`, iconName: 'tag', tone: 'teal' })}
    ${kpi({ label: '漂移 PSI', value: fixed(mon.psi, 2), foot: mon.psi > psiLimit ? `超过告警阈值 ${psiLimit}` : `低于告警阈值 ${psiLimit}`, iconName: 'activity', tone: mon.psi > psiLimit ? 'amber' : 'green' })}
    ${kpi({ label: 'GPU 训练', value: `${fixed(cost.gpu_hours, 1)}<span class="tiny muted"> h</span>`, foot: `人工 ${fixed(cost.human_minutes, 0)} min`, iconName: 'cpu' })}
    ${kpi({ label: 'T8 评测', value: ev.passed === undefined ? '–' : (ev.passed ? '通过' : '拒绝'), foot: (ev.regressions || []).length ? `回归：${esc(ev.regressions.join(', '))}` : '各切片不退化', iconName: 'trend', tone: ev.passed ? 'green' : 'red' })}
    ${kpi({ label: '发布状态', value: stageBadge(rel.status), foot: `${esc(rep.model || '–')} · ${esc(rep.dataset || '–')}`, iconName: 'rocket', tone: rel.status === 'released' ? 'green' : '' })}
  </div>`;

  const historyChart = lineChart({
    labels: items.map((x) => `R${x.round}`),
    series: [
      { name: '接入事件', values: items.map((x) => (x.counters || {})['a4.ingest_events'] || 0), color: palette()[0] },
      { name: '确认缺陷', values: items.map((x) => (x.counters || {})['b7.confirmed'] || 0), color: palette()[1] },
      { name: '新增标注', values: items.map((x) => (x.counters || {})['p5.annotations'] || 0), color: palette()[3] },
    ],
    height: 190,
    format: (v) => num(v),
  });

  const qualityChart = lineChart({
    labels: items.map((x) => `R${x.round}`),
    series: [
      {
        name: '门禁通过率',
        values: items.map((x) => {
          const a = Number((x.counters || {})['p4.passed']) || 0;
          const b = Number((x.counters || {})['p4.rejected']) || 0;
          return a + b ? (a / (a + b)) * 100 : 0;
        }),
        color: palette()[1],
      },
    ],
    height: 190,
    threshold: gateMin * 100, thresholdLabel: `最低通过率 ${pct(gateMin, 0)}`,
    format: (v) => `${v.toFixed(0)}%`,
  });

  const counterRows = groupCounters(c);
  const counterTable = counterRows.length ? `<div class="table-wrap"><table class="data compact"><thead><tr>
      <th>分层</th><th>计数键</th><th class="num">值</th>
    </tr></thead><tbody>${counterRows.map((r) => `<tr>
      <td><span class="badge muted" style="font-size:10px">${esc(r.group)}</span></td>
      <td class="id">${esc(r.key)}</td>
      <td class="num">${num(r.value)}</td>
    </tr>`).join('')}</tbody></table></div>` : emptyState({ title: '本轮没有计数' });

  const alerts = (mon.alerts || []).map((a) => `
    <div class="alert-row ${alertTone(a.kind)}">
      ${icon(a.kind === 'drift' ? 'activity' : 'warn')}
      <div><div class="alert-msg">${esc(a.message)}</div><div class="alert-meta">建议动作 ${esc(a.action || '–')}</div></div>
      ${badge(a.kind, alertTone(a.kind))}
    </div>`).join('');

  const govDist = Object.entries(gov.version_distribution || {}).sort((a, b) => String(b[0]).localeCompare(String(a[0])));

  root.innerHTML = `
    ${pageHead({
      title: '飞轮轮次',
      tag: `R${rep.round}`,
      sub: `<span>${esc(rep._file || `round-${rep.round}.json`)}</span><span class="sep"></span><span>${esc(ts(rep._mtime))}</span><span class="sep"></span><span>${items.length} 轮已归档</span>`,
      actions: `
        <button class="btn btn-sm btn-ghost" data-nav="prev" ${prev ? '' : 'disabled'}>${icon('chevronLeft')}<span>上一轮</span></button>
        <button class="btn btn-sm btn-ghost" data-nav="next" ${next ? '' : 'disabled'}>${icon('chevronRight')}<span>下一轮</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="card">
      <div class="spread" style="gap:14px;align-items:flex-start">
        <div class="round-switch">${chips}</div>
        <div class="row tiny muted"><span>最近一轮 ${esc(ts(rep._mtime))}</span></div>
      </div>
    </div>

    ${kpis}

    <div class="card mt-20">
      <div class="card-head">
        <div>
          <div class="card-title"><span class="dot"></span>闭环流水线</div>
          <div class="card-sub">点击任意节点查看职责、计数口径与配置项</div>
        </div>
        <div class="card-actions">${badge(`第 ${rep.round} 轮`, 'info')}</div>
      </div>
      <div class="lanes">
        ${lane('L1', '数据与接入', 'A0–A4 · 设备回流 / 网关 / 入湖', 'l-l1', L1)}
        ${lane('L2', '缺陷发现分级', 'B2 / D1 / D2 / B7 / N1', 'l-l2', L2)}
        ${lane('L3', '云端飞轮', 'P2–P6 / N2 · 扩充 → 门禁 → 标注 → 导出', 'l-l3', L3)}
        ${lane('L4', '训练与发布', 'T7–T10 · 训练 / 评测 / 编译 / OTA', 'l-l4', L4)}
        ${lane('L5', '设备与监控', 'M11 / M12 · 漂移 / 治理', 'l-l5', L5)}
        ${lane('CX', '横切能力', 'C3 成本 / C4 审计', 'l-cx', CX)}
      </div>
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        <div class="g-2">
          ${card({ title: '流量与产出', sub: '接入 → 确认 → 标注', body: historyChart })}
          ${card({ title: '门禁通过率', sub: '合成数据质量随轮次变化', body: qualityChart })}
        </div>
        ${card({
          title: '本轮计数明细',
          sub: 'report.counters 全量口径',
          pad0: true,
          body: counterTable,
        })}
        <div class="card">
          <div class="card-head"><div><div class="card-title"><span class="dot"></span>报告原文</div>
            <div class="card-sub">${esc(rep._file || '')} · 只读快照</div></div></div>
          <details>
            <summary class="small muted" style="cursor:pointer">展开 JSON</summary>
            <pre class="code-block">${esc(JSON.stringify(rep, null, 2))}</pre>
          </details>
        </div>
      </div>

      <div class="stack">
        ${card({
          title: '监控与告警',
          sub: mon.devices_reporting ? `${num(mon.devices_reporting)} 台设备上报 · 难例率 ${pct(mon.hard_case_rate, 2)}` : '本轮无遥测',
          actions: mon.psi > psiLimit ? badge('漂移', 'warn') : badge('稳定', 'ok'),
          body: `${alerts ? `<div class="list">${alerts}</div>` : '<div class="panel-note">本轮没有触发告警。</div>'}
            <div class="mt-14">${barRow('PSI', mon.psi || 0, 2, { display: fixed(mon.psi, 2), tone: mon.psi > psiLimit ? 'amber' : 'green' })}</div>`,
        })}
        ${card({
          title: 'T8 评测详情',
          sub: `${esc(rep.model || '–')} vs ${esc(ev.baseline_version || '基线')}`,
          body: ev.slices ? `<div class="list">${Object.entries(ev.slices).map(([slice, v]) => {
            const cand = Number(v.candidate) || 0;
            const base = Number(v.baseline) || 0;
            const diff = cand - base;
            const bad = diff < -0.01;
            return `<div class="list-row">
              <span class="badge ${bad ? 'bad' : 'ok'}" style="min-width:62px;justify-content:center">${esc(slice)}</span>
              <div class="grow">
                <div class="mono-sm">${pct(cand, 2)} <span class="muted">vs ${pct(base, 2)}</span></div>
                <div class="bar ${bad ? 'red' : 'green'}" style="margin-top:5px"><span style="width:${Math.min(100, cand * 100).toFixed(1)}%"></span></div>
              </div>
              <span class="mono-sm" style="color:${bad ? 'var(--red)' : 'var(--green)'}">${diff >= 0 ? '+' : ''}${(diff * 100).toFixed(2)}pp</span>
            </div>`;
          }).join('')}</div>` : '<div class="panel-note">本轮没有 T8 报告。</div>',
        })}
        ${card({
          title: '成本与治理',
          sub: 'C3 预算 · M12 版本分布',
          body: `<div class="mini-grid">
              ${miniStat('L2 调用', num(cost.l2_calls), '大模型路由次数')}
              ${miniStat('人工分钟', fixed(cost.human_minutes, 0), 'B6 / P5 仲裁')}
              ${miniStat('碎片率', pct(gov.fragmentation, 1), `最新 ${esc(gov.latest || '–')}`)}
            </div>
            <div class="dotline"></div>
            <div class="list">${govDist.length ? govDist.map(([v, n]) => barRow(v, n, Math.max(...govDist.map(([, x]) => x)), { display: `${n} 台` })).join('') : '<div class="muted tiny">暂无版本分布</div>'}</div>
            ${gov.force_upgrade?.length ? `<div class="mt-14">${callout('warn', `强制升级：${esc(gov.force_upgrade.join(', '))}`, 'warn')}</div>` : ''}`,
        })}
        ${card({
          title: 'B8 校准抽检',
          sub: cal.sampled ? `抽样 ${num(cal.sampled)} 条` : '本轮未抽样',
          body: `<div class="mini-grid">
            ${miniStat('自动接受精度', cal.auto_accept_precision === undefined ? '–' : pct(cal.auto_accept_precision, 1), '抽检命中率')}
            ${miniStat('错误数', num(cal.errors), '自动接受后被推翻')}
          </div>
          ${cal.error_cases?.length ? `<div class="code-block mt-14">${esc(JSON.stringify(cal.error_cases, null, 2))}</div>` : ''}`,
        })}
      </div>
    </div>
  `;

  root.querySelectorAll('[data-round]').forEach((b) => b.addEventListener('click', () => ctx.go('rounds', { round: b.dataset.round })));
  root.querySelector('[data-nav="prev"]')?.addEventListener('click', () => prev && ctx.go('rounds', { round: prev.round }));
  root.querySelector('[data-nav="next"]')?.addEventListener('click', () => next && ctx.go('rounds', { round: next.round }));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));

  root.querySelectorAll('[data-node]').forEach((el) => el.addEventListener('click', () => {
    const def = nodeMap.get(el.dataset.node);
    if (!def) return;
    const info = def.info || {};
    const metric = `${def.metric}${def.unit ? ` ${def.unit}` : ''}`;
    ctx.drawer({
      eyebrow: `流水线节点 · ${def.id}`,
      title: def.name,
      body: `${info.desc ? `<div class="callout info">${icon('info')}<div>${esc(info.desc)}</div></div>` : ''}
        <div class="detail-section mt-14"><h4>本轮产出</h4>${kv([
          ['指标', `<b class="mono-sm">${metric}</b>`],
          ['说明', esc(def.label || '–')],
          ['计数键', (info.keys || []).length ? info.keys.map((k) => `<code class="code-inline">${esc(k)} = ${num(c[k])}</code>`).join('<br>') : '<span class="muted">本轮无独立计数</span>'],
        ])}</div>
        <div class="detail-section"><h4>相关配置</h4>${(info.cfg || []).length ? (info.cfg || []).map((k) => `<code class="code-inline" style="display:inline-block;margin:0 4px 6px 0">${esc(k)}</code>`).join('') : '<span class="muted small">无</span>'}</div>
        <div class="detail-section"><h4>上下游</h4><div class="panel-note">节点编号与设计文档一致；泳道内的箭头表示单轮数据流方向，跨轮回流由 M11 → A1 完成。</div></div>`,
    });
  }));
}
