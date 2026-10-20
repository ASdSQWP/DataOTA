/* DataOTA 控制台入口：导航、路由、主题、抽屉、运行任务与全局状态 */

import { api } from './api.js';
import { loadMeta } from './meta.js';
import { esc, icon, ts } from './util.js';

import * as overview from './views/overview.js';
import * as rounds from './views/rounds.js';
import * as datasets from './views/datasets.js';
import * as models from './views/models.js';
import * as fleet from './views/fleet.js';
import * as monitor from './views/monitor.js';
import * as annotations from './views/annotations.js';
import * as lineage from './views/lineage.js';
import * as review from './views/review.js';
import * as audit from './views/audit.js';
import * as system from './views/system.js';
import * as sources from './views/sources.js';
import * as detectors from './views/detectors.js';
import * as world from './views/world.js';

const NAV = [
  {
    group: '飞轮',
    items: [
      { id: 'overview', label: '运行总览', icon: 'grid', view: overview, title: '运行总览' },
      { id: 'rounds', label: '飞轮轮次', icon: 'flow', view: rounds, title: '飞轮轮次' },
      { id: 'sources', label: '数据接入', icon: 'download', view: sources, title: '数据接入', badge: 'inbox' },
      { id: 'monitor', label: '漂移监控', icon: 'activity', view: monitor, title: '漂移监控' },
    ],
  },
  {
    group: '模型',
    items: [
      { id: 'detectors', label: '检测模型', icon: 'target', view: detectors, title: '检测模型' },
      { id: 'world', label: '世界模型', icon: 'layers', view: world, title: '世界模型' },
      { id: 'models', label: '训练与门禁', icon: 'cpu', view: models, title: '模型与门禁' },
    ],
  },
  {
    group: '资产',
    items: [
      { id: 'datasets', label: '数据集', icon: 'database', view: datasets, title: '数据集仓库' },
      { id: 'fleet', label: '设备与 OTA', icon: 'rocket', view: fleet, title: '设备与 OTA' },
    ],
  },
  {
    group: '追溯',
    items: [
      { id: 'annotations', label: '标注样例', icon: 'tag', view: annotations, title: '标注样例' },
      { id: 'lineage', label: '血缘图谱', icon: 'git', view: lineage, title: '血缘图谱' },
      { id: 'review', label: '审核台', icon: 'user', view: review, title: '人工审核台', badge: 'reviews' },
    ],
  },
  {
    group: '系统',
    items: [
      { id: 'audit', label: '审计日志', icon: 'list', view: audit, title: '审计日志' },
      { id: 'system', label: '系统配置', icon: 'settings', view: system, title: '系统与配置' },
    ],
  },
];

const ROUTES = new Map();
NAV.forEach((g) => g.items.forEach((it) => ROUTES.set(it.id, it)));

const $ = (sel) => document.querySelector(sel);
const view = $('#view');
const nav = $('#nav');
const crumb = $('#crumb-page');
const drawer = $('#drawer');
const drawerBody = $('#drawer-body');
const drawerEyebrow = $('#drawer-eyebrow');
const drawerTitle = $('#drawer-title');
const drawerBackdrop = $('#drawer-backdrop');
const runModalEl = $('#run-modal');
const runForm = $('#run-form');
const runLog = $('#run-log');
const runSubmit = $('#run-submit');
const sideStatus = $('#side-status');
const sideVersion = $('.side-version');

const state = { route: 'overview', params: new URLSearchParams(), booted: false, running: false, timer: null, notifiedFinish: null };
let renderSeq = 0;

/* ---------------------------------------------------------------- 主题 */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('dataota.theme', theme); } catch { /* ignore */ }
}
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('dataota.theme'); } catch { /* ignore */ }
  const prefers = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  applyTheme(saved || prefers);
}

/* ---------------------------------------------------------------- 提示 */
function toast(message, tone = '') {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.innerHTML = `<i></i><span>${esc(message)}</span>`;
  box.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    el.style.transition = 'all .2s';
    setTimeout(() => el.remove(), 240);
  }, 3600);
}

/* ---------------------------------------------------------------- 抽屉 */
function openDrawer({ eyebrow = '', title = '详情', body = '' }) {
  drawerEyebrow.textContent = eyebrow;
  drawerTitle.textContent = title;
  drawerBody.innerHTML = body;
  drawer.hidden = false;
  drawerBackdrop.hidden = false;
  document.body.classList.add('locked');
  requestAnimationFrame(() => {
    drawer.classList.add('open');
    drawerBackdrop.classList.add('show');
  });
  drawerBody.scrollTop = 0;
}

function closeDrawer() {
  drawer.classList.remove('open');
  drawerBackdrop.classList.remove('show');
  setTimeout(() => {
    drawer.hidden = true;
    drawerBackdrop.hidden = true;
    if (runModalEl.hidden) document.body.classList.remove('locked');
  }, 220);
}

/* ---------------------------------------------------------------- 运行弹窗 */
function openRunModal() {
  runModalEl.hidden = false;
  document.body.classList.add('locked');
  requestAnimationFrame(() => runModalEl.classList.add('show'));
  pollRun();
}

function closeRunModal() {
  runModalEl.classList.remove('show');
  setTimeout(() => {
    runModalEl.hidden = true;
    if (drawer.hidden) document.body.classList.remove('locked');
  }, 200);
}

function renderRunLog(st) {
  if (!st) return;
  const lines = st.log || [];
  runLog.hidden = !lines.length && !st.error;
  runLog.textContent = lines.join('\n') + (st.error ? `\n[error] ${st.error}` : '');
  runLog.scrollTop = runLog.scrollHeight;
}

function setRunning(running) {
  state.running = running;
  runSubmit.disabled = running;
  runSubmit.innerHTML = running ? '<span class="spinner sm"></span> 运行中…' : `${icon('play')}<span>开始运行</span>`;
  sideStatus.className = running ? 'status-dot busy' : 'status-dot';
  sideStatus.querySelector('span').textContent = running ? '闭环运行中…' : '服务正常';
}

async function pollRun() {
  try {
    const st = await api.runStatus();
    renderRunLog(st);
    setRunning(!!st.running);
    if (st.running) {
      if (!state.timer) state.timer = setInterval(pollRun, 1400);
    } else if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
      if (st.finished_at && state.notifiedFinish !== st.finished_at) {
        state.notifiedFinish = st.finished_at;
        const ok = !st.error;
        toast(ok ? `闭环完成：${st.done}/${st.rounds} 轮` : `闭环失败：${st.error}`, ok ? 'ok' : 'bad');
        await loadMeta(true);
        await refreshBadges();
        renderRoute();
      }
    }
  } catch {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    setRunning(false);
  }
}

runForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (state.running) return;
  const fd = new FormData(runForm);
  const seed = fd.get('seed_size');
  const payload = {
    rounds: Number(fd.get('rounds') || 3),
    seed_size: seed === '' || seed === null ? 60 : Number(seed),
    fresh: fd.get('fresh') === '1',
    include_inbox: fd.get('include_inbox') === '1',
  };
  runLog.hidden = false;
  runLog.textContent = '正在提交任务…';
  runSubmit.disabled = true;
  try {
    const res = await api.startRun(payload);
    toast('闭环任务已启动', 'ok');
    renderRunLog(res.status);
    setRunning(true);
    if (!state.timer) state.timer = setInterval(pollRun, 1400);
  } catch (err) {
    toast(err.message, 'bad');
    runLog.textContent = err.message;
    runSubmit.disabled = false;
    runSubmit.innerHTML = `${icon('play')}<span>开始运行</span>`;
  }
});

/* ---------------------------------------------------------------- 路由 */
function buildNav() {
  nav.innerHTML = NAV.map((g) => `
    <div class="nav-group">
      <div class="nav-group-title">${esc(g.group)}</div>
      ${g.items.map((it) => `
        <a class="nav-item" href="#/${it.id}" data-nav-id="${it.id}">
          ${icon(it.icon)}<span>${esc(it.label)}</span>
          ${it.badge ? `<span class="nav-badge" data-badge="${it.badge}" hidden>0</span>` : ''}
        </a>`).join('')}
    </div>`).join('');
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [id, query] = raw.split('?');
  return { id: id || 'overview', params: new URLSearchParams(query || '') };
}

function setActive(routeId) {
  nav.querySelectorAll('[data-nav-id]').forEach((a) => a.classList.toggle('active', a.dataset.navId === routeId));
}

async function refreshBadges() {
  try {
    const o = await api.overview();
    const setBadge = (key, n) => {
      const el = nav.querySelector(`[data-badge="${key}"]`);
      if (!el) return;
      el.textContent = n > 99 ? '99+' : String(n);
      el.hidden = !n;
    };
    setBadge('reviews', Number(o.kpi?.open_reviews) || 0);
    setBadge('inbox', Number(o.inbox_pending) || 0);
    if (o.workdir) $('#workdir-text').textContent = o.workdir;
    const k = o.kpi || {};
    if (!state.running) sideStatus.querySelector('span').textContent = `服务正常 · ${k.rounds ?? 0} 轮`;
  } catch { /* 首次启动后端可能尚未就绪 */ }
}

async function renderRoute() {
  const seq = ++renderSeq;
  const { id, params } = parseHash();
  const route = ROUTES.get(id) || ROUTES.get('overview');
  state.route = route.id;
  state.params = params;
  setActive(route.id);
  crumb.textContent = route.title;
  document.title = `${route.title} · DataOTA`;
  view.innerHTML = '<div class="boot"><div class="spinner"></div><p>正在读取运行产物…</p></div>';

  const ctx = {
    params,
    go(next, extra) {
      const qs = new URLSearchParams(extra || {}).toString();
      const target = `#/${next}${qs ? `?${qs}` : ''}`;
      if (location.hash === target) { renderRoute(); return; }
      location.hash = target;
    },
    refresh: () => renderRoute(),
    toast,
    drawer: openDrawer,
    closeDrawer,
    runModal: openRunModal,
  };

  try {
    await route.view.render(view, ctx);
    if (seq !== renderSeq) return;
    view.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch (err) {
    if (seq !== renderSeq) return;
    view.innerHTML = `<div class="card"><div class="empty">
      <div class="empty-icon" style="background:var(--red-soft);color:var(--red)">${icon('alert')}</div>
      <h3>页面加载失败</h3>
      <p>${esc(err.message || String(err))}</p>
      <details style="max-width:720px;width:100%"><summary class="tiny muted" style="cursor:pointer">错误堆栈</summary>
        <pre class="code-block" style="text-align:left;margin-top:8px">${esc(err.stack || '')}</pre></details>
      <div class="row" style="justify-content:center">
        <button class="btn" id="err-retry">重试</button>
        <button class="btn btn-primary" id="err-run">运行闭环</button>
      </div></div></div>`;
    $('#err-retry')?.addEventListener('click', renderRoute);
    $('#err-run')?.addEventListener('click', openRunModal);
  }
}

/* ---------------------------------------------------------------- 全局事件 */
window.addEventListener('hashchange', renderRoute);

$('#refresh-btn').addEventListener('click', async () => {
  const btn = $('#refresh-btn');
  btn.disabled = true;
  await loadMeta(true);
  await refreshBadges();
  await renderRoute();
  btn.disabled = false;
  toast('数据已刷新', 'ok');
});

$('#theme-btn').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  renderRoute();
});

$('#run-btn').addEventListener('click', openRunModal);
$('#drawer-close').addEventListener('click', closeDrawer);
drawerBackdrop.addEventListener('click', closeDrawer);

$('#menu-btn').addEventListener('click', () => {
  $('#sidebar').classList.add('open');
  $('#sidebar-backdrop').hidden = false;
  requestAnimationFrame(() => $('#sidebar-backdrop').classList.add('show'));
});
function closeSidebar() {
  $('#sidebar').classList.remove('open');
  $('#sidebar-backdrop').classList.remove('show');
  setTimeout(() => { $('#sidebar-backdrop').hidden = true; }, 200);
}
$('#sidebar-backdrop').addEventListener('click', closeSidebar);
nav.addEventListener('click', (e) => {
  if (e.target.closest('.nav-item') && window.innerWidth <= 980) closeSidebar();
});

runModalEl.querySelectorAll('[data-close-modal]').forEach((b) => b.addEventListener('click', closeRunModal));
runModalEl.addEventListener('click', (e) => { if (e.target === runModalEl) closeRunModal(); });

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!runModalEl.hidden) { closeRunModal(); return; }
  if (!drawer.hidden) { closeDrawer(); return; }
  closeSidebar();
});

/* ---------------------------------------------------------------- 启动 */
async function boot() {
  initTheme();
  buildNav();
  if (!location.hash) history.replaceState(null, '', '#/overview');
  await loadMeta();
  await renderRoute();
  await refreshBadges();
  try {
    const h = await api.health();
    sideStatus.className = 'status-dot';
    sideStatus.querySelector('span').textContent = `服务正常 · v${h.version}`;
    if (sideVersion) sideVersion.textContent = `v${h.version} · Demo`;
  } catch {
    sideStatus.className = 'status-dot offline';
    sideStatus.querySelector('span').textContent = '服务离线';
  }
  await pollRun();
  state.booted = true;
}

boot();
