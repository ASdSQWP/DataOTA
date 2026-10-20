/* 配置元数据：阈值、类别 / 切片中文名、视角列表都来自 /api/meta（即 configs/*.toml），
   前端不再硬编码。加载失败时退回下面的默认值，页面仍可渲染。 */

import { api } from './api.js';

const DEFAULTS = {
  thresholds: {
    gate_min_pass_rate: 0.6, gate_pass_score: 0.7, gate_metric_floor: 0.5, psi: 0.2, drift_method: 'psi',
    drift: { psi: 0.2 }, latency_p95_ms: 60, fragmentation: 0.3, regression: 0.01, rollback_fail_rate: 0.25,
    shadow_disagree: 0.1, d1_min_confidence: 0.85, d2_min_confidence: 0.8, l1_score: 0.5, consensus: 0.67,
  },
  classes: ['crack', 'scratch', 'dent', 'rust'],
  class_labels: { crack: '裂缝', scratch: '划痕', dent: '凹陷', rust: '锈蚀' },
  slices: ['day', 'night', 'far', 'occluded'],
  slice_labels: { day: '白天', night: '夜间', far: '远距', occluded: '遮挡' },
  views: [],
  view_hardness: {},
  upload: { max_bytes: 1500000, sources: ['user_upload', 'crawl', 'device'], types: ['jpg', 'png'] },
};

let cache = null;
let pending = null;

export async function loadMeta(force = false) {
  if (cache && !force) return cache;
  if (!pending || force) {
    pending = api.meta()
      .then((m) => {
        cache = { ...DEFAULTS, ...m, thresholds: { ...DEFAULTS.thresholds, ...(m.thresholds || {}) } };
        return cache;
      })
      .catch(() => cache || DEFAULTS)
      .finally(() => { pending = null; });
  }
  return pending;
}

export const meta = () => cache || DEFAULTS;

/** 阈值：thr('psi') → 0.2 */
export const thr = (key, fallback) => {
  const v = meta().thresholds?.[key];
  return v === undefined || v === null ? (fallback ?? DEFAULTS.thresholds[key]) : v;
};

export const sliceName = (s) => meta().slice_labels?.[s] || s;
export const classLabel = (c) => meta().class_labels?.[c] || DEFAULTS.class_labels[c] || c;

export const VIEW_LABEL = {
  'yaw-30': '左偏 30°', 'yaw+30': '右偏 30°', 'yaw-60': '左偏 60°', 'yaw+60': '右偏 60°',
  'pitch-15': '俯视 15°', 'pitch+15': '仰视 15°', far: '拉远', near: '拉近',
  night: '夜间光照', occluded: '遮挡', 'orbit-360': '环绕',
};
export const viewName = (v) => (v ? `${VIEW_LABEL[v] || v}` : '–');

export const SOURCE_LABEL = {
  crawl: '爬取', user_upload: '用户上传', video_stream: '视频流', audio: '音频', device: '设备回流',
};
export const sourceName = (s) => SOURCE_LABEL[s] || s || '–';

export const FLAG_LABEL = {
  blurry: '模糊', underexposed: '欠曝', overexposed: '过曝', low_contrast: '低对比', noisy: '噪声',
};
export const flagName = (f) => FLAG_LABEL[f] || f;

export const STATUS_LABEL = {
  ready: ['可用', 'ok'], missing_deps: ['缺依赖', 'warn'], not_configured: ['未配置', 'muted'],
  unknown_impl: ['未注册', 'bad'],
};
