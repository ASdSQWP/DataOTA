/* 检测模型：模型库目录、B2 L1 配置、逐轮评测与混淆矩阵、场景质量检测、检测结果回放 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, debounce, defectName } from '../util.js';
import { pageHead, kpi, card, emptyState, callout, kv, miniStat, pager, barRow, delta } from '../ui.js';
import { lineChart, barChart, donut, palette } from '../charts.js';
import { boxOverlay, frameScene, imageStage, wireFrameImages } from '../frames.js';
import { STATUS_LABEL, flagName, sourceName, thr } from '../meta.js';

const OUTCOME = {
  correct: ['命中', 'ok'], true_negative: ['正确判无', 'teal'], missed: ['漏检', 'warn'],
  wrong_class: ['错类', 'bad'], false_alarm: ['误报', 'bad'], unknown: ['无参考', 'muted'],
};
const outcomeBadge = (o) => badge(...(OUTCOME[o] || [o, 'muted']));
const RANK_COLORS = ['#2f6bff', '#7c5cff', '#d97706', '#db2777', '#64748b'];
const REF_COLOR = '#10b981';

const state = { page: 1, size: 24, outcome: '', label: '', source: '', round: '', q: '' };

function statusBadge(status, reason) {
  const [label, tone] = STATUS_LABEL[status] || [status, 'muted'];
  return `<span title="${esc(reason || '')}">${badge(label, tone)}</span>`;
}

function usedBy(name, d) {
  const out = [];
  if (d.l1?.detector === name && d.l1_impl === 'detector') out.push(badge('B2 L1', 'info', false));
  if ((d.agents || []).some((a) => a === `detector:${name}`)) out.push(badge('P5 智能体', 'violet', false));
  (d.catalog || []).forEach((c) => { if ((c.members || []).includes(name)) out.push(badge(`${c.name} 成员`, 'muted', false)); });
  return out.join(' ') || '<span class="muted tiny">未使用</span>';
}

function confusion(latest) {
  const labels = latest.labels || [];
  if (!labels.length) return '<div class="panel-note">本轮没有可评测事件。</div>';
  const cm = latest.confusion || {};
  const max = Math.max(1, ...labels.flatMap((a) => labels.map((b) => cm[a]?.[b] || 0)));
  const name = (l) => (l === 'None' ? '无缺陷' : defectName(l));
  return `<div class="table-wrap"><table class="data compact matrix">
    <thead><tr><th>参考 \\ 预测</th>${labels.map((l) => `<th class="num">${esc(name(l))}</th>`).join('')}<th class="num">召回</th></tr></thead>
    <tbody>${labels.map((a) => {
      const row = labels.map((b) => cm[a]?.[b] || 0);
      const total = row.reduce((x, y) => x + y, 0);
      return `<tr><th>${esc(name(a))}</th>${labels.map((b, j) => {
        const v = row[j];
        const k = v / max;
        const tone = a === b ? 'var(--green)' : 'var(--red)';
        return `<td class="num heat" style="background:color-mix(in srgb, ${tone} ${Math.round(k * 55)}%, transparent)">${v || '<span class="muted">·</span>'}</td>`;
      }).join('')}<td class="num">${total ? pct((cm[a]?.[a] || 0) / total, 0) : '–'}</td></tr>`;
    }).join('')}</tbody></table></div>`;
}

function gallery(items) {
  if (!items.length) return emptyState({ title: '没有匹配的检测记录', text: '调整筛选条件，或运行一轮闭环。' });
  return `<div class="gallery wide">${items.map((r) => `
    <div class="gallery-item" data-det="${esc(r.defect_id)}" role="button" tabindex="0">
      ${frameScene(r.defect_id, null, { fit: 'none' })}
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="${esc(r.defect_id)}" loading="lazy">` : ''}
      ${boxOverlay([{ box: r.truth_bbox, color: REF_COLOR, dash: true }, { box: r.bbox, color: RANK_COLORS[0] }])}
      <div class="gi-top">${outcomeBadge(r.outcome)}${r.category_uncertain ? badge('类别不确定', 'warn') : ''}${r.hard_slice ? badge('困难', 'violet') : ''}</div>
      <div class="gi-body">
        <span>${esc(r.pred ? defectName(r.pred) : '判无缺陷')} · ${esc(sourceName(r.source_type))}</span>
        <b class="mono">${pct(r.confidence, 0)}</b>
      </div>
    </div>`).join('')}</div>`;
}

export async function render(root, ctx) {
  if (ctx.params.get('outcome')) { state.outcome = ctx.params.get('outcome'); state.page = 1; }
  const [d, list] = await Promise.all([api.detectors(), api.detections(state)]);
  let last = list;
  const rounds = d.rounds || [];
  const latest = d.latest || {};
  const prev = rounds.length > 1 ? rounds[rounds.length - 2] : null;
  const catalog = d.catalog || [];
  const ready = catalog.filter((c) => c.status === 'ready').length;
  const q = d.quality || {};
  const flagTotal = Object.values(q.flags || {}).reduce((a, b) => a + b, 0);
  const l1 = d.l1 || {};
  const l1Spec = catalog.find((c) => c.name === l1.detector);

  if (!rounds.length && !catalog.length) {
    root.innerHTML = pageHead({ title: '检测模型', sub: '尚无评测数据' })
      + card({ body: emptyState({ iconName: 'target', title: '还没有检测记录', text: '运行闭环后，B2 检测器的每次推理都会留存在 defect_event.l1_result 中，并按轮评测。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const evalChart = lineChart({
    labels: rounds.map((r) => `R${r.round}`),
    series: [
      { name: '准确率', values: rounds.map((r) => (r.accuracy == null ? null : r.accuracy * 100)), color: palette()[0] },
      { name: '命中框 mIoU', values: rounds.map((r) => (r.mean_iou == null ? null : r.mean_iou * 100)), color: palette()[1] },
    ],
    height: 200, yMin: 0, yMax: 100, format: (v) => `${v.toFixed(0)}%`,
  });
  const latChart = barChart({
    labels: rounds.map((r) => `R${r.round}`),
    series: [
      { name: 'P50', values: rounds.map((r) => r.latency_ms_p50), color: palette()[1] },
      { name: 'P95', values: rounds.map((r) => r.latency_ms_p95), color: palette()[2] },
    ],
    height: 160, format: (v) => `${v.toFixed(0)}ms`,
  });

  const outcomes = d.outcomes || {};
  const outcomeDonut = donut({
    items: Object.keys(OUTCOME).filter((k) => outcomes[k]).map((k) => ({ label: OUTCOME[k][0], value: outcomes[k], color: `var(--${{ ok: 'green', teal: 'teal', warn: 'amber', bad: 'red', muted: 'text-3' }[OUTCOME[k][1]]})` })),
    centerLabel: '检测事件',
  });

  const reasons = Object.entries(d.d1_reasons || {}).sort((a, b) => b[1] - a[1]);
  const reasonMax = Math.max(1, ...reasons.map(([, v]) => v));
  const REASON = { low_confidence: '低置信', category_uncertain: '类别不确定', hard_slice: '困难切片', claim_conflict: '与用户描述矛盾', audio_anomaly_no_visual: '音频异常无视觉', l1_unavailable: 'L1 不可用' };

  const perClass = Object.entries(latest.per_class || {});
  const catalogRows = catalog.map((c) => `
    <tr>
      <td><b class="mono-sm">${esc(c.name)}</b><div class="tiny muted">${esc(c.desc || '')}</div></td>
      <td>${badge(c.impl, 'muted', false)}</td>
      <td>${badge(c.tier === 'prod' ? '生产' : 'Demo', c.tier === 'prod' ? 'warn' : 'info', false)}</td>
      <td>${statusBadge(c.status, c.reason)}${c.reason ? `<div class="tiny muted">${esc(c.reason)}</div>` : ''}</td>
      <td class="tiny">${(c.requires || []).length ? c.requires.map((r) => `<span class="${(c.missing || []).includes(r) ? 'neg' : 'muted'}">${esc(r)}</span>`).join(' ') : '<span class="muted">零依赖</span>'}</td>
      <td>${usedBy(c.name, d)}</td>
    </tr>`).join('');

  const statsRows = (d.model_stats || []).map((m) => `
    <tr><td>${badge(m.kind, 'muted', false)}</td><td class="mono-sm">${esc(m.model)}</td><td class="num">${num(m.calls)}</td>
      <td class="num ${m.failures ? 'neg' : ''}">${num(m.failures)}</td><td class="num">${fixed(m.latency_ms_p50, 1)}</td><td class="num">${fixed(m.latency_ms_p95, 1)}</td></tr>`).join('');

  const facets = last.facets || {};
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? 'selected' : ''}>${esc(label)}</option>`;
  const filterBar = () => `<div class="filterbar">
    <input type="search" id="det-q" placeholder="搜索缺陷 ID / 接入 ID / 类别" value="${esc(state.q)}">
    <select id="det-outcome">${opt('', '全部结果', state.outcome)}${Object.keys(facets.outcome || {}).map((k) => opt(k, `${(OUTCOME[k] || [k])[0]} (${facets.outcome[k]})`, state.outcome)).join('')}</select>
    <select id="det-label">${opt('', '全部预测', state.label)}${Object.keys(facets.label || {}).map((k) => opt(k, `${k === 'None' ? '判无缺陷' : defectName(k)} (${facets.label[k]})`, state.label)).join('')}</select>
    <select id="det-source">${opt('', '全部来源', state.source)}${Object.keys(facets.source || {}).map((k) => opt(k, `${sourceName(k)} (${facets.source[k]})`, state.source)).join('')}</select>
    <select id="det-round">${opt('', '全部轮次', state.round)}${Object.keys(facets.round || {}).sort((a, b) => a - b).map((k) => opt(k, `R${k} (${facets.round[k]})`, state.round)).join('')}</select>
    <span class="grow"></span>
    <div class="legend-swatches"><span><i style="background:${RANK_COLORS[0]}"></i>检测框</span><span><i style="background:${REF_COLOR}"></i>参考框（模拟真值）</span></div>
  </div>`;

  root.innerHTML = `
    ${pageHead({
      title: '检测模型',
      tag: esc(l1.detector || d.l1_impl),
      sub: `<span>B2 L1 实现 <b>${esc(d.l1_impl)}</b></span><span class="sep"></span><span>判定阈值 ${fixed(thr('l1_score'), 2)}</span><span class="sep"></span><span>D1 自动接受 ≥ ${fixed(thr('d1_min_confidence'), 2)}</span><span class="sep"></span><span>参考标签：模拟真值 / L2·L3 独立裁决</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="world">${icon('layers')}<span>世界模型</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '最新准确率', value: latest.accuracy == null ? '–' : pct(latest.accuracy, 1), foot: `${delta((latest.accuracy || 0) * 100, prev ? (prev.accuracy || 0) * 100 : null, { digits: 1, suffix: 'pp' })} R${esc(latest.round ?? '–')} · ${num(latest.evaluated)} / ${num(latest.events)} 事件`, iconName: 'target', tone: (latest.accuracy || 0) >= 0.8 ? 'green' : 'amber' })}
      ${kpi({ label: '命中框 mIoU', value: latest.mean_iou == null ? '–' : pct(latest.mean_iou, 1), foot: '类别正确时检测框与参考框', iconName: 'box', tone: 'teal' })}
      ${kpi({ label: '推理延迟 P50', value: `${fixed(latest.latency_ms_p50, 1)}<span class="tiny muted"> ms</span>`, foot: `P95 ${fixed(latest.latency_ms_p95, 1)} ms · 不可用 ${num(latest.unavailable)}`, iconName: 'clock' })}
      ${kpi({ label: '检测器目录', value: `${num(ready)}<span class="tiny muted"> / ${num(catalog.length)}</span>`, foot: '可用 / 已登记（生产候选需装依赖或部署服务）', iconName: 'cpu', tone: 'violet' })}
      ${kpi({ label: '场景质量异常', value: num(flagTotal), foot: `${num(q.count)} 张图像 · ${esc(q.model || '未启用')}`, iconName: 'alert', tone: flagTotal ? 'amber' : 'green' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({ title: '逐轮评测', sub: '准确率 = 预测类别与参考一致的事件占比；mIoU 只统计类别命中的框', body: evalChart })}
        ${card({ title: `混淆矩阵 · R${esc(latest.round ?? '–')}`, sub: `行 = 参考标签，列 = 检测器预测；参考来源 ${esc(Object.entries(latest.reference_mix || {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '–')}`, body: confusion(latest) })}
      </div>
      <div class="stack">
        ${card({
          title: 'B2 L1 筛查配置',
          sub: 'configs [models.l1] · 检测器不可用时回退 mock 并写审计',
          body: kv([
            ['检测器', `<b class="mono-sm">${esc(l1.detector || '–')}</b> ${l1Spec ? statusBadge(l1Spec.status, l1Spec.reason) : ''}`],
            ['实现', esc(l1Spec?.impl || d.l1_impl)],
            ['集成成员', l1Spec?.members?.length ? l1Spec.members.map((m) => badge(m, 'muted', false)).join(' ') : '<span class="muted">–</span>'],
            ['有缺陷阈值', fixed(d.thresholds?.score, 2)],
            ['类别不确定', `前两名分差 < ${fixed(d.thresholds?.uncertain_margin, 2)} 或集成成员不一致`],
            ['D1 自动接受', `置信度 ≥ ${fixed(d.thresholds?.d1_min_confidence, 2)} 且非困难切片`],
            ['P5 智能体', (d.agents || []).map((a) => badge(a, a.startsWith('detector') ? 'violet' : 'muted', false)).join(' ') || '<span class="muted">–</span>'],
          ]),
        })}
        ${card({ title: '检测结果分布', sub: '全部轮次 · 相对参考标签', body: outcomeDonut })}
        ${card({ title: 'D1 升级原因', sub: '检测结果如何影响路由（一个事件可多条原因）', body: reasons.length ? `<div class="list">${reasons.map(([k, v]) => barRow(REASON[k] || k, v, reasonMax, { display: num(v) })).join('')}</div>` : '<div class="panel-note">没有升级记录。</div>' })}
      </div>
    </div>

    ${card({ title: '检测模型目录', sub: 'configs [models.detectors.*] · 状态在不实例化模型的前提下检查依赖与配置', cls: 'mt-20', pad0: true,
      body: `<div class="table-wrap" style="border:0;border-radius:0"><table class="data compact">
        <thead><tr><th>名字</th><th>实现</th><th>层级</th><th>状态</th><th>依赖</th><th>使用位置</th></tr></thead>
        <tbody>${catalogRows}</tbody></table></div>` })}

    <div class="g-2 mt-20">
      ${card({ title: `分类别指标 · R${esc(latest.round ?? '–')}`, sub: '精确率 / 召回 / F1，support = 参考为该类的事件数',
        body: perClass.length ? `<div class="table-wrap"><table class="data compact">
          <thead><tr><th>类别</th><th class="num">精确率</th><th class="num">召回</th><th class="num">F1</th><th class="num">support</th></tr></thead>
          <tbody>${perClass.map(([k, v]) => `<tr><td>${esc(defectName(k))}</td><td class="num">${pct(v.precision, 0)}</td><td class="num">${pct(v.recall, 0)}</td>
            <td class="num"><b>${pct(v.f1, 0)}</b></td><td class="num">${num(v.support)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="panel-note">暂无。</div>' })}
      ${card({ title: '场景质量检测（A2）', sub: `${esc(q.model || '未启用')} · 模糊 / 曝光 / 对比度 / 噪声；困难切片会升级到 L2`,
        body: q.count ? `<div class="list">${Object.entries(q.flags || {}).map(([k, v]) => barRow(flagName(k), v, Math.max(1, q.count), { display: `${num(v)} · ${pct(v / q.count, 0)}`, tone: v ? 'amber' : '' })).join('')}</div>
          <div class="mt-14">${barChart({ labels: (q.score_hist || []).map((_, i) => (i / 10).toFixed(1)), series: [{ name: '综合质量分', values: q.score_hist || [], color: palette()[1] }], height: 130, format: (v) => num(v) })}</div>`
          : '<div class="panel-note">未启用场景质量检测（[impl] scene_quality）。</div>' })}
    </div>

    <div class="g-2 mt-20">
      ${card({ title: '推理延迟', sub: 'B2 检测器每轮 P50 / P95', body: latChart })}
      ${card({ title: '本轮模型调用', sub: '检测器 / 场景质量 / L2 VLM 的调用、失败与耗时', pad0: true,
        body: statsRows ? `<div class="table-wrap" style="border:0;border-radius:0"><table class="data compact">
          <thead><tr><th>类型</th><th>模型</th><th class="num">调用</th><th class="num">失败</th><th class="num">P50 ms</th><th class="num">P95 ms</th></tr></thead>
          <tbody>${statsRows}</tbody></table></div>` : '<div class="panel-note" style="padding:0 16px 16px">暂无调用统计。</div>' })}
    </div>

    <div class="card mt-20">
      <div class="card-head"><div><div class="card-title"><span class="dot"></span>检测回放</div>
        <div class="card-sub">每个接入事件上 B2 检测器的原始输出（defect_event.l1_result），与参考标签对照</div></div></div>
      <div id="det-filter">${filterBar()}</div>
      <div id="det-body">${gallery(last.items)}</div>
      <div id="det-pager">${pager(last)}</div>
    </div>
  `;

  const body = root.querySelector('#det-body');
  const pagerBox = root.querySelector('#det-pager');
  let busy = false;

  async function load() {
    if (busy) return;
    busy = true;
    body.style.opacity = '.55';
    try {
      last = await api.detections(state);
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

  function openDetail(id) {
    const r = (last.items || []).find((x) => x.defect_id === id);
    if (!r) return;
    const dets = r.detections || [];
    const boxes = [{ box: r.truth_bbox, color: REF_COLOR, dash: true }, ...dets.map((x, i) => ({ box: x.bbox, color: RANK_COLORS[i % RANK_COLORS.length], width: i ? 1 : 1.8 }))];
    const sq = r.scene_quality || {};
    ctx.drawer({
      eyebrow: `${sourceName(r.source_type)} · R${r.round ?? '–'} · ${r.model || '–'}`,
      title: id,
      body: `${imageStage(r.thumb, id, boxes, `${r.ingest_id} · 检测框按分数排序着色`)}
        <div class="legend-swatches mt-14"><span><i style="background:${REF_COLOR}"></i>参考框</span>${dets.map((x, i) => `<span><i style="background:${RANK_COLORS[i % RANK_COLORS.length]}"></i>${esc(defectName(x.label))} ${pct(x.score, 0)}${x.support < 1 ? ` · 支持 ${pct(x.support, 0)}` : ''}</span>`).join('')}</div>
        <div class="mini-grid mt-14">
          ${miniStat('L1 预测', esc(r.pred ? defectName(r.pred) : '判无缺陷'), `置信度 ${pct(r.confidence, 1)}`)}
          ${miniStat('参考', esc(r.reference ? defectName(r.reference) : r.ref_source === 'none' ? '–' : '无缺陷'), esc(r.ref_source))}
          ${miniStat('结果', outcomeBadge(r.outcome), `最终 ${esc(r.final_decision || '–')} · ${esc(r.level || '')}`)}
        </div>
        <div class="detail-section mt-14"><h4>路由</h4>${kv([
          ['D1', `${badge(r.d1 || '–', r.d1 === 'auto_accept' ? 'ok' : 'warn', false)} ${(r.d1_reasons || []).map((x) => badge(x, 'muted', false)).join(' ')}`],
          ['类别不确定', r.category_uncertain ? badge('是', 'warn') : '否'],
          ['困难切片', r.hard_slice ? badge('是', 'violet') : '否'],
          ['推理耗时', `${fixed(r.latency_ms, 1)} ms`],
          ['最终类型', esc(defectName(r.final_type))],
        ])}</div>
        ${sq.score !== undefined ? `<div class="detail-section"><h4>场景质量 · ${esc(sq.model || '')}</h4>
          ${barRow('综合质量', sq.score * 100, 100, { display: pct(sq.score, 0), tone: sq.score < 0.4 ? 'amber' : 'green' })}
          ${barRow('模糊度', sq.blur * 100, 100, { display: pct(sq.blur, 0) })}
          ${barRow('噪声', sq.noise * 100, 100, { display: pct(sq.noise, 0) })}
          ${barRow('对比度', sq.contrast * 100, 100, { display: pct(sq.contrast, 0), tone: 'teal' })}
          <div class="row mt-14">${badge(`曝光 ${fixed(sq.exposure, 2)}`, 'muted', false)}${(sq.flags || []).map((f) => badge(flagName(f), 'warn', false)).join('')}</div></div>` : ''}
        <div class="row"><button class="btn btn-sm" data-go-lineage="${esc(id)}">${icon('git')}<span>查看血缘</span></button></div>`,
    });
    document.querySelector('[data-go-lineage]')?.addEventListener('click', () => { ctx.closeDrawer(); ctx.go('lineage', { focus: id }); });
    wireFrameImages(document);
  }

  function bind() {
    wireFrameImages(body);
    body.querySelectorAll('[data-det]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.dataset.det));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.dataset.det); } });
    });
    pagerBox.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
      const p = Number(b.dataset.page);
      if (p >= 1 && p <= last.pages && p !== last.page) { state.page = p; load(); }
    }));
  }

  const onFilter = () => { state.page = 1; load(); };
  root.querySelector('#det-q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); onFilter(); }, 320));
  [['#det-outcome', 'outcome'], ['#det-label', 'label'], ['#det-source', 'source'], ['#det-round', 'round']].forEach(([sel, key]) => {
    root.querySelector(sel).addEventListener('change', (e) => { state[key] = e.target.value; onFilter(); });
  });
  bind();
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="world"]').forEach((b) => b.addEventListener('click', () => ctx.go('world')));
  if (!rounds.length) body.insertAdjacentHTML('beforebegin', callout('info', '还没有评测轮次：先运行闭环。'));
}
