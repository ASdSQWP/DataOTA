/* 运行总览：KPI、趋势、漏斗、健康度、发布与告警 */

import { api } from '../api.js';
import {
  esc, num, fixed, pct, ts, relTime, badge, statusBadge, alertTone,
  icon, LAYER_LABEL,
} from '../util.js';
import {
  pageHead, kpi, card, emptyState, callout, tabs, barRow, stageBadge,
  healthCard, miniStat,
} from '../ui.js';
import { lineChart, barChart, donut, sparkline, palette } from '../charts.js';
import { thr } from '../meta.js';

let trendMetric = 'ingest';

const TRENDS = {
  ingest: { label: '接入 / 确认', unit: '' },
  gate: { label: '门禁通过率', unit: '%' },
  models: { label: '检测 / 世界模型', unit: '%' },
  psi: { label: '漂移 PSI', unit: '' },
  cost: { label: '成本', unit: '' },
  frag: { label: '版本碎片率', unit: '%' },
};

function trendSpec(t, key) {
  const labels = (t.rounds || []).map((r) => `R${r}`);
  const num2 = (v) => Number(v) || 0;
  if (key === 'gate') {
    return {
      labels,
      series: [{ name: '合成门禁通过率', values: (t.gate_pass_rate || []).map((v) => num2(v) * 100), color: palette()[1] }],
      threshold: thr('gate_min_pass_rate') * 100, thresholdLabel: `最低可用率 ${pct(thr('gate_min_pass_rate'), 0)}`, format: (v) => `${v.toFixed(0)}%`,
    };
  }
  if (key === 'models') {
    const pctOrNull = (v) => (v === null || v === undefined ? null : Number(v) * 100);
    return {
      labels,
      series: [
        { name: 'B2 检测器准确率', values: (t.detector_accuracy || []).map(pctOrNull), color: palette()[0] },
        { name: 'P3 世界模型门禁通过率', values: (t.world_pass_rate || []).map(pctOrNull), color: palette()[1] },
      ],
      yMin: 0, yMax: 100, format: (v) => `${v.toFixed(0)}%`,
    };
  }
  if (key === 'psi') {
    return {
      labels,
      series: [{ name: 'PSI', values: (t.psi || []).map(num2), color: palette()[2] }],
      threshold: thr('psi'), thresholdLabel: `告警阈值 ${thr('psi')}`, format: (v) => v.toFixed(2),
    };
  }
  if (key === 'cost') {
    return {
      labels,
      series: [
        { name: 'GPU 小时', values: (t.gpu_hours || []).map(num2), color: palette()[0] },
        { name: '人工分钟', values: (t.human_minutes || []).map(num2), color: palette()[3] },
      ],
      format: (v) => (Math.abs(v) >= 100 ? Math.round(v).toString() : v.toFixed(1)),
    };
  }
  if (key === 'frag') {
    return {
      labels,
      series: [{ name: '碎片率', values: (t.fragmentation || []).map((v) => num2(v) * 100), color: palette()[4] }],
      threshold: thr('fragmentation') * 100, thresholdLabel: `上限 ${pct(thr('fragmentation'), 0)}`, format: (v) => `${v.toFixed(0)}%`,
    };
  }
  return {
    labels,
    series: [
      { name: '接入事件', values: (t.ingest || []).map(num2), color: palette()[0] },
      { name: '确认缺陷', values: (t.confirmed || []).map(num2), color: palette()[1] },
      { name: '拒绝', values: (t.rejected || []).map(num2), color: palette()[4] },
    ],
    format: (v) => num(v),
  };
}

function trendBody(t) {
  const spec = trendSpec(t, trendMetric);
  return `${tabs(Object.entries(TRENDS).map(([id, x]) => ({ id, label: x.label })), trendMetric)}
    <div class="mt-14">${lineChart({ ...spec, height: 205 })}</div>`;
}

function funnelHtml(funnel) {
  if (!funnel || !funnel.length) return emptyState({ title: '还没有漏斗数据', text: '运行一轮闭环后即可看到 L1→L3 的逐级转化。' });
  const first = funnel[0].value || 1;
  return `<div class="funnel">${funnel.map((f, i) => {
    const prev = i ? funnel[i - 1].value : f.value;
    const drop = i && prev ? (f.value - prev) / prev : 0;
    const w = f.value ? Math.max(1.5, (f.value / first) * 100) : 0;
    return `<div class="funnel-row">
      <span class="funnel-label">${esc(f.label)}</span>
      <div class="funnel-track"><div class="funnel-fill" style="width:${w.toFixed(1)}%"></div></div>
      <span class="funnel-val">${num(f.value)}${i ? `<span class="funnel-drop">${drop >= 0 ? '+' : ''}${(drop * 100).toFixed(0)}%</span>` : ''}</span>
    </div>`;
  }).join('')}</div>`;
}

function evalChart(model) {
  const ev = model?.eval;
  const slices = ev?.slices || {};
  const keys = Object.keys(slices);
  if (!keys.length) return emptyState({ title: '暂无 T8 评估', text: '模型生成后会写入真实留出集的门禁结果。' });
  return `${barChart({
    labels: keys,
    series: [
      { name: '候选模型', values: keys.map((k) => (Number(slices[k].candidate) || 0) * 100), color: palette()[0] },
      { name: '基线模型', values: keys.map((k) => (Number(slices[k].baseline) || 0) * 100), color: palette()[3] },
    ],
    height: 168,
    format: (v) => `${v.toFixed(0)}%`,
  })}
  <div class="row tiny muted" style="margin-top:6px;gap:10px">
    <span>回归阈值 ≤ ${((model?.eval?.regression_threshold ?? thr('regression')) * 100).toFixed(0)}%</span>
    ${ev?.passed ? badge('门禁通过', 'ok') : badge(`回归：${(ev?.regressions || []).join(', ') || '未知'}`, 'bad')}
    ${ev?.contamination_ok === false ? badge('留出集污染', 'bad') : ''}
  </div>`;
}

export async function render(root, ctx) {
  const o = await api.overview();
  const k = o.kpi || {};
  const t = o.trend || {};
  const r = o.latest_round || {};
  const health = o.health || {};
  const alerts = o.alerts || [];
  const model = o.latest_model;
  const rel = o.latest_release;
  const fleet = o.fleet || {};
  const spent = o.cost?.spent || {};
  const budget = o.cost?.budget || {};

  if (!o.has_data) {
    root.innerHTML = pageHead({
      title: '运行总览', tag: 'Empty',
      sub: '工作目录里还没有飞轮产物',
      actions: `<button class="btn btn-primary btn-sm" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    }) + card({
      body: emptyState({
        iconName: 'layers',
        title: '飞轮尚未启动',
        text: `工作目录 <code>${esc(o.workdir)}</code> 中还没有 round / dataset / model 产物。点击右上角「运行闭环」跑 2–3 轮，控制台会自动刷新。`,
        action: '<button class="btn btn-primary" data-act="run">运行闭环</button>',
      }),
    });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const h = health;
  const evalTone = h.eval_passed ? 'ok' : 'bad';
  const driftTone = h.drift_alerts ? 'warn' : 'ok';
  const fragTone = Number(h.fragmentation) > thr('fragmentation') ? 'bad' : Number(h.fragmentation) > thr('fragmentation') / 2 ? 'warn' : 'ok';
  const reviewTone = h.open_reviews ? 'warn' : 'ok';
  const rollbackTone = h.rolled_back ? 'bad' : 'ok';

  const actionButtons = `
    <button class="btn btn-sm btn-ghost" data-act="go-rounds">${icon('flow')}<span>查看轮次</span></button>
    <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`;

  const healthStrip = `<div class="health-grid">
    ${healthCard({ tone: evalTone, iconName: h.eval_passed ? 'shield' : 'alert', label: 'T8 评估门禁', value: h.eval_passed ? '通过' : `未通过 · ${esc((h.eval_regressions || []).join(', ') || '回归')}` })}
    ${healthCard({ tone: driftTone, iconName: 'activity', label: '漂移告警', value: `${h.drift_alerts || 0} 条 · PSI ${fixed(h.psi, 2)}` })}
    ${healthCard({ tone: fragTone, iconName: 'layers', label: '版本碎片率', value: pct(h.fragmentation, 1) })}
    ${healthCard({ tone: reviewTone, iconName: 'user', label: '人工待审', value: `${h.open_reviews || 0} 条` })}
    ${healthCard({ tone: rollbackTone, iconName: 'refresh', label: 'OTA 回滚', value: `${h.rolled_back || 0} 次` })}
  </div>`;

  const spark = (arr) => sparkline(arr || [], { height: 26 });

  const kpis = `<div class="grid g-kpi mt-14">
    ${kpi({ label: '飞轮轮次', value: num(k.rounds), foot: `最近一轮 R${r.round ?? '–'} · ${relTime(o.generated_at)}`, iconName: 'flow', spark: spark(t.ingest) })}
    ${kpi({ label: '数据集版本', value: num(k.datasets), foot: `最新 ${esc(o.latest_dataset || '–')}`, iconName: 'database', tone: 'teal' })}
    ${kpi({ label: '模型版本', value: num(k.models), foot: `${esc(model?.model_version || '–')} · T8 ${model?.eval?.passed ? '通过' : '未通过'}`, iconName: 'cpu', tone: 'violet', spark: spark((t.gate_pass_rate || []).map((v) => v * 100)) })}
    ${kpi({ label: 'OTA 发布', value: num(k.releases), foot: rel ? `${esc(rel.release_id)} · ${esc(rel.status)}` : '暂无发布', iconName: 'rocket', spark: spark(t.fragmentation) })}
    ${kpi({ label: '车队设备', value: num(k.devices), foot: `<span class="muted">在线 ${num(fleet.online)} / ${num(fleet.total)}</span> · 碎片 ${pct(fleet.fragmentation, 1)}`, iconName: 'target', tone: 'green' })}
    ${kpi({ label: '标注样本', value: num(k.annotations), foot: '真实 + 合成，含人工仲裁', iconName: 'tag', tone: 'teal' })}
    ${kpi({ label: '待人工审核', value: num(k.open_reviews), foot: k.open_reviews ? '队列中有待裁决条目' : '审核队列已清空', iconName: 'user', tone: k.open_reviews ? 'amber' : 'green' })}
    ${kpi({ label: '审计事件', value: num(k.audit_events), foot: '全链路可追溯动作', iconName: 'list' })}
  </div>`;

  const alertsHtml = alerts.length ? `<div class="list">${alerts.slice(-5).reverse().map((a) => `
    <div class="alert-row ${alertTone(a.kind)}">
      ${icon(a.kind === 'drift' ? 'activity' : 'warn')}
      <div>
        <div class="alert-msg">${esc(a.message)}</div>
        <div class="alert-meta">R${a.round ?? '?'} · ${esc(a.kind)} · 建议 ${esc(a.action || '–')}</div>
      </div>
      ${badge(a.kind, alertTone(a.kind))}
    </div>`).join('')}</div>` : emptyState({ title: '没有活跃告警', text: '置信度分布、延迟与碎片率都在阈值内。', iconName: 'shield' });

  const dist = fleet.distribution || {};
  const distItems = Object.entries(dist).sort((a, b) => String(b[0]).localeCompare(String(a[0])))
    .map(([label, value]) => ({ label: label === 'none' ? '未部署' : label, value }));

  const relStages = (rel?.stages || []).map((s) => `
    <div class="list-row">
      <span class="badge muted" style="min-width:64px;justify-content:center">${esc(s.stage)}</span>
      <div class="grow">
        <div>${num(s.devices)} 台设备${s.disagree_rate !== undefined ? ` · 分歧 ${pct(s.disagree_rate, 1)}` : ''}${s.fail_rate !== undefined ? ` · 失败 ${pct(s.fail_rate, 1)}` : ''}</div>
        <div class="meta">${s.devices ? `${num(s.devices)} 台参与该阶段` : '本阶段无设备'}</div>
      </div>
      ${s.fail_rate > 0.25 ? badge('超阈值', 'bad') : ''}
    </div>`).join('');

  const recentModels = (o.models || []).map((m) => `
    <tr class="clickable" data-model="${esc(m.model_version)}">
      <td><span class="mono-sm">${esc(m.model_version)}</span></td>
      <td>${esc(m.dataset_version || '–')}</td>
      <td class="num">${pct(m.metrics?.day, 1)}</td>
      <td class="num">${pct(m.metrics?.night, 1)}</td>
      <td class="num">${pct(m.metrics?.far, 1)}</td>
      <td>${m.eval ? (m.eval.passed ? badge('通过', 'ok') : badge('回归', 'bad')) : '<span class="muted">未评估</span>'}</td>
      <td>${m.release ? stageBadge(m.release.status) : '<span class="muted">–</span>'}</td>
    </tr>`).join('');

  root.innerHTML = `
    ${pageHead({
      title: '运行总览',
      tag: `R${r.round ?? '–'}`,
      sub: `<span>最近闭环 <b>${esc(ts(r._mtime || o.generated_at))}</b></span><span class="sep"></span><span>${esc(o.workdir)}</span>`,
      actions: actionButtons,
    })}
    ${healthStrip}
    ${kpis}

    <div class="g-side mt-20">
      <div class="stack">
        ${card({
          title: '关键趋势',
          sub: '按轮次观察流量、门禁与成本，点击切换口径',
          body: `<div id="trend-wrap">${trendBody(t)}</div>`,
        })}
        <div class="g-2">
          ${card({
            title: '本轮漏斗',
            sub: 'L1 接入 → L2 缺陷 → L3 合成 → 标注',
            body: funnelHtml(o.funnel),
          })}
          ${card({
            title: 'T8 切片评估',
            sub: model ? `${esc(model.model_version)} vs 基线` : '',
            body: evalChart(model),
            actions: `<button class="btn btn-sm btn-ghost" data-act="go-models">模型页</button>`,
          })}
        </div>
        ${card({
          title: '最近模型',
          sub: '真实留出集门禁结果与发布状态',
          pad0: true,
          actions: `<button class="btn btn-sm btn-ghost" data-act="go-models">全部模型</button>`,
          body: recentModels ? `<div class="table-wrap" style="border:0;border-radius:0">
            <table class="data"><thead><tr>
              <th>模型</th><th>数据集</th><th class="num">day</th><th class="num">night</th><th class="num">far</th><th>T8</th><th>发布</th>
            </tr></thead><tbody>${recentModels}</tbody></table></div>` : emptyState({ title: '暂无模型' }),
        })}
      </div>

      <div class="stack">
        ${card({
          title: '最新发布',
          sub: rel ? `${esc(rel.release_id)} · ${esc(rel.model_version)}` : '还没有 OTA 发布',
          actions: stageBadge(rel?.status),
          body: rel ? `
            <div class="mini-grid" style="margin-bottom:12px">
              ${miniStat('成功设备', num(rel.success), 'released / shadow 切换成功')}
              ${miniStat('失败设备', num(rel.failed), '回滚或被拒绝')}
              ${miniStat('发布阶段', `${(rel.stages || []).length}`, 'internal → full')}
            </div>
            <div class="list">${relStages || '<div class="muted tiny">暂无阶段记录</div>'}</div>` : emptyState({ title: '暂无发布记录', text: 'T9 编译 + T10 灰度后会出现发布单。' }),
        })}
        ${card({
          title: '设备版本分布',
          sub: `${num(fleet.total)} 台设备 · 碎片率 ${pct(fleet.fragmentation, 1)}`,
          body: donut({ items: distItems, size: 156, centerLabel: '设备', centerValue: num(fleet.total) }),
          actions: `<button class="btn btn-sm btn-ghost" data-act="go-fleet">设备页</button>`,
        })}
        ${card({
          title: '活跃告警',
          sub: '来自 M11 漂移监控与 M12 治理',
          body: alertsHtml,
          actions: `<button class="btn btn-sm btn-ghost" data-act="go-monitor">监控页</button>`,
        })}
        ${card({
          title: '成本占用',
          sub: '累计 / 预算',
          body: `<div class="list">
            ${barRow('GPU 小时', spent.gpu_hours || 0, budget.gpu_hours || Math.max(spent.gpu_hours || 0, 1) * 1.4, { display: `${fixed(spent.gpu_hours, 1)} / ${fixed(budget.gpu_hours || 0, 0)}` })}
            ${barRow('人工分钟', spent.human_minutes || 0, budget.human_minutes || Math.max(spent.human_minutes || 0, 1) * 1.4, { display: `${fixed(spent.human_minutes, 0)} / ${fixed(budget.human_minutes || 0, 0)}`, tone: 'amber' })}
            ${barRow('L2 调用', spent.l2_calls || 0, budget.l2_calls || Math.max(spent.l2_calls || 0, 1) * 1.4, { display: `${fixed(spent.l2_calls, 0)} / ${fixed(budget.l2_calls || 0, 0)}`, tone: 'teal' })}
          </div>`,
        })}
      </div>
    </div>
  `;

  // ---- 交互
  const trendWrap = root.querySelector('#trend-wrap');
  trendWrap.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-tab]');
    if (!btn) return;
    trendMetric = btn.dataset.tab;
    trendWrap.innerHTML = trendBody(t);
  });

  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="go-rounds"]').forEach((b) => b.addEventListener('click', () => ctx.go('rounds')));
  root.querySelectorAll('[data-act="go-models"]').forEach((b) => b.addEventListener('click', () => ctx.go('models')));
  root.querySelectorAll('[data-act="go-fleet"]').forEach((b) => b.addEventListener('click', () => ctx.go('fleet')));
  root.querySelectorAll('[data-act="go-monitor"]').forEach((b) => b.addEventListener('click', () => ctx.go('monitor')));
  root.querySelectorAll('[data-model]').forEach((row) => row.addEventListener('click', () => ctx.go('models', { mv: row.dataset.model })));
}
