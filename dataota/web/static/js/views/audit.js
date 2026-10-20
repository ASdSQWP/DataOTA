/* 审计日志：全链路动作检索、详情查看与 CSV 导出 */

import { api } from '../api.js';
import { esc, num, ts, badge, icon, shortId, debounce } from '../util.js';
import { pageHead, kpi, card, emptyState, pager, kv, barRow, miniStat } from '../ui.js';
import { palette } from '../charts.js';

const state = { page: 1, size: 30, action: '', actor: '', q: '' };
let last = null;

const ACTION_TONE = {
  redact: 'teal', arbitrate: 'violet', ingest: 'info', route: 'warn', review_decision: 'ok',
  train: 'violet', eval: 'warn', release: 'ok', rollback: 'bad', ota: 'info', delete: 'bad',
};

export async function render(root, ctx) {
  last = await api.audit(state);
  const facets = last.facets || {};
  const actions = Object.entries(facets.actions || {}).sort((a, b) => b[1] - a[1]);
  const actors = Object.entries(facets.actors || {}).sort((a, b) => b[1] - a[1]);
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(label)}</option>`;

  const rows = () => (last.items || []).map((r) => {
    const detail = JSON.stringify(r.detail || {});
    return `<tr class="clickable" data-audit="${esc(String(r.ts || ''))}">
      <td class="mono-sm nowrap">${esc(r.ts_iso || ts(r.ts))}</td>
      <td>${badge(r.actor || '–', r.actor === 'oracle' ? 'violet' : 'muted', false)}</td>
      <td>${badge(r.action || '–', ACTION_TONE[r.action] || 'info', false)}</td>
      <td class="id truncate" style="max-width:280px" title="${esc(r.target || '')}">${esc(r.target || '–')}</td>
      <td class="muted tiny truncate" style="max-width:280px">${esc(detail === '{}' ? '' : detail)}</td>
    </tr>`;
  }).join('');

  const actionBars = actions.slice(0, 10).map(([a, n], i) => barRow(a, n, actions[0]?.[1] || 1, {
    display: num(n), tone: ['', 'teal', 'amber', 'red'][i % 4],
  })).join('');

  root.innerHTML = `
    ${pageHead({
      title: '审计日志',
      tag: `${num(last.total)} 条`,
      sub: `<span>append-only JSONL</span><span class="sep"></span><span>${num(actions.length)} 种动作</span><span class="sep"></span><span>${num(actors.length)} 个执行主体</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="csv">${icon('download')}<span>导出 CSV</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '审计事件', value: num(facets.total || 0), foot: 'audit.jsonl 全量', iconName: 'list' })}
      ${kpi({ label: '执行主体', value: num(actors.length), foot: actors.slice(0, 3).map(([a]) => a).join(' · '), iconName: 'user', tone: 'violet' })}
      ${kpi({ label: '动作类型', value: num(actions.length), foot: actions.slice(0, 3).map(([a]) => a).join(' · '), iconName: 'tag', tone: 'teal' })}
      ${kpi({ label: '当前筛选', value: `<span id="a-filtered">${num(last.total)}</span>`, foot: '动作 / 主体 / 关键词组合', iconName: 'filter', tone: 'amber' })}
    </div>

    <div class="g-side mt-20">
      ${card({
        title: '事件流',
        sub: '按时间倒序 · 点击行查看完整 detail',
        body: `<div class="filterbar">
            <select id="a-action">${opt('', '全部动作', state.action)}${actions.map(([a, n]) => opt(a, `${a} (${n})`, state.action)).join('')}</select>
            <select id="a-actor">${opt('', '全部主体', state.actor)}${actors.map(([a, n]) => opt(a, `${a} (${n})`, state.actor)).join('')}</select>
            <input type="search" id="a-q" placeholder="搜索 target / detail" value="${esc(state.q)}">
            <span class="grow"></span>
            <select id="a-size">${[20, 30, 50, 100].map((n) => opt(String(n), `${n} / 页`, String(state.size))).join('')}</select>
          </div>
          <div class="table-wrap"><table class="data compact"><thead><tr>
            <th>时间</th><th>主体</th><th>动作</th><th>目标</th><th>详情</th>
          </tr></thead><tbody id="a-rows">${rows() || '<tr><td colspan="5"><div class="table-empty">没有匹配的审计事件</div></td></tr>'}</tbody></table></div>
          <div id="a-pager">${pager(last)}</div>`,
      })}
      <div class="stack">
        ${card({
          title: '动作分布',
          sub: '审计事件按 action 聚合',
          body: actionBars || '<div class="panel-note">暂无动作。</div>',
        })}
        ${card({
          title: '审计说明',
          sub: 'C4 可追溯性',
          body: `<div class="panel-note">
            每个关键动作写入一行 JSON：<code class="code-inline">ts / actor / action / target / detail</code>。
            训练、评测、发布、回滚、人工裁决都可回放到具体版本与设备。
          </div>
          <div class="mini-grid mt-14">
            ${miniStat('最近事件', last.items?.[0] ? esc(shortId(last.items[0].action || '–', 24)) : '–', last.items?.[0]?.ts_iso || '')}
            ${miniStat('覆盖主体', num(actors.length), 'A0–T10 / oracle / web')}
          </div>`,
        })}
      </div>
    </div>
  `;

  const tbody = root.querySelector('#a-rows');
  const pagerBox = root.querySelector('#a-pager');
  let busy = false;

  async function load() {
    if (busy) return;
    busy = true;
    tbody.style.opacity = '.55';
    try {
      last = await api.audit(state);
      tbody.innerHTML = rows() || '<tr><td colspan="5"><div class="table-empty">没有匹配的审计事件</div></td></tr>';
      pagerBox.innerHTML = pager(last);
      const filtered = root.querySelector('#a-filtered');
      if (filtered) filtered.textContent = num(last.total);
      bindRows();
      bindPager();
    } catch (e) {
      ctx.toast(e.message, 'bad');
    } finally {
      busy = false;
      tbody.style.opacity = '';
    }
  }

  function bindPager() {
    pagerBox.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
      const p = Number(b.dataset.page);
      if (p >= 1 && p <= last.pages && p !== last.page) { state.page = p; load(); }
    }));
  }

  function bindRows() {
    tbody.querySelectorAll('[data-audit]').forEach((tr) => tr.addEventListener('click', () => {
      const tsKey = tr.dataset.audit;
      const r = (last.items || []).find((x) => String(x.ts || '') === tsKey);
      if (!r) return;
      ctx.drawer({
        eyebrow: `审计 · ${r.action || '–'}`,
        title: r.actor || '未知主体',
        body: `${kv([
          ['时间', esc(r.ts_iso || ts(r.ts))],
          ['动作', badge(r.action || '–', ACTION_TONE[r.action] || 'info', false)],
          ['主体', esc(r.actor || '–')],
          ['目标', `<span class="mono-sm">${esc(r.target || '–')}</span>`],
        ])}
        <div class="detail-section mt-14"><h4>detail</h4>
          <pre class="code-block">${esc(JSON.stringify(r.detail || {}, null, 2))}</pre>
        </div>`,
      });
    }));
  }

  root.querySelector('#a-action').addEventListener('change', (e) => { state.action = e.target.value; state.page = 1; load(); });
  root.querySelector('#a-actor').addEventListener('change', (e) => { state.actor = e.target.value; state.page = 1; load(); });
  root.querySelector('#a-size').addEventListener('change', (e) => { state.size = Number(e.target.value); state.page = 1; load(); });
  root.querySelector('#a-q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); state.page = 1; load(); }, 320));

  bindRows();
  bindPager();

  root.querySelectorAll('[data-act="csv"]').forEach((b) => b.addEventListener('click', () => {
    const url = api.auditCsvUrl({ action: state.action, actor: state.actor, q: state.q });
    const a = document.createElement('a');
    a.href = url;
    a.download = 'dataota-audit.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    ctx.toast('已开始下载 CSV', 'ok');
  }));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
}
