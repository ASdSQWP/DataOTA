/* 血缘图谱：按分层排布的 DAG、节点检索、上下游与全链路追溯 */

import { api } from '../api.js';
import { esc, num, pct, badge, icon, shortId, debounce, kindBadge, LAYER_LABEL } from '../util.js';
import { pageHead, card, emptyState, callout, kv, miniStat, tabs } from '../ui.js';

const KIND_ORDER = ['ingest', 'defect', 'anchor', 'generated', 'gate', 'annotation', 'dataset', 'model', 'build', 'release', 'device'];
const KIND_COLOR = {
  ingest: '#2f6bff', defect: '#7c5cff', anchor: '#0ea5a4', generated: '#0d9488', gate: '#d97706',
  annotation: '#16a34a', dataset: '#2563eb', model: '#8b5cf6', build: '#b45309',
  release: '#059669', device: '#64748b',
};
const NODE_W = 132;
const NODE_H = 56;
const GAP_X = 42;
const GAP_Y = 16;

const cut = (s, n = 17) => (String(s ?? '').length > n ? `${String(s).slice(0, n - 1)}…` : String(s ?? ''));

function nodeSub(n) {
  const a = n.attrs || {};
  switch (n.kind) {
    case 'ingest': return a.source_type || '';
    case 'defect': return `${a.defect_type || ''} · ${a.level || ''}`;
    case 'anchor': return a.priority || '';
    case 'generated': return a.view || '';
    case 'gate': return `${a.status || ''} · ${(Number(a.score) || 0).toFixed(2)}`;
    case 'annotation': return `${pct(a.agent_consensus, 0)} · ${a.split || ''}`;
    case 'dataset': return `${num(a.total)} 样本`;
    case 'model': return `day ${pct(a.m_day, 1)}`;
    case 'build': return a.target || '';
    case 'release': return a.model_version || '';
    case 'device': return a.current_version || '';
    default: return '';
  }
}

function layout(nodes) {
  const cols = new Map();
  nodes.forEach((n) => {
    const idx = Math.max(0, KIND_ORDER.indexOf(n.kind));
    if (!cols.has(idx)) cols.set(idx, []);
    cols.get(idx).push(n);
  });
  const pos = new Map();
  let maxRows = 1;
  [...cols.keys()].sort((a, b) => a - b).forEach((idx) => {
    const arr = cols.get(idx).sort((a, b) => String(a.label).localeCompare(String(b.label)));
    maxRows = Math.max(maxRows, arr.length);
    arr.forEach((n, row) => {
      pos.set(n.id, {
        x: 24 + idx * (NODE_W + GAP_X),
        y: 52 + row * (NODE_H + GAP_Y),
        row,
      });
    });
  });
  const width = 24 * 2 + (Math.max(...cols.keys(), 0) + 1) * (NODE_W + GAP_X) - GAP_X;
  const height = 52 + maxRows * (NODE_H + GAP_Y) + 20;
  return { pos, width, height, cols };
}

function svgGraph(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const { pos, width, height, cols } = layout(nodes);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const colorOf = (k) => KIND_COLOR[k] || '#64748b';

  const headers = [...cols.keys()].sort((a, b) => a - b).map((idx) => {
    const kind = KIND_ORDER[idx];
    const x = 24 + idx * (NODE_W + GAP_X);
    return `<g>
      <line x1="${x}" y1="36" x2="${x + NODE_W}" y2="36" stroke="${colorOf(kind)}" stroke-width="3" stroke-linecap="round" opacity=".75"/>
      <text class="gkind-label" x="${x}" y="26" fill="${colorOf(kind)}">${esc(kind)} · ${cols.get(idx).length}</text>
    </g>`;
  }).join('');

  const edgeSvg = edges.map((e) => {
    const a = pos.get(e.parent);
    const b = pos.get(e.child);
    if (!a || !b) return '';
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const dx = Math.max(28, (x2 - x1) * 0.5);
    const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
    const deployed = e.rel === 'deployed';
    return `<path class="gedge" data-parent="${esc(e.parent)}" data-child="${esc(e.child)}"
      d="${d}" fill="none" stroke="${deployed ? '#0f9d6e' : 'var(--border-strong)'}"
      stroke-width="${deployed ? 1.8 : 1.3}" ${deployed ? 'stroke-dasharray="5 4"' : ''}
      marker-end="url(#arrow-${deployed ? 'ok' : 'base'})" opacity=".85"/>`;
  }).join('');

  const nodeSvg = nodes.map((n) => {
    const p = pos.get(n.id) || { x: 0, y: 0 };
    const color = colorOf(n.kind);
    return `<g class="gnode" data-id="${esc(n.id)}" transform="translate(${p.x},${p.y})">
      <rect width="${NODE_W}" height="${NODE_H}" rx="9" fill="${color}" fill-opacity=".075" stroke="${color}" stroke-width="1.25"/>
      <rect width="3" height="${NODE_H}" rx="1.5" fill="${color}" opacity=".9"/>
      <text x="11" y="16" class="gnode-id" fill="${color}">${esc(cut(n.id, 22))}</text>
      <text x="11" y="33" class="gnode-label" fill="var(--text)">${esc(cut(n.label, 18))}</text>
      <text x="11" y="47" class="gkind-label" fill="var(--text-3)">${esc(cut(nodeSub(n), 20))}</text>
      <title>${esc(n.id)}${n.attrs ? `\n${esc(JSON.stringify(n.attrs).slice(0, 220))}` : ''}</title>
    </g>`;
  }).join('');

  return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="血缘图">
    <defs>
      <marker id="arrow-base" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="var(--border-strong)"/></marker>
      <marker id="arrow-ok" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" fill="#0f9d6e"/></marker>
    </defs>
    ${headers}
    ${edgeSvg}
    ${nodeSvg}
  </svg>`;
}

function relatedSets(nodes, edges, id) {
  const up = new Set([id]);
  const down = new Set([id]);
  const nbr = new Set([id]);
  let changed = true;
  while (changed) {
    changed = false;
    edges.forEach((e) => {
      if (up.has(e.child) && !up.has(e.parent)) { up.add(e.parent); changed = true; }
      if (down.has(e.parent) && !down.has(e.child)) { down.add(e.child); changed = true; }
    });
  }
  edges.forEach((e) => {
    if (e.parent === id) nbr.add(e.child);
    if (e.child === id) nbr.add(e.parent);
  });
  return { up, down, nbr };
}

async function traceDrawer(ctx, node) {
  const kindMap = { annotation: 'annotation', model: 'model', device: 'device' };
  const kind = kindMap[node.kind];
  const ident = node.kind === 'device' ? String(node.id).replace(/^device:/, '') : node.id;
  if (!kind) return;
  const body = document.querySelector('#drawer-body');
  if (!body) return;
  body.innerHTML = '<div class="graph-loading">正在沿血缘回溯…</div>';
  try {
    const t = await api.trace(kind, ident);
    const res = t.result || {};
    const summary = res && typeof res === 'object' && res._summary ? res._summary : null;
    body.innerHTML = `<div class="callout info">${icon('info')}<div>从 <b>${esc(node.id)}</b> 出发沿血缘回溯，返回上游数据与下游发布。</div></div>
      ${summary ? `<div class="mini-grid mt-14">${Object.entries(summary).map(([k, v]) => miniStat(k, num(v), '条')).join('')}</div>` : ''}
      <div class="detail-section mt-14"><h4>追溯结果</h4><pre class="code-block">${esc(JSON.stringify(res, null, 2))}</pre></div>`;
  } catch (e) {
    body.innerHTML = `<div class="callout bad">${icon('alert')}<div>${esc(e.message)}</div></div>`;
  }
}

export async function render(root, ctx) {
  const focus = ctx.params.get('focus') || '';
  const depth = Math.max(1, Math.min(4, Number(ctx.params.get('depth')) || 2));
  const graph = await api.graph({ focus, depth, max_nodes: 90 });
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];

  if (!nodes.length) {
    root.innerHTML = pageHead({ title: '血缘图谱', sub: graph.message || '尚未生成血缘库' })
      + card({ body: emptyState({
        iconName: 'git', title: '血缘库为空',
        text: graph.message || '运行一轮闭环后，lineage.sqlite 会记录 ingest → defect → … → device 的完整链路。',
        action: '<button class="btn btn-primary" data-act="run">运行闭环</button>',
      }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const counts = graph.counts || {};
  const legend = KIND_ORDER.filter((k) => counts[k]).map((k) => `
    <span><i style="background:${KIND_COLOR[k]}"></i>${esc(LAYER_LABEL[k] || k)} <b class="mono" style="font-weight:600">${num(counts[k])}</b></span>`).join('');

  root.innerHTML = `
    ${pageHead({
      title: '血缘图谱',
      tag: `${nodes.length} 节点`,
      sub: `<span>${edges.length} 条边</span><span class="sep"></span><span>Q1 数据为何这样 · Q2 模型部署到哪 · Q3 标注为何可信</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="card">
      <div class="graph-toolbar">
        <div class="search-wrap">
          <input class="input" id="node-q" type="search" placeholder="搜索节点：ann- / mv- / dev- / ds- …" style="width:260px" autocomplete="off">
          <div class="search-results" id="node-results" hidden></div>
        </div>
        <label class="tiny muted" style="display:flex;align-items:center;gap:6px">深度
          <select class="input" id="graph-depth" style="height:32px;width:70px">
            ${[1, 2, 3, 4].map((d) => `<option value="${d}" ${d === depth ? 'selected' : ''}>${d}</option>`).join('')}
          </select>
        </label>
        ${graph.focus ? `<button class="btn btn-sm btn-ghost" data-clear>${icon('x')}<span>退出聚焦</span></button>` : ''}
        <span class="grow"></span>
        <span class="tiny muted">悬停高亮直连 · 点击节点查看属性</span>
      </div>
      <div class="legend-swatches mt-14">${legend}</div>
      ${graph.truncated ? `<div class="mt-14">${callout('warn', '节点数超过 90，视图已截断。使用聚焦或减小深度查看局部链路。', 'warn')}</div>` : ''}
      <div class="graph-wrap mt-14" id="graph-wrap" style="max-height:660px">${svgGraph(graph)}</div>
    </div>

    <div class="grid g-3 mt-20" style="margin-top:16px">
      ${card({ title: 'Q1 · 数据集为什么这样', sub: '从 dataset 向上游回溯', body: `<div class="panel-note">点击 dataset 节点，查看它由哪些 annotation 汇聚、锚点来自哪些 defect、最终追溯到哪条 ingest。</div>
        <div class="row mt-14">${nodes.filter((n) => n.kind === 'dataset').slice(0, 3).map((n) => `<button class="chip" data-focus="${esc(n.id)}">${esc(n.id)}</button>`).join('')}</div>` })}
      ${card({ title: 'Q2 · 模型部署到哪', sub: '从 release / device 向上游回溯', body: `<div class="panel-note">点击 release 或 device 节点，可看到 build、model、dataset 与最终设备版本分布。</div>
        <div class="row mt-14">${nodes.filter((n) => n.kind === 'release' || n.kind === 'device').slice(0, 4).map((n) => `<button class="chip" data-focus="${esc(n.id)}">${esc(shortId(n.label, 20))}</button>`).join('')}</div>` })}
      ${card({ title: 'Q3 · 标注为什么可信', sub: 'gate + 一致性 + 人工裁决', body: `<div class="panel-note">点击 annotation 节点查看 agent_consensus、门禁得分与人工介入记录；也可用追溯按钮生成完整证据链。</div>
        <div class="row mt-14">${nodes.filter((n) => n.kind === 'annotation').slice(0, 3).map((n) => `<button class="chip" data-focus="${esc(n.id)}">${esc(shortId(n.id, 18))}</button>`).join('')}</div>` })}
    </div>
  `;

  const wrap = root.querySelector('#graph-wrap');
  const svg = wrap.querySelector('svg');
  const nodeEls = [...svg.querySelectorAll('.gnode')];
  const edgeEls = [...svg.querySelectorAll('.gedge')];

  nodeEls.forEach((el) => {
    const id = el.dataset.id;
    el.addEventListener('mouseenter', () => {
      const { nbr } = relatedSets(nodes, edges, id);
      nodeEls.forEach((n) => { n.style.opacity = n.dataset.id === id || nbr.has(n.dataset.id) ? '1' : '.22'; });
      edgeEls.forEach((e) => {
        e.style.opacity = e.dataset.parent === id || e.dataset.child === id ? '1' : '.08';
      });
    });
    el.addEventListener('mouseleave', () => {
      nodeEls.forEach((n) => { n.style.opacity = '1'; });
      edgeEls.forEach((e) => { e.style.opacity = '1'; });
    });
    el.addEventListener('click', () => {
      const n = nodes.find((x) => x.id === id);
      if (!n) return;
      const parents = edges.filter((e) => e.child === id).map((e) => ({ id: e.parent, kind: nodes.find((x) => x.id === e.parent)?.kind }));
      const children = edges.filter((e) => e.parent === id).map((e) => ({ id: e.child, kind: nodes.find((x) => x.id === e.child)?.kind }));
      const attrs = Object.entries(n.attrs || {});
      ctx.drawer({
        eyebrow: `血缘节点 · ${n.label_kind || LAYER_LABEL[n.kind] || n.kind}`,
        title: n.id,
        body: `${kv([['类型', kindBadge(n.kind)], ['标签', esc(n.label)], ['出度 / 入度', `${children.length} / ${parents.length}`]])}
          <div class="detail-section mt-14"><h4>属性</h4>
            ${attrs.length ? kv(attrs.map(([k, v]) => [k, typeof v === 'object' ? `<span class="mono-sm">${esc(JSON.stringify(v))}</span>` : esc(v)])) : '<div class="muted small">无属性</div>'}
          </div>
          <div class="detail-section"><h4>上游（${parents.length}）</h4>
            ${parents.length ? `<div class="row" style="gap:5px">${parents.map((x) => `<button class="chip" data-focus-node="${esc(x.id)}">${esc(shortId(x.id, 26))}</button>`).join('')}</div>` : '<div class="muted small">无</div>'}
          </div>
          <div class="detail-section"><h4>下游（${children.length}）</h4>
            ${children.length ? `<div class="row" style="gap:5px">${children.map((x) => `<button class="chip" data-focus-node="${esc(x.id)}">${esc(shortId(x.id, 26))}</button>`).join('')}</div>` : '<div class="muted small">无</div>'}
          </div>
          <div class="row">
            <button class="btn btn-sm btn-ghost" data-focus-node="${esc(n.id)}">${icon('target')}<span>以此为焦点</span></button>
            ${['annotation', 'model', 'device'].includes(n.kind) ? `<button class="btn btn-sm btn-primary" data-trace-node="${esc(n.id)}">${icon('git')}<span>全链路追溯</span></button>` : ''}
          </div>`,
      });
      const drawerBody = document.querySelector('#drawer-body');
      drawerBody.querySelectorAll('[data-focus-node]').forEach((b) => b.addEventListener('click', () => {
        ctx.closeDrawer();
        ctx.go('lineage', { focus: b.dataset.focusNode, depth });
      }));
      drawerBody.querySelector('[data-trace-node]')?.addEventListener('click', () => traceDrawer(ctx, n));
    });
  });

  root.querySelectorAll('[data-focus]').forEach((b) => b.addEventListener('click', () => ctx.go('lineage', { focus: b.dataset.focus, depth })));
  root.querySelectorAll('[data-clear]').forEach((b) => b.addEventListener('click', () => ctx.go('lineage', {})));
  root.querySelector('#graph-depth')?.addEventListener('change', (e) => ctx.go('lineage', focus ? { focus, depth: e.target.value } : { depth: e.target.value }));

  const search = root.querySelector('#node-q');
  const results = root.querySelector('#node-results');
  search?.addEventListener('input', debounce(async (e) => {
    const q = e.target.value.trim();
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    try {
      const r = await api.searchNodes(q);
      const items = r.items || [];
      results.innerHTML = items.length ? items.map((x) => `
        <button type="button" class="search-item" data-focus-node="${esc(x.id)}">
          ${kindBadge(x.kind)}<span class="mono-sm truncate">${esc(x.id)}</span>
        </button>`).join('') : '<div class="search-empty">没有匹配节点</div>';
      results.hidden = false;
      results.querySelectorAll('[data-focus-node]').forEach((b) => b.addEventListener('click', () => {
        ctx.go('lineage', { focus: b.dataset.focusNode, depth });
      }));
    } catch {
      results.hidden = true;
    }
  }, 260));
  search?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const id = results.querySelector('[data-focus-node]')?.dataset.focusNode;
      if (id) ctx.go('lineage', { focus: id, depth });
    }
  });

  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
}
