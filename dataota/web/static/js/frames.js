/* 帧示意：装了 numpy + Pillow 时 Demo 会渲染真实 JPEG，<img> 直接显示真实像素；
   旧产物 / 缺依赖时 jpg 是占位文件，前端按 ID 确定性合成一张示意图兜底，并叠加真实 bbox。 */

import { esc } from './util.js';

const W = 160;
const H = 90;

function seeded(seed) {
  let h = 2166136261;
  const s = String(seed || 'frame');
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  let t = h >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let x = t;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

function keyOf(seed) {
  return String(seed || 'frame').replace(/[^a-zA-Z0-9]/g, '').slice(-10) || 'f';
}

/**
 * @param {string} seed      标注 / 片段 ID，决定纹理布局
 * @param {object} [box]     归一化 bbox {x,y,w,h}
 * @param {object} [opts]    { night, tone, boxStroke, note }
 * @returns {string} svg 字符串
 */
export function frameScene(seed, box = null, opts = {}) {
  const rand = seeded(seed);
  const k = keyOf(seed);
  const night = Boolean(opts.night);
  const sky = night ? ['#101a28', '#25344a'] : ['#c8d3dc', '#eef2f6'];
  const ground = night ? ['#1b2532', '#0e1620'] : ['#79838f', '#4c5662'];
  const road = night ? '#161f2b' : '#555f6b';
  const mark = night ? 'rgba(226,232,240,.32)' : 'rgba(250,250,250,.62)';
  const stroke = opts.boxStroke || '#4f8cff';

  const buildings = Array.from({ length: 7 }, () => {
    const w = 7 + rand() * 15;
    const x = rand() * W;
    const hgt = 3 + rand() * 14;
    return `<rect x="${x.toFixed(1)}" y="${(44 - hgt).toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}"
      fill="${night ? '#0b1521' : '#8d9aa6'}" opacity="${(0.14 + rand() * 0.16).toFixed(2)}"/>`;
  }).join('');

  const patches = Array.from({ length: 12 }, () => {
    const cx = rand() * W;
    const cy = 46 + rand() * 42;
    const rx = 1.6 + rand() * 8;
    const ry = (0.5 + rand() * 2.4) * (0.5 + (cy - 44) / 90);
    return `<ellipse cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}"
      fill="${rand() > 0.45 ? '#111827' : '#e5e7eb'}" opacity="${(0.05 + rand() * 0.08).toFixed(2)}"/>`;
  }).join('');

  let boxEl = '';
  const bw = Number(box?.w) || 0;
  const bh = Number(box?.h) || 0;
  if (bw > 0 && bh > 0) {
    const x = (Number(box.x) || 0) * W;
    const y = (Number(box.y) || 0) * H;
    const w = bw * W;
    const h = bh * H;
    boxEl = `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${w.toFixed(2)}" height="${h.toFixed(2)}"
        fill="${stroke}14" stroke="${stroke}" stroke-width="1.1" vector-effect="non-scaling-stroke"/>
      <rect x="${(x - 1.6).toFixed(2)}" y="${(y - 1.6).toFixed(2)}" width="${(w + 3.2).toFixed(2)}" height="${(h + 3.2).toFixed(2)}"
        fill="none" stroke="${stroke}" stroke-width=".5" opacity=".45" vector-effect="non-scaling-stroke" stroke-dasharray="2 2"/>`;
  }

  const fit = opts.fit === 'none' ? 'none' : 'xMidYMid slice';
  return `<svg class="frame-art" viewBox="0 0 ${W} ${H}" preserveAspectRatio="${fit}" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="fk-${k}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${sky[0]}"/><stop offset="1" stop-color="${sky[1]}"/>
    </linearGradient>
    <linearGradient id="fg-${k}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${ground[0]}"/><stop offset="1" stop-color="${ground[1]}"/>
    </linearGradient>
    <radialGradient id="fv-${k}" cx=".5" cy=".45" r=".72">
      <stop offset=".55" stop-color="#000" stop-opacity="0"/>
      <stop offset="1" stop-color="#000" stop-opacity="${night ? '.5' : '.26'}"/>
    </radialGradient>
  </defs>
  <rect width="${W}" height="${H}" fill="url(#fk-${k})"/>
  ${buildings}
  <rect y="44" width="${W}" height="${H - 44}" fill="url(#fg-${k})"/>
  <path d="M68,44 L94,44 L152,${H} L4,${H} Z" fill="${road}"/>
  <path d="M68,44 L71,44 L20,${H} L8,${H} Z" fill="${mark}" opacity=".38"/>
  <path d="M92,44 L94,44 L150,${H} L138,${H} Z" fill="${mark}" opacity=".38"/>
  <path d="M81,45 L63,${H}" stroke="${mark}" stroke-width="1.5" stroke-dasharray="4 7" opacity=".7" fill="none"/>
  ${patches}
  ${night ? `<path d="M74,${H} L86,${H} L96,56 L74,56 Z" fill="#fef3c7" opacity=".07"/>` : ''}
  <rect width="${W}" height="${H}" fill="url(#fv-${k})"/>
  ${boxEl}
</svg>`;
}

/** 给容器内所有占位帧图片绑定失败回退：隐藏 <img>，露出下面合成的场景。 */
export function wireFrameImages(scope = document) {
  scope.querySelectorAll('.bbox-stage img, .gallery-item img, .film-frame img').forEach((img) => {
    if (img.dataset.frameWired === '1') return;
    img.dataset.frameWired = '1';
    const fallback = () => {
      img.style.display = 'none';
      const note = img.parentElement?.querySelector('.stage-note');
      if (note && !note.dataset.fallback) {
        note.dataset.fallback = '1';
        note.textContent = `${note.dataset.id || ''} 占位帧 · 按标注数据合成`.trim();
      }
    };
    img.addEventListener('error', fallback);
    if (img.complete && img.naturalWidth === 0) fallback();
  });
}

/** 归一化 bbox 叠加层：boxes = [{ box: {x,y,w,h}, color, dash, label }] */
export function boxOverlay(boxes = []) {
  const rects = boxes.filter((b) => b && b.box && Number(b.box.w) > 0 && Number(b.box.h) > 0).map(({ box, color = '#2f6bff', dash = false, width = 1.5 }) => `
    <rect x="${(Number(box.x) || 0) * 100}" y="${(Number(box.y) || 0) * 100}" width="${Number(box.w) * 100}" height="${Number(box.h) * 100}"
      fill="${color}16" stroke="${color}" stroke-width="${width}" vector-effect="non-scaling-stroke" ${dash ? 'stroke-dasharray="4 3"' : ''}/>`).join('');
  return `<svg class="bbox-overlay" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${rects}</svg>`;
}

/** 16:9 图像舞台：真实图像 + 叠加框；图像不可读时露出合成示意。 */
export function imageStage(url, seed, boxes = [], note = '') {
  return `<div class="bbox-stage">
    ${frameScene(seed, null)}
    ${url ? `<img class="bbox-img" src="${esc(url)}" alt="${esc(seed)}" loading="lazy">` : ''}
    ${boxOverlay(boxes)}
    ${note ? `<span class="stage-note" data-id="${esc(seed)}">${esc(note)}</span>` : ''}
  </div>`;
}
