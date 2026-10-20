/* 后端 JSON API 封装：统一错误消息，便于页面直接展示 */

async function request(path, options = {}) {
  const opts = { ...options, headers: { Accept: 'application/json', ...(options.headers || {}) } };
  if (opts.body && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`响应不是 JSON（HTTP ${res.status}）`);
  }
  if (!res.ok) throw new Error(data?.error || `请求失败（HTTP ${res.status}）`);
  return data;
}

const qs = (params) => {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  overview: () => request('/api/overview'),
  rounds: () => request('/api/rounds'),
  round: (n) => request(`/api/rounds/${n}`),
  datasets: () => request('/api/datasets'),
  dataset: (v) => request(`/api/datasets/${encodeURIComponent(v)}`),
  models: () => request('/api/models'),
  releases: () => request('/api/releases'),
  fleet: () => request('/api/fleet'),
  monitor: () => request('/api/monitor'),
  telemetry: (round) => request(`/api/telemetry${qs({ round })}`),
  annotations: (params) => request(`/api/annotations${qs(params)}`),
  annotation: (id) => request(`/api/annotations/${encodeURIComponent(id)}`),
  audit: (params) => request(`/api/audit${qs(params)}`),
  auditCsvUrl: (params) => `/api/audit.csv${qs(params)}`,
  review: (resolved = false) => request(`/api/review${qs({ resolved: resolved ? 1 : '' })}`),
  submitReview: (id, payload) => request(`/api/review/${encodeURIComponent(id)}`, { method: 'POST', body: payload }),
  trace: (kind, id) => request(`/api/trace${qs({ kind, id })}`),
  graph: (params) => request(`/api/lineage/graph${qs(params)}`),
  searchNodes: (q) => request(`/api/lineage/search${qs({ q, limit: 30 })}`),
  system: () => request('/api/system'),
  meta: () => request('/api/meta'),
  zoo: () => request('/api/zoo'),
  world: () => request('/api/world'),
  clips: (params) => request(`/api/world/clips${qs(params)}`),
  clip: (id) => request(`/api/world/clips/${encodeURIComponent(id)}`),
  detectors: () => request('/api/detectors'),
  detections: (params) => request(`/api/detections${qs(params)}`),
  sources: () => request('/api/sources'),
  ingest: (params) => request(`/api/ingest${qs(params)}`),
  inbox: () => request('/api/inbox'),
  upload: (payload) => request('/api/inbox', { method: 'POST', body: payload }),
  health: () => request('/api/health'),
  runStatus: () => request('/api/run/status'),
  startRun: (payload) => request('/api/run', { method: 'POST', body: payload }),
};
