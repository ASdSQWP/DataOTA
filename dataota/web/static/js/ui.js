/* 通用视图组件：页面头、KPI、卡片、空态、分页、标签、进度条、说明块 */

import { esc, icon, badge } from './util.js';

export function pageHead({ title, sub = '', actions = '', tag = '' }) {
  return `<div class="page-head">
    <div>
      <h1 class="page-title">${esc(title)}${tag ? `<span class="tag-pill">${esc(tag)}</span>` : ''}</h1>
      ${sub ? `<div class="page-sub">${sub}</div>` : ''}
    </div>
    ${actions ? `<div class="page-actions">${actions}</div>` : ''}
  </div>`;
}

export function kpi({ label, value, foot = '', tone = '', spark = '', iconName = '' }) {
  return `<div class="kpi ${tone ? `t-${tone}` : ''}">
    <div class="kpi-label">${iconName ? icon(iconName) : ''}<span>${esc(label)}</span></div>
    <div class="kpi-value">${value}</div>
    <div class="kpi-foot">${foot}</div>
    ${spark ? `<div class="kpi-spark">${spark}</div>` : ''}
  </div>`;
}

export function card({ title = '', sub = '', actions = '', body = '', cls = '', id = '', pad0 = false }) {
  return `<section class="card ${pad0 ? 'pad-0' : ''} ${cls}" ${id ? `id="${esc(id)}"` : ''}>
    ${title || actions || sub ? `<div class="card-head">
      <div>
        ${title ? `<div class="card-title"><span class="dot"></span>${esc(title)}</div>` : ''}
        ${sub ? `<div class="card-sub">${sub}</div>` : ''}
      </div>
      ${actions ? `<div class="card-actions">${actions}</div>` : ''}
    </div>` : ''}
    ${body}
  </section>`;
}

export function tabs(items, active, cls = '') {
  return `<div class="tabs ${cls}">${items.map((it) => `
    <button type="button" class="tab ${it.id === active ? 'active' : ''}" data-tab="${esc(it.id)}">
      ${esc(it.label)}${it.count !== undefined ? `<span class="count">${esc(it.count)}</span>` : ''}
    </button>`).join('')}</div>`;
}

export function barRow(label, value, max, { tone = '', display = '', hint = '' } = {}) {
  const w = max > 0 ? Math.min(100, (Number(value) / max) * 100) : 0;
  return `<div class="bar-row">
    <span class="bar-label" title="${esc(label)}">${esc(label)}</span>
    <div class="bar ${tone}"><span style="width:${w.toFixed(1)}%"></span></div>
    <span class="val">${display || esc(value)}</span>
    ${hint ? `<span class="val muted tiny">${hint}</span>` : ''}
  </div>`;
}

export function pager({ page, pages, total, size }, { id = 'pager' } = {}) {
  const start = total ? (page - 1) * size + 1 : 0;
  const end = Math.min(total, page * size);
  return `<div class="pager" data-pager="${esc(id)}">
    <div class="pager-info">共 <b>${total}</b> 条${total ? ` · 当前 ${start}–${end}` : ''} · 第 ${page} / ${pages} 页</div>
    <div class="pager-btns">
      <button type="button" class="btn btn-sm btn-ghost" data-page="1" ${page <= 1 ? 'disabled' : ''}>首页</button>
      <button type="button" class="btn btn-sm btn-ghost" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>
      <button type="button" class="btn btn-sm btn-ghost" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>下一页</button>
      <button type="button" class="btn btn-sm btn-ghost" data-page="${pages}" ${page >= pages ? 'disabled' : ''}>末页</button>
    </div>
  </div>`;
}

export function emptyState({ title = '暂无数据', text = '', action = '', iconName = 'box' }) {
  return `<div class="empty">
    <div class="empty-icon">${icon(iconName)}</div>
    <h3>${esc(title)}</h3>
    ${text ? `<p>${text}</p>` : ''}
    ${action || ''}
  </div>`;
}

export function callout(tone, html, iconName = 'info') {
  return `<div class="callout ${tone}">${icon(iconName)}<div>${html}</div></div>`;
}

export function kv(pairs) {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v ?? '<span class="muted">–</span>'}</dd>`).join('')}</dl>`;
}

export function miniStat(label, value, hint = '') {
  return `<div class="mini-stat">
    <div class="mini-label">${esc(label)}</div>
    <div class="mini-value">${value}</div>
    ${hint ? `<div class="mini-hint">${hint}</div>` : ''}
  </div>`;
}

export function delta(cur, prev, { invert = false, digits = 0, suffix = '' } = {}) {
  const a = Number(cur);
  const b = Number(prev);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '';
  const d = a - b;
  if (Math.abs(d) < 1e-9) return `<span class="delta flat">±0</span>`;
  const good = invert ? d < 0 : d > 0;
  const text = `${d > 0 ? '+' : ''}${d.toLocaleString('zh-CN', { maximumFractionDigits: digits })}${suffix}`;
  return `<span class="delta ${good ? 'up' : 'down'}">${esc(text)}</span>`;
}

export function section(title, body, sub = '', actions = '') {
  return card({ title, sub, actions, body });
}

export const TONE_BY_HEALTH = { ok: 'ok', warn: 'warn', bad: 'bad', info: 'info' };

export function healthCard({ tone = 'info', iconName = 'check', label, value }) {
  return `<div class="health ${tone}">
    <div class="health-icon">${icon(iconName)}</div>
    <div>
      <div class="health-label">${esc(label)}</div>
      <div class="health-value">${value}</div>
    </div>
  </div>`;
}

export function stageBadge(status) {
  const s = String(status ?? '');
  if (s === 'released') return badge('已发布', 'ok');
  if (s === 'gate_rejected') return badge('门禁拒绝', 'bad');
  if (s.startsWith('rolled_back')) return badge(s === 'rolled_back_at_canary' ? '灰度回滚' : '已回滚', 'bad');
  if (s.includes('halted')) return badge('已暂停', 'warn');
  if (s === 'partial') return badge('部分发布', 'info');
  return badge(status, 'muted');
}
