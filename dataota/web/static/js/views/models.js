/* 模型版本与 T8 评测门禁：切片对比、构建产物、发布状态 */

import { api } from '../api.js';
import { esc, num, pct, fixed, badge, icon, shortId, ts } from '../util.js';
import { pageHead, kpi, card, emptyState, kv, miniStat, callout, stageBadge, barRow } from '../ui.js';
import { lineChart, barChart, palette } from '../charts.js';

import { sliceName, thr } from '../meta.js';

function avg(metrics) {
  const vals = Object.values(metrics || {}).map(Number).filter(Number.isFinite);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
}

export async function render(root, ctx) {
  const { items } = await api.models();
  if (!items.length) {
    root.innerHTML = pageHead({ title: '模型与门禁', sub: '还没有训练产物' })
      + card({ body: emptyState({ iconName: 'cpu', title: '暂无模型版本', text: 'T7 训练完成后写入 mv-001，并在 T8 生成分切片评测报告。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const versions = items.map((m) => m.model_version);
  const pick = ctx.params.get('mv') || versions[versions.length - 1];
  const m = items.find((x) => x.model_version === pick) || items[items.length - 1];
  const ev = m.eval;
  const metrics = m.metrics || {};
  const slices = ev?.slices || {};
  const sliceKeys = Object.keys(slices).length ? Object.keys(slices) : Object.keys(metrics);

  const rows = items.slice().reverse().map((x) => {
    const active = x.model_version === m.model_version;
    const e = x.eval;
    return `<tr class="clickable ${active ? 'selected' : ''}" data-mv="${esc(x.model_version)}">
      <td><span class="mono-sm" style="font-weight:600">${esc(x.model_version)}</span></td>
      <td class="muted">${esc(x.base_model || '–')}</td>
      <td class="muted">${esc(x.dataset_version || '–')}</td>
      <td class="num">${pct(x.metrics?.day, 1)}</td>
      <td class="num">${pct(x.metrics?.night, 1)}</td>
      <td class="num">${pct(x.metrics?.far, 1)}</td>
      <td class="num">${pct(x.metrics?.occluded, 1)}</td>
      <td>${e ? (e.passed ? badge('通过', 'ok') : badge('回归', 'bad')) : badge('未评估', 'muted')}</td>
      <td>${x.release ? stageBadge(x.release.status) : '<span class="muted">–</span>'}</td>
    </tr>`;
  }).join('');

  const evalTable = sliceKeys.length ? sliceKeys.map((k) => {
    const s = slices[k] || {};
    const cand = Number(s.candidate ?? metrics[k]) || 0;
    const base = Number(s.baseline) || 0;
    const diff = cand - base;
    const reg = (ev?.regressions || []).includes(k);
    return `<tr>
      <td>${badge(sliceName(k), reg ? 'bad' : 'muted', false)}</td>
      <td class="num">${pct(cand, 2)}</td>
      <td class="num">${pct(base, 2)}</td>
      <td class="num" style="color:${diff < -0.01 ? 'var(--red)' : diff > 0 ? 'var(--green)' : 'var(--text-2)'}">${diff >= 0 ? '+' : ''}${(diff * 100).toFixed(2)}pp</td>
      <td>${reg ? badge('退化', 'bad') : diff >= 0 ? badge('提升', 'ok') : badge('持平', 'muted')}</td>
    </tr>`;
  }).join('') : '';

  const evalChart = barChart({
    labels: sliceKeys.map(sliceName),
    series: [
      { name: '候选模型', values: sliceKeys.map((k) => ((slices[k]?.candidate ?? metrics[k]) || 0) * 100), color: palette()[0] },
      { name: '基线模型', values: sliceKeys.map((k) => (slices[k]?.baseline || 0) * 100), color: palette()[3] },
    ],
    height: 195,
    format: (v) => `${v.toFixed(0)}%`,
    threshold: 60, thresholdLabel: '代理指标参考线',
  });

  const trendChart = lineChart({
    labels: items.map((x) => x.model_version),
    series: ['day', 'night', 'far', 'occluded'].filter((k) => items.some((x) => x.metrics?.[k] !== undefined)).map((k, i) => ({
      name: sliceName(k),
      values: items.map((x) => (x.metrics?.[k] !== undefined ? x.metrics[k] * 100 : null)),
      color: palette()[i],
    })),
    height: 205,
    format: (v) => `${v.toFixed(0)}%`,
  });

  const builds = (m.builds || []).map((b) => `
    <tr>
      <td class="id">${esc(b.build_id)}</td>
      <td>${badge(b.target || '–', 'info', false)}</td>
      <td class="muted">${esc(b.runtime || '–')}</td>
      <td class="id" title="${esc(b.checksum || '')}">${esc(shortId(b.checksum || '–', 18))}</td>
    </tr>`).join('');

  const config = m.train_config || {};
  const avgScore = avg(metrics);

  root.innerHTML = `
    ${pageHead({
      title: '模型与门禁',
      tag: m.model_version,
      sub: `<span>${items.length} 个模型版本</span><span class="sep"></span><span>基线 ${esc(ev?.baseline_version || m.base_model || '–')}</span><span class="sep"></span><span>T8 ${ev ? (ev.passed ? '已通过' : '未通过') : '未评估'}</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="graph">${icon('git')}<span>模型血缘</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '平均代理指标', value: pct(avgScore, 2), foot: '四切片算术平均', iconName: 'target', tone: 'violet' })}
      ${kpi({ label: '白天 day', value: pct(metrics.day, 2), foot: '主场景，回归判定关键切片', iconName: 'activity' })}
      ${kpi({ label: '夜间 night', value: pct(metrics.night, 2), foot: '低照度长尾', iconName: 'activity', tone: 'teal' })}
      ${kpi({ label: '远距 far', value: pct(metrics.far, 2), foot: '小目标 / 远距离', iconName: 'target' })}
      ${kpi({ label: '遮挡 occluded', value: pct(metrics.occluded, 2), foot: '部分可见缺陷', iconName: 'shield', tone: 'amber' })}
      ${kpi({ label: '训练成本', value: `${fixed(m.cost?.gpu_hours, 1)}<span class="tiny muted"> h</span>`, foot: `数据集 ${esc(m.dataset_version || '–')}`, iconName: 'cpu' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({
          title: 'T8 分切片评测',
          sub: `${esc(m.model_version)} vs ${esc(ev?.baseline_version || '基线')} · 评估集 ${esc(ev?.eval_set || '–')}`,
          actions: ev ? (ev.passed ? badge('门禁通过', 'ok') : badge('拒绝发布', 'bad')) : badge('未评估', 'muted'),
          body: `${evalChart}
            ${evalTable ? `<div class="table-wrap mt-14"><table class="data compact"><thead><tr>
              <th>切片</th><th class="num">候选</th><th class="num">基线</th><th class="num">Δ</th><th>判定</th>
            </tr></thead><tbody>${evalTable}</tbody></table></div>` : ''}
            <div class="row tiny muted mt-14" style="gap:12px">
              <span>回归阈值 <b>${pct(ev?.regression_threshold ?? thr('regression'), 0)}</b></span>
              <span>污染检查 ${ev ? (ev.contamination_ok ? badge('通过', 'ok') : badge('异常', 'bad')) : '–'}</span>
              ${ev?.regressions?.length ? `<span>${badge(`回归切片：${ev.regressions.map(sliceName).join(' / ')}`, 'bad')}</span>` : ''}
            </div>`,
        })}

        ${card({
          title: '模型演进',
          sub: '代理指标随版本变化，观察是否陷入平台期',
          body: trendChart,
        })}

        ${card({
          title: '全部模型',
          sub: '点击行切换上方详情',
          pad0: true,
          body: `<div class="table-wrap" style="border:0;border-radius:0"><table class="data">
            <thead><tr><th>模型</th><th>基线</th><th>数据集</th><th class="num">day</th><th class="num">night</th><th class="num">far</th><th class="num">occ</th><th>T8</th><th>发布</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`,
        })}
      </div>

      <div class="stack">
        ${card({
          title: '门禁结论',
          sub: 'T8 不通过则不会进入 T9/T10',
          body: `${ev ? callout(ev.passed ? 'ok' : 'bad', ev.passed
            ? `所有切片相对基线退化均小于 <b>${pct(ev.regression_threshold ?? thr('regression'), 0)}</b>，允许生成发布计划。`
            : `切片 <b>${esc((ev.regressions || []).join(', ') || '未知')}</b> 退化超过阈值，模型被拒绝发布并进入 N2 失败分析。`, ev.passed ? 'shield' : 'alert')
            : '<div class="panel-note">该模型还没有 T8 报告。</div>'}
            ${m.release ? `<div class="mt-14">${kv([
              ['发布单', `<span class="mono-sm">${esc(m.release.release_id)}</span>`],
              ['状态', stageBadge(m.release.status)],
              ['成功 / 失败', `${num(m.release.success)} / ${num(m.release.failed)}`],
            ])}</div>` : ''}`,
        })}

        ${card({
          title: '训练配置',
          sub: 'T7 增量训练参数',
          body: `<div class="mini-grid">
            ${miniStat('训练方式', esc(config.recipe || '–'), 'incremental / full')}
            ${miniStat('难例权重', fixed(config.hard_case_weight, 1), 'train.hard_case_weight')}
            ${miniStat('合成比例上限', pct(config.synthetic_ratio_cap, 0), 'train.synthetic_ratio_cap')}
          </div>
          <div class="dotline"></div>
          ${kv([
            ['基础模型', `<span class="mono-sm">${esc(m.base_model || '–')}</span>`],
            ['训练数据集', `<button class="link-btn mono-sm" data-act="dataset">${esc(m.dataset_version || '–')}</button>`],
            ['模型 ID', `<span class="mono-sm">${esc(m.model_version)}</span>`],
          ])}`,
        })}

        ${card({
          title: '构建产物',
          sub: 'T9 量化编译 / manifest / 签名',
          pad0: true,
          body: builds ? `<div class="table-wrap" style="border:0;border-radius:0"><table class="data compact">
            <thead><tr><th>Build</th><th>硬件</th><th>Runtime</th><th>Checksum</th></tr></thead>
            <tbody>${builds}</tbody></table></div>` : emptyState({ title: '暂无构建产物', text: 'T9 会为每个 hardware.targets 生成独立包。' }),
        })}
      </div>
    </div>
  `;

  root.querySelectorAll('[data-mv]').forEach((tr) => tr.addEventListener('click', () => ctx.go('models', { mv: tr.dataset.mv })));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="graph"]').forEach((b) => b.addEventListener('click', () => ctx.go('lineage', { focus: m.model_version })));
  root.querySelectorAll('[data-act="dataset"]').forEach((b) => b.addEventListener('click', () => ctx.go('datasets', { v: m.dataset_version })));
}
