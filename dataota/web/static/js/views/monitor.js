/* 漂移监控：PSI、延迟、置信度分布、M11 告警与 M12 治理 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, alertTone, ts } from '../util.js';
import { pageHead, kpi, card, emptyState, callout, kv, miniStat, barRow } from '../ui.js';
import { lineChart, barChart, palette, histogram } from '../charts.js';
import { thr } from '../meta.js';

const KIND_LABEL = { drift: '分布漂移', plateau: '精度停滞', latency: '延迟超限', rollback_rate: '回滚率', fragmentation: '版本碎片' };

export async function render(root, ctx) {
  const m = await api.monitor();
  const reports = m.reports || [];
  if (!reports.length) {
    root.innerHTML = pageHead({ title: '漂移监控', sub: '还没有 M11 监控报告' })
      + card({ body: emptyState({ iconName: 'activity', title: '暂无监控数据', text: '运行一轮闭环后，M11 会聚合遥测并计算 PSI、P95 与难例率。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const last = reports[reports.length - 1];
  const psi = Number(last.psi) || 0;
  const p95 = m.latency_series?.[m.latency_series.length - 1]?.p95 || 0;
  const alerts = m.alerts || [];
  const gov = m.governance || {};
  const dist = gov.version_distribution || {};
  const psiLimit = Number(m.psi_threshold) || thr('psi');
  const latLimit = Number(m.latency_threshold) || thr('latency_p95_ms');
  const driftMethod = thr('drift_method') || 'psi';
  const driftThr = thr('drift') || {};
  const driftNow = last.drift || { psi: last.psi };

  const psiChart = lineChart({
    labels: (m.psi_series || []).map((x) => `R${x.round}`),
    series: [{ name: 'PSI', values: (m.psi_series || []).map((x) => x.psi), color: palette()[2] }],
    height: 210,
    threshold: psiLimit,
    thresholdLabel: `告警阈值 ${psiLimit}`,
    format: (v) => fixed(v, 2),
  });

  const latChart = lineChart({
    labels: (m.latency_series || []).map((x) => `R${x.round}`),
    series: [
      { name: 'P95 平均', values: (m.latency_series || []).map((x) => x.p95), color: palette()[0] },
      { name: 'P50 平均', values: (m.latency_series || []).map((x) => x.p50), color: palette()[1] },
    ],
    height: 210,
    threshold: latLimit,
    thresholdLabel: `SLO ${latLimit}ms`,
    format: (v) => `${v.toFixed(0)}ms`,
  });

  const histChart = (m.baseline_hist || []).length && (m.current_hist || []).length
    ? barChart({
        labels: m.current_hist.map((_, i) => `${(i / 10).toFixed(1)}`),
        series: [
          { name: '基线分布', values: m.baseline_hist.map((v) => v * 100), color: palette()[3] },
          { name: '当前分布', values: m.current_hist.map((v) => v * 100), color: palette()[0] },
        ],
        height: 200,
        format: (v) => `${v.toFixed(0)}%`,
      })
    : '<div class="panel-note">缺少基线或当前直方图。</div>';

  const alertRows = alerts.slice().reverse().map((a) => `
    <tr>
      <td><span class="badge muted">R${a.round ?? '–'}</span></td>
      <td>${badge(KIND_LABEL[a.kind] || a.kind, alertTone(a.kind), false)}</td>
      <td>${esc(a.message)}</td>
      <td>${esc(a.action || '–')}</td>
    </tr>`).join('');

  const reportRows = reports.slice().reverse().map((r) => `
    <tr class="clickable" data-round="${r.round}">
      <td><span class="badge info">R${r.round}</span></td>
      <td class="num">${num(r.devices_reporting)}</td>
      <td class="num" style="color:${Number(r.psi) > psiLimit ? 'var(--red)' : 'inherit'}">${fixed(r.psi, 3)}</td>
      <td class="num">${r.drift ? fixed(r.drift.js, 3) : '–'}</td>
      <td class="num">${r.drift ? fixed(r.drift.ks, 3) : '–'}</td>
      <td class="num">${pct(r.hard_case_rate, 2)}</td>
      <td class="num">${num((r.alerts || []).length)}</td>
      <td>${(r.alerts || []).length ? badge((r.alerts || [])[0].kind, 'warn') : badge('稳定', 'ok')}</td>
    </tr>`).join('');

  const govDist = Object.entries(dist).sort((a, b) => String(b[0]).localeCompare(String(a[0])));
  const govMax = Math.max(1, ...govDist.map(([, v]) => v));

  root.innerHTML = `
    ${pageHead({
      title: '漂移监控',
      tag: `R${last.round}`,
      sub: `<span>${num(last.devices_reporting)} 台设备上报</span><span class="sep"></span><span>PSI 阈值 ${fixed(psiLimit, 2)}</span><span class="sep"></span><span>P95 SLO ${num(latLimit)} ms</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="fleet">${icon('target')}<span>设备页</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '置信度 PSI', value: fixed(psi, 2), foot: psi > psiLimit ? `超过阈值 ${fixed(psiLimit, 2)}` : `低于阈值 ${fixed(psiLimit, 2)}`, iconName: 'activity', tone: psi > psiLimit ? 'amber' : 'green' })}
      ${kpi({ label: 'P95 延迟', value: `${fixed(p95, 1)}<span class="tiny muted"> ms</span>`, foot: `SLO ${num(latLimit)} ms`, iconName: 'clock', tone: p95 > latLimit ? 'red' : 'green' })}
      ${kpi({ label: '漂移检测', value: `${esc(driftMethod.toUpperCase())} ${fixed(driftNow[driftMethod] ?? psi, 3)}`, foot: ['psi', 'js', 'ks'].map((k) => `${k.toUpperCase()} ${driftNow[k] === undefined ? '–' : fixed(driftNow[k], 3)}${driftThr[k] !== undefined ? `/${driftThr[k]}` : ''}`).join(' · '), iconName: 'trend', tone: Number(driftNow[driftMethod] ?? psi) > Number(driftThr[driftMethod] ?? psiLimit) ? 'amber' : 'green' })}
      ${kpi({ label: '难例率', value: pct(m.hard_case_rate, 2), foot: '高难例会回流 A1 补样', iconName: 'alert', tone: 'violet' })}
      ${kpi({ label: '活跃告警', value: num(alerts.length), foot: `${new Set(alerts.map((a) => a.kind)).size} 种类型`, iconName: 'warn', tone: alerts.length ? 'amber' : 'green' })}
      ${kpi({ label: '版本碎片率', value: pct(gov.fragmentation, 1), foot: `最新版本 ${esc(gov.latest || '–')}`, iconName: 'layers', tone: Number(gov.fragmentation) > thr('fragmentation') ? 'red' : 'green' })}
      ${kpi({ label: '过期删除', value: num(gov.expired_deleted), foot: 'M12 生命周期清理', iconName: 'box' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({
          title: 'PSI 漂移趋势',
          sub: '置信度分布相对基线的群体稳定性指数',
          actions: psi > psiLimit ? badge('已触发漂移', 'warn') : badge('分布稳定', 'ok'),
          body: psiChart,
        })}
        ${card({
          title: '置信度分布对比',
          sub: '基线 vs 最新一轮车队平均，10 个分桶',
          body: histChart,
        })}
        ${card({
          title: 'M11 报告',
          sub: '每轮遥测聚合结果，点击查看对应轮次',
          pad0: true,
          body: `<div class="table-wrap" style="border:0;border-radius:0"><table class="data">
            <thead><tr><th>轮次</th><th class="num">设备</th><th class="num">PSI</th><th class="num">JS</th><th class="num">KS</th><th class="num">难例率</th><th class="num">告警</th><th>状态</th></tr></thead>
            <tbody>${reportRows}</tbody></table></div>`,
        })}
      </div>

      <div class="stack">
        ${card({ title: '延迟趋势', sub: `P95 SLO ${num(latLimit)}ms`, body: latChart })}
        ${card({
          title: '告警与回流动作',
          sub: '告警不只展示，还带回流动作',
          pad0: true,
          body: alertRows ? `<div class="table-wrap" style="border:0;border-radius:0"><table class="data compact">
            <thead><tr><th>轮次</th><th>类型</th><th>消息</th><th>动作</th></tr></thead>
            <tbody>${alertRows}</tbody></table></div>` : emptyState({ title: '没有告警', text: 'PSI、延迟与难例率都在阈值内。', iconName: 'shield' }),
        })}
        ${card({
          title: 'M12 版本治理',
          sub: `上限 ${pct(0.3, 0)} · 当前 ${pct(gov.fragmentation, 1)}`,
          body: `<div class="list">${govDist.length ? govDist.map(([v, n]) => barRow(v, n, govMax, { display: `${num(n)} 台`, tone: v === gov.latest ? 'green' : 'amber' })).join('') : '<div class="panel-note">暂无版本分布。</div>'}</div>
            ${(gov.force_upgrade || []).length ? `<div class="mt-14">${callout('warn', `强制升级：${esc(gov.force_upgrade.join(', '))}`, 'warn')}</div>` : '<div class="panel-note mt-14">无需强制升级。</div>'}`,
        })}
        ${card({
          title: '监控口径',
          sub: '告警阈值与回流关系',
          body: `${kv([
            ['PSI 告警', fixed(psiLimit, 2)],
            ['P95 SLO', `${num(latLimit)} ms`],
            ['难例率', pct(m.hard_case_rate, 2)],
            ['上报设备', `${num(last.devices_reporting)} 台`],
          ])}
          <div class="panel-note mt-14">漂移告警会回流难例到 A1 重新入湖；若连续多轮指标无提升，M11 触发「精度停滞」并补锚点到 P3。</div>`,
        })}
      </div>
    </div>
  `;

  root.querySelectorAll('[data-act="fleet"]').forEach((b) => b.addEventListener('click', () => ctx.go('fleet')));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-round]').forEach((tr) => tr.addEventListener('click', () => ctx.go('rounds', { round: tr.dataset.round })));
}
