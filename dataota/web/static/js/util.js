/* 通用工具：转义、格式化、图标、DOM 小助手 */

import { meta } from './meta.js';

export const esc = (v) => String(v ?? '')
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#39;');

export const num = (v, digits = 0) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '–';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

export const fixed = (v, digits = 2) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : '–';
};

export const pct = (v, digits = 1) => {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : '–';
};

export const pp = (v, digits = 2) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '–';
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(digits)}pp`;
};

export const ts = (sec) => {
  if (!sec) return '–';
  const d = new Date(Number(sec) * 1000);
  if (Number.isNaN(d.getTime())) return '–';
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

export const relTime = (sec) => {
  if (!sec) return '';
  const diff = Date.now() / 1000 - Number(sec);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
};

export const bytes = (n) => {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
};

export const shortId = (id, keep = 22) => {
  const s = String(id ?? '');
  return s.length > keep ? `${s.slice(0, keep - 1)}…` : s;
};

export const uniq = (arr) => [...new Set(arr)];
export const sum = (arr) => arr.reduce((a, b) => a + (Number(b) || 0), 0);
export const last = (arr) => (arr && arr.length ? arr[arr.length - 1] : undefined);

export function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function debounce(fn, ms = 220) {
  let h;
  return (...args) => {
    clearTimeout(h);
    h = setTimeout(() => fn(...args), ms);
  };
}

export function download(filename, text, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

/* ---------------------------------------------------------------- 图标 */
const P = {
  grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
  flow: '<rect x="3" y="4" width="6" height="5" rx="1.4"/><rect x="15" y="4" width="6" height="5" rx="1.4"/><rect x="9" y="15" width="6" height="5" rx="1.4"/><path d="M6 9v3h12V9M12 12v3"/>',
  layers: '<path d="m12 3 9 4.5-9 4.5-9-4.5L12 3Z"/><path d="m3 12.5 9 4.5 9-4.5"/><path d="m3 17 9 4.5 9-4.5"/>',
  database: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  tag: '<path d="M20 13.3 13.3 20a2 2 0 0 1-2.8 0L3.5 13V3.5H13l7 7a2 2 0 0 1 0 2.8Z"/><circle cx="8" cy="8" r="1.4" fill="currentColor" stroke="none"/>',
  git: '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M6 7.2v9.6M8.2 5H13a3 3 0 0 1 3 3v1.8"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  rocket: '<path d="M13.5 4.5c3 1 5.5 3.5 6.5 6.5-2.5 2.5-5.5 4-9 4.5l-2-2c.5-3.5 2-6.5 4.5-9Z"/><path d="m9 13-4 1 2 3 3-2M6.5 17.5 4 20"/><circle cx="15" cy="9" r="1.4"/>',
  activity: '<path d="M3 12h4l2.5-7 4 14 2.5-7h5"/>',
  check: '<path d="m4.5 12.5 5 5 10-11"/>',
  shield: '<path d="M12 3 5 6v6c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6l-7-3Z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M4.5 7.4l2 1.2M17.5 15.4l2 1.2M4.5 16.6l2-1.2M17.5 8.6l2-1.2"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>',
  play: '<path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  alert: '<path d="M12 3.5 21 19H3l9-15.5Z"/><path d="M12 9v4.5M12 16.6v.1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  arrow: '<path d="M5 12h13M13 6l6 6-6 6"/>',
  chevronLeft: '<path d="m14 6-6 6 6 6"/>',
  chevronRight: '<path d="m10 6 6 6-6 6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  download: '<path d="M12 4v11M7.5 11.5 12 16l4.5-4.5M4.5 20h15"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.5l3.5 2"/>',
  box: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  link: '<path d="M10 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/><path d="M14 10.5a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/>',
  filter: '<path d="M3.5 5h17l-6.5 8v6l-4 2v-8L3.5 5Z"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c.8-3.4 3.8-5.2 7.5-5.2s6.7 1.8 7.5 5.2"/>',
  bot: '<rect x="4" y="7.5" width="16" height="11" rx="3"/><path d="M12 4v3.5M9 12v1M15 12v1M8 18.5v2M16 18.5v2"/>',
  warn: '<path d="M12 4 21 20H3L12 4Z"/><path d="M12 10v5M12 17.5v.1"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>',
  package: '<path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7Z"/><path d="m8 6.5 8 4.5v9"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.1M3.5 12h.1M3.5 18h.1"/>',
  trend: '<path d="m3 17 5.5-5.5 3.5 3.5L21 6"/><path d="M15.5 6H21v5.5"/>',
};

export function icon(name, cls = '') {
  const body = P[name] || P.info;
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/* ---------------------------------------------------------------- 语义映射 */
export const TONE = {
  released: 'ok', active: 'ok', passed: 'ok', confirmed: 'ok', success: 'ok',
  draft: 'muted', pending: 'info', shadow: 'violet',
  gate_rejected: 'bad', rejected: 'bad', rolled_back: 'bad', verify_failed: 'bad',
  install_failed: 'bad', switch_failed: 'bad', rolled_back_at_canary: 'bad',
  needs_more_data: 'warn', suspended: 'warn', human_review: 'warn', halted: 'warn',
  drift: 'warn', plateau: 'warn', latency: 'warn', fragmentation: 'warn',
};

export function toneOf(value, fallback = 'muted') {
  const s = String(value ?? '');
  if (TONE[s]) return TONE[s];
  if (s.startsWith('rolled_back') || s.startsWith('halted')) return 'bad';
  return fallback;
}

export function badge(text, tone = 'muted', dot = true) {
  if (text === undefined || text === null || text === '') return '<span class="muted">–</span>';
  return `<span class="badge ${tone}">${dot ? '<i></i>' : ''}${esc(text)}</span>`;
}

export function statusBadge(status) {
  return badge(status, toneOf(status));
}

export function decisionBadge(decision) {
  const map = { confirmed: ['ok', '确认'], rejected: ['bad', '拒绝'], needs_more_data: ['warn', '补数据'] };
  const [tone, label] = map[decision] || ['muted', decision ?? '–'];
  return badge(label, tone);
}

export const DEFECT_LABEL = { crack: '裂缝', scratch: '划痕', dent: '凹陷', rust: '锈蚀', none: '无缺陷' };
/* 类别中文名优先取 /api/meta（configs 中 dataset.class_labels），见 meta.js */
export const defectName = (t) => (t && t !== 'none' && t !== 'None' ? `${meta().class_labels?.[t] || DEFECT_LABEL[t] || t}` : '无缺陷');

export const SEV_TONE = { low: 'muted', medium: 'info', high: 'warn', critical: 'bad' };

export function sevBadge(sev) {
  if (!sev) return '<span class="muted">–</span>';
  return badge(sev, SEV_TONE[sev] || 'muted');
}

export function alertTone(kind) {
  return { drift: 'warn', plateau: 'warn', latency: 'warn', rollback_rate: 'bad', fragmentation: 'bad' }[kind] || 'info';
}

export const LAYER_LABEL = {
  ingest: '接入事件', defect: '缺陷事件', anchor: '锚点', generated: '生成片段', gate: '几何门禁',
  annotation: '标注', dataset: '数据集', model: '模型', build: '构建包', release: '发布单', device: '设备',
};

export function kindBadge(kind) {
  const tone = {
    ingest: 'info', defect: 'violet', anchor: 'teal', generated: 'teal', gate: 'warn',
    annotation: 'ok', dataset: 'info', model: 'violet', build: 'warn', release: 'ok', device: 'muted',
  }[kind] || 'muted';
  return badge(LAYER_LABEL[kind] || kind, tone, false);
}
