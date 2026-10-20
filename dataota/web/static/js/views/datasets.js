/* 数据集仓库：版本演进、类别分布、导出格式与样本预览 */

import { api } from '../api.js';
import {
  esc, num, pct, badge, icon,
  defectName,
} from '../util.js';
import { pageHead, kpi, card, emptyState, kv, miniStat, callout } from '../ui.js';
import { barChart, donut, palette } from '../charts.js';

const FMT_LABEL = { coco: 'COCO', yolo: 'YOLO', sharegpt: 'ShareGPT', messages: 'messages' };
const CLASS_COLOR = { crack: 'var(--red)', scratch: 'var(--amber)', dent: 'var(--violet)', rust: 'var(--teal)' };

export async function render(root, ctx) {
  const { items } = await api.datasets();
  if (!items.length) {
    root.innerHTML = pageHead({ title: '数据集仓库', sub: '还没有导出的数据集版本' })
      + card({ body: emptyState({ iconName: 'database', title: '暂无数据集', text: '跑完一轮闭环后，P6 会导出 ds-v001 并写入类别直方图与血缘。', action: '<button class="btn btn-primary" data-act="run">运行闭环</button>' }) });
    root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
    return;
  }

  const versions = items.map((x) => x.dataset_version);
  const pick = ctx.params.get('v') || versions[versions.length - 1];
  const detail = await api.dataset(pick);
  const total = detail.sample_count || detail.stats?.total || 0;
  const synthetic = detail.stats?.synthetic || 0;
  const real = Math.max(0, total - synthetic);
  const classHist = detail.class_hist || detail.stats?.class_hist || {};
  const classes = Object.keys(classHist);
  const exportCfg = detail.export_config || {};
  const formats = detail.formats || [];

  const list = items.slice().reverse().map((d) => {
    const active = d.dataset_version === pick;
    const classSum = Object.values(d.class_hist || {}).reduce((a, b) => a + (Number(b) || 0), 0);
    return `<button type="button" class="list-row" data-version="${esc(d.dataset_version)}"
      style="text-align:left;cursor:pointer;width:100%;${active ? 'border-color:var(--accent);background:var(--accent-soft)' : ''}">
      <span class="badge ${active ? 'info' : 'muted'}" style="min-width:70px;justify-content:center">${esc(d.dataset_version)}</span>
      <div class="grow">
        <div style="font-weight:600">${num(d.sample_count)} 条样本</div>
        <div class="meta">${(d.formats || []).map((f) => FMT_LABEL[f] || f).join(' · ')}${d.parent ? ` · 父版本 ${esc(d.parent)}` : ''}</div>
      </div>
      <span class="muted tiny">${num(classSum)} 标注</span>
    </button>`;
  }).join('');

  const splitDonut = donut({
    size: 150,
    items: [
      { label: '训练集', value: detail.stats?.train || 0, color: palette()[0] },
      { label: '真实留出', value: detail.stats?.holdout || 0, color: palette()[2] },
    ],
    centerLabel: '样本',
    centerValue: num(total),
  });

  const synthDonut = donut({
    size: 150,
    items: [
      { label: '合成样本', value: synthetic, color: palette()[3] },
      { label: '真实样本', value: real, color: palette()[1] },
    ],
    centerLabel: '合成占比',
    centerValue: pct(total ? synthetic / total : 0, 0),
  });

  const classChart = barChart({
    labels: classes.length ? classes.map(defectName) : ['无'],
    series: [{ name: '样本数', values: classes.length ? classes.map((c) => classHist[c]) : [0] }],
    height: 180,
    format: (v) => num(v),
  });

  const exportRows = (detail.exported_files || []).map((e) => `
    <tr>
      <td><span class="badge ${e.split === 'holdout' ? 'warn' : 'info'}">${e.split === 'holdout' ? '真实留出' : '训练'}</span></td>
      <td>${badge(FMT_LABEL[e.format] || e.format, 'muted', false)}</td>
      <td class="num">${num(e.count)}</td>
      <td class="id">${e.files?.length ? e.files.slice(0, 3).map((f) => esc(f)).join('<br>') : '<span class="muted">–</span>'}${e.files?.length > 3 ? `<br><span class="muted">… +${e.files.length - 3}</span>` : ''}</td>
    </tr>`).join('');

  const sampleIds = detail.sample_preview || [];

  root.innerHTML = `
    ${pageHead({
      title: '数据集仓库',
      tag: pick,
      sub: `<span>共 ${items.length} 个版本</span><span class="sep"></span><span>Schema ${esc(detail.schema_version || '–')} · Exporter ${esc(detail.exporter_version || '–')}</span><span class="sep"></span><span>导出 ${formats.length} 种格式</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="graph">${icon('git')}<span>查看血缘</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="g-1-2">
      <div class="stack">
        ${card({ title: '版本演进', sub: '按 dataset_version 递增，点击切换', body: `<div class="list">${list}</div>` })}
        ${card({
          title: '导出配置',
          sub: 'P6 双格式导出参数',
          body: `<div class="mini-grid">
            ${miniStat('图像尺寸', `${num(exportCfg.image_width || '–')} × ${num(exportCfg.image_height || '–')}`, '训练输入分辨率')}
            ${miniStat('类别数', num((exportCfg.classes || []).length), (exportCfg.classes || []).map(defectName).join(' · '))}
            ${miniStat('真实留出比', pct(real ? (detail.stats?.holdout || 0) / real : (exportCfg.real_holdout_ratio ?? 0), 1), `占全量 ${pct(total ? (detail.stats?.holdout || 0) / total : 0, 1)} · T8 评测口径`)}
          </div>
          <div class="dotline"></div>
          <div class="row">${formats.map((f) => badge(FMT_LABEL[f] || f, 'info', false)).join('')}</div>`,
        })}
      </div>

      <div class="stack">
        <div class="grid g-kpi">
          ${kpi({ label: '样本总量', value: num(total), foot: `训练 ${num(detail.stats?.train)} · 留出 ${num(detail.stats?.holdout)}`, iconName: 'database' })}
          ${kpi({ label: '合成样本', value: pct(total ? synthetic / total : 0, 1), foot: `${num(synthetic)} 条 · 来自 P3/P4`, iconName: 'spark', tone: 'violet' })}
          ${kpi({ label: '人工仲裁', value: num(detail.stats?.human_arbitrated), foot: '进入过 B6 / P5 人工', iconName: 'user', tone: 'amber' })}
          ${kpi({ label: '血缘标注', value: num(detail.lineage_annotation_count), foot: 'dataset ← annotation 边', iconName: 'link', tone: 'teal' })}
        </div>

        <div class="g-2">
          ${card({ title: '训练 / 留出划分', sub: '真实留出集不参与训练', body: splitDonut })}
          ${card({ title: '真实 vs 合成', sub: '合成比例受 train.synthetic_ratio_cap 限制', body: synthDonut })}
        </div>

        ${card({
          title: '类别分布',
          sub: `共 ${classes.length} 类 · ${num(total)} 条标注`,
          body: classChart,
        })}

        ${card({
          title: '导出产物',
          sub: '磁盘上实际生成的文件计数（每种格式每个 split 只列前几个文件名）',
          pad0: true,
          body: exportRows ? `<div class="table-wrap" style="border:0;border-radius:0"><table class="data">
            <thead><tr><th>Split</th><th>格式</th><th class="num">文件数</th><th>样例文件</th></tr></thead>
            <tbody>${exportRows}</tbody></table></div>` : emptyState({ title: '还没有导出文件', text: '检查 datasets 目录是否已生成 coco.json / data.yaml 等产物。' }),
        })}

        ${card({
          title: '样本索引',
          sub: `${num(sampleIds.length)} 条预览 · 点击查看标注详情`,
          body: sampleIds.length ? `<div class="list">${sampleIds.map((id) => `
            <button type="button" class="list-row" data-ann="${esc(id)}" style="text-align:left;cursor:pointer;width:100%">
              <span class="badge info" style="min-width:32px;justify-content:center">#</span>
              <span class="grow mono-sm truncate">${esc(id)}</span>
              ${icon('chevronRight')}
            </button>`).join('')}</div>` : '<div class="panel-note">该版本没有 sample_list 预览。</div>',
        })}
      </div>
    </div>
  `;

  root.querySelectorAll('[data-version]').forEach((b) => b.addEventListener('click', () => ctx.go('datasets', { v: b.getAttribute('data-version') })));
  root.querySelectorAll('[data-act="graph"]').forEach((b) => b.addEventListener('click', () => ctx.go('lineage', { focus: pick })));
  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));

  root.querySelectorAll('[data-ann]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.getAttribute('data-ann');
    ctx.drawer({ eyebrow: '标注详情', title: id, body: '<div class="graph-loading">读取标注…</div>' });
    try {
      const d = await api.annotation(id);
      const a = d.annotation || {};
      const bbox = a.bbox || {};
      ctx.drawer({
        eyebrow: `标注 · ${a.split || '–'} · ${a.synthetic ? '合成' : '真实'}`,
        title: id,
        body: `${d.frame_url ? `<div class="bbox-stage"><img class="bbox-img" src="${esc(d.frame_url)}" alt="">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none"><rect x="${(bbox.x || 0) * 100}" y="${(bbox.y || 0) * 100}"
              width="${(bbox.w || 0) * 100}" height="${(bbox.h || 0) * 100}" fill="rgba(47,107,255,.12)"
              stroke="#2f6bff" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>
            <span class="stage-note">${esc(a.annotation_id || id)}</span></div>` : ''}
          <div class="detail-section mt-14"><h4>字段</h4>${kv([
            ['缺陷类型', esc(defectName(a.defect_type))],
            ['缺陷 ID', `<span class="mono-sm">${esc(a.defect_id || '–')}</span>`],
            ['门禁', badge(a.gate_status, a.gate_status === 'passed' ? 'ok' : 'bad')],
            ['状态', badge(a.state, 'muted')],
            ['多智能体一致性', pct(a.agent_consensus, 1)],
            ['人工裁决', a.human_decision ? badge('已人工', 'ok') : badge('自动', 'muted')],
            ['Caption', esc(a.caption || '–')],
          ])}</div>
          ${(a.qa || []).length ? `<div class="detail-section"><h4>QA</h4><div class="qa">${a.qa.map((q) => `<div class="qa-item"><div class="qa-q">${esc(q.q)}</div><div class="qa-a">${esc(q.a)}</div></div>`).join('')}</div></div>` : ''}`,
      });
    } catch (e) {
      ctx.drawer({ eyebrow: '标注详情', title: id, body: callout('bad', esc(e.message), 'alert') });
    }
  }));
}
