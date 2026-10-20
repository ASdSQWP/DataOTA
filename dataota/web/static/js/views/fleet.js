/* 设备与 OTA：车队版本分布、遥测明细、灰度发布与回执 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, ts, shortId, alertTone } from '../util.js';
import { pageHead, kpi, card, emptyState, callout, kv, miniStat, tabs, stageBadge, barRow } from '../ui.js';
import { barChart, donut, palette } from '../charts.js';
import { thr } from '../meta.js';

const FINAL_LABEL = {
  active: ['ok', '已切换'],
  pending: ['muted', '待处理'],
  failed: ['bad', '失败'],
  rolled_back: ['warn', '已回滚'],
  install_failed: ['bad', '安装失败'],
  verify_failed: ['bad', '校验失败'],
  switch_failed: ['bad', '切换失败'],
};

function finalBadge(s) {
  const [tone, label] = FINAL_LABEL[s] || ['muted', s || '–'];
  return badge(label, tone);
}

export async function render(root, ctx) {
  const [fleet, releases, tel] = await Promise.all([api.fleet(), api.releases(), api.telemetry()]);
  const tab = ctx.params.get('tab') || 'devices';
  const devices = fleet.devices || [];
  const rels = (releases.items || []).slice().reverse();
  const counts = { total: fleet.total || 0, online: fleet.online || 0 };

  const relTone = (r) => (r.rolled_back || String(r.status).startsWith('rolled_back') ? 'bad'
    : String(r.status) === 'released' ? 'ok' : String(r.status).includes('halted') ? 'warn' : 'muted');

  const kpis = `<div class="grid g-kpi">
    ${kpi({ label: '车队规模', value: num(counts.total), foot: `在线 ${num(counts.online)} · 离线 ${num(counts.total - counts.online)}`, iconName: 'target' })}
    ${kpi({ label: '最新版本', value: esc(fleet.latest_version || '–'), foot: '版本分布中的最高 mv', iconName: 'cpu', tone: 'violet' })}
    ${kpi({ label: '碎片率', value: pct(fleet.fragmentation, 1), foot: '非最新版本设备占比', iconName: 'layers', tone: Number(fleet.fragmentation) > thr('fragmentation') ? 'red' : Number(fleet.fragmentation) > thr('fragmentation') / 2 ? 'amber' : 'green' })}
    ${kpi({ label: '遥测轮次', value: `R${tel.round || '–'}`, foot: `${num(tel.devices_reporting)} 台设备上报`, iconName: 'activity', tone: 'teal' })}
    ${kpi({ label: 'OTA 发布单', value: num(rels.length), foot: `最近 ${esc(rels[0]?.release_id || '–')}`, iconName: 'rocket' })}
  </div>`;

  const dist = fleet.distribution || {};
  const distItems = Object.entries(dist).sort((a, b) => String(b[0]).localeCompare(String(a[0])))
    .map(([label, value], i) => ({ label: label === 'none' ? '未部署' : label, value, color: palette()[i % 5] }));

  // ---------------------------------------------------------------- 设备清单
  const hardwareSet = [...new Set(devices.map((d) => d.hardware).filter(Boolean))];
  const groupSet = [...new Set(devices.map((d) => d.group).filter(Boolean))];
  const versionSet = [...new Set(devices.map((d) => d.current_version).filter(Boolean))].sort();

  const devicesBody = () => {
    const hw = root.querySelector('#f-hw')?.value || '';
    const gp = root.querySelector('#f-gp')?.value || '';
    const vs = root.querySelector('#f-vs')?.value || '';
    const q = (root.querySelector('#f-q')?.value || '').toLowerCase();
    const rows = devices.filter((d) => (!hw || d.hardware === hw) && (!gp || d.group === gp) && (!vs || d.current_version === vs)
      && (!q || d.device_id.toLowerCase().includes(q)));
    if (!rows.length) {
      return `<tr><td colspan="8"><div class="table-empty">没有匹配的设备</div></td></tr>`;
    }
    return rows.map((d) => {
      const t = d.telemetry || {};
      const stale = d.current_version !== fleet.latest_version;
      return `<tr class="clickable" data-dev="${esc(d.device_id)}">
        <td><span class="mono-sm" style="font-weight:600">${esc(d.device_id)}</span></td>
        <td>${badge(d.group || '–', d.group === 'internal' ? 'violet' : 'muted', false)}</td>
        <td class="muted">${esc(d.hardware || '–')}</td>
        <td>${badge(d.current_version || 'none', stale ? 'warn' : 'ok')}</td>
        <td>${d.online ? badge('在线', 'ok') : badge('离线', 'muted')}</td>
        <td class="num">${t.latency_ms_p50 ? fixed(t.latency_ms_p50, 1) : '<span class="muted">–</span>'}</td>
        <td class="num">${t.latency_ms_p95 ? fixed(t.latency_ms_p95, 1) : '<span class="muted">–</span>'}</td>
        <td class="num">${t.hard_case_rate === undefined || t.hard_case_rate === null ? '<span class="muted">–</span>' : pct(t.hard_case_rate, 1)}</td>
      </tr>`;
    }).join('');
  };

  // ---------------------------------------------------------------- 遥测
  const telDevices = (tel.devices || []).slice().sort((a, b) => (b.latency_ms_p95 || 0) - (a.latency_ms_p95 || 0));
  const telTable = telDevices.length ? `<div class="table-wrap"><table class="data compact">
    <thead><tr><th>设备</th><th>模型</th><th class="num">P50 ms</th><th class="num">P95 ms</th><th class="num">内存 MB</th><th class="num">难例率</th><th class="num">影子分歧</th><th class="num">离线 h</th></tr></thead>
    <tbody>${telDevices.map((d) => `<tr class="clickable" data-tel="${esc(d.device_id)}">
      <td class="mono-sm">${esc(d.device_id)}</td>
      <td>${badge(d.model_version || '–', 'muted', false)}</td>
      <td class="num">${fixed(d.latency_ms_p50, 1)}</td>
      <td class="num" style="color:${d.latency_ms_p95 > 60 ? 'var(--red)' : 'inherit'}">${fixed(d.latency_ms_p95, 1)}</td>
      <td class="num">${fixed(d.mem_mb, 1)}</td>
      <td class="num">${pct(d.hard_case_rate, 2)}</td>
      <td class="num">${pct(d.shadow_disagree_rate, 2)}</td>
      <td class="num">${fixed(d.offline_hours, 1)}</td>
    </tr>`).join('')}</tbody></table></div>` : emptyState({ title: '没有遥测数据', text: 'M11 会在每轮结束后聚合设备上报。' });

  const p95Chart = barChart({
    labels: telDevices.slice(0, 10).map((d) => d.device_id.replace('dev-', 'D')),
    series: [{ name: 'P95 延迟', values: telDevices.slice(0, 10).map((d) => d.latency_ms_p95 || 0), color: palette()[2] }],
    height: 175,
    threshold: thr('latency_p95_ms'), thresholdLabel: `阈值 ${thr('latency_p95_ms')}ms`,
    format: (v) => `${v.toFixed(0)}ms`,
  });

  // ---------------------------------------------------------------- 发布单
  const releaseTable = rels.length ? `<div class="table-wrap"><table class="data">
    <thead><tr><th>发布单</th><th>模型</th><th>状态</th><th class="num">成功</th><th class="num">失败</th><th>阶段</th><th>签名</th></tr></thead>
    <tbody>${rels.map((r) => `<tr class="clickable" data-rel="${esc(r.release_id)}">
      <td class="mono-sm" style="font-weight:600">${esc(r.release_id)}</td>
      <td>${esc(r.model_version || '–')}</td>
      <td>${stageBadge(r.status)}</td>
      <td class="num">${num(r.success)}</td>
      <td class="num" style="color:${r.failed ? 'var(--red)' : 'inherit'}">${num(r.failed)}</td>
      <td><span class="muted tiny">${(r.stages || []).map((s) => esc(s.stage)).join(' → ')}</span></td>
      <td class="id" title="${esc(r.plan?.signature || '')}">${esc(shortId(r.plan?.signature || '–', 14))}</td>
    </tr>`).join('')}</tbody></table></div>` : emptyState({ title: '暂无发布单', text: 'T8 通过后 T10 会生成灰度计划与回执。' });

  const lastRel = rels[0];
  const lastStages = (lastRel?.stages || []).map((s) => {
    const fail = Number(s.fail_rate) || 0;
    const tone = fail > 0.25 ? 'red' : fail > 0 ? 'amber' : 'green';
    return `<div class="list-row">
      <span class="badge muted" style="min-width:66px;justify-content:center">${esc(s.stage)}</span>
      <div class="grow">
        <div>${num(s.devices)} 台设备${s.disagree_rate !== undefined ? ` · 影子分歧 ${pct(s.disagree_rate, 1)}` : ''}</div>
        ${fail ? `<div class="bar ${tone}" style="margin-top:5px"><span style="width:${Math.min(100, fail * 100 / 0.3).toFixed(0)}%"></span></div>` : '<div class="meta">无失败回执</div>'}
      </div>
      <span class="mono-sm">${s.fail_rate !== undefined ? `失败 ${pct(s.fail_rate, 1)}` : '–'}</span>
    </div>`;
  }).join('');

  const tabBody = tab === 'telemetry'
    ? `<div class="g-side">
        ${card({ title: '设备遥测明细', sub: `第 ${tel.round} 轮 · ${telDevices.length} 台设备`, pad0: true, body: telTable })}
        <div class="stack">
          ${card({ title: 'P95 延迟 Top 10', sub: `平均 ${fixed(tel.latency_p95_avg, 1)}ms · 最大 ${fixed(tel.latency_p95_max, 1)}ms`, body: p95Chart })}
          ${card({ title: '置信度分布', sub: '车队平均 · 0.1 分桶', body: tel.hist_avg?.length
            ? barChart({ labels: tel.hist_avg.map((_, i) => `${(i / 10).toFixed(1)}`), series: [{ name: '占比', values: tel.hist_avg.map((v) => v * 100) }], height: 170, format: (v) => `${v.toFixed(0)}%` })
            : '<div class="panel-note">暂无置信度直方图。</div>' })}
        </div>
      </div>`
    : tab === 'releases'
      ? `<div class="g-side">
          ${card({ title: 'OTA 发布单', sub: 'T10 灰度 / 影子 / 回滚', pad0: true, body: releaseTable })}
          <div class="stack">
            ${card({ title: '最近发布阶段', sub: lastRel ? `${esc(lastRel.release_id)} · ${esc(lastRel.status)}` : '暂无', body: lastStages || '<div class="panel-note">暂无阶段记录。</div>' })}
            ${card({ title: '发布约束', sub: '来自 ota 配置', body: `<div class="mini-grid">
              ${miniStat('回滚失败率', pct(lastRel?.plan?.rollback_threshold?.fail_rate ?? thr('rollback_fail_rate'), 0), '超过即自动回滚')}
              ${miniStat('影子分歧上限', pct(lastRel?.plan?.rollback_threshold?.shadow_disagree ?? thr('shadow_disagree'), 0), 'shadow 阶段判定')}
              ${miniStat('评测切片', num((lastRel?.plan?.eval_slices || []).length), (lastRel?.plan?.eval_slices || []).join(' · '))}
            </div>
            <div class="dotline"></div>
            ${(lastRel?.plan?.rollout_plan || []).map((s) => barRow(s.stage, Number(s.ratio) * 100, 100, { display: pct(s.ratio, 0) })).join('') || '<div class="panel-note">没有灰度计划。</div>'}` })}
          </div>
        </div>`
      : `<div class="g-side">
          ${card({
            title: '设备清单',
            sub: `共 ${devices.length} 台 · 碎片率 ${pct(fleet.fragmentation, 1)}`,
            actions: `<span class="tiny muted">点击行查看详情</span>`,
            body: `<div class="filterbar">
                <select id="f-hw"><option value="">全部硬件</option>${hardwareSet.map((h) => `<option value="${esc(h)}">${esc(h)}</option>`).join('')}</select>
                <select id="f-gp"><option value="">全部分组</option>${groupSet.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join('')}</select>
                <select id="f-vs"><option value="">全部版本</option>${versionSet.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select>
                <input id="f-q" type="search" placeholder="搜索 dev-xxx">
                <span class="grow"></span>
                <span class="tiny muted" id="f-count">${devices.length} 台</span>
              </div>
              <div class="table-wrap"><table class="data"><thead><tr>
                <th>设备</th><th>分组</th><th>硬件</th><th>当前版本</th><th>状态</th><th class="num">P50</th><th class="num">P95</th><th class="num">难例率</th>
              </tr></thead><tbody id="dev-body">${devicesBody()}</tbody></table></div>`,
          })}
          <div class="stack">
            ${card({ title: '版本分布', sub: `最新 ${esc(fleet.latest_version || '–')}`, body: donut({ items: distItems, size: 160, centerLabel: '设备', centerValue: num(counts.total) }) })}
            ${card({ title: '治理提示', sub: 'M12 版本治理', body: `<div class="list">
              ${barRow('最新版本', dist[fleet.latest_version] || 0, counts.total || 1, { display: `${num(dist[fleet.latest_version] || 0)} 台`, tone: 'green' })}
              ${barRow('落后版本', counts.total - (dist[fleet.latest_version] || 0), counts.total || 1, { display: `${num(counts.total - (dist[fleet.latest_version] || 0))} 台`, tone: 'amber' })}
            </div>
            <div class="mt-14 panel-note">碎片率超过 <b>${pct(thr('fragmentation'), 0)}</b> 时 M12 会触发强制升级；Demo 中升级仅在模拟设备上执行。</div>` })}
          </div>
        </div>`;

  root.innerHTML = `
    ${pageHead({
      title: '设备与 OTA',
      tag: `R${fleet.telemetry_round || '–'}`,
      sub: `<span>${num(counts.total)} 台设备</span><span class="sep"></span><span>在线 ${num(counts.online)}</span><span class="sep"></span><span>最新 ${esc(fleet.latest_version || '–')}</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="monitor">${icon('activity')}<span>漂移监控</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}
    ${kpis}
    <div class="row mt-20" style="justify-content:space-between">
      ${tabs([{ id: 'devices', label: '设备清单', count: devices.length }, { id: 'telemetry', label: '遥测明细', count: telDevices.length }, { id: 'releases', label: 'OTA 发布', count: rels.length }], tab)}
      <span class="tiny muted">OTA 为状态机模拟：下载 → 校验 → 安装 → 切换 → 影子 → 灰度</span>
    </div>
    <div class="mt-14">${tabBody}</div>
  `;

  root.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => ctx.go('fleet', { tab: b.dataset.tab })));
  root.querySelectorAll('[data-act="monitor"]').forEach((b) => b.addEventListener('click', () => ctx.go('monitor')));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));

  const body = root.querySelector('#dev-body');
  const refresh = () => {
    if (!body) return;
    body.innerHTML = devicesBody();
    const n = body.querySelectorAll('tr').length;
    const counter = root.querySelector('#f-count');
    if (counter) counter.textContent = `${n} 台`;
    bindDevices();
  };
  ['#f-hw', '#f-gp', '#f-vs'].forEach((sel) => root.querySelector(sel)?.addEventListener('change', refresh));
  root.querySelector('#f-q')?.addEventListener('input', refresh);

  const findDevice = (id) => devices.find((d) => d.device_id === id);

  function bindDevices() {
    body?.querySelectorAll('[data-dev]').forEach((tr) => tr.addEventListener('click', () => {
      const d = findDevice(tr.dataset.dev);
      if (!d) return;
      const t = tel.devices?.find((x) => x.device_id === d.device_id) || {};
      const hist = t.confidence_hist || [];
      ctx.drawer({
        eyebrow: `设备 · ${d.group || '–'} · ${d.hardware || '–'}`,
        title: d.device_id,
        body: `<div class="mini-grid">
            ${miniStat('当前模型', esc(d.current_version || '–'), d.current_release || '')}
            ${miniStat('上一版本', esc(d.previous_version || '–'), d.prev_release || '')}
            ${miniStat('固件', esc(d.firmware || '–'), d.online ? '在线' : '离线')}
          </div>
          <div class="detail-section mt-14"><h4>遥测</h4>${kv([
            ['P50 / P95', `${fixed(t.latency_ms_p50, 1)} / ${fixed(t.latency_ms_p95, 1)} ms`],
            ['内存占用', `${fixed(t.mem_mb, 1)} MB`],
            ['难例率', pct(t.hard_case_rate, 2)],
            ['影子分歧率', pct(t.shadow_disagree_rate, 2)],
            ['离线时长', `${fixed(t.offline_hours, 1)} h`],
            ['模型版本', esc(t.model_version || '–')],
          ])}</div>
          ${hist.length ? `<div class="detail-section"><h4>置信度直方图</h4>
            ${barChart({ labels: hist.map((_, i) => `${(i / 10).toFixed(1)}`), series: [{ name: '占比', values: hist.map((v) => v * 100) }], height: 160, format: (v) => `${v.toFixed(0)}%` })}
          </div>` : ''}
          <div class="detail-section"><h4>追溯</h4>
            <button class="btn btn-sm btn-ghost" data-trace="device">${icon('git')}<span>查看设备血缘</span></button>
          </div>`,
      });
      document.querySelector('#drawer-body [data-trace="device"]')?.addEventListener('click', () => {
        ctx.closeDrawer();
        ctx.go('lineage', { focus: d.device_id, depth: 3 });
      });
    }));
  }
  bindDevices();

  root.querySelectorAll('[data-tel]').forEach((tr) => tr.addEventListener('click', () => {
    const t = telDevices.find((x) => x.device_id === tr.dataset.tel);
    if (!t) return;
    ctx.drawer({
      eyebrow: '遥测样本',
      title: t.device_id,
      body: `${kv([
        ['模型', esc(t.model_version || '–')],
        ['P50 / P95', `${fixed(t.latency_ms_p50, 1)} / ${fixed(t.latency_ms_p95, 1)} ms`],
        ['内存', `${fixed(t.mem_mb, 1)} MB`],
        ['难例率', pct(t.hard_case_rate, 2)],
        ['影子分歧', pct(t.shadow_disagree_rate, 2)],
        ['离线时长', `${fixed(t.offline_hours, 1)} h`],
      ])}
      ${(t.confidence_hist || []).length ? `<div class="detail-section mt-14"><h4>置信度分布</h4>
        ${barChart({ labels: t.confidence_hist.map((_, i) => `${(i / 10).toFixed(1)}`), series: [{ name: '占比', values: t.confidence_hist.map((v) => v * 100), color: palette()[1] }], height: 160, format: (v) => `${v.toFixed(0)}%` })}
      </div>` : ''}`,
    });
  }));

  root.querySelectorAll('[data-rel]').forEach((tr) => tr.addEventListener('click', () => {
    const r = rels.find((x) => x.release_id === tr.dataset.rel);
    if (!r) return;
    const rec = r.receipts || [];
    const failed = rec.filter((x) => !x.success);
    ctx.drawer({
      eyebrow: `发布单 · ${r.model_version || ''}`,
      title: r.release_id,
      body: `<div class="row" style="gap:8px">${stageBadge(r.status)}${badge(`成功 ${r.success}`, 'ok')}${badge(`失败 ${r.failed}`, r.failed ? 'bad' : 'muted')}</div>
        <div class="mini-grid mt-14">
          ${miniStat('阶段数', num((r.stages || []).length), 'internal → full')}
          ${miniStat('回执数', num(rec.length), '每台设备一份')}
          ${miniStat('失败回执', num(failed.length), failed.length ? (failed[0].reason || '查看详情') : '无失败')}
        </div>
        <div class="detail-section mt-14"><h4>灰度阶段</h4><div class="list">
          ${(r.stages || []).map((s) => `<div class="list-row">
            <span class="badge muted" style="min-width:62px;justify-content:center">${esc(s.stage)}</span>
            <div class="grow"><div>${num(s.devices)} 台设备</div>
              <div class="meta">${s.fail_rate !== undefined ? `失败 ${pct(s.fail_rate, 1)}` : ''}${s.disagree_rate !== undefined ? `影子分歧 ${pct(s.disagree_rate, 1)}` : ''}</div></div>
            ${Number(s.fail_rate) > 0.25 ? badge('超阈值', 'bad') : ''}
          </div>`).join('')}
        </div></div>
        <div class="detail-section"><h4>回滚阈值</h4>${kv([
          ['失败率', pct(r.plan?.rollback_threshold?.fail_rate ?? 0.25, 0)],
          ['影子分歧', pct(r.plan?.rollback_threshold?.shadow_disagree ?? 0.1, 0)],
          ['签名', `<span class="mono-sm">${esc(r.plan?.signature || '–')}</span>`],
        ])}</div>
        <div class="detail-section"><h4>设备回执（${rec.length}）</h4>
          ${rec.length ? `<div class="list">${rec.map((x) => `<details class="qa-item">
            <summary style="cursor:pointer;font-size:12px;display:flex;align-items:center;gap:8px">
              <span class="mono-sm">${esc(x.device_id)}</span>
              <span class="muted tiny">${esc(x.from_version)} → ${esc(x.to_version)}</span>
              ${finalBadge(x.final_state)}
              <span class="muted tiny" style="margin-left:auto">${fixed(x.duration_s, 1)}s</span>
            </summary>
            <div class="timeline mt-14">
              ${(x.history || []).map((h) => `<div class="tl-item ${h === 'active' ? 'ok' : ['verify_failed', 'install_failed', 'switch_failed'].includes(h) ? 'bad' : ''}">
                <span class="tl-dot"></span><div><div class="tl-title">${esc(h)}</div></div></div>`).join('')}
            </div>
            ${x.reason ? `<div class="callout bad mt-14">${icon('alert')}<div>${esc(x.reason)}</div></div>` : ''}
          </details>`).join('')}</div>` : '<div class="panel-note">没有回执。</div>'}
        </div>`,
    });
  }));
}
