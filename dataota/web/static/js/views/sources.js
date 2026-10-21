/* 数据接入：多源接入统计（L1 A0–A4）、隔离与去重、设备难例触发、场景质量、接入事件浏览、上传收件箱 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, debounce, defectName, bytes, ts, decisionBadge } from '../util.js';
import { pageHead, kpi, card, emptyState, kv, pager, barRow } from '../ui.js';
import { barChart, donut, palette } from '../charts.js';
import { frameScene, imageStage, wireFrameImages } from '../frames.js';
import { flagName, meta, sourceName } from '../meta.js';

const state = { page: 1, size: 24, source: '', round: '', flag: '', q: '' };
const SOURCES = ['crawl', 'user_upload', 'video_stream', 'audio', 'device'];
const TRIGGER = { low_confidence: '低置信', shadow_disagree: '影子分歧', scene_anomaly: '场景异常', downstream_correction: '下游纠正' };

function gallery(items) {
  if (!items.length) return emptyState({ title: '没有匹配的接入事件', text: '调整筛选条件，或运行一轮闭环 / 上传图像。' });
  return `<div class="gallery wide">${items.map((r) => {
    const q = r.scene_quality;
    return `<div class="gallery-item" data-ing="${esc(r.ingest_id)}" role="button" tabindex="0">
      ${frameScene(r.ingest_id, null, { fit: 'none', night: r.scene_tags?.light === 'night' })}
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="${esc(r.ingest_id)}" loading="lazy">` : ''}
      <div class="gi-top">${badge(sourceName(r.source_type), r.source_type === 'device' ? 'violet' : r.upload_id ? 'warn' : 'info')}
        ${(q?.flags || []).slice(0, 2).map((f) => badge(flagName(f), 'muted')).join('')}</div>
      <div class="gi-body">
        <span>${r.final_decision ? esc(r.final_decision === 'confirmed' ? defectName(r.final_type) : r.final_decision) : '待处理'} · R${esc(r.round ?? '–')}</span>
        <b class="mono">${q ? pct(q.score, 0) : '–'}</b>
      </div>
    </div>`;
  }).join('')}</div>`;
}

function inboxList(inbox) {
  const items = inbox.items || [];
  if (!items.length) return '<div class="panel-note">收件箱为空。上传的图像会在下一次“运行闭环”时作为 A1 接入源进入流水线。</div>';
  return `<div class="list">${items.slice(0, 12).map((x) => `
    <div class="list-row">
      ${x.url ? `<img class="thumb" src="${esc(x.url)}" alt="">` : '<span class="thumb"></span>'}
      <div class="grow">
        <div class="truncate"><b class="mono-sm">${esc(x.upload_id)}</b> ${badge(sourceName(x.source_type), 'info', false)}</div>
        <div class="meta truncate">${esc(x.description || x.filename || '无描述')} · ${bytes(x.bytes)} · ${ts(x.created_at)}</div>
      </div>
      ${x.consumed ? badge('已接入', 'ok') : badge('待接入', 'warn')}
    </div>`).join('')}</div>`;
}

export async function render(root, ctx) {
  const [s, list] = await Promise.all([api.sources(), api.ingest(state)]);
  let last = list;
  const per = s.per_round || [];
  const totals = s.totals || {};
  const inbox = s.inbox || { items: [] };
  const latest = per[per.length - 1] || {};
  const up = meta().upload || {};

  const sourceChart = barChart({
    labels: per.map((r) => `R${r.round}`),
    series: SOURCES.map((k, i) => ({ name: sourceName(k), values: per.map((r) => r.by_source?.[k] || 0), color: palette()[i % 5] })),
    height: 200, format: (v) => num(v),
  });
  const flagsAll = ['blurry', 'underexposed', 'overexposed', 'low_contrast', 'noisy'];
  const qualityChart = barChart({
    labels: per.map((r) => `R${r.round}`),
    series: flagsAll.map((f, i) => ({ name: flagName(f), values: per.map((r) => r.quality_flags?.[f] || 0), color: palette()[i % 5] })),
    height: 170, format: (v) => num(v),
  });
  const srcDonut = donut({ items: Object.entries(totals.by_source || {}).map(([k, v]) => ({ label: sourceName(k), value: v })), centerLabel: '接入事件' });
  const qReasons = Object.entries(s.quarantine_reasons || {}).sort((a, b) => b[1] - a[1]);
  const triggers = Object.entries(s.triggers || {}).sort((a, b) => b[1] - a[1]);
  const trigMax = Math.max(1, ...triggers.map(([, v]) => v));
  const priv = s.privacy || {};

  const opt = (v, label, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? 'selected' : ''}>${esc(label)}</option>`;
  const filterBar = `<div class="filterbar">
    <input type="search" id="src-q" placeholder="搜索接入 ID / 描述 / 设备" value="${esc(state.q)}">
    <select id="src-source">${opt('', '全部来源', state.source)}${SOURCES.map((k) => opt(k, `${sourceName(k)} (${totals.by_source?.[k] || 0})`, state.source)).join('')}</select>
    <select id="src-round">${opt('', '全部轮次', state.round)}${per.map((r) => opt(r.round, `R${r.round} (${r.ingest})`, state.round)).join('')}</select>
    <select id="src-flag">${opt('', '场景质量', state.flag)}${flagsAll.map((f) => opt(f, flagName(f), state.flag)).join('')}</select>
  </div>`;

  root.innerHTML = `
    ${pageHead({
      title: '数据接入',
      tag: `${num(totals.ingest)} 条`,
      sub: `<span>L1：A0 设备代理信号 · A1 多源接入 · A2 网关（脱敏 / 去重 / 场景质量）· A4 ingest_event</span><span class="sep"></span><span>原图离开设备：${priv.allow_raw_media_off_device ? '允许' : '不允许（⚠ 待定 1）'}</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="detectors">${icon('target')}<span>检测模型</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '接入事件', value: num(totals.ingest), foot: `${num(totals.with_image)} 带图像 · ${num(totals.with_audio)} 带音频`, iconName: 'database' })}
      ${kpi({ label: '设备回流', value: num(totals.device), foot: `最近一轮触发 ${num(latest.hard_cases)} · 预算 ${num(s.device_budget)}/轮`, iconName: 'target', tone: 'violet' })}
      ${kpi({ label: '隔离', value: num(totals.quarantined), foot: '版权不明 / 隐私 / Schema 失败', iconName: 'shield', tone: totals.quarantined ? 'amber' : 'green' })}
      ${kpi({ label: '去重丢弃', value: num(per.reduce((a, r) => a + (r.dedup_dropped || 0), 0)), foot: 'A2 内容哈希', iconName: 'filter' })}
      ${kpi({ label: '收件箱', value: num(inbox.pending), foot: `待接入 · 已接入 ${num(inbox.consumed)}`, iconName: 'download', tone: inbox.pending ? 'amber' : '' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({ title: '逐轮接入来源', sub: '第 1 轮为种子 + 上传，之后以设备难例回流为主（A0 → A1）', body: sourceChart })}
        ${card({ title: '场景质量标志（A2）', sub: '场景质量检测器的逐轮标志计数；模糊 / 欠曝等会在 D1 判为困难切片', body: qualityChart })}
      </div>
      <div class="stack">
        ${card({ title: '上传到收件箱', sub: `A1 用户上传源 · JPEG / PNG ≤ ${bytes(up.max_bytes || 1500000)}`,
          body: `<form id="up-form" class="stack" style="gap:10px">
            <label class="dropzone" id="up-drop">
              <input type="file" id="up-file" accept="image/jpeg,image/png" hidden>
              <span id="up-label">${icon('download')} 选择或拖入图像</span>
            </label>
            <div class="form-grid">
              <label class="form-field"><span>来源</span><select name="source_type">${(up.sources || ['user_upload']).map((k) => `<option value="${esc(k)}">${esc(sourceName(k))}</option>`).join('')}</select></label>
              <label class="form-field"><span>光照</span><select name="light"><option value="">未知</option><option value="day">白天</option><option value="night">夜间</option></select></label>
            </div>
            <label class="form-field"><span>描述（进入 B1 用户报告解析）</span><input name="description" maxlength="300" placeholder="例如：右下角有一条裂缝，比较严重"></label>
            <div class="row"><button class="btn btn-primary btn-sm" type="submit" id="up-submit" disabled>${icon('download')}<span>上传</span></button>
              <span class="tiny muted">上传图像没有模拟真值：mock 的 L2 / oracle 会按无缺陷处理，建议 reviewer = "queue" 人工裁决</span></div>
          </form>
          <div class="dotline"></div>${inboxList(inbox)}` })}
        ${card({ title: '来源构成', sub: '全部轮次', body: srcDonut })}
      </div>
    </div>

    <div class="g-2 mt-20">
      ${card({ title: '设备难例触发原因（A0）', sub: '设备拿不到真值，只能用代理信号',
        body: triggers.length ? `<div class="list">${triggers.map(([k, v]) => barRow(TRIGGER[k] || k, v, trigMax, { display: num(v), tone: 'violet' })).join('')}</div>` : '<div class="panel-note">还没有设备回流。</div>' })}
      ${card({ title: '隔离原因', sub: `lake/quarantine · 保留 ${esc(priv.retention_days?.quarantine ?? '–')} 天`,
        body: qReasons.length ? `<div class="list">${qReasons.map(([k, v]) => `<div class="list-row"><span class="grow">${esc(k)}</span><b class="mono-sm">${num(v)}</b></div>`).join('')}</div>` : '<div class="panel-note">没有隔离记录。</div>' })}
    </div>

    <div class="card mt-20">
      <div class="card-head"><div><div class="card-title"><span class="dot"></span>接入事件</div>
        <div class="card-sub">ingest_event 与下游结论；右下角为场景质量综合分</div></div></div>
      ${filterBar}
      <div id="src-body">${gallery(last.items)}</div>
      <div id="src-pager">${pager(last)}</div>
    </div>
  `;

  const body = root.querySelector('#src-body');
  const pagerBox = root.querySelector('#src-pager');
  let busy = false;

  async function load() {
    if (busy) return;
    busy = true;
    body.style.opacity = '.55';
    try {
      last = await api.ingest(state);
      body.innerHTML = gallery(last.items);
      pagerBox.innerHTML = pager(last);
      bind();
    } catch (e) {
      ctx.toast(e.message, 'bad');
    } finally {
      busy = false;
      body.style.opacity = '';
    }
  }

  function openDetail(id) {
    const r = (last.items || []).find((x) => x.ingest_id === id);
    if (!r) return;
    const q = r.scene_quality || {};
    ctx.drawer({
      eyebrow: `${sourceName(r.source_type)} · R${r.round ?? '–'}`,
      title: id,
      body: `${imageStage(r.thumb, id, [], r.image ? r.image.split(/[\\/]/).pop() : '无图像媒体')}
        <div class="detail-section mt-14"><h4>接入字段</h4>${kv([
          ['来源', badge(sourceName(r.source_type), 'info', false)],
          ['媒体', (r.media_kinds || []).map((k) => badge(k, 'muted', false)).join(' ')],
          ['描述', esc(r.description || '–')],
          ['音频事件', (r.audio || []).map((a) => badge(a, 'muted', false)).join(' ') || '–'],
          ['场景标签', Object.entries(r.scene_tags || {}).map(([k, v]) => badge(`${k}=${v}`, 'muted', false)).join(' ') || '–'],
          ['脱敏', r.privacy_flag ? badge('已脱敏', 'ok') : '否'],
          ['设备', r.device_id ? `<span class="mono-sm">${esc(r.device_id)}</span> · ${esc(TRIGGER[r.trigger_reason] || r.trigger_reason || '')}` : '–'],
          ['上传', r.upload_id ? `<span class="mono-sm">${esc(r.upload_id)}</span>` : '–'],
        ])}</div>
        ${q.score !== undefined ? `<div class="detail-section"><h4>场景质量 · ${esc(q.model || '')}</h4>
          ${barRow('综合质量', q.score * 100, 100, { display: pct(q.score, 0), tone: q.score < 0.4 ? 'amber' : 'green' })}
          ${barRow('模糊度', q.blur * 100, 100, { display: pct(q.blur, 0) })}
          ${barRow('噪声', q.noise * 100, 100, { display: pct(q.noise, 0) })}
          ${barRow('对比度', q.contrast * 100, 100, { display: pct(q.contrast, 0), tone: 'teal' })}
          <div class="row mt-14">${badge(`曝光 ${fixed(q.exposure, 2)}`, 'muted', false)}${(q.flags || []).map((f) => badge(flagName(f), 'warn', false)).join('')}</div></div>` : ''}
        <div class="detail-section"><h4>下游结论</h4>${kv([
          ['缺陷事件', r.defect_id ? `<span class="mono-sm">${esc(r.defect_id)}</span>` : '<span class="muted">未生成（隔离 / 挂起）</span>'],
          ['L1 预测', esc(r.pred ? defectName(r.pred) : '判无缺陷')],
          ['最终', r.final_decision ? `${decisionBadge(r.final_decision)} ${esc(defectName(r.final_type))} · ${esc(r.level || '')}` : '–'],
        ])}</div>
        <div class="row"><button class="btn btn-sm" data-go-lineage="${esc(id)}">${icon('git')}<span>查看血缘</span></button></div>`,
    });
    document.querySelector('[data-go-lineage]')?.addEventListener('click', () => { ctx.closeDrawer(); ctx.go('lineage', { focus: id }); });
    wireFrameImages(document);
  }

  function bind() {
    wireFrameImages(body);
    body.querySelectorAll('[data-ing]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.dataset.ing));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.dataset.ing); } });
    });
    pagerBox.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => {
      const p = Number(b.dataset.page);
      if (p >= 1 && p <= last.pages && p !== last.page) { state.page = p; load(); }
    }));
  }

  // ------------------------------------------------------------ 上传
  const form = root.querySelector('#up-form');
  const fileInput = root.querySelector('#up-file');
  const drop = root.querySelector('#up-drop');
  const label = root.querySelector('#up-label');
  const submit = root.querySelector('#up-submit');
  let picked = null;

  function pick(file) {
    if (!file) return;
    if (!/^image\/(jpeg|png)$/.test(file.type)) { ctx.toast('只支持 JPEG / PNG', 'bad'); return; }
    if (file.size > (up.max_bytes || 1500000)) { ctx.toast(`图像超过 ${bytes(up.max_bytes || 1500000)}`, 'bad'); return; }
    picked = file;
    label.textContent = `${file.name} · ${bytes(file.size)}`;
    submit.disabled = false;
  }
  fileInput.addEventListener('change', () => pick(fileInput.files?.[0]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); pick(e.dataTransfer?.files?.[0]); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!picked) return;
    submit.disabled = true;
    try {
      const data = await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result));
        fr.onerror = () => reject(new Error('读取文件失败'));
        fr.readAsDataURL(picked);
      });
      const fd = new FormData(form);
      const light = fd.get('light');
      await api.upload({
        data, filename: picked.name, source_type: fd.get('source_type'),
        description: fd.get('description') || '', scene_tags: light ? { light } : {},
      });
      ctx.toast('已上传到收件箱，下次运行闭环时接入', 'ok');
      ctx.refresh();
    } catch (err) {
      ctx.toast(err.message, 'bad');
      submit.disabled = false;
    }
  });

  const onFilter = () => { state.page = 1; load(); };
  root.querySelector('#src-q').addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); onFilter(); }, 320));
  [['#src-source', 'source'], ['#src-round', 'round'], ['#src-flag', 'flag']].forEach(([sel, key]) => {
    root.querySelector(sel).addEventListener('change', (e) => { state[key] = e.target.value; onFilter(); });
  });
  bind();
  wireFrameImages(root);
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="detectors"]').forEach((b) => b.addEventListener('click', () => ctx.go('detectors')));
}
