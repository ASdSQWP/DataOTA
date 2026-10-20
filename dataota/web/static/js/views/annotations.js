/* 标注样例：分面筛选、画廊 / 表格、bbox 与多智能体一致性详情 */

import { api } from '../api.js';
import {
  esc, num, pct, badge, icon, debounce, defectName, sevBadge, shortId, ts,
} from '../util.js';
import { pageHead, card, emptyState, pager, callout, kv, miniStat, tabs, barRow } from '../ui.js';
import { barChart, palette } from '../charts.js';
import { frameScene, wireFrameImages } from '../frames.js';
import { thr } from '../meta.js';

const state = { page: 1, size: 24, split: '', synthetic: '', defect_type: '', gate: '', human: '', q: '', mode: 'gallery' };
let last = null;

const AGENT_COLORS = ['#2f6bff', '#0d9488', '#d97706', '#db2777'];

function bboxStage(frameUrl, annotation, id) {
  const scene = frameScene(id, null, { night: false });
  const box = (b, color, label, dash = false) => `<rect x="${(b.x || 0) * 100}" y="${(b.y || 0) * 100}"
    width="${(b.w || 0) * 100}" height="${(b.h || 0) * 100}" fill="${color}18" stroke="${color}"
    stroke-width="1.4" vector-effect="non-scaling-stroke" ${dash ? 'stroke-dasharray="3 2"' : ''}/>`;
  const agents = (annotation.agent_labels || []).map((a, i) => box(a.bbox || {}, AGENT_COLORS[i % AGENT_COLORS.length], a.agent, true)).join('');
  return `<div class="bbox-stage">
    ${scene}
    ${frameUrl ? `<img class="bbox-img" src="${esc(frameUrl)}" alt="${esc(id)}" loading="lazy">` : ''}
    <svg class="bbox-overlay" viewBox="0 0 100 100" preserveAspectRatio="none">
      ${box(annotation.bbox || {}, '#2f6bff')}
      ${agents}
    </svg>
    <span class="stage-note" data-id="${esc(id)}">${esc(id)} · bbox 归一化坐标</span>
  </div>`;
}

function gallery(items) {
  if (!items.length) return emptyState({ title: '没有匹配的标注', text: '调整筛选条件，或运行一轮闭环生成更多样本。' });
  return `<div class="gallery">${items.map((a) => `
    <div class="gallery-item" data-ann="${esc(a.annotation_id)}" role="button" tabindex="0">
      ${frameScene(a.annotation_id, a.bbox || {}, { fit: 'none' })}
      ${a.frame_url ? `<img src="${esc(a.frame_url)}" alt="${esc(a.annotation_id)}" loading="lazy">` : ''}
      <div class="gi-top">
        ${a.synthetic ? badge('合成', 'violet') : badge('真实', 'teal')}
        ${a.human_decision ? badge('人工', 'warn') : ''}
      </div>
      <div class="gi-body">
        <span>${esc(defectName(a.defect_type))} · ${esc(shortId(a.annotation_id, 16))}</span>
        <b class="mono">${pct(a.agent_consensus, 0)}</b>
      </div>
    </div>`).join('')}</div>`;
}

function table(items) {
  if (!items.length) return emptyState({ title: '没有匹配的标注', text: '调整筛选条件后重试。' });
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>标注</th><th>缺陷</th><th>Split</th><th>来源</th><th class="num">一致性</th><th>门禁</th><th>人工</th><th>状态</th></tr></thead>
    <tbody>${items.map((a) => `<tr class="clickable" data-ann="${esc(a.annotation_id)}">
      <td class="id">${esc(a.annotation_id)}</td>
      <td>${esc(defectName(a.defect_type))}</td>
      <td>${badge(a.split || '–', a.split === 'holdout' ? 'warn' : 'muted', false)}</td>
      <td>${a.synthetic ? badge('合成', 'violet') : badge('真实', 'teal')}</td>
      <td class="num">${pct(a.agent_consensus, 0)}</td>
      <td>${a.gate_status === 'passed' ? badge('通过', 'ok') : badge(a.gate_status || '–', 'bad')}</td>
      <td>${a.human_decision ? badge('已介入', 'warn') : '<span class="muted">–</span>'}</td>
      <td>${badge(a.state || '–', 'muted', false)}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

export async function render(root, ctx) {
  if (ctx.params.get('q') !== null) { state.q = ctx.params.get('q') || ''; state.page = 1; }
  if (ctx.params.get('split')) { state.split = ctx.params.get('split'); state.page = 1; }
  last = await api.annotations(state);
  const facets = last.facets || {};

  const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(label)}</option>`;

  const filterBar = `<div class="filterbar">
    <input type="search" id="ann-q" placeholder="搜索标注 ID / 缺陷 ID / 类型" value="${esc(state.q)}">
    <select id="ann-split">${opt('', '全部 split', state.split)}${Object.keys(facets.split || {}).map((k) => opt(k, `${k} (${facets.split[k]})`, state.split)).join('')}</select>
    <select id="ann-synthetic">${opt('', '真实 + 合成', state.synthetic)}${opt('true', `合成 (${facets.synthetic?.synthetic ?? 0})`, state.synthetic)}${opt('false', `真实 (${facets.synthetic?.real ?? 0})`, state.synthetic)}</select>
    <select id="ann-defect">${opt('', '全部缺陷', state.defect_type)}${Object.keys(facets.defect_type || {}).map((k) => opt(k, `${defectName(k)} (${facets.defect_type[k]})`, state.defect_type)).join('')}</select>
    <select id="ann-gate">${opt('', '全部门禁', state.gate)}${Object.keys(facets.gate || {}).map((k) => opt(k, `${k} (${facets.gate[k]})`, state.gate)).join('')}</select>
    <select id="ann-human">${opt('', '人工 / 自动', state.human)}${opt('true', `人工介入 (${facets.human?.human ?? 0})`, state.human)}${opt('false', `纯自动 (${facets.human?.auto ?? 0})`, state.human)}</select>
    <span class="grow"></span>
    <select id="ann-size">${[12, 24, 48, 96].map((n) => opt(String(n), `${n} / 页`, String(state.size))).join('')}</select>
    <div class="tabs">
      <button class="tab ${state.mode === 'gallery' ? 'active' : ''}" data-mode="gallery">${icon('grid')}画廊</button>
      <button class="tab ${state.mode === 'table' ? 'active' : ''}" data-mode="table">${icon('list')}表格</button>
    </div>
  </div>`;

  const content = () => (state.mode === 'gallery' ? gallery(last.items) : table(last.items));

  root.innerHTML = `
    ${pageHead({
      title: '标注样例',
      tag: `${num(facets.total || last.total)} 条`,
      sub: `<span>真实 + 合成标注</span><span class="sep"></span><span id="ann-count">当前筛选 ${num(last.total)} 条</span><span class="sep"></span><span>多智能体一致性 ≥ ${pct(thr('consensus'), 0)} 才自动接受</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="review">${icon('user')}<span>审核台</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${miniStat('样本总量', num(facets.total || 0), '全部标注文件')}
      ${miniStat('合成样本', num(facets.synthetic?.synthetic || 0), `占比 ${pct((facets.synthetic?.synthetic || 0) / Math.max(1, facets.total || 1), 1)}`)}
      ${miniStat('真实样本', num(facets.synthetic?.real || 0), '来自现场接入')}
      ${miniStat('门禁通过', num(facets.gate?.passed || 0), 'P4 几何门禁')}
      ${miniStat('人工介入', num(facets.human?.human || 0), 'B6 / P5 仲裁')}
      ${miniStat('训练 / 留出', `${num(facets.split?.train || 0)} / ${num(facets.split?.holdout || 0)}`, 'T8 使用真实留出')}
    </div>

    <div class="card mt-20">
      ${filterBar}
      <div id="ann-body">${content()}</div>
      <div id="ann-pager">${pager(last)}</div>
    </div>
  `;

  const body = root.querySelector('#ann-body');
  const pagerBox = root.querySelector('#ann-pager');
  const flag = { busy: false };

  async function load() {
    if (flag.busy) return;
    flag.busy = true;
    body.style.opacity = '.55';
    try {
      last = await api.annotations(state);
      body.innerHTML = content();
      pagerBox.innerHTML = pager(last);
      const count = root.querySelector('#ann-count');
      if (count) count.textContent = `当前筛选 ${num(last.total)} 条`;
      bindContent();
      bindPager();
    } catch (e) {
      ctx.toast(e.message, 'bad');
    } finally {
      flag.busy = false;
      body.style.opacity = '';
    }
  }

  const onFilter = () => { state.page = 1; load(); };
  root.querySelector('#ann-q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); onFilter(); }, 320));
  root.querySelector('#ann-split').addEventListener('change', (e) => { state.split = e.target.value; onFilter(); });
  root.querySelector('#ann-synthetic').addEventListener('change', (e) => { state.synthetic = e.target.value; onFilter(); });
  root.querySelector('#ann-defect').addEventListener('change', (e) => { state.defect_type = e.target.value; onFilter(); });
  root.querySelector('#ann-gate').addEventListener('change', (e) => { state.gate = e.target.value; onFilter(); });
  root.querySelector('#ann-human').addEventListener('change', (e) => { state.human = e.target.value; onFilter(); });
  root.querySelector('#ann-size').addEventListener('change', (e) => { state.size = Number(e.target.value); onFilter(); });
  root.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    state.mode = b.dataset.mode;
    root.querySelectorAll('[data-mode]').forEach((x) => x.classList.toggle('active', x === b));
    body.innerHTML = content();
    bindContent();
  }));

  function bindPager() {
    pagerBox.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
      const p = Number(b.dataset.page);
      if (p >= 1 && p <= last.pages && p !== last.page) { state.page = p; load(); }
    }));
  }

  async function openDetail(id) {
    ctx.drawer({ eyebrow: '标注详情', title: id, body: '<div class="graph-loading">读取标注与血缘…</div>' });
    try {
      const d = await api.annotation(id);
      const a = d.annotation || {};
      const trust = d.trust || {};
      const gate = trust.gate || {};
      ctx.drawer({
        eyebrow: `${a.split || '–'} · ${a.synthetic ? '合成样本' : '真实样本'} · ${defectName(a.defect_type)}`,
        title: id,
        body: `${bboxStage(d.frame_url, a, id)}
          <div class="legend-swatches mt-14">
            <span><i style="background:#2f6bff"></i>最终标注</span>
            ${(a.agent_labels || []).map((x, i) => `<span><i style="background:${AGENT_COLORS[i % AGENT_COLORS.length]}"></i>${esc(x.agent)}</span>`).join('')}
          </div>
          <div class="mini-grid mt-14">
            ${miniStat('一致性', pct(a.agent_consensus, 1), `${(a.agent_labels || []).length} 个智能体`)}
            ${miniStat('门禁得分', gate.gate_score === undefined ? '–' : pct(gate.gate_score, 1), gate.gate_status || a.gate_status || '')}
            ${miniStat('人工裁决', a.human_decision ? '已介入' : '未介入', a.human_decision ? 'reviewer 已确认' : '自动通过')}
          </div>
          <div class="detail-section mt-14"><h4>标注字段</h4>${kv([
            ['缺陷类型', esc(defectName(a.defect_type))],
            ['缺陷 ID', `<span class="mono-sm">${esc(a.defect_id || '–')}</span>`],
            ['来源片段', a.source_clip_id ? `<a class="link-btn mono-sm" href="#/world?clip=${encodeURIComponent(a.source_clip_id)}">${esc(a.source_clip_id)}</a>` : '<span class="muted">真实样本</span>'],
            ['bbox', `x ${pct(a.bbox?.x, 1)} · y ${pct(a.bbox?.y, 1)} · w ${pct(a.bbox?.w, 1)} · h ${pct(a.bbox?.h, 1)}`],
            ['Caption', esc(a.caption || '–')],
            ['状态', badge(a.state || '–', 'muted', false)],
          ])}</div>
          <div class="detail-section"><h4>为什么可信（Q3）</h4>
            <div class="panel-note">答案由血缘反查：annotation ← gate + agent_consensus + human_decision。</div>
            <div class="mt-14">${barRow('智能体一致性', (a.agent_consensus || 0) * 100, 100, { display: pct(a.agent_consensus, 0), tone: (a.agent_consensus || 0) >= thr('consensus') ? 'green' : 'amber' })}</div>
            ${gate.depth_continuity !== undefined ? `<div class="mt-14">${barRow('深度连续性', gate.depth_continuity * 100, 100, { display: pct(gate.depth_continuity, 1) })}
              ${barRow('形状稳定性', gate.shape_stability * 100, 100, { display: pct(gate.shape_stability, 1), tone: 'teal' })}
              ${barRow('纹理闪烁', gate.texture_flicker * 100, 100, { display: pct(gate.texture_flicker, 1), tone: 'amber' })}</div>` : ''}
          </div>
          ${(a.agent_labels || []).length ? `<div class="detail-section"><h4>智能体标注</h4>
            <div class="agent-list">${a.agent_labels.map((x, i) => `<div class="agent-row">
              <span class="who" style="color:${AGENT_COLORS[i % AGENT_COLORS.length]}">${esc(x.agent)}</span>
              <span>${esc(defectName(x.defect_type))}</span>
              <span class="cons">${pct(a.agent_consensus, 0)}</span>
            </div>`).join('')}</div>
          </div>` : ''}
          ${(a.qa || []).length ? `<div class="detail-section"><h4>语义问答</h4><div class="qa">${a.qa.map((q) => `<div class="qa-item"><div class="qa-q">${esc(q.q)}</div><div class="qa-a">${esc(q.a)}</div></div>`).join('')}</div></div>` : ''}`,
      });
    } catch (e) {
      ctx.drawer({ eyebrow: '标注详情', title: id, body: callout('bad', esc(e.message), 'alert') });
    } finally {
      wireFrameImages(document);
    }
  }

  function bindContent() {
    wireFrameImages(body);
    body.querySelectorAll('[data-ann]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.getAttribute('data-ann')));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.getAttribute('data-ann')); } });
    });
  }

  bindContent();
  bindPager();

  root.querySelectorAll('[data-act="review"]').forEach((b) => b.addEventListener('click', () => ctx.go('review')));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
}
