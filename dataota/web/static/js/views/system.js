/* 系统与配置：实现注册表、目录占用、指标字典、待定项与配置树 */

import { api } from '../api.js';
import { esc, num, bytes, badge, icon, ts } from '../util.js';
import { pageHead, kpi, card, emptyState, kv } from '../ui.js';

export async function render(root, ctx) {
  const s = await api.system();
  const impls = s.impl || [];
  const zones = (s.zones || []).slice().sort((a, b) => b.bytes - a.bytes);
  const metrics = Object.entries(s.metrics || {});
  const lineage = s.lineage_counts || {};
  const open = s.open_items || [];
  const zoneMax = Math.max(1, ...zones.map((z) => z.bytes));

  const implRows = impls.map((x) => {
    const fellBack = x.effective && x.selected && x.effective !== x.selected;
    return `
    <tr>
      <td class="mono-sm" style="font-weight:600">${esc(x.iface)}</td>
      <td>${x.selected ? badge(x.selected, 'info', false) : badge('未配置', 'muted', false)}</td>
      <td>${x.effective ? badge(x.effective, fellBack ? 'warn' : 'ok', false) : '<span class="muted tiny">未运行</span>'}
        ${fellBack ? `<div class="tiny" style="color:var(--amber)">${esc(x.fallback_reason || '已回退')}</div>` : ''}</td>
      <td><div class="row" style="gap:4px">${(x.available || []).map((a) => {
        const miss = (x.missing || {})[a];
        return `<span title="${miss ? esc(`缺少依赖：${miss.join(', ')}`) : ''}">${badge(a + (miss ? ' ⚠' : ''), a === x.selected ? 'ok' : miss ? 'warn' : 'muted', false)}</span>`;
      }).join('')}</div></td>
    </tr>`;
  }).join('');

  const openCards = open.map((o) => `
    <div class="list-row" style="align-items:flex-start">
      <span class="badge warn" style="min-width:26px;justify-content:center">${o.id}</span>
      <div class="grow">
        <div style="font-weight:600">${esc(o.title)}</div>
        <div class="meta">影响位置 ${esc(o.where)} · ${esc(o.impact)}</div>
      </div>
    </div>`).join('');

  const lineageRows = Object.entries(lineage).sort((a, b) => b[1] - a[1]).map(([k, v]) => `
    <div class="list-row">
      ${badge(k, 'muted', false)}
      <span class="grow"></span>
      <b class="mono-sm">${num(v)}</b>
    </div>`).join('');

  const cfgJson = JSON.stringify(s.config_tree || {}, null, 2);

  root.innerHTML = `
    ${pageHead({
      title: '系统与配置',
      tag: `v${esc(s.version || '0.1.0')}`,
      sub: `<span>${esc(s.workdir)}</span><span class="sep"></span><span>${esc(s.config)}</span>`,
      actions: `<button class="btn btn-sm btn-ghost" data-act="health">${icon('activity')}<span>健康检查</span></button>
        <button class="btn btn-sm btn-primary" data-act="run">${icon('play')}<span>运行闭环</span></button>`,
    })}

    <div class="grid g-kpi">
      ${kpi({ label: '流水线版本', value: `v${esc(s.version || '0.1.0')}`, foot: '核心零依赖 · 视觉模型可选 numpy + Pillow', iconName: 'package' })}
      ${kpi({ label: '实现接口', value: num(impls.length), foot: `${impls.filter((x) => x.selected).length} 个已选实现`, iconName: 'cpu', tone: 'violet' })}
      ${kpi({ label: '数据分区', value: num(zones.length), foot: `${bytes(zones.reduce((a, b) => a + b.bytes, 0))} 磁盘占用`, iconName: 'database', tone: 'teal' })}
      ${kpi({ label: '血缘节点', value: num(Object.values(lineage).reduce((a, b) => a + b, 0)), foot: `${Object.keys(lineage).length} 种类型`, iconName: 'git' })}
      ${kpi({ label: '待定项', value: num(open.length), foot: '需要业务方确认的口径', iconName: 'alert', tone: 'amber' })}
      ${kpi({ label: '指标字典', value: num(metrics.length), foot: 'C5 指标口径注释', iconName: 'list' })}
    </div>

    <div class="g-side mt-20">
      <div class="stack">
        ${card({
          title: '实现注册表',
          sub: 'configs/demo.toml 的 [impl] 选择；缺少可选依赖时回退到默认实现并写审计（实际生效取自最近一轮）',
          pad0: true,
          body: impls.length ? `<div class="table-wrap" style="border:0;border-radius:0"><table class="data compact">
            <thead><tr><th>接口</th><th>配置选择</th><th>实际生效</th><th>已注册实现（⚠ 缺依赖）</th></tr></thead>
            <tbody>${implRows}</tbody></table></div>` : emptyState({ title: '没有注册实现', text: '检查 dataota.pipeline 是否成功导入。' }),
        })}

        ${card({
          title: '⚠ 待定项',
          sub: '来自设计文档第 8 节，当前 config 为占位值',
          body: openCards ? `<div class="list">${openCards}</div>` : '<div class="panel-note">没有待定项。</div>',
        })}
      </div>

      <div class="stack">
        ${card({
          title: '运行路径',
          sub: '只读控制台 · 数据写入由 pipeline 完成',
          body: kv([
            ['工作目录', `<span class="mono-sm">${esc(s.workdir)}</span>`],
            ['配置文件', `<span class="mono-sm">${esc(s.config)}</span>`],
            ['当前时间', esc(ts(Date.now() / 1000))],
          ]),
        })}
        ${card({
          title: '数据分区',
          sub: 'lake 下各 zone 的文件数与占用',
          body: zones.length ? `<div class="list">${zones.map((z) => `
            <div class="list-row">
              <span class="mono-sm" style="width:86px;flex:none">${esc(z.zone)}</span>
              <div class="grow"><div class="bar"><span style="width:${((z.bytes / zoneMax) * 100).toFixed(1)}%"></span></div></div>
              <span class="muted tiny nowrap" style="width:62px;text-align:right">${bytes(z.bytes)}</span>
              <span class="muted tiny nowrap" style="width:58px;text-align:right">${num(z.files)} 文件</span>
            </div>`).join('')}</div>` : '<div class="panel-note">数据湖尚未创建。</div>',
        })}
        ${card({
          title: '血缘节点统计',
          sub: 'lineage.sqlite 按 kind 计数',
          body: lineageRows ? `<div class="list">${lineageRows}</div>` : '<div class="panel-note">尚未生成血缘库。</div>',
        })}
      </div>
    </div>

    <div class="g-side mt-20">
      ${card({
        title: '配置树',
        sub: '只读展示 configs/demo.toml 解析结果',
        actions: `<button class="btn btn-sm btn-ghost" id="copy-cfg">${icon('download')}<span>复制 JSON</span></button>`,
        body: `<pre class="code-block config-tree" id="cfg-json">${esc(cfgJson)}</pre>`,
      })}
      <div class="stack">
        ${card({
          title: '指标字典',
          sub: '所有指标都有明确分子 / 分母',
          body: metrics.length ? `<div class="list">${metrics.map(([k, v]) => `
            <div class="list-row" style="align-items:flex-start">
              <span class="mono-sm" style="color:var(--accent);width:168px;flex:none">${esc(k)}</span>
              <span class="grow small">${esc(v)}</span>
            </div>`).join('')}</div>` : '<div class="panel-note">没有指标定义。</div>',
        })}
      </div>
    </div>
  `;

  root.querySelectorAll('[data-act="run"]').forEach((b) => b.addEventListener('click', () => ctx.runModal()));
  root.querySelectorAll('[data-act="health"]').forEach((b) => b.addEventListener('click', async () => {
    try {
      const h = await api.health();
      ctx.toast(`服务正常 · ${h.workdir}`, 'ok');
    } catch (e) {
      ctx.toast(e.message, 'bad');
    }
  }));

  root.querySelector('#copy-cfg')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(cfgJson);
      ctx.toast('配置 JSON 已复制', 'ok');
    } catch {
      ctx.toast('浏览器拒绝访问剪贴板', 'bad');
    }
  });
}
