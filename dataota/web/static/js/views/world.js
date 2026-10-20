/* 世界模型：后端目录与视角路由、模型 × 视角可用率、几何门禁度量分布、N2 反馈、生成片段回放 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, debounce, defectName } from '../util.js';
import { pageHead, kpi, card, emptyState, callout, kv, miniStat, pager, barRow } from '../ui.js';
import { barChart, palette } from '../charts.js';
import { frameScene, imageStage, wireFrameImages } from '../frames.js';
import { STATUS_LABEL, thr, viewName } from '../meta.js';

const state = { page: 1, size: 24, view: '', model: '', status: '', round: '', q: '' };
const BOX = '#2f6bff';

function statusBadge(status, reason) {
  const [label, tone] = STATUS_LABEL[status] || [status, 'muted'];
  return `<span title="${esc(reason || '')}">${badge(label, tone)}</span>`;
}

const gateBadge = (s) => (s === 'passed' ? badge('通过', 'ok') : s === 'rejected' ? badge('拒绝', 'bad') : badge(s || '–', 'muted'));

function routeTable(routes, catalog) {
  const byName = Object.fromEntries(catalog.map((c) => [c.name, c]));
  const views = Object.keys(routes).sort((a, b) => (a === 'default' ? -1 : b === 'default' ? 1 : a.localeCompare(b)));
  return `<div class="list">${views.map((v) => {
    const chain = routes[v] || [];
    const firstReady = chain.find((m) => (byName[m]?.status || (m === 'demo' ? 'ready' : '')) === 'ready');
    return `<div class="list-row" style="align-items:flex-start">
      <span class="mono-sm" style="width:92px;flex:none;padding-top:3px">${esc(v === 'default' ? '默认' : viewName(v))}</span>
      <div class="grow route-chain">${chain.map((m, i) => {
        const st = byName[m]?.status || 'unknown_impl';
        const on = m === firstReady;
        return `${i ? `<span class="route-arrow">${icon('chevronRight')}</span>` : ''}<span class="route-step ${on ? 'on' : ''} s-${esc(st)}" title="${esc(byName[m]?.reason || '')}">${esc(m)}${on ? ' ✓' : ''}</span>`;
      }).join('')}</div>
    </div>`;
  }).join('')}</div>`;
}

function matrix(rows, minRate) {
  if (!rows.length) return '<div class="panel-note">还没有生成片段。</div>';
  const models = [...new Set(rows.map((r) => r.model))];
  const views = [...new Set(rows.map((r) => r.view))];
  const cell = (m, v) => rows.find((r) => r.model === m && r.view === v);
  return `<div class="table-wrap"><table class="data compact matrix">
    <thead><tr><th>模型 \\ 视角</th>${views.map((v) => `<th class="num" title="${esc(v)}">${esc(viewName(v))}</th>`).join('')}</tr></thead>
    <tbody>${models.map((m) => `<tr><th class="mono-sm">${esc(m)}</th>${views.map((v) => {
      const c = cell(m, v);
      if (!c) return '<td class="num muted">·</td>';
      const tone = c.pass_rate >= minRate ? 'var(--green)' : 'var(--red)';
      const k = c.pass_rate >= minRate ? (c.pass_rate - minRate) / (1 - minRate || 1) : 1 - c.pass_rate / (minRate || 1);
      return `<td class="num heat" style="background:color-mix(in srgb, ${tone} ${Math.round(12 + k * 40)}%, transparent)" title="${c.passed}/${c.total}">${pct(c.pass_rate, 0)}<div class="tiny muted">${c.passed}/${c.total}</div></td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function gallery(items) {
  if (!items.length) return emptyState({ title: '没有匹配的生成片段', text: '调整筛选条件，或运行一轮闭环。' });
  return `<div class="gallery wide">${items.map((c) => `
    <div class="gallery-item" data-clip="${esc(c.clip_id)}" role="button" tabindex="0">
      ${frameScene(c.clip_id, null, { fit: 'none', night: c.view === 'night' })}
      ${c.thumb ? `<img src="${esc(c.thumb)}" alt="${esc(c.clip_id)}" loading="lazy">` : ''}
      <div class="gi-top">${gateBadge(c.status)}${badge(viewName(c.view), 'violet')}${c.fallback_from?.length ? badge('回退', 'warn') : ''}</div>
      <div class="gi-body">
        <span>${esc(c.model)} · ${esc(defectName(c.defect_type))}</span>
        <b class="mono">${c.score == null ? '–' : fixed(c.score, 2)}</b>
      </div>
    </div>`).join('')}</div>`;
}

function poseText(p) {
  if (!p) return '–';
  const parts = [];
  if (p.yaw) parts.push(`yaw ${p.yaw}°`);
  if (p.pitch) parts.push(`pitch ${p.pitch}°`);
  if (p.distance && p.distance !== 1) parts.push(`距离 ×${p.distance}`);
  if (p.light !== undefined && p.light !== 1) parts.push(`光照 ${pct(p.light, 0)}`);
  if (p.occlusion) parts.push(`遮挡 ${pct(p.occlusion, 0)}`);
  return parts.join(' · ') || '锚点位姿';
}

export async function render(root, ctx) {
  const focusClip = ctx.params.get('clip');
  const [w, list] = await Promise.all([api.world(), api.clips(state)]);
  let last = list;
  const catalog = w.catalog || [];
  const totals = w.totals || {};
  const t = w.thresholds || {};
  const minRate = Number(t.min_pass_rate ?? thr('gate_min_pass_rate'));
  const ready = catalog.filter((c) => c.status === 'ready');
  const rounds = w.rounds || [];
  const n2 = w.n2 || {};

  if (!totals.clips && !focusClip) {
    root.innerHTML = pageHead({ title: '世界模型', sub: '还没有生成片段' })
      + card({ body: emptyState({ iconName: 'layers', title: '还没有 P3 生成记录', text: '运行闭环后，确认缺陷会成为锚点，按视角路由到世界模型生成多视角片段，并经 P4 几何门禁。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) })
      + card({ title: '视角路由', sub: 'configs [flywheel.world_models]', cls: 'mt-20', body: routeTable(w.routes || {}, catalog) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const models = [...new Set(rounds.flatMap((r) => Object.keys(r.by_model || {})))];
  const genChart = barChart({
    labels: rounds.map((r) => `R${r.round}`),
    series: models.flatMap((m, i) => [
      { name: `${m} 生成`, values: rounds.map((r) => r.by_model?.[m]?.clips ?? null), color: palette()[(i * 2) % 5] },
      { name: `${m} 通过`, values: rounds.map((r) => r.by_model?.[m]?.passed ?? null), color: palette()[(i * 2 + 1) % 5] },
    ]),
    height: 190, format: (v) => num(v),
  });
  const histLabels = Array.from({ length: 10 }, (_, i) => (i / 10).toFixed(1));
  const mh = w.metric_hist || {};
  const metricChart = barChart({
    labels: histLabels,
    series: [
      { name: '深度连续性', values: mh.depth || [], color: palette()[0] },
      { name: '形状稳定性', values: mh.shape || [], color: palette()[1] },
      { name: '纹理闪烁', values: mh.flicker || [], color: palette()[2] },
    ],
    height: 170, format: (v) => num(v),
  });
  const scoreChart = barChart({
    labels: histLabels, series: [{ name: '门禁得分', values: w.score_hist || [], color: palette()[3] }],
    height: 140, format: (v) => num(v),
  });
  const reasons = Object.entries(w.reasons || {}).sort((a, b) => b[1] - a[1]);
  const reasonMax = Math.max(1, ...reasons.map(([, v]) => v));
  const switches = n2.feedback_switch_models || [];
  const drops = n2.feedback_drop_views || [];

  const catalogCards = catalog.map((c) => `
    <div class="wm-card ${c.status === 'ready' ? 'ready' : ''}">
      <div class="spread"><b class="mono-sm">${esc(c.name)}</b>${statusBadge(c.status, c.reason)}</div>
      <div class="tiny muted mt-6">${esc(c.desc || '')}</div>
      <div class="row mt-8">${badge(c.impl, 'muted', false)}${badge(c.tier === 'prod' ? '生产' : 'Demo', c.tier === 'prod' ? 'warn' : 'info', false)}
        ${c.gpu_hours_per_clip != null ? badge(`${c.gpu_hours_per_clip} GPU·h/段`, 'muted', false) : ''}</div>
      <div class="tiny muted mt-6">视角：${(c.views || ['*']).map((v) => esc(v === '*' ? '全部' : viewName(v))).join('、')}</div>
      ${c.reason ? `<div class="tiny mt-6" style="color:var(--amber)">${esc(c.reason)}</div>` : ''}
    </div>`).join('');

  const facets = last.facets || {};
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? 'selected' : ''}>${esc(label)}</option>`;
  const filterBar = `<div class="filterbar">
    <input type="search" id="wm-q" placeholder="搜索片段 / 锚点 / 类别" value="${esc(state.q)}">
    <select id="wm-view">${opt('', '全部视角', state.view)}${Object.keys(facets.view || {}).map((k) => opt(k, `${viewName(k)} (${facets.view[k]})`, state.view)).join('')}</select>
    <select id="wm-model">${opt('', '全部模型', state.model)}${Object.keys(facets.model || {}).map((k) => opt(k, `${k} (${facets.model[k]})`, state.model)).join('')}</select>
    <select id="wm-status">${opt('', '门禁结果', state.status)}${Object.keys(facets.status || {}).map((k) => opt(k, `${k} (${facets.status[k]})`, state.status)).join('')}</select>
    <select id="wm-round">${opt('', '全部轮次', state.round)}${Object.keys(facets.round || {}).sort((a, b) => a - b).map((k) => opt(k, `R${k} (${facets.round[k]})`, state.round)).join('')}</select>
  </div>`;

  root.innerHTML = `
    ${pageHead({
      title: '世界模型',
      tag: `${num(ready.length)} / ${num(catalog.length)} 可用`,
      sub: `<span>P3 视角扩充 → P4 几何门禁</span><span class="sep"></span><span>每段 ${num(w.frames_per_clip)} 帧</span><span class="sep"></span><span>门禁得分 ≥ ${fixed(t.pass_score, 2)}，单项 ≥ ${fixed(t.floor, 2)}</span><span class="sep"></span><span>可用率 < ${pct(minRate, 0)} 触发 N2 换模型 / 收窄视角</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="detectors">${icon('target')}<span>检测模型</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '生成片段', value: num(totals.clips), foot: `${num(rounds.length)} 轮累计`, iconName: 'layers' })}
      ${kpi({ label: '门禁通过率', value: pct((totals.passed || 0) / Math.max(1, totals.clips || 0), 1), foot: `${num(totals.passed)} 通过 · 最低 ${pct(minRate, 0)}`, iconName: 'shield', tone: (totals.passed || 0) / Math.max(1, totals.clips || 0) >= minRate ? 'green' : 'amber' })}
      ${kpi({ label: '像素级度量', value: pct((totals.pixels || 0) / Math.max(1, totals.clips || 0), 0), foot: '门禁基于真实帧计算（其余读模拟质量）', iconName: 'activity', tone: 'teal' })}
      ${kpi({ label: '初筛失败', value: num(totals.prescreen_failed), foot: '空帧 / 帧数不符 / 冻结 / 目标出画', iconName: 'alert', tone: totals.prescreen_failed ? 'amber' : 'green' })}
      ${kpi({ label: '模型回退', value: num(totals.fallback), foot: '生成失败后改用下一个候选', iconName: 'refresh', tone: totals.fallback ? 'amber' : 'green' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({ title: '模型 × 视角可用率', sub: `P4 通过率；低于 ${pct(minRate, 0)} 且样本 ≥ N2 阈值时触发换模型`, body: matrix(w.matrix || [], minRate) })}
        ${card({ title: '逐轮生成量', sub: '按实际生效的世界模型拆分', body: genChart })}
      </div>
      <div class="stack">
        ${card({ title: '视角路由', sub: 'configs [flywheel.world_models] · ✓ = 当前生效（前面的候选未部署或已被 N2 降级）', body: routeTable(w.routes || {}, catalog) })}
        ${card({ title: 'N2 反馈', sub: '最近一轮失败分析对 P3 的调整',
          body: `${kv([
            ['整体通过率', pct(n2.pass_rate, 1)],
            ['换模型', switches.length ? switches.map((s) => badge(`${viewName(s.view)}：${s.model} ${pct(s.pass_rate, 0)}`, 'warn', false)).join(' ') : '<span class="muted">无</span>'],
            ['收窄视角', drops.length ? drops.map((v) => badge(viewName(v), 'bad', false)).join(' ') : '<span class="muted">无</span>'],
          ])}
          ${reasons.length ? `<div class="list mt-14">${reasons.slice(0, 8).map(([k, v]) => barRow(k, v, reasonMax, { display: num(v), tone: 'amber' })).join('')}</div>` : ''}` })}
      </div>
    </div>

    ${card({ title: '世界模型目录', sub: 'configs [models.world.*] · http 后端按统一协议接入生成式模型服务（协议见 dataota/models/world.py）', cls: 'mt-20',
      body: `<div class="wm-grid">${catalogCards}</div>` })}

    <div class="g-2 mt-20">
      ${card({ title: '门禁三项度量分布', sub: `权重 深度 ${esc(t.weights?.depth ?? '–')} · 形状 ${esc(t.weights?.shape ?? '–')} · 闪烁 ${esc(t.weights?.flicker ?? '–')}`, body: metricChart })}
      ${card({ title: '门禁得分分布', sub: `通过线 ${fixed(t.pass_score, 2)}`, body: scoreChart })}
    </div>

    <div class="card mt-20">
      <div class="card-head"><div><div class="card-title"><span class="dot"></span>生成片段</div>
        <div class="card-sub">首帧缩略图；点开查看逐帧画面、随视角变换的目标框、位姿与门禁明细</div></div></div>
      ${filterBar}
      <div id="wm-body">${gallery(last.items)}</div>
      <div id="wm-pager">${pager(last)}</div>
    </div>
  `;

  const body = root.querySelector('#wm-body');
  const pagerBox = root.querySelector('#wm-pager');
  let busy = false;

  async function load() {
    if (busy) return;
    busy = true;
    body.style.opacity = '.55';
    try {
      last = await api.clips(state);
      body.innerHTML = gallery(last.items);
      pagerBox.innerHTML = pager(last);
      bind();
    } catch (e) {
      ctx.toast(e.message, 'bad');
    } finally {
      busy = false;
      body.style.opacity = '';
    }
  }

  async function openDetail(id) {
    ctx.drawer({ eyebrow: '生成片段', title: id, body: '<div class="graph-loading">读取片段、门禁与锚点…</div>' });
    try {
      const d = await api.clip(id);
      const s = d.summary || {};
      const g = d.gate || {};
      const meta = d.meta || {};
      const floor = Number(t.floor ?? thr('gate_metric_floor'));
      const film = (d.frames || []).map((f, i) => `
        <div class="film-frame">
          ${imageStage(f.url, `${id}-${i}`, [{ box: f.bbox, color: BOX }], '')}
          <div class="tiny muted mt-6">f${i} · ${esc(poseText(f.pose))}${f.bbox ? '' : ' · <span style="color:var(--red)">目标出画</span>'}</div>
        </div>`).join('');
      const anchor = d.anchor;
      ctx.drawer({
        eyebrow: `${viewName(s.view || d.clip?.trajectory)} · ${s.model || meta.model || '–'} · R${meta.round ?? '–'}`,
        title: id,
        body: `<div class="filmstrip">${film}</div>
          <div class="mini-grid mt-14">
            ${miniStat('门禁', gateBadge(g.gate_status), `得分 ${fixed(g.gate_score, 3)} · ${esc(g.source || '')}`)}
            ${miniStat('生成耗时', `${fixed(meta.latency_ms, 0)} ms`, `${fixed(meta.gpu_hours, 3)} GPU·h`)}
            ${miniStat('初筛', s.prescreen_ok === false ? badge('失败', 'bad') : badge('通过', 'ok'), (s.prescreen || []).join(' · ') || '无问题')}
          </div>
          <div class="detail-section mt-14"><h4>几何门禁（单项下限 ${fixed(floor, 2)}）</h4>
            ${barRow('深度连续性', (g.depth_continuity || 0) * 100, 100, { display: fixed(g.depth_continuity, 3), tone: (g.depth_continuity || 0) < floor ? 'amber' : 'green' })}
            ${barRow('形状稳定性', (g.shape_stability || 0) * 100, 100, { display: fixed(g.shape_stability, 3), tone: (g.shape_stability || 0) < floor ? 'amber' : 'teal' })}
            ${barRow('1 − 纹理闪烁', (1 - (g.texture_flicker || 0)) * 100, 100, { display: fixed(1 - (g.texture_flicker || 0), 3), tone: 1 - (g.texture_flicker || 0) < floor ? 'amber' : 'green' })}
            ${(g.reasons || []).length ? `<div class="row mt-14">${g.reasons.map((r) => badge(r, 'bad', false)).join('')}</div>` : ''}
          </div>
          <div class="detail-section"><h4>来源</h4>${kv([
            ['生成器', `<span class="mono-sm">${esc(d.clip?.generator || '–')}</span>`],
            ['回退记录', (meta.fallback_from || []).length ? meta.fallback_from.map((x) => badge(x, 'warn', false)).join(' ') : '<span class="muted">无</span>'],
            ['锚点', `<span class="mono-sm">${esc(d.clip?.anchor_id || '–')}</span>`],
            ['缺陷类型', esc(defectName(meta.defect_type))],
            ['派生标注', (d.annotations || []).length ? d.annotations.slice(0, 6).map((a) => `<a class="link-btn mono-sm" href="#/annotations?q=${encodeURIComponent(a)}">${esc(a)}</a>`).join('<br>') : '<span class="muted">门禁未通过，未进入标注</span>'],
          ])}</div>
          ${anchor ? `<div class="detail-section"><h4>锚点原图</h4>${imageStage(anchor.url, anchor.anchor_id, [{ box: anchor.bbox, color: '#10b981', dash: true }], anchor.anchor_id)}</div>` : ''}
          <div class="row"><button class="btn btn-sm" data-go-lineage="${esc(id)}">${icon('git')}<span>查看血缘</span></button></div>`,
      });
      document.querySelector('[data-go-lineage]')?.addEventListener('click', () => { ctx.closeDrawer(); ctx.go('lineage', { focus: id }); });
    } catch (e) {
      ctx.drawer({ eyebrow: '生成片段', title: id, body: callout('bad', esc(e.message), 'alert') });
    } finally {
      wireFrameImages(document);
    }
  }

  function bind() {
    wireFrameImages(body);
    body.querySelectorAll('[data-clip]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.dataset.clip));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.dataset.clip); } });
    });
    pagerBox.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
      const p = Number(b.dataset.page);
      if (p >= 1 && p <= last.pages && p !== last.page) { state.page = p; load(); }
    }));
  }

  const onFilter = () => { state.page = 1; load(); };
  root.querySelector('#wm-q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); onFilter(); }, 320));
  [['#wm-view', 'view'], ['#wm-model', 'model'], ['#wm-status', 'status'], ['#wm-round', 'round']].forEach(([sel, key]) => {
    root.querySelector(sel).addEventListener('change', (e) => { state[key] = e.target.value; onFilter(); });
  });
  bind();
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="detectors"]').forEach((b) => b.addEventListener('click', () => ctx.go('detectors')));
  if (focusClip) openDetail(focusClip);
}
