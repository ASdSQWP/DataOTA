/* 轻量 SVG 图表：折线 / 柱状 / 环图 / 直方图 / 迷你趋势，无第三方依赖 */

import { esc, fixed } from './util.js';

let uidSeq = 0;
const uid = (p = 'c') => `${p}${++uidSeq}`;

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function palette() {
  return [1, 2, 3, 4, 5].map((i) => cssVar(`--chart-${i}`, '#2f6bff'));
}

const defaultFormat = (v) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('zh-CN')
  : Math.abs(v) >= 100 ? String(Math.round(v)) : fixed(v, 2));

export function niceScale(min, max, count = 4) {
  let lo = Number.isFinite(min) ? min : 0;
  let hi = Number.isFinite(max) ? max : 1;
  if (lo === hi) { hi = lo + 1; }
  const span = hi - lo;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0 || 1));
  const norm = step0 / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step * 1e-6; v += step) ticks.push(+v.toFixed(8));
  return { lo: start, hi: end, step, ticks };
}

function seriesMax(series) {
  let m = -Infinity;
  (series.values || []).forEach((v) => {
    const n = Number(v);
    if (Number.isFinite(n)) m = Math.max(m, n);
  });
  return Number.isFinite(m) ? m : 0;
}

function xTickIdx(n, maxLabels = 7) {
  if (n <= maxLabels) return Array.from({ length: n }, (_, i) => i);
  const step = Math.ceil(n / maxLabels);
  const out = [];
  for (let i = 0; i < n; i += step) out.push(i);
  if (out[out.length - 1] !== n - 1) out.push(n - 1);
  return out;
}

/* ------------------------------------------------------------------ 折线 */
export function lineChart(opts = {}) {
  const {
    series = [], labels = [], height = 190, yMin, yMax, threshold, thresholdLabel,
    format = defaultFormat, area = true, unit = '',
  } = opts;
  if (!series.length) return '<div class="table-empty">暂无可绘制的数据</div>';

  const W = 720, H = height;
  const pad = { l: 46, r: 14, t: 14, b: 26 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const maxVal = Math.max(...series.map((s) => seriesMax(s)), threshold ?? -Infinity, yMax ?? -Infinity);
  const minVal = Math.min(...series.flatMap((s) => (s.values || []).map(Number).filter(Number.isFinite)), threshold ?? Infinity, yMin ?? Infinity);
  const scale = niceScale(yMin ?? Math.min(0, minVal), yMax ?? maxVal);
  const x = (i) => pad.l + (labels.length <= 1 ? iw / 2 : (i / (labels.length - 1)) * iw);
  const y = (v) => pad.t + ih - ((Number(v) - scale.lo) / (scale.hi - scale.lo || 1)) * ih;
  const colors = palette();

  const grid = scale.ticks.map((t) => `
    <line class="grid-line" x1="${pad.l}" y1="${y(t)}" x2="${W - pad.r}" y2="${y(t)}"/>
    <text class="axis-label" x="${pad.l - 7}" y="${y(t) + 3.5}" text-anchor="end">${esc(format(t))}${esc(unit)}</text>`).join('');

  const xticks = xTickIdx(labels.length).map((i) => `
    <text class="axis-label" x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(labels[i] ?? i + 1)}</text>`).join('');

  const defs = [];
  const paths = series.map((s, si) => {
    const color = s.color || colors[si % colors.length];
    const vals = (s.values || []).map((v) => Number(v));
    let d = '';
    let started = false;
    vals.forEach((v, i) => {
      if (!Number.isFinite(v)) { started = false; return; }
      d += `${started ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)}`;
      started = true;
    });
    let areaPath = '';
    if (area && series.length === 1) {
      const gid = uid('grad');
      defs.push(`<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity=".22"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>`);
      const firstIdx = vals.findIndex(Number.isFinite);
      const lastIdx = vals.length - 1 - [...vals].reverse().findIndex(Number.isFinite);
      if (firstIdx >= 0 && lastIdx >= firstIdx) {
        areaPath = `<path d="${d}L${x(lastIdx)},${pad.t + ih}L${x(firstIdx)},${pad.t + ih}Z" fill="url(#${gid})" stroke="none"/>`;
      }
    }
    const dots = vals.map((v, i) => (Number.isFinite(v)
      ? `<circle class="point" cx="${x(i)}" cy="${y(v)}" r="2.8" fill="${color}" stroke="var(--panel)" stroke-width="1.4">
           <title>${esc(s.name || '')} ${esc(labels[i] ?? i + 1)}：${esc(format(v))}${esc(unit)}</title></circle>` : '')).join('');
    return `${areaPath}<path d="${d}" fill="none" stroke="${color}" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round" ${s.dashed ? 'stroke-dasharray="5 4"' : ''}/>${dots}`;
  }).join('');

  const thr = Number.isFinite(Number(threshold)) && threshold !== undefined && threshold !== null ? `
    <line class="threshold" x1="${pad.l}" y1="${y(threshold)}" x2="${W - pad.r}" y2="${y(threshold)}"/>
    <text class="axis-label" x="${W - pad.r}" y="${y(threshold) - 5}" text-anchor="end" fill="var(--red)">${esc(thresholdLabel || `阈值 ${format(threshold)}`)}</text>` : '';

  const legend = series.length > 1 ? `<div class="legend">${series.map((s, i) => `
    <span class="item"><span class="swatch line" style="background:${s.color || colors[i % colors.length]}"></span>${esc(s.name || `序列 ${i + 1}`)}</span>`).join('')}</div>` : '';

  return `<div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="折线图">
      <defs>${defs.join('')}</defs>
      ${grid}${xticks}${thr}${paths}
    </svg>${legend}
  </div>`;
}

/* ------------------------------------------------------------------ 柱状 */
export function barChart(opts = {}) {
  const {
    labels = [], series = [], height = 190, yMax, format = defaultFormat,
    threshold, thresholdLabel, unit = '',
  } = opts;
  if (!series.length) return '<div class="table-empty">暂无可绘制的数据</div>';
  const W = 720, H = height;
  const pad = { l: 46, r: 14, t: 14, b: 30 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const maxVal = Math.max(...series.map((s) => seriesMax(s)), Number(threshold) || 0, Number(yMax) || 0);
  const scale = niceScale(0, yMax ?? maxVal);
  const y = (v) => pad.t + ih - ((Number(v) - scale.lo) / (scale.hi - scale.lo || 1)) * ih;
  const colors = palette();
  const n = Math.max(1, labels.length);
  const band = iw / n;
  const gap = Math.min(14, band * 0.22);
  const bw = Math.max(4, (band - gap) / series.length);

  const grid = scale.ticks.map((t) => `
    <line class="grid-line" x1="${pad.l}" y1="${y(t)}" x2="${W - pad.r}" y2="${y(t)}"/>
    <text class="axis-label" x="${pad.l - 7}" y="${y(t) + 3.5}" text-anchor="end">${esc(format(t))}${esc(unit)}</text>`).join('');

  const bars = labels.map((label, i) => series.map((s, si) => {
    const v = Number(s.values?.[i]);
    const color = s.color || colors[si % colors.length];
    if (!Number.isFinite(v)) return '';
    const top = y(Math.max(0, v));
    const base = y(0);
    const h = Math.max(1, Math.abs(base - top));
    const bx = pad.l + i * band + gap / 2 + si * bw;
    return `<rect x="${bx.toFixed(2)}" y="${Math.min(top, base).toFixed(2)}" width="${Math.max(2, bw - 2).toFixed(2)}"
      height="${h.toFixed(2)}" rx="2.5" fill="${color}" opacity=".9">
      <title>${esc(s.name || '')} ${esc(label)}：${esc(format(v))}${esc(unit)}</title></rect>`;
  }).join('')).join('');

  const xlabels = labels.map((label, i) => `
    <text class="axis-label" x="${(pad.l + i * band + band / 2).toFixed(2)}" y="${H - 10}" text-anchor="middle">${esc(label)}</text>`).join('');

  const thr = Number.isFinite(Number(threshold)) ? `
    <line class="threshold" x1="${pad.l}" y1="${y(threshold)}" x2="${W - pad.r}" y2="${y(threshold)}"/>
    <text class="axis-label" x="${W - pad.r}" y="${y(threshold) - 5}" text-anchor="end" fill="var(--red)">${esc(thresholdLabel || `阈值 ${format(threshold)}`)}</text>` : '';

  const legend = series.length > 1 ? `<div class="legend">${series.map((s, i) => `
    <span class="item"><span class="swatch" style="background:${s.color || colors[i % colors.length]}"></span>${esc(s.name || `序列 ${i + 1}`)}</span>`).join('')}</div>` : '';

  return `<div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="柱状图">
      ${grid}${bars}${xlabels}${thr}
    </svg>${legend}
  </div>`;
}

/* ------------------------------------------------------------------ 环图 */
export function donut(opts = {}) {
  const { items = [], size = 168, thickness = 18, centerLabel = '', centerValue = '' } = opts;
  const total = items.reduce((a, b) => a + (Number(b.value) || 0), 0);
  const fmt = (v) => (Number.isFinite(Number(v)) ? Number(v).toLocaleString('en-US') : v);
  const r = (size - thickness) / 2 - 2;
  const c = 2 * Math.PI * r;
  const colors = palette();
  let offset = 0;
  const segs = items.map((it, i) => {
    const v = Number(it.value) || 0;
    const frac = total ? v / total : 0;
    const len = Math.max(0, frac * c - (frac > 0 ? 1.5 : 0));
    const color = it.color || colors[i % colors.length];
    const seg = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}"
      stroke-width="${thickness}" stroke-linecap="butt"
      stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}"
      stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 ${size / 2} ${size / 2})">
      <title>${esc(it.label)}：${esc(fmt(it.value))}（${(frac * 100).toFixed(1)}%）</title></circle>`;
    offset += frac * c;
    return seg;
  }).join('');

  const legendItems = items.map((it, i) => `
    <span class="item"><span class="swatch" style="background:${it.color || colors[i % colors.length]}"></span>
      ${esc(it.label)} <b class="mono" style="font-weight:600">${esc(fmt(it.value))}</b>
      <span class="muted tiny">${total ? ((Number(it.value) / total) * 100).toFixed(0) : 0}%</span></span>`).join('');

  return `<div class="row" style="gap:18px;align-items:center">
    <div style="position:relative;flex:none">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="max-width:100%">
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--panel-3)" stroke-width="${thickness}"/>
        ${total ? segs : ''}
      </svg>
      <div style="position:absolute;inset:0;display:grid;place-content:center;text-align:center;pointer-events:none">
        <div style="font-size:19px;font-weight:700;letter-spacing:-.02em">${esc(fmt(centerValue || total))}</div>
        <div class="muted tiny">${esc(centerLabel)}</div>
      </div>
    </div>
    <div class="legend" style="flex-direction:column;align-items:flex-start;gap:6px">${legendItems || '<span class="muted tiny">暂无数据</span>'}</div>
  </div>`;
}

/* ------------------------------------------------------------------ 直方图 */
export function histogram(opts = {}) {
  const { values = [], compare = [], height = 130, labels } = opts;
  const colors = palette();
  const bucket = (arr) => {
    const b = new Array(10).fill(0);
    const total = arr.length || 1;
    arr.forEach((v) => {
      const n = Number(v) || 0;
      const i = Math.max(0, Math.min(9, Math.floor(n * 10)));
      b[i] += 1;
    });
    return b.map((x) => x / total);
  };
  const cur = labels ? values : bucket(values);
  const base = compare.length ? (labels ? compare : bucket(compare)) : null;
  const series = [{ name: labels ? '当前' : '当前分布', values: cur, color: colors[0] }];
  if (base) series.push({ name: '基线分布', values: base, color: colors[1] });
  const xs = Array.from({ length: 10 }, (_, i) => `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`);
  return barChart({ labels: xs, series, height, format: (v) => `${(v * 100).toFixed(0)}%` });
}

/* ------------------------------------------------------------------ 迷你趋势 */
export function sparkline(values = [], opts = {}) {
  const { width = 220, height = 30, color, fill = true } = opts;
  const vals = values.map(Number).filter(Number.isFinite);
  if (!vals.length) return '<div style="height:26px"></div>';
  const stroke = color || cssVar('--accent', '#2f6bff');
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const pad = 3;
  const x = (i) => pad + (i / Math.max(1, vals.length - 1)) * (width - pad * 2);
  const y = (v) => height - pad - ((v - min) / span) * (height - pad * 2);
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const gid = uid('sp');
  const area = fill ? `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${stroke}" stop-opacity=".22"/>
      <stop offset="100%" stop-color="${stroke}" stop-opacity="0"/></linearGradient></defs>
    <path d="${d}L${x(vals.length - 1)},${height}L${x(0)},${height}Z" fill="url(#${gid})"/>` : '';
  return `<svg viewBox="0 0 ${width} ${height}" style="width:100%;height:${height}px;display:block" preserveAspectRatio="none">
    ${area}<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}
