/* 人工审核台：待审队列、证据链、裁决回写 */

import { api } from '../api.js';
import { esc, num, ts, badge, icon, pct } from '../util.js';
import { pageHead, card, emptyState, callout, kv, miniStat, barRow, stageBadge } from '../ui.js';
import { decisionBadge } from '../util.js';
import { imageStage, wireFrameImages } from '../frames.js';
import { meta } from '../meta.js';

const DECISIONS = [
  { id: 'confirmed', label: '确认缺陷' },
  { id: 'rejected', label: '拒绝 / 误报' },
  { id: 'needs_more_data', label: '补数据' },
];
const DEFECTS = [...(meta().classes || ['crack', 'scratch', 'dent', 'rust']), 'none'];
const BOX_COLORS = ['#2f6bff', '#7c5cff', '#d97706'];
const SEVERITIES = ['low', 'medium', 'high', 'critical'];

function reasonsHtml(reasons) {
  if (!reasons?.length) return '<span class="muted tiny">无</span>';
  return `<div class="row" style="gap:5px">${reasons.map((r) => badge(r, r.includes('disagree') ? 'warn' : 'muted', false)).join('')}</div>`;
}

function objKv(obj) {
  if (!obj || typeof obj !== 'object') return '<span class="muted">–</span>';
  const entries = Object.entries(obj);
  if (!entries.length) return '<span class="muted">–</span>';
  return kv(entries.slice(0, 14).map(([k, v]) => [k, typeof v === 'object' ? `<span class="mono-sm">${esc(JSON.stringify(v))}</span>` : esc(v)]));
}

export async function render(root, ctx) {
  const data = await api.review(true);
  const pending = data.pending || [];
  const resolved = data.resolved || [];
  const first = ctx.params.get('id') || pending[0]?.queue_id || '';
  let selected = pending.find((x) => x.queue_id === first) || pending[0] || null;

  const impl = data.reviewer_impl || '–';
  const queueEmpty = !pending.length;

  const listHtml = pending.length ? `<div class="list">${pending.map((p) => `
    <button type="button" class="list-row" data-q="${esc(p.queue_id)}" style="text-align:left;width:100%;cursor:pointer;${selected?.queue_id === p.queue_id ? 'border-color:var(--accent);background:var(--accent-soft)' : ''}">
      <span class="badge ${Number(p.priority) >= 8 ? 'bad' : Number(p.priority) >= 5 ? 'warn' : 'muted'}" style="min-width:36px;justify-content:center">${num(p.priority)}</span>
      <div class="grow">
        <div class="mono-sm" style="font-weight:600">${esc(p.queue_id)}</div>
        <div class="meta">${esc((p.reasons || []).join(' · ') || p.ingest_id || '')}</div>
      </div>
      ${icon('chevronRight')}
    </button>`).join('')}</div>` : emptyState({
    iconName: 'shield',
    title: '待审队列为空',
    text: impl === 'oracle'
      ? `当前 <code>impl.reviewer = "oracle"</code>，模拟真值会自动裁决，因此不会产生待审条目。把配置改为 <code>queue</code> 并重跑闭环，即可在此进行人工裁决。`
      : '所有条目都已裁决。运行新一轮后，新的分歧样本会进入队列。',
    action: '<button class="btn btn-primary" data-act="run">运行闭环</button>',
  });

  const resolvedRows = resolved.length ? `<div class="table-wrap"><table class="data compact">
    <thead><tr><th>队列条目</th><th>优先级</th><th>裁决</th><th>缺陷</th><th>耗时</th></tr></thead>
    <tbody>${resolved.slice(0, 20).map((p) => {
      const d = p.decision || {};
      return `<tr>
        <td class="id">${esc(p.queue_id)}</td>
        <td class="num">${num(p.priority)}</td>
        <td>${decisionBadge(d.decision)}</td>
        <td>${esc(d.defect_type || '–')}</td>
        <td class="num">${d.minutes_spent === undefined ? '–' : `${num(d.minutes_spent)} min`}</td>
      </tr>`;
    }).join('')}</tbody></table></div>` : '<div class="panel-note">还没有已裁决条目。</div>';

  const detail = selected ? `
    <div class="card">
      <div class="card-head">
        <div>
          <div class="card-title"><span class="dot"></span>${esc(selected.queue_id)}</div>
          <div class="card-sub">优先级 ${num(selected.priority)} · 来源接入 ${esc(selected.ingest_id || '–')}</div>
        </div>
        <div class="card-actions">${badge(`impl: ${impl}`, 'muted', false)}</div>
      </div>
      <div class="mini-grid">
        ${miniStat('优先级', num(selected.priority), Number(selected.priority) >= 8 ? '高优先' : '常规')}
        ${miniStat('触发原因', num((selected.reasons || []).length), (selected.reasons || []).slice(0, 2).join(' · ') || '–')}
        ${miniStat('来源', esc(selected.ingest?.source_type || '–'), esc(selected.ingest?.ingest_id || selected.ingest_id || ''))}
      </div>
      ${selected.frame_url ? `<div class="detail-section mt-14"><h4>媒体与 L1 检测框</h4>
        ${imageStage(selected.frame_url, selected.queue_id, (selected.l1?.detections || []).slice(0, 3).map((d, i) => ({ box: d.bbox, color: BOX_COLORS[i] })), (selected.l1?.detections || []).slice(0, 3).map((d) => `${d.label} ${pct(d.score, 0)}`).join(' · ') || 'L1 无候选框')}</div>` : ''}
      <div class="detail-section mt-14"><h4>触发原因</h4>${reasonsHtml(selected.reasons)}</div>
      ${selected.why ? `<div class="detail-section"><h4>为什么进入人工</h4><div class="panel-note">${esc(selected.why)}</div></div>` : ''}
      <div class="split-2">
        <div class="detail-section"><h4>用户 claim</h4>${objKv(selected.claim)}</div>
        <div class="detail-section"><h4>接入事件</h4>${objKv(selected.ingest)}</div>
      </div>
      <div class="split-2">
        <div class="detail-section"><h4>L1 结果</h4>${objKv(selected.l1)}</div>
        <div class="detail-section"><h4>L2 结果</h4>${objKv(selected.l2)}</div>
      </div>
      <div class="detail-section"><h4>裁决</h4>
        <form id="review-form" class="stack" style="gap:12px">
          <label class="form-field">结论
            <select name="decision">
              ${DECISIONS.map((d) => `<option value="${d.id}" ${selected.decision_template?.decision === d.id ? 'selected' : ''}>${esc(d.label)}</option>`).join('')}
            </select>
          </label>
          <div class="form-grid">
            <label class="form-field">缺陷类型
              <select name="defect_type">
                <option value="">保持原判</option>
                ${DEFECTS.map((d) => `<option value="${d}" ${selected.decision_template?.defect_type === d ? 'selected' : ''}>${esc(d)}</option>`).join('')}
              </select>
            </label>
            <label class="form-field">严重度
              <select name="severity">
                <option value="">未标注</option>
                ${SEVERITIES.map((s) => `<option value="${s}" ${selected.decision_template?.severity === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
              </select>
            </label>
            <label class="form-field">审核人
              <input name="reviewer" value="web-reviewer" maxlength="64">
            </label>
            <label class="form-field">耗时（分钟）
              <input name="minutes_spent" type="number" min="0" step="0.5" value="0">
            </label>
            <label class="form-field full">理由
              <textarea name="reason" placeholder="记录判断依据，会写入审计日志">${esc(selected.decision_template?.reason || '')}</textarea>
            </label>
          </div>
          <div class="row" style="justify-content:flex-end">
            <button type="submit" class="btn btn-primary" id="review-submit">${icon('check')}<span>提交裁决</span></button>
          </div>
        </form>
      </div>
    </div>` : '';

  root.innerHTML = `
    ${pageHead({
      title: '人工审核台',
      tag: `${num(data.pending_count || 0)} 待审`,
      sub: `<span>待审 ${num(data.pending_count || 0)}</span><span class="sep"></span><span>已裁决 ${num(data.resolved_count || 0)}</span><span class="sep"></span><span>reviewer = ${esc(impl)}</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="annotations">${icon('tag')}<span>标注样例</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    ${queueEmpty ? callout('info', `审核台读取 <code class="code-inline">lake/review_queue/*.json</code>。oracle 模式不会落盘待审文件；如需体验人工裁决，把 <code class="code-inline">configs/demo.toml</code> 里的 <code class="code-inline">reviewer = "oracle"</code> 改为 <code class="code-inline">"queue"</code>，再运行 1–2 轮。`, 'info') : ''}

    <div class="g-side mt-20">
      <div class="stack">
        ${card({ title: '待审队列', sub: '按优先级降序 · 点击切换', body: listHtml })}
        ${card({ title: '已裁决', sub: '最近 20 条', pad0: true, body: resolvedRows })}
      </div>
      <div class="stack">${detail}</div>
    </div>
  `;

  wireFrameImages(root);
  root.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => ctx.go('review', { id: b.dataset.q })));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="annotations"]').forEach((b) => b.addEventListener('click', () => ctx.go('annotations')));

  const form = root.querySelector('#review-form');
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = root.querySelector('#review-submit');
    const fd = new FormData(form);
    const payload = {
      decision: fd.get('decision'),
      defect_type: fd.get('defect_type') || null,
      severity: fd.get('severity') || null,
      reviewer: fd.get('reviewer') || 'web-reviewer',
      minutes_spent: Number(fd.get('minutes_spent') || 0),
      reason: fd.get('reason') || '',
    };
    btn.disabled = true;
    btn.innerHTML = '提交中…';
    try {
      const res = await api.submitReview(selected.queue_id, payload);
      ctx.toast(`已提交 ${res.queue_id}：${payload.decision}`, 'ok');
      ctx.refresh();
    } catch (err) {
      ctx.toast(err.message, 'bad');
      btn.disabled = false;
      btn.innerHTML = `${icon('check')}<span>提交裁决</span>`;
    }
  });
}
