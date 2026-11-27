/* 千机链 Web GUI —— React + htm，无构建步骤 */
const { useState, useEffect, useMemo, useCallback, useRef } = React;
const html = htm.bind(React.createElement);

const THEME_KEY = 'qjl-theme';
const initialTheme = (() => {
  try { return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; } catch (e) { return 'dark'; }
})();
document.documentElement.dataset.theme = initialTheme;

const PAGES = [
  { key: 'chains', label: '脚本链', icon: 'list', group: '运行', desc: '编排脚本执行顺序、依赖与日志规则' },
  { key: 'schedule', label: '周计划', icon: 'calendar-days', group: '运行', desc: '按星期安排每天要运行的脚本链' },
  { key: 'policy', label: '运行策略', icon: 'sliders-horizontal', group: '配置', desc: '时间窗、截止、重试、锁屏与账户校验' },
  { key: 'notify', label: '消息推送', icon: 'bell', group: '配置', desc: '推送渠道、测试推送与通知时机' },
  { key: 'rules', label: '日志规则', icon: 'scroll-text', group: '配置', desc: '任务级错误与卡死打转规则集，可加载、试跑、下发到脚本' },
  { key: 'settings', label: '设置', icon: 'settings', group: '配置', desc: '下载源、版本更新与运行环境' },
  { key: 'report', label: '运行报告', icon: 'file-text', group: '诊断', desc: '最近一次运行的汇总、明细与日志' },
];

const PAGE_GROUPS = ['运行', '配置', '诊断'];

const PAGE_KEY = 'qjl-page';

function initialPage() {
  const hash = (window.location.hash || '').replace('#', '');
  if (PAGES.some((item) => item.key === hash)) return hash;
  try {
    const saved = localStorage.getItem(PAGE_KEY);
    if (PAGES.some((item) => item.key === saved)) return saved;
  } catch (e) { /* 忽略存储异常 */ }
  return 'chains';
}

const TYPE_META = {
  external: { label: '外部程序', cls: 'tag-ext', color: '#409EFF' },
  python: { label: 'Python', cls: 'tag-py', color: '#2DBE55' },
  computer_op: { label: '电脑操作', cls: 'tag-op', color: '#E08020' },
};

const DAILY_BADGE = {
  completed: 'badge-ok',
  running: 'badge-cfg',
  interrupted: 'badge-fail',
  deadline: 'badge-cfg',
  blocked: 'badge-miss',
  not_started: 'badge-miss',
};

const SCRIPT_DEFAULTS = {
  display_name: '', game_label: '', script_type: 'external', script_path: '',
  computer_operation: '', script_process_name: [], game_process_name: '',
  launcher_mode: false, run_timeout_seconds: 3600, check_done: 'game_or_script_closed',
  kill_script_after_done: true, kill_game_after_done: true, kill_process_tree: true,
  script_arguments: '', notify_start: true, notify_done: true, notify_log_interval: 0,
  enabled: true, attach_direction: '', no_log_timeout_seconds: 300, no_log_max_retries: 3,
  fail_fast: {}, stall: {}, loop: {},
};

const FAIL_FAST_DEFAULTS = {
  enabled: false, action: 'report', grace_seconds: 240, error_patterns: [], ignore_patterns: [],
};
const STALL_DEFAULTS = {
  enabled: true, window_minutes: 8, min_repeats: 200, grace_seconds: 60,
  ignore_pattern: '', ignore_levels: [],
};
const LOOP_DEFAULTS = {
  enabled: false, runtime: false, min_iterations: 200, max_progress: 8,
  same_state_repeats: 90, grace_seconds: 60,
  segment_start: '', segment_end: '', step_pattern: '', progress_pattern: '',
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch (e) { payload = text; }
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : (text || response.statusText);
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

async function pickPath(kind, initial, notify) {
  try {
    const payload = await api('/api/dialog/pick', {
      method: 'POST',
      body: JSON.stringify({ kind: kind === 'pick_dir' ? 'dir' : 'file', initial: initial || '' }),
    });
    return (payload && payload.path) || '';
  } catch (e) {
    if (notify) notify(`打开选择窗口失败：${e.message}`, true);
    return '';
  }
}

/* ------------------------------ 基础组件 ------------------------------ */

const ATTR_MAP = {
  class: 'className', 'stroke-width': 'strokeWidth', 'stroke-linecap': 'strokeLinecap',
  'stroke-linejoin': 'strokeLinejoin', 'fill-rule': 'fillRule', 'clip-rule': 'clipRule',
};

function _iconProps(attrs, extra) {
  const props = { ...extra };
  Object.entries(attrs || {}).forEach(([key, value]) => {
    props[ATTR_MAP[key] || key] = value;
  });
  return props;
}

function Icon({ name, size = 16, strokeWidth = 1.9, className }) {
  const pascal = String(name).split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('');
  const icons = (window.lucide && window.lucide.icons) || {};
  const node = icons[pascal];
  if (!node) return null;
  const props = _iconProps(node[1], { width: size, height: size });
  props.strokeWidth = strokeWidth;
  if (className) props.className = className;
  return React.createElement(
    node[0],
    props,
    (node[2] || []).map((child, index) => React.createElement(
      child[0],
      _iconProps(child[1], { key: index }),
    )),
  );
}

function Switch({ on, onChange, title }) {
  return html`<button
    class=${'switch' + (on ? ' on' : '')}
    role="switch"
    aria-checked=${!!on}
    aria-label=${title || '开关'}
    title=${title || ''}
    onClick=${() => onChange(!on)}
  ></button>`;
}

function Field({ label, children, hint }) {
  return html`<div class="field">
    <label class="field-label">${label}</label>
    ${children}
    ${hint ? html`<div class="field-hint">${hint}</div>` : null}
  </div>`;
}

function PathInput({ label, hint, value, onChange, placeholder, kind = 'pick_file', pickLabel = '选择文件', notify }) {
  const pick = async () => {
    const result = await pickPath(kind, value, notify);
    if (result) onChange(result);
  };
  return html`<div class="field">
    <label class="field-label">${label}</label>
    <div class="input-group">
      <input
        class="input mono"
        value=${value || ''}
        placeholder=${placeholder || ''}
        onInput=${(e) => onChange(e.target.value)}
      />
      <button class="btn input-pick" onClick=${pick} type="button">
        <${Icon} name=${kind === 'pick_dir' ? 'folder-open' : 'file-search'} />${pickLabel}
      </button>
    </div>
    ${hint ? html`<div class="field-hint">${hint}</div>` : null}
  </div>`;
}

function ToggleRow({ title, desc, on, onChange }) {
  return html`<div class="toggle-row">
    <div class="grow">
      <div class="toggle-title">${title}</div>
      ${desc ? html`<div class="toggle-desc">${desc}</div>` : null}
    </div>
    <${Switch} on=${on} onChange=${onChange} title=${title} />
  </div>`;
}

function Card({ title, sub, icon, actions, children, bodyClass, headless }) {
  return html`<section class="card">
    ${headless ? null : html`<div class="card-head">
      ${icon ? html`<span class="card-head-icon"><${Icon} name=${icon} size=${16} /></span>` : null}
      <div class="grow">
        <div class="card-title">${title}</div>
        ${sub ? html`<div class="card-sub">${sub}</div>` : null}
      </div>
      ${actions ? html`<div class="card-actions">${actions}</div>` : null}
    </div>`}
    <div class=${'card-body' + (bodyClass ? ' ' + bodyClass : '')}>${children}</div>
  </section>`;
}

function EmptyState({ icon = 'inbox', title, desc }) {
  return html`<div class="empty">
    <div class="empty-icon"><${Icon} name=${icon} size=${22} /></div>
    <div class="empty-title">${title}</div>
    ${desc ? html`<div class="small">${desc}</div>` : null}
  </div>`;
}

function LoadingBlock({ text = '加载中…' }) {
  return html`<div class="loading-block">
    <${Icon} name="loader-2" size=${16} className="spinner" />
    <span>${text}</span>
  </div>`;
}

function SkelCard() {
  return html`<div class="chan-card">
    <div style=${{ padding: '12px 13px', display: 'flex', gap: '11px', alignItems: 'center' }}>
      <div class="skel" style=${{ width: '32px', height: '32px', borderRadius: '10px' }}></div>
      <div class="grow">
        <div class="skel skel-line" style=${{ width: '46%' }}></div>
        <div class="skel skel-line" style=${{ width: '72%', marginTop: '7px', height: '8px' }}></div>
      </div>
    </div>
  </div>`;
}

/* ------------------------------ 脚本链 ------------------------------ */

function scriptTypeMeta(type) {
  return TYPE_META[type] || TYPE_META.external;
}

/* 规则集是否支持某个游戏：未声明 games 的规则文件视为通用，任何游戏都能用 */
function profileSupportsGame(profile, game) {
  const games = profile.games || [];
  return games.length === 0 || (!!game && games.includes(game));
}

/* 与后端 _script_rule_summary 同口径，额外用于展示「刚改还没保存」的状态 */
function ruleSummary(script) {
  const rawFailFast = script.fail_fast || {};
  const rawStall = script.stall || {};
  const rawLoop = script.loop || {};
  const failFast = { ...FAIL_FAST_DEFAULTS, ...rawFailFast };
  const stall = { ...STALL_DEFAULTS, ...rawStall };
  const loop = { ...LOOP_DEFAULTS, ...rawLoop };
  // 是否「配过」看原始对象，避免默认值把空配置也算成已启用
  const hasFailFast = Object.keys(rawFailFast).length > 0;
  const hasStall = Object.keys(rawStall).length > 0;
  const hasLoop = Object.keys(rawLoop).length > 0;
  const errorPatterns = (failFast.error_patterns || []).filter(Boolean);
  const errorOn = hasFailFast && failFast.enabled !== false && errorPatterns.length > 0;
  const stallOn = hasStall && stall.enabled !== false;
  const loopConfigured = hasLoop;
  // 段内轮转要显式 runtime: true 且段起点/步进正则齐全才参与运行时终止
  const loopOn = loopConfigured && loop.enabled !== false && loop.runtime === true
    && !!(loop.segment_start && loop.step_pattern);
  const stops = failFast.action === 'stop_task' || failFast.stop === true;
  return {
    error: errorOn,
    errorStops: errorOn && stops,
    errorCount: errorPatterns.length,
    errorGrace: Number(failFast.grace_seconds) || 0,
    stall: stallOn,
    stallWindow: Number(stall.window_minutes) || 0,
    stallRepeats: Number(stall.min_repeats) || 0,
    stallGrace: Number(stall.grace_seconds) || 0,
    loop: loopOn,
    loopPending: loopConfigured && !loopOn,
    loopSteps: Number(loop.min_iterations) || 0,
    loopProgress: Number(loop.max_progress) || 0,
    loopRepeats: Number(loop.same_state_repeats) || 0,
    count: (errorOn ? 1 : 0) + (stallOn ? 1 : 0) + (loopOn ? 1 : 0),
  };
}

/* 规则明细：一条规则一行短语，参数直接摊在卡片上，不用点开抽屉 */
function ruleDetails(rules) {
  const items = [];
  if (rules.error) {
    items.push(`错误 ${rules.errorCount} 条 · 宽限 ${rules.errorGrace} 秒 · ${rules.errorStops ? '终止' : '只登记'}`);
  }
  if (rules.stall) {
    items.push(`打转 ${rules.stallWindow} 分钟 ≥${rules.stallRepeats} 次 · 宽限 ${rules.stallGrace} 秒`);
  }
  if (rules.loop || rules.loopPending) {
    items.push(`轮转 ${rules.loopSteps} 步 · 不同状态 ≤${rules.loopProgress} · 同状态 ≥${rules.loopRepeats}`
      + (rules.loop ? '' : ' · 仅试跑'));
  }
  return items;
}

function formatSpan(seconds) {
  const value = Number(seconds) || 0;
  if (value <= 0) return '不限';
  if (value % 3600 === 0) return `${value / 3600} 小时`;
  if (value % 60 === 0) return `${value / 60} 分钟`;
  return `${value} 秒`;
}

function stripDerived(scripts) {
  return scripts.map((script) => {
    const copy = { ...script };
    [
      'idx', 'script_display_name', 'game_display_name', 'script_process_display_name',
      'check_done_display_name', 'computer_operation_display_name', 'invalid_message', 'attached_to_prev',
    ].forEach((key) => delete copy[key]);
    if (copy.fail_fast && !Object.keys(copy.fail_fast).length) delete copy.fail_fast;
    if (copy.stall && !Object.keys(copy.stall).length) delete copy.stall;
    return copy;
  });
}

function ChainsPage({ data, refresh, notify }) {
  const [selected, setSelected] = useState('');
  const [chain, setChain] = useState(null);
  const [scripts, setScripts] = useState([]);
  const [editing, setEditing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (data.chains.length === 0) {
      setSelected('');
      setChain(null);
      setScripts([]);
      return;
    }
    if (!data.chains.some((item) => item.name === selected)) {
      setSelected(data.chains[0].name);
    }
  }, [data.chains, selected]);

  useEffect(() => {
    setConfirmingDelete(false);
    setEditing(null);
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    api(`/api/chains/${encodeURIComponent(selected)}`)
      .then((payload) => {
        if (cancelled) return;
        setChain(payload);
        setScripts(payload.scripts.map((item) => ({ ...item })));
      })
      .catch((e) => notify(e.message, true))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  const dirty = useMemo(() => {
    if (!chain) return false;
    return JSON.stringify(stripDerived(scripts)) !== JSON.stringify(stripDerived(chain.scripts));
  }, [chain, scripts]);

  const save = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const payload = await api(`/api/chains/${encodeURIComponent(selected)}`, {
        method: 'PUT',
        body: JSON.stringify({ scripts }),
      });
      setChain(payload);
      setScripts(payload.scripts.map((item) => ({ ...item })));
      notify('脚本链已保存');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const run = async (debugIndex) => {
    if (!selected) return;
    try {
      await api(`/api/chains/${encodeURIComponent(selected)}/run`, {
        method: 'POST',
        body: JSON.stringify({ debug_index: debugIndex ?? null }),
      });
      notify(debugIndex === null || debugIndex === undefined
        ? `已启动脚本链 ${selected}`
        : `已仅调试第 ${debugIndex + 1} 个脚本`);
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  const stop = async () => {
    try {
      const result = await api('/api/run/stop', { method: 'POST' });
      notify(result.message);
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  const createChain = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      await api('/api/chains', { method: 'POST', body: JSON.stringify({ name }) });
      setNewName('');
      setSelected(name);
      notify(`已创建脚本链 ${name}`);
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  const copyChain = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const payload = await api(`/api/chains/${encodeURIComponent(selected)}/copy`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setSelected(payload.name);
      notify(`已复制为 ${payload.name}`);
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const removeChain = async () => {
    if (!selected) return;
    try {
      await api(`/api/chains/${encodeURIComponent(selected)}`, { method: 'DELETE' });
      notify(`已删除脚本链 ${selected}`);
      setSelected('');
      setChain(null);
      setScripts([]);
      refresh();
    } catch (e) {
      notify(e.message, true);
      setConfirmingDelete(false);
    }
  };

  const addScript = (type) => {
    const next = scripts.concat([{ ...SCRIPT_DEFAULTS, script_type: type }]);
    setScripts(next);
    setEditing(next.length - 1);
  };

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= scripts.length) return;
    setScripts((list) => {
      const next = list.slice();
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
    if (editing === index) setEditing(target);
  };

  const removeScript = (index) => {
    setScripts((list) => list.filter((_, i) => i !== index));
    if (editing === index) setEditing(null);
  };

  const duplicate = (index) => {
    setScripts((list) => {
      const next = list.slice();
      next.splice(index + 1, 0, { ...list[index] });
      return next;
    });
  };

  const updateScript = (index, patch) => {
    setScripts((list) => list.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  };

  const enabledCount = scripts.filter((item) => item.enabled).length;
  const retry = (chain && chain.retry) || (data.run_config && data.run_config.retry) || {};
  const retryRounds = Number(retry.max_rounds) || 0;
  const current = editing !== null ? scripts[editing] : null;

  return html`<div class="page">
    <div class="chains-layout">
      <section class="card chain-list-card">
        <div class="card-head">
          <div class="grow">
            <div class="card-title">脚本链</div>
            <div class="card-sub">共 ${data.chains.length} 条</div>
          </div>
        </div>
        <div class="card-body tight">
          ${data.chains.length === 0
            ? html`<${EmptyState} icon="layers" title="还没有脚本链" desc="在下方输入名称新建一条" />`
            : html`<div class="chain-list">
                ${data.chains.map((item) => html`<div
                  key=${item.name}
                  class=${'chain-item' + (item.name === selected ? ' active' : '')}
                  role="button"
                  tabIndex=${0}
                  aria-current=${item.name === selected ? 'true' : undefined}
                  onClick=${() => setSelected(item.name)}
                  onKeyDown=${(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(item.name); }
                  }}
                >
                  <div class="grow" style=${{ minWidth: 0 }}>
                    <div class="chain-item-name">${item.name}</div>
                    <div class="chain-item-meta">${item.script_count} 个脚本 · ${item.games.join(' / ') || '未设置游戏'}</div>
                  </div>
                  ${item.invalid_count > 0 ? html`<span class="badge badge-fail">${item.invalid_count}</span>` : null}
                  ${item.script_count > 0 && !item.rule_covered
                    ? html`<span class="badge badge-miss" title="这条链没有任何脚本启用日志规则，异常只能靠超时兜底">无规则</span>`
                    : null}
                </div>`)}
              </div>`}
          <div class="row" style=${{ marginTop: '12px', gap: '6px' }}>
            <input
              class="input"
              placeholder="新建脚本链名称"
              value=${newName}
              onInput=${(e) => setNewName(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter') createChain(); }}
            />
            <button class="btn btn-primary" onClick=${createChain} title="新建"><${Icon} name="plus" /></button>
          </div>
        </div>
      </section>

      <div class="detail">
        ${!chain
          ? html`<section class="card"><${EmptyState} icon="mouse-pointer-click" title="选择一条脚本链" desc="或先在左侧新建一条" /></section>`
          : html`
            <div class="detail-head">
              <div class="grow" style=${{ minWidth: 0 }}>
                <div class="detail-title">${chain.name}</div>
                <div class="detail-meta">${scripts.length} 个脚本 · ${enabledCount} 个启用${dirty ? ' · 有未保存修改' : ''}</div>
              </div>
              <div class="detail-actions">
                ${dirty ? html`<span class="tag tag-off">未保存</span>` : null}
                <button class="btn" onClick=${stop} title="结束由控制台启动的运行实例"><${Icon} name="square" />停止</button>
                <button class="btn" onClick=${() => run(null)}><${Icon} name="play" />运行</button>
                <button class="btn btn-primary" onClick=${save} disabled=${busy || !dirty}><${Icon} name="save" />保存</button>
                <button class="icon-btn" title="复制脚本链" onClick=${copyChain} disabled=${busy}><${Icon} name="copy" /></button>
                ${confirmingDelete
                  ? html`<button class="btn btn-danger" onClick=${removeChain}>确认删除</button>`
                  : html`<button class="icon-btn danger" title="删除脚本链" onClick=${() => setConfirmingDelete(true)}><${Icon} name="trash-2" /></button>`}
              </div>
            </div>

            <div class="chain-facts">
              <span
                class=${'chip' + (retryRounds > 0 ? ' chip-live' : '')}
                title=${retryRounds > 0
                  ? `脚本异常结束后${retry.only_failed === false ? '整链重跑' : '只重跑失败的脚本'}，每轮前等待 ${retry.delay_seconds || 0} 秒；到「运行策略」页可改`
                  : '脚本异常结束后不自动重跑，到「运行策略」页可开启'}
              >
                <${Icon} name="repeat" size=${12} />
                ${retryRounds > 0
                  ? `整链失败重试 ${retryRounds} 轮 · 间隔 ${retry.delay_seconds || 0} 秒`
                  : '整链失败不重试'}
              </span>
            </div>

            ${chain.warnings.map((warning) => html`<div class="notice" key=${warning}>
              <${Icon} name="alert-triangle" size=${15} /><span class="grow">${warning}</span>
            </div>`)}

            ${loading
              ? html`<section class="card"><div class="empty">加载中…</div></section>`
              : html`<section class="card">
                  <div class="card-head">
                    <div class="grow">
                      <div class="card-title">脚本顺序</div>
                      <div class="card-sub">点击脚本卡片打开详细配置</div>
                    </div>
                  </div>
                  <div class="card-body tight">
                    ${scripts.length === 0
                      ? html`<${EmptyState} icon="list-plus" title="这条链还没有脚本" desc="从下方选择一种类型添加" />`
                      : html`<div class="script-list">
                          ${scripts.map((script, index) => {
                            const meta = scriptTypeMeta(script.script_type);
                            const isOp = script.script_type === 'computer_op';
                            const rules = ruleSummary(script);
                            const ruleLines = ruleDetails(rules);
                            return html`<div
                              key=${index}
                              class=${'script-card' + (script.enabled ? '' : ' disabled') + (editing === index ? ' editing' : '')}
                              role="button"
                              tabIndex=${0}
                              aria-expanded=${editing === index}
                              onClick=${() => setEditing(editing === index ? null : index)}
                              onKeyDown=${(e) => {
                                if (e.target !== e.currentTarget) return;
                                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(editing === index ? null : index); }
                              }}
                            >
                              <div class="type-bar" style=${{ background: meta.color }}></div>
                              <div class="script-index">${index + 1}</div>
                              <div class="script-main">
                                <div class="row" style=${{ gap: '8px' }}>
                                  <span class="script-name">${script.script_display_name}</span>
                                  ${script.attached_to_prev ? html`<span class="tag tag-off">挂靠</span>` : null}
                                </div>
                                <div class="script-tags">
                                  <span class=${'tag ' + meta.cls}>${meta.label}</span>
                                  ${!isOp && script.game_display_name ? html`<span class="tag">${script.game_display_name}</span>` : null}
                                  ${rules.error
                                    ? html`<span
                                        class=${'tag ' + (rules.errorStops ? 'tag-rule-stop' : 'tag-rule')}
                                        title=${rules.errorStops
                                          ? '任务级错误规则：过宽限后结束当前脚本'
                                          : '任务级错误规则：过宽限后只登记，脚本继续跑'}
                                      >${rules.errorStops ? '错误·终止' : '错误·登记'}</span>`
                                    : null}
                                  ${rules.stall
                                    ? html`<span class="tag tag-rule" title="卡死打转规则：同一行日志在时间窗内高频重复即判定">打转</span>`
                                    : null}
                                  ${rules.loop
                                    ? html`<span class="tag tag-rule" title="段内轮转规则：步进很多但位置没变即判定">轮转</span>`
                                    : null}
                                  ${rules.loopPending
                                    ? html`<span class="tag tag-empty" title="段内轮转已配置但未开启「运行时终止」，只在日志规则页试跑时生效">轮转·仅试跑</span>`
                                    : null}
                                  ${!rules.count && !rules.loopPending
                                    ? html`<span class="tag tag-empty" title="未启用日志规则：命中也只是超时兜底，不会因日志内容终止">无日志规则</span>`
                                    : null}
                                  ${script.invalid_message ? html`<span class="badge badge-fail">${script.invalid_message}</span>` : null}
                                </div>
                                <div class="script-path mono">
                                  ${isOp
                                    ? (script.computer_operation_display_name || '未选择电脑操作')
                                    : (script.script_path || '未设置脚本路径')}
                                </div>
                                <div class="script-rules">
                                  <span class="script-rules-label">日志规则</span>
                                  ${ruleLines.length
                                    ? ruleLines.map((text) => html`<span class="script-rule-item" key=${text}>${text}</span>`)
                                    : html`<span class="script-rules-none">未启用，异常只靠超时兜底</span>`}
                                </div>
                                <div class="script-facts">
                                  <span title="脚本跑满该时长后强制结束">超时 ${formatSpan(script.run_timeout_seconds)}</span>
                                  <span title="超过该时长没有新日志视为卡死">无日志 ${formatSpan(script.no_log_timeout_seconds)}</span>
                                  <span title="疑似卡死后自动重启脚本的次数">卡死重试 ${Number(script.no_log_max_retries) || 0} 次</span>
                                </div>
                              </div>
                              <div class="script-actions" onClick=${(e) => e.stopPropagation()}>
                                <${Switch} on=${script.enabled} onChange=${(value) => updateScript(index, { enabled: value })} title="启用" />
                                <button class="icon-btn" title="上移" onClick=${() => move(index, -1)}><${Icon} name="arrow-up" /></button>
                                <button class="icon-btn" title="下移" onClick=${() => move(index, 1)}><${Icon} name="arrow-down" /></button>
                                <button class="icon-btn" title="复制" onClick=${() => duplicate(index)}><${Icon} name="copy" /></button>
                                <button class="icon-btn danger" title="删除" onClick=${() => removeScript(index)}><${Icon} name="trash-2" /></button>
                              </div>
                            </div>`;
                          })}
                        </div>`}
                    <div class="add-row" style=${{ marginTop: '12px' }}>
                      <button class="add-tile" onClick=${() => addScript('external')}><${Icon} name="plus" />外部程序</button>
                      <button class="add-tile" onClick=${() => addScript('python')}><${Icon} name="plus" />Python 脚本</button>
                      <button class="add-tile" onClick=${() => addScript('computer_op')}><${Icon} name="plus" />电脑操作</button>
                    </div>
                  </div>
                </section>`}
          `}
      </div>
    </div>

    ${current ? html`<${ScriptDrawer}
      key=${editing}
      script=${current}
      index=${editing}
      total=${scripts.length}
      options=${data.options}
      onChange=${(patch) => updateScript(editing, patch)}
      onClose=${() => setEditing(null)}
      onRun=${() => run(editing)}
      notify=${notify}
    />` : null}
  </div>`;
}

function ScriptDrawer({ script, index, total, options, onChange, onClose, onRun, notify }) {
  const [tab, setTab] = useState('basic');
  const meta = scriptTypeMeta(script.script_type);
  const isOp = script.script_type === 'computer_op';
  const isPython = script.script_type === 'python';
  const [customGame, setCustomGame] = useState(() => {
    const current = script.game_process_name || '';
    return !!current && !options.game.some((item) => item.value === current);
  });

  const failFast = { ...FAIL_FAST_DEFAULTS, ...(script.fail_fast || {}) };
  const stall = { ...STALL_DEFAULTS, ...(script.stall || {}) };
  const loop = { ...LOOP_DEFAULTS, ...(script.loop || {}) };
  const [ruleProfiles, setRuleProfiles] = useState([]);
  const [rulePick, setRulePick] = useState('');
  const [ruleBusy, setRuleBusy] = useState(false);
  const processNames = Array.isArray(script.script_process_name) ? script.script_process_name : [];

  const gameValue = script.game_process_name || '';
  const gameText = (options.game.find((item) => item.value === gameValue) || {}).label || gameValue;
  // 规则来源下拉按脚本已设置的游戏过滤，避免把别的游戏的规则填进来
  const availableProfiles = gameValue
    ? ruleProfiles.filter((item) => profileSupportsGame(item, gameValue))
    : ruleProfiles;

  useEffect(() => {
    if (rulePick && !availableProfiles.some((item) => item.id === rulePick)) setRulePick('');
  }, [rulePick, gameValue, ruleProfiles]);

  const patchFailFast = (patch) => onChange({ fail_fast: { ...failFast, ...patch } });
  const patchStall = (patch) => onChange({ stall: { ...stall, ...patch } });
  const patchLoop = (patch) => onChange({ loop: { ...loop, ...patch } });
  const patchProcessNames = (text) => onChange({
    script_process_name: text.split(',').map((item) => item.trim()).filter(Boolean),
  });
  const patchPatterns = (key, text) => patchFailFast({
    [key]: text.split('\n').map((item) => item.trim()).filter(Boolean),
  });

  // 规则集下拉：进高级设置时才拉一次，避免每次打开抽屉都请求
  useEffect(() => {
    if (tab !== 'advanced') return undefined;
    let alive = true;
    api('/api/rules')
      .then((payload) => { if (alive) setRuleProfiles(payload.profiles || []); })
      .catch(() => {});
    return () => { alive = false; };
  }, [tab]);

  const fillFromProfile = async () => {
    if (!rulePick) return;
    setRuleBusy(true);
    try {
      const profile = await api('/api/rules/' + encodeURIComponent(rulePick));
      const rules = profile.rules || [];
      const errors = rules.filter((r) => r.level === 'fatal' || r.level === 'task').map((r) => r.match);
      const ignores = rules.filter((r) => r.level === 'ignore').map((r) => r.match);
      const ff = { ...FAIL_FAST_DEFAULTS, ...(profile.fail_fast || {}) };
      onChange({
        fail_fast: {
          ...ff,
          enabled: !!ff.enabled && errors.length > 0,
          error_patterns: errors,
          ignore_patterns: ignores,
        },
        stall: { ...STALL_DEFAULTS, ...(profile.stall || {}) },
        loop: { ...LOOP_DEFAULTS, ...(profile.loop || {}) },
      });
      notify(`已填入「${profile.name}」：${errors.length} 条错误规则、${ignores.length} 条忽略规则`);
    } catch (e) {
      notify(e.message, true);
    } finally {
      setRuleBusy(false);
    }
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return html`<div class="drawer-backdrop" onClick=${onClose}></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="脚本配置" onClick=${(e) => e.stopPropagation()}>
    <div class="drawer-head">
      <div class="drawer-title-row">
        <div class="grow" style=${{ minWidth: 0 }}>
          <div class="drawer-title">第 ${index + 1} / ${total} 个脚本</div>
          <div class="drawer-sub">${meta.label}${isOp ? '' : ' · ' + (script.script_path || '未设置路径')}</div>
        </div>
        <button class="icon-btn" onClick=${onClose} title="关闭"><${Icon} name="x" /></button>
      </div>
      <div class="tabs">
        ${[['basic', '基本'], ['run', '运行'], ['advanced', '高级']].map(([key, label]) => html`<button
          key=${key}
          class=${'tab' + (tab === key ? ' active' : '')}
          onClick=${() => setTab(key)}
        >${label}</button>`)}
      </div>
    </div>

    <div class="drawer-body">
      ${tab === 'basic' ? html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <${Field} label="显示名称" hint="留空时自动使用脚本文件名">
          <input class="input" value=${script.display_name || ''} placeholder="例如：清理日常" onInput=${(e) => onChange({ display_name: e.target.value })} />
        <//>
        <${Field} label="脚本类型">
          <div class="row" style=${{ gap: '8px', flexWrap: 'wrap' }}>
            ${options.script_type.map((item) => html`<button
              key=${item.value}
              class=${'chip-toggle' + (script.script_type === item.value ? ' on' : '')}
              onClick=${() => onChange({ script_type: item.value })}
            >${item.label}</button>`)}
          </div>
        <//>
        ${isOp ? html`<${Field} label="电脑操作" hint="直接执行，不启动外部进程；可配合隐私屏使用">
          <select class="input" value=${script.computer_operation || ''} onChange=${(e) => onChange({ computer_operation: e.target.value })}>
            <option value="">请选择</option>
            ${options.computer_operation.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>` : html`
          <${PathInput}
            label=${isPython ? 'Python 脚本路径' : '脚本路径'}
            hint="支持 exe / bat / py 等可执行文件的完整路径"
            value=${script.script_path || ''}
            placeholder="C:\\path\\to\\script.exe"
            onChange=${(path) => onChange({ script_path: path })}
            notify=${notify}
          />
          <${Field} label="启动参数" hint="原样传给脚本，含空格时请使用引号">
            <input class="input mono" value=${script.script_arguments || ''} placeholder="--auto" onInput=${(e) => onChange({ script_arguments: e.target.value })} />
          <//>
        `}
        <div style=${{ borderTop: '1px solid var(--line)', paddingTop: '4px' }}>
          <${ToggleRow} title="启用" desc="关闭后运行时跳过此脚本" on=${!!script.enabled} onChange=${(value) => onChange({ enabled: value })} />
        </div>
      </div>` : null}

      ${tab === 'run' ? html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        ${!isOp ? html`
          <${Field} label="游戏进程" hint="用于判断游戏是否仍在运行，以及完成后关闭游戏">
            <select
              class="input"
              value=${customGame ? '__custom__' : (script.game_process_name || '')}
              onChange=${(e) => {
                const value = e.target.value;
                if (value === '__custom__') {
                  setCustomGame(true);
                  onChange({ game_process_name: '' });
                } else {
                  setCustomGame(false);
                  onChange({ game_process_name: value });
                }
              }}
            >
              <option value="">不指定</option>
              ${options.game.map((item) => html`<option value=${item.value} key=${item.value}>${item.label} · ${item.value}</option>`)}
              <option value="__custom__">自定义进程名…</option>
            </select>
          <//>
          ${customGame ? html`<div class="grid-2">
            <${Field} label="游戏进程名" hint="例如 MyGame.exe">
              <input class="input mono" value=${script.game_process_name || ''} onInput=${(e) => onChange({ game_process_name: e.target.value })} />
            <//>
            <${Field} label="游戏显示名" hint="仅用于界面展示，可留空">
              <input class="input" value=${script.game_label || ''} onInput=${(e) => onChange({ game_label: e.target.value })} />
            <//>
          </div>` : null}
          <${Field} label="检查完成方式">
            <select class="input" value=${script.check_done || ''} onChange=${(e) => onChange({ check_done: e.target.value })}>
              <option value="">请选择</option>
              ${options.check_done.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
            </select>
          <//>

          <div class="rule-box">
            <div class="rule-head">
              <div class="grow">
                <div class="rule-title">启动器启动</div>
                <div class="rule-desc">脚本路径是启动器时开启，并填写启动后实际运行的目标进程</div>
              </div>
              <${Switch} on=${!!script.launcher_mode} onChange=${(value) => onChange({ launcher_mode: value })} />
            </div>
            <${Field} label="启动后实际运行的程序" hint="多个用英文逗号分隔；可点击下方预设快速填充">
              <input class="input mono" value=${processNames.join(', ')} placeholder="python.exe, pythonw.exe" onInput=${(e) => patchProcessNames(e.target.value)} />
            <//>
            <div class="row" style=${{ flexWrap: 'wrap', gap: '6px' }}>
              ${options.script_process.map((item) => {
                const values = Array.isArray(item.value) ? item.value : [item.value];
                const active = values.length === processNames.length && values.every((value) => processNames.includes(value));
                return html`<button
                  key=${item.label}
                  class=${'chip-toggle' + (active ? ' on' : '')}
                  onClick=${() => onChange({ script_process_name: active ? [] : values })}
                >${item.label}</button>`;
              })}
            </div>
          </div>
        ` : null}

        <div class="grid-2">
          <${Field} label="挂靠">
            <select class="input" value=${script.attach_direction || ''} onChange=${(e) => onChange({ attach_direction: e.target.value })}>
              ${options.attach_direction.map((item) => html`<option value=${item.value} key=${item.value || 'none'}>${item.label}</option>`)}
            </select>
          <//>
          <${Field} label="运行超时（秒）" hint="超时后终止脚本并判定失败">
            <input class="input" type="number" value=${script.run_timeout_seconds} onInput=${(e) => onChange({ run_timeout_seconds: Number(e.target.value) })} />
          <//>
        </div>

        <div style=${{ borderTop: '1px solid var(--line)', paddingTop: '4px' }}>
          <${ToggleRow} title="完成后关闭脚本" on=${!!script.kill_script_after_done} onChange=${(value) => onChange({ kill_script_after_done: value })} />
          ${!isOp ? html`<${ToggleRow} title="完成后关闭游戏" on=${!!script.kill_game_after_done} onChange=${(value) => onChange({ kill_game_after_done: value })} />` : null}
          <${ToggleRow} title="结束整个进程树" desc="关闭时连同子进程一起结束" on=${!!script.kill_process_tree} onChange=${(value) => onChange({ kill_process_tree: value })} />
        </div>
      </div>` : null}

      ${tab === 'advanced' ? html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <div class="grid-2">
          <${Field} label="无日志超时（秒）" hint="脚本长时间无输出视为卡死；0 或空会回落默认 300 秒">
            <input class="input" type="number" value=${script.no_log_timeout_seconds} onInput=${(e) => onChange({ no_log_timeout_seconds: Number(e.target.value) })} />
          <//>
          <${Field} label="无日志最大重试" hint="疑似卡死后自动重试的次数">
            <input class="input" type="number" value=${script.no_log_max_retries} onInput=${(e) => onChange({ no_log_max_retries: Number(e.target.value) })} />
          <//>
        </div>

        <div style=${{ borderTop: '1px solid var(--line)', paddingTop: '4px' }}>
          <${ToggleRow} title="启动时通知" on=${!!script.notify_start} onChange=${(value) => onChange({ notify_start: value })} />
          <${ToggleRow} title="完成时通知" on=${!!script.notify_done} onChange=${(value) => onChange({ notify_done: value })} />
        </div>
        <${Field} label="日志推送间隔（秒）" hint="0 表示不推送运行过程日志">
          <input class="input" type="number" value=${script.notify_log_interval} onInput=${(e) => onChange({ notify_log_interval: Number(e.target.value) })} />
        <//>

        <div class="rule-box">
          <div class="rule-head">
            <div class="grow">
              <div class="rule-title">规则来源</div>
              <div class="rule-desc">用按真实日志校准过的规则集一键填入下面三块，填完仍可逐项修改</div>
            </div>
            <${Icon} name="scroll-text" size=${14} />
          </div>
          <div class="row" style=${{ gap: '8px' }}>
            <select class="input grow" value=${rulePick} onChange=${(e) => setRulePick(e.target.value)}>
              <option value="">选择规则集…</option>
              ${availableProfiles.map((item) => html`<option value=${item.id} key=${item.id}>
                ${item.name}${item.error ? '（文件有误）' : ` · ${item.total} 条`}
              </option>`)}
            </select>
            <button class="btn" onClick=${fillFromProfile} disabled=${!rulePick || ruleBusy}>
              <${Icon} name=${ruleBusy ? 'loader-2' : 'download'} className=${ruleBusy ? 'spinner' : ''} />填入
            </button>
          </div>
          <div class="field-hint">
            ${!availableProfiles.length
              ? (gameValue
                  ? `当前游戏（${gameText}）还没有匹配的规则集，可到「日志规则」页加载支持该游戏的 .yml。`
                  : '还没有可用规则集，可到「日志规则」页加载 .yml 后再回来。')
              : gameValue
                ? `只显示支持「${gameText}」的规则集；填入会覆盖下面三块，但不保存。`
                : '为脚本设置游戏后，这里只显示支持该游戏的规则集；填入会覆盖下面三块，但不保存。'}
          </div>
        </div>

        <div class="rule-box">
          <div class="rule-head">
            <div class="grow">
              <div class="rule-title">任务级错误规则</div>
              <div class="rule-desc">
                命中错误日志后先宽限一段时间，期间脚本自行恢复就不算数；
                ${failFast.action === 'stop_task' ? '宽限结束仍命中则终止当前脚本' : '宽限结束仍命中只登记，脚本继续跑'}
              </div>
            </div>
            <${Switch} on=${!!failFast.enabled} onChange=${(value) => patchFailFast({ enabled: value })} />
          </div>
          <div class="field">
            <label class="field-label">命中之后</label>
            <div class="row" style=${{ gap: '8px', flexWrap: 'wrap' }}>
              <button
                class=${'chip-toggle' + (failFast.action !== 'stop_task' ? ' on' : '')}
                onClick=${() => patchFailFast({ action: 'report' })}
              >只登记，继续运行</button>
              <button
                class=${'chip-toggle' + (failFast.action === 'stop_task' ? ' on' : '')}
                onClick=${() => patchFailFast({ action: 'stop_task' })}
              >终止当前脚本</button>
            </div>
            <div class="field-hint">
              只登记：命中项写进运行报告，链上后面的脚本照常跑；
              终止：结束当前脚本并标记失败，适合「这一步失败后面全是白做」的场景。
            </div>
          </div>
          <${Field} label="宽限时间（秒）" hint="命中后等这么久，期间脚本自己恢复就当没发生">
            <input class="input" type="number" value=${failFast.grace_seconds} onInput=${(e) => patchFailFast({ grace_seconds: Number(e.target.value) })} />
          <//>
          <${Field} label="错误正则" hint="每行一条，例如 ${'\\[ERROR\\]\\[task_flow\\.py\\]'}">
            <textarea class="input mono" rows="3" value=${(failFast.error_patterns || []).join('\n')} onInput=${(e) => patchPatterns('error_patterns', e.target.value)} placeholder=${'\\[ERROR\\]'}></textarea>
          <//>
          <${Field} label="忽略正则" hint="每行一条，命中后不参与错误判定（噪音、框架已自愈的失败）">
            <textarea class="input mono" rows="2" value=${(failFast.ignore_patterns || []).join('\n')} onInput=${(e) => patchPatterns('ignore_patterns', e.target.value)} placeholder="DEBUG"></textarea>
          <//>
          ${(failFast.error_patterns || []).length === 0
            ? html`<div class="field-hint">错误正则为空时该规则不会生效。</div>`
            : null}
        </div>

        <div class="rule-box">
          <div class="rule-head">
            <div class="grow">
              <div class="rule-title">同行高频重复</div>
              <div class="rule-desc">同一行日志在时间窗内重复次数超过阈值时判定为打转</div>
            </div>
            <${Switch} on=${!!stall.enabled} onChange=${(value) => patchStall({ enabled: value })} />
          </div>
          <div class="grid-3">
            <${Field} label="时间窗（分钟）">
              <input class="input" type="number" value=${stall.window_minutes} onInput=${(e) => patchStall({ window_minutes: Number(e.target.value) })} />
            <//>
            <${Field} label="最少重复次数">
              <input class="input" type="number" value=${stall.min_repeats} onInput=${(e) => patchStall({ min_repeats: Number(e.target.value) })} />
            <//>
            <${Field} label="宽限（秒）">
              <input class="input" type="number" value=${stall.grace_seconds} onInput=${(e) => patchStall({ grace_seconds: Number(e.target.value) })} />
            <//>
          </div>
          <${Field} label="忽略正则" hint="命中该正则的日志行不参与打转统计">
            <input class="input mono" value=${stall.ignore_pattern || ''} onInput=${(e) => patchStall({ ignore_pattern: e.target.value })} placeholder="DEBUG" />
          <//>
          <${Field} label="忽略级别" hint="逗号分隔，例如 DBG,TRC；这些级别的刷屏不计入重复">
            <input
              class="input mono"
              value=${(stall.ignore_levels || []).join(',')}
              onInput=${(e) => patchStall({
                ignore_levels: e.target.value.split(',').map((item) => item.trim()).filter(Boolean),
              })}
              placeholder="DBG,TRC"
            />
          <//>
        </div>

        <div class="rule-box">
          <div class="rule-head">
            <div class="grow">
              <div class="rule-title">段内原地轮转</div>
              <div class="rule-desc">
                一段任务里走了很多步、位置却没变。步进达到阈值而推进不足，或同一步骤连续重复超限即判定
              </div>
            </div>
            <${Switch} on=${!!loop.enabled} onChange=${(value) => patchLoop({ enabled: value })} />
          </div>
          <div class="grid-3">
            <${Field} label="步进阈值">
              <input class="input" type="number" value=${loop.min_iterations} onInput=${(e) => patchLoop({ min_iterations: Number(e.target.value) })} />
            <//>
            <${Field} label="推进上限" hint="留空推进正则时按「不同步骤数」算">
              <input class="input" type="number" value=${loop.max_progress} onInput=${(e) => patchLoop({ max_progress: Number(e.target.value) })} />
            <//>
            <${Field} label="连续重复">
              <input class="input" type="number" value=${loop.same_state_repeats} onInput=${(e) => patchLoop({ same_state_repeats: Number(e.target.value) })} />
            <//>
          </div>
          <${ToggleRow}
            title="参与运行时终止"
            desc=${loop.runtime
              ? `命中后给 ${loop.grace_seconds} 秒，期间继续推进就撤销，仍无进展才终止`
              : '关闭时只在「日志规则」页的试跑里判定，运行时不终止'}
            on=${!!loop.runtime}
            onChange=${(value) => patchLoop({ runtime: value })}
          />
          <${Field} label="宽限（秒）">
            <input class="input" type="number" value=${loop.grace_seconds} onInput=${(e) => patchLoop({ grace_seconds: Number(e.target.value) })} />
          <//>
          <${Field} label="段起点正则" hint="一段任务从哪里开始，例如 task start:">
            <textarea class="input mono" rows="2" value=${loop.segment_start || ''} onInput=${(e) => patchLoop({ segment_start: e.target.value })} placeholder="task start:"></textarea>
          <//>
          <${Field} label="段终点正则" hint="可留空：下一段起点出现即视为上一段结束">
            <textarea class="input mono" rows="2" value=${loop.segment_end || ''} onInput=${(e) => patchLoop({ segment_end: e.target.value })} placeholder="task end:"></textarea>
          <//>
          <${Field} label="步进正则" hint="用捕获组 ( ) 取出当前步骤名，没有捕获组时只计步数">
            <textarea class="input mono" rows="2" value=${loop.step_pattern || ''} onInput=${(e) => patchLoop({ step_pattern: e.target.value })} placeholder="节点 (.+?) 返回状态"></textarea>
          <//>
          <${Field} label="推进正则" hint="留空表示用段内出现过的不同步骤数当推进量">
            <textarea class="input mono" rows="2" value=${loop.progress_pattern || ''} onInput=${(e) => patchLoop({ progress_pattern: e.target.value })} placeholder="留空即可"></textarea>
          <//>
          ${loop.enabled && (!(loop.segment_start || '').trim() || !(loop.step_pattern || '').trim())
            ? html`<div class="field-hint">段起点与步进正则必须都填，否则运行时不会启用轮转判定。</div>`
            : null}
        </div>
      </div>` : null}
    </div>

    <div class="drawer-foot">
      <button class="btn" onClick=${onRun}><${Icon} name="bug" />仅调试此脚本</button>
      <div class="grow"></div>
      <button class="btn btn-primary" onClick=${onClose}><${Icon} name="check" />完成</button>
    </div>
  </aside>`;
}

/* ------------------------------ 周计划 ------------------------------ */

function SchedulePage({ data, refresh, notify }) {
  const [schedule, setSchedule] = useState(data.run_config.schedule || {});
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setSchedule(data.run_config.schedule || {}); }, [data.run_config.schedule]);

  const chainNames = data.chains.map((item) => item.name);
  const fallbackChains = schedule.default || [];

  const toggleChain = (day, name) => {
    setSchedule((current) => {
      const list = current[day] ? current[day].slice() : [];
      const index = list.indexOf(name);
      if (index >= 0) list.splice(index, 1);
      else list.push(name);
      return { ...current, [day]: list };
    });
  };

  const save = async () => {
    setBusy(true);
    try {
      await api('/api/run-config/schedule', { method: 'PUT', body: JSON.stringify({ schedule }) });
      notify('周计划已保存');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const runPlan = async () => {
    try {
      await api('/api/plan/run', { method: 'POST' });
      notify('已启动今日计划');
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  return html`<div class="page">
    <div class="page-toolbar">
      <div class="toolbar-note">
        <${Icon} name="calendar-check" size=${15} />
        今日计划：${data.today_plan.length ? data.today_plan.join(' → ') : '未安排'}
      </div>
      <div class="grow"></div>
      <button class="btn" onClick=${runPlan}><${Icon} name="play" />立即补跑今日计划</button>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存周计划</button>
    </div>

    <${Card} title="周计划" sub="未单独配置的星期会回退到「默认」" icon="calendar-days" bodyClass="tight">
      ${data.options.weekdays.map((day) => {
        const own = schedule[day.value] || [];
        const usingDefault = day.value !== 'default' && own.length === 0 && fallbackChains.length > 0;
        const shown = usingDefault ? fallbackChains : own;
        const open = editing === day.value;
        return html`<div key=${day.value}>
          <div class="weekday-row">
            <div class="weekday-name">${day.label}</div>
            <div class="weekday-chips">
              ${shown.length === 0
                ? html`<span class="tag tag-empty">未配置</span>
                    <span class="small muted">${day.value === 'default' ? '未单独配置的星期沿用这里' : '当天不运行'}</span>`
                : html`${shown.map((name) => html`<span class="tag tag-pre" key=${name}>${name}</span>`)}
                    ${usingDefault
                      ? html`<span class="tag tag-off" title="当天未单独配置，实际运行「默认」配置">沿用默认</span>`
                      : null}`}
            </div>
            <button class="btn btn-ghost btn-sm" aria-expanded=${open} onClick=${() => setEditing(open ? null : day.value)}>
              <${Icon} name=${open ? 'chevron-up' : 'chevron-down'} />${open ? '收起' : '编辑'}
            </button>
          </div>
          ${open ? html`<div class="weekday-edit">
            ${usingDefault
              ? html`<span class="small muted" style=${{ width: '100%' }}>当天未单独配置，勾选脚本后才会覆盖「默认」配置。</span>`
              : null}
            ${chainNames.length === 0
              ? html`<span class="small muted">还没有脚本链，请先在「脚本链」页新建</span>`
              : chainNames.map((name) => html`<button
                  key=${name}
                  class=${'chip-toggle' + (own.includes(name) ? ' on' : '')}
                  onClick=${() => toggleChain(day.value, name)}
                >${own.includes(name) ? '✓ ' : ''}${name}</button>`)}
          </div>` : null}
        </div>`;
      })}
    <//>
  </div>`;
}

/* ------------------------------ 运行策略 ------------------------------ */

function PolicyPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.run_config);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setForm(data.run_config); }, [data.run_config]);

  const patch = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const patchRetry = (key, value) => setForm((current) => ({
    ...current, retry: { ...current.retry, [key]: value },
  }));

  const save = async () => {
    setBusy(true);
    try {
      const payload = await api('/api/run-config', {
        method: 'PUT',
        body: JSON.stringify({
          run_window: form.run_window,
          once_per_day: form.once_per_day,
          deadline_minutes: Number(form.deadline_minutes),
          scheduler_enabled: form.scheduler_enabled,
          scheduler_poll_seconds: Number(form.scheduler_poll_seconds),
          shutdown_after_run: Number(form.shutdown_after_run),
          on_locked: form.on_locked,
          keep_awake: form.keep_awake,
          run_as_sid: form.run_as_sid,
          run_as_sid_action: form.run_as_sid_action,
          autostart_enabled: form.autostart_enabled,
          privacy_screen_close_key: form.privacy_screen_close_key,
          retry: {
            max_rounds: Number(form.retry.max_rounds),
            delay_seconds: Number(form.retry.delay_seconds),
            only_failed: form.retry.only_failed,
            skip_on_config_error: form.retry.skip_on_config_error,
            skip_on_crash: form.retry.skip_on_crash,
          },
        }),
      });
      setForm(payload);
      notify(payload.issues.length ? `已保存，仍有 ${payload.issues.length} 项配置问题` : '运行策略已保存', payload.issues.length > 0);
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const startScheduler = async () => {
    try {
      await api('/api/scheduler/start', { method: 'POST' });
      notify('已拉起常驻调度进程');
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  return html`<div class="page">
    <div class="page-toolbar">
      <div class="toolbar-note">
        <${Icon} name="shield-check" size=${15} />
        修改后点击右侧保存写入 config/run.yml
      </div>
      <div class="grow"></div>
      <button class="btn" onClick=${startScheduler}><${Icon} name="power" />启动常驻调度</button>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>
    </div>

    ${form.issues && form.issues.length
      ? html`<div class="notice">
          <${Icon} name="alert-triangle" size=${15} />
          <div class="grow">${form.issues.join('\n')}</div>
        </div>`
      : null}

    <div class="grid-2">
      <${Card} title="时间与截止" icon="clock" sub="限制计划启动与结束的时机">
        <div class="grid-2">
          <${Field} label="时间窗开始" hint="留空表示不限时间">
            <input class="input mono" placeholder="04:15" value=${form.run_window.start} onInput=${(e) => patch('run_window', { ...form.run_window, start: e.target.value })} />
          <//>
          <${Field} label="时间窗结束">
            <input class="input mono" placeholder="06:15" value=${form.run_window.end} onInput=${(e) => patch('run_window', { ...form.run_window, end: e.target.value })} />
          <//>
          <${Field} label="全局截止（分钟）" hint="0 表示不限；与时间窗取较早值">
            <input class="input" type="number" value=${form.deadline_minutes} onInput=${(e) => patch('deadline_minutes', e.target.value)} />
          <//>
          <${Field} label="调度轮询间隔（秒）" hint="常驻调度检查是否到点的频率">
            <input class="input" type="number" value=${form.scheduler_poll_seconds} onInput=${(e) => patch('scheduler_poll_seconds', e.target.value)} />
          <//>
        </div>
      <//>

      <${Card} title="每日行为" icon="activity" sub="一天内的执行与电源行为">
        <${ToggleRow} title="每日只运行一次" desc="关闭后窗口内会反复执行" on=${form.once_per_day} onChange=${(value) => patch('once_per_day', value)} />
        <${ToggleRow} title="运行期保持唤醒" desc="阻止系统睡眠与熄屏，结束后自动恢复" on=${form.keep_awake} onChange=${(value) => patch('keep_awake', value)} />
        <${ToggleRow} title="启用内置常驻调度" on=${form.scheduler_enabled} onChange=${(value) => patch('scheduler_enabled', value)} />
        <${ToggleRow} title="随系统登录自启" desc="写入当前用户注册表 Run 项" on=${form.autostart_enabled} onChange=${(value) => patch('autostart_enabled', value)} />
        <div class="mt">
          <${Field} label="跑完关机延迟（秒）" hint="0 表示不关机">
            <input class="input" type="number" value=${form.shutdown_after_run} onInput=${(e) => patch('shutdown_after_run', e.target.value)} />
          <//>
        </div>
      <//>

      <${Card} title="重试策略" icon="rotate-cw" sub="脚本失败后的自动重跑">
        <div class="grid-2">
          <${Field} label="最大轮数" hint="0 表示不重试">
            <input class="input" type="number" value=${form.retry.max_rounds} onInput=${(e) => patchRetry('max_rounds', e.target.value)} />
          <//>
          <${Field} label="重试间隔（秒）">
            <input class="input" type="number" value=${form.retry.delay_seconds} onInput=${(e) => patchRetry('delay_seconds', e.target.value)} />
          <//>
        </div>
        <div class="mt">
          <${ToggleRow} title="只重跑失败脚本" on=${form.retry.only_failed} onChange=${(value) => patchRetry('only_failed', value)} />
          <${ToggleRow} title="配置错误不重试" on=${form.retry.skip_on_config_error} onChange=${(value) => patchRetry('skip_on_config_error', value)} />
          <${ToggleRow} title="环境崩溃不重试" on=${form.retry.skip_on_crash} onChange=${(value) => patchRetry('skip_on_crash', value)} />
        </div>
      <//>

      <${Card} title="安全与账户" icon="lock" sub="锁屏处理与运行账户校验">
        <div class="grid-2">
          <${Field} label="锁屏处理">
            <select class="input" value=${form.on_locked} onChange=${(e) => patch('on_locked', e.target.value)}>
              ${data.options.lock_policies.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
            </select>
          <//>
          <${Field} label="隐私屏关闭热键" hint="Ctrl+Alt+Shift + 此键">
            <input class="input mono" value=${form.privacy_screen_close_key} onInput=${(e) => patch('privacy_screen_close_key', e.target.value)} />
          <//>
        </div>
        <div class="grid-2 mt">
          <${Field} label="期望运行账户 SID" hint="留空表示不校验">
            <input class="input mono" value=${form.run_as_sid} placeholder="S-1-5-21-..." onInput=${(e) => patch('run_as_sid', e.target.value)} />
          <//>
          <${Field} label="账户不一致时">
            <select class="input" value=${form.run_as_sid_action} onChange=${(e) => patch('run_as_sid_action', e.target.value)}>
              <option value="abort">立即退出（退出码 2）</option>
              <option value="warn">仅告警继续</option>
            </select>
          <//>
        </div>
      <//>
    </div>
  </div>`;
}

/* ------------------------------ 运行报告 ------------------------------ */

const VERDICT_LABEL = {
  ok: '成功',
  fail: '失败',
  config_error: '配置错误',
  env_error: '环境异常',
  missing: '未找到',
};

const VERDICT_BADGE = {
  ok: 'badge-ok',
  config_error: 'badge-cfg',
  env_error: 'badge-cfg',
};

const AUTO_REFRESH_KEY = 'qjl-log-auto';
const AUTO_REFRESH_MS = 3000;

function ReportPage({ data, refresh, notify }) {
  const [payload, setPayload] = useState(data.report);
  const [logs, setLogs] = useState(data.logs);
  const [auto, setAuto] = useState(() => {
    try { return localStorage.getItem(AUTO_REFRESH_KEY) !== 'off'; } catch (e) { return true; }
  });
  const [pulledAt, setPulledAt] = useState('');
  const logRef = useRef(null);
  const atBottomRef = useRef(true);

  useEffect(() => { setPayload(data.report); setLogs(data.logs); }, [data.report, data.logs]);

  const reload = async () => {
    try {
      const [report, log] = await Promise.all([
        api('/api/report'),
        api('/api/logs?lines=200'),
      ]);
      setPayload(report);
      setLogs(log);
      notify('已刷新');
    } catch (e) {
      notify(e.message, true);
    }
  };

  const pull = useCallback(async () => {
    const [report, log] = await Promise.all([
      api('/api/report'),
      api('/api/logs?lines=200'),
    ]);
    setPayload(report);
    setLogs(log);
    setPulledAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
  }, []);

  // 自动刷新：静默拉取，失败不弹提示；标签页切到后台时暂停，回来立刻补一次
  useEffect(() => {
    if (!auto) return undefined;
    const tick = () => {
      if (document.hidden) return;
      pull().catch(() => { /* 下一轮再试 */ });
    };
    // 进页面先拉一次，别等第一个 3 秒
    pull().catch(() => { /* 首拉失败等下一次定时 */ });
    const timer = setInterval(tick, AUTO_REFRESH_MS);
    const onVisible = () => { if (!document.hidden) tick(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [auto, pull]);

  // 新日志到达时贴底；用户自己往上翻看历史时不打扰
  useEffect(() => {
    const el = logRef.current;
    if (el && atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [logs.lines.length, logs.file]);

  const toggleAuto = () => setAuto((on) => {
    const next = !on;
    try { localStorage.setItem(AUTO_REFRESH_KEY, next ? 'on' : 'off'); } catch (e) { /* 忽略存储失败 */ }
    return next;
  });

  const running = !!(data.status && data.status.launched && data.status.launched.pid);
  const report = payload.report;
  const summary = (report && report.summary) || {};

  const stats = [
    ['总计', summary.total || 0, ''],
    ['成功', summary.ok || 0, 'ok'],
    ['失败', summary.fail || 0, 'fail'],
    ['配置错误', summary.config_error || 0, 'cfg'],
    ['环境异常', summary.env_error || 0, 'cfg'],
    ['未找到', summary.missing || 0, ''],
  ];

  return html`<div class="page">
    <${Card}
      title=${report ? report.chain : '运行报告'}
      sub=${payload.updated_at ? `最近更新 ${payload.updated_at}` : '运行一次脚本链后这里会显示汇总报告'}
      icon="clipboard-list"
      actions=${html`<button class="btn" onClick=${reload}><${Icon} name="refresh-cw" />刷新</button>`}
    >
      ${!report
        ? html`<${EmptyState} icon="file-text" title="还没有运行记录" desc="在「脚本链」页运行一次后即可查看" />`
        : html`<div>
            <div class="row" style=${{ marginBottom: '14px', flexWrap: 'wrap' }}>
              <span class=${'badge ' + (report.exit_code === 0 ? 'badge-ok' : 'badge-fail')}>
                退出码 ${report.exit_code}${report.deadline_hit ? ' · 到达截止' : ''}
              </span>
              <span class="small muted mono">${report.started_at || '-'} → ${report.ended_at || '-'}</span>
              <span class="small muted">耗时 ${report.duration_sec}s</span>
            </div>
            <div class="stat-grid">
              ${stats.map(([label, value, tone]) => html`<div class=${'stat' + (tone ? ' ' + tone : '')} key=${label}>
                <div class="stat-num">${value}</div>
                <div class="stat-label">${label}</div>
              </div>`)}
            </div>
          </div>`}
    <//>

    ${report ? html`<${Card} title="脚本明细" icon="list" bodyClass="tight">
      ${(report.scripts || []).map((item) => html`<div class="row" style=${{ padding: '10px 12px', borderBottom: '1px solid var(--line)' }} key=${item.index}>
        <div class="script-index">${item.index + 1}</div>
        <div class="grow" style=${{ minWidth: 0 }}>
          <div style=${{ fontSize: '12.5px' }}>${item.display_name || item.script_path || '(未命名)'}</div>
          <div class="small muted">${item.end_reason || ''} ${item.error || ''}</div>
        </div>
        <span class=${'badge ' + (VERDICT_BADGE[item.verdict] || 'badge-fail')}>${VERDICT_LABEL[item.verdict] || item.verdict}</span>
      </div>`)}
    <//>` : null}

    <${Card}
      title="最近日志"
      sub=${[
        logs.file || '',
        auto ? `每 ${AUTO_REFRESH_MS / 1000} 秒自动拉取` : '自动刷新已关闭',
        pulledAt ? `最近 ${pulledAt}` : '',
      ].filter(Boolean).join(' · ')}
      icon="terminal"
      actions=${html`<div class="row" style=${{ gap: '6px' }}>
        ${running ? html`<span class="chip chip-live"><span class="dot"></span>运行中</span>` : null}
        <button
          class=${'chip-toggle' + (auto ? ' on' : '')}
          onClick=${toggleAuto}
          title=${auto ? '点击暂停自动刷新' : '点击开启自动刷新'}
        ><${Icon} name=${auto ? 'pause' : 'play'} size=${12} />自动刷新</button>
        <button class="btn btn-ghost btn-sm" onClick=${reload}><${Icon} name="refresh-cw" />刷新</button>
      </div>`}
      bodyClass="tight"
    >
      <div
        class="log"
        ref=${logRef}
        onScroll=${(e) => {
          const el = e.currentTarget;
          atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
      >${logs.lines.length ? logs.lines.join('\n') : '暂无日志'}</div>
    <//>
  </div>`;
}

/* ------------------------------ 消息推送（推送中心） ------------------------------ */

const CHANNEL_GROUPS = ['全部', '常用', '消息机器人', '推送服务'];

const PREFERENCE_DEFAULTS = { send_image: true, proxy: 'NONE', proxy_options: [] };

function ChannelField({ field, value, onChange }) {
  const label = html`<label class="field-label">
    ${field.label}${field.required ? html`<span class="muted" style=${{ marginLeft: '3px' }}>*</span>` : null}
  </label>`;

  let control;
  if (field.type === 'combo') {
    control = html`<select class="input mono" value=${value || field.default || ''} onChange=${(e) => onChange(e.target.value)}>
      ${field.options.map((option) => html`<option value=${option} key=${option}>${option}</option>`)}
    </select>`;
  } else if (field.type === 'key_value' || field.type === 'code_editor') {
    const rows = field.type === 'code_editor' ? 4 : 2;
    control = html`<textarea
      class="input mono"
      rows=${rows}
      value=${value || ''}
      placeholder=${field.placeholder || (field.type === 'key_value' ? '{"Header": "Value"}' : '')}
      onInput=${(e) => onChange(e.target.value)}
    ></textarea>`;
  } else {
    control = html`<input
      class="input mono"
      type="text"
      autocomplete="off"
      spellcheck=${false}
      value=${value || ''}
      placeholder=${field.placeholder || ''}
      onInput=${(e) => onChange(e.target.value)}
    />`;
  }

  return html`<div class="field">
    ${label}
    ${control}
  </div>`;
}

function ChannelTile({ channel, onOpen }) {
  return html`<button
    class=${'chan-card' + (channel.configured ? ' on' : '')}
    onClick=${onOpen}
    title=${`配置 ${channel.name}`}
  >
    <span class="chan-icon">
      <${Icon} name=${channel.icon} size=${15} />
      ${channel.configured ? html`<span class="chan-state" title="配置完整，会参与推送"></span>` : null}
    </span>
    <span class="grow" style=${{ minWidth: 0 }}>
      <span class="chan-name">${channel.name}</span>
      <span class="chan-hint">${channel.hint || channel.id}</span>
    </span>
    <span class="chan-side">
      <span class="chan-chevron"><${Icon} name="chevron-right" size=${15} /></span>
    </span>
  </button>`;
}

function ChannelDrawer({ channel, values, dirty, saving, testing, result, onChange, onSave, onTest, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const missing = channel.missing || [];

  return html`<div class="drawer-backdrop" onClick=${onClose}></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label=${`配置 ${channel.name}`} onClick=${(e) => e.stopPropagation()}>
    <div class="drawer-head">
      <div class="drawer-title-row">
        <span class="chan-icon" style=${{ width: '38px', height: '38px', borderRadius: '12px' }}>
          <${Icon} name=${channel.icon} size=${18} />
        </span>
        <div class="grow" style=${{ minWidth: 0 }}>
          <div class="drawer-title">${channel.name}</div>
          <div class="drawer-sub">${channel.hint || channel.id}</div>
        </div>
        <button class="icon-btn" onClick=${onClose} title="关闭"><${Icon} name="x" /></button>
      </div>
      <div class="row" style=${{ marginTop: '12px', gap: '6px', flexWrap: 'wrap' }}>
        ${channel.configured
          ? html`<span class="tag tag-ok">可用</span>`
          : html`<span class="tag tag-empty">未配置</span>`}
        <span class="tag">${channel.id}</span>
        <span class="tag">${channel.group}</span>
      </div>
    </div>

    <div class="drawer-body">
      ${!channel.configured && missing.length
        ? html`<div class="chan-next">
            <${Icon} name="circle-alert" size=${14} />
            <span>还需填写：${missing.join('、')}</span>
          </div>`
        : null}
      ${!channel.configured && channel.reason && !missing.length
        ? html`<div class="chan-next">
            <${Icon} name="circle-alert" size=${14} />
            <span>${channel.reason}</span>
          </div>`
        : null}

      ${channel.fields.map((field) => html`<${ChannelField}
        key=${field.key}
        field=${field}
        value=${values[field.key]}
        onChange=${(next) => onChange(field.key, next)}
      />`)}

      ${result ? html`<div class=${'test-result ' + (result.ok ? 'ok' : 'err')}>
        <${Icon} name=${result.ok ? 'check-circle-2' : 'alert-circle'} size=${14} />
        <span class="grow">${result.message}${result.elapsed_ms ? `（${result.elapsed_ms} ms）` : ''}</span>
      </div>` : null}

      <div class="notice hint">
        <${Icon} name="info" size=${14} />
        <span class="grow">字段保存在 config/push.yml。必填项齐全后该渠道自动参与推送，可先「发送测试」确认能收到。</span>
      </div>
    </div>

    <div class="drawer-foot">
      <button class="btn" onClick=${onTest} disabled=${testing}>
        <${Icon} name=${testing ? 'loader-2' : 'send'} className=${testing ? 'spinner' : ''} />发送测试
      </button>
      <div class="grow"></div>
      <button class="btn" onClick=${onClose}>关闭</button>
      <button class="btn btn-primary" onClick=${onSave} disabled=${saving || !dirty}>
        <${Icon} name="save" />${dirty ? '保存' : '已保存'}
      </button>
    </div>
  </aside>`;
}

function NotifyPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.notify);
  const [prefs, setPrefs] = useState({ ...PREFERENCE_DEFAULTS, ...(data.notify.preferences || {}) });
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [group, setGroup] = useState('全部');
  const [query, setQuery] = useState('');
  const [openId, setOpenId] = useState('');
  const [drafts, setDrafts] = useState({});
  const [savingId, setSavingId] = useState('');
  const [testingId, setTestingId] = useState('');
  const [results, setResults] = useState({});
  const [saving, setSaving] = useState(false);
  const [testingAll, setTestingAll] = useState(false);
  const [allResult, setAllResult] = useState(null);

  useEffect(() => {
    setForm(data.notify);
    setPrefs({ ...PREFERENCE_DEFAULTS, ...(data.notify.preferences || {}) });
  }, [data.notify]);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const payload = await api('/api/push/channels?wait=25');
      setCatalog(payload);
    } catch (e) {
      setLoadError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadChannels(); }, [loadChannels]);

  const channels = (catalog && catalog.channels) || [];
  const summary = (catalog && catalog.summary) || { total: 0, configured: 0 };

  const saveBasics = async () => {
    setSaving(true);
    try {
      const saved = await api('/api/notify', {
        method: 'PUT',
        body: JSON.stringify({
          title: form.title,
          enable_notify: form.enable_notify,
          enable_before_notify: form.enable_before_notify,
        }),
      });
      await api('/api/push/preferences', {
        method: 'PUT',
        body: JSON.stringify({ send_image: prefs.send_image, proxy: prefs.proxy }),
      });
      setForm(saved);
      setPrefs({ ...PREFERENCE_DEFAULTS, ...(saved.preferences || {}) });
      notify('推送设置已保存，下一次运行生效');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setSaving(false);
    }
  };

  const valuesOf = (channel) => drafts[channel.id] || channel.values;

  const patchChannel = (channel, key, value) => {
    setDrafts((current) => ({
      ...current,
      [channel.id]: { ...valuesOf(channel), [key]: value },
    }));
  };

  const isDirty = (channel) => {
    const draft = drafts[channel.id];
    if (!draft) return false;
    return channel.fields.some((field) => (draft[field.key] || '') !== (channel.values[field.key] || ''));
  };

  const saveChannel = async (channel) => {
    setSavingId(channel.id);
    try {
      const payload = await api(`/api/push/channels/${encodeURIComponent(channel.id)}`, {
        method: 'PUT',
        body: JSON.stringify({ values: valuesOf(channel) }),
      });
      setCatalog(payload);
      setDrafts((current) => {
        const next = { ...current };
        delete next[channel.id];
        return next;
      });
      notify(`${channel.name} 已保存`);
    } catch (e) {
      notify(e.message, true);
    } finally {
      setSavingId('');
    }
  };

  const testChannel = async (channel) => {
    setTestingId(channel.id);
    try {
      const payload = await api('/api/push/test', {
        method: 'POST',
        body: JSON.stringify({ channel_id: channel.id, title: form.title }),
      });
      setResults((current) => ({ ...current, [channel.id]: payload }));
      notify(payload.ok ? `${channel.name} 测试推送成功` : `${channel.name} 推送失败`, !payload.ok);
    } catch (e) {
      setResults((current) => ({ ...current, [channel.id]: { ok: false, message: e.message } }));
      notify(e.message, true);
    } finally {
      setTestingId('');
    }
  };

  const testAll = async () => {
    setTestingAll(true);
    setAllResult(null);
    try {
      const payload = await api('/api/push/test', {
        method: 'POST',
        body: JSON.stringify({ channel_id: null, title: form.title }),
      });
      setAllResult(payload);
      notify(payload.ok ? '已向全部可用渠道推送' : `推送失败：${payload.message}`, !payload.ok);
    } catch (e) {
      setAllResult({ ok: false, message: e.message });
      notify(e.message, true);
    } finally {
      setTestingAll(false);
    }
  };

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return channels.filter((item) => {
      if (group !== '全部' && item.group !== group) return false;
      if (!keyword) return true;
      return [item.name, item.id, item.hint]
        .some((text) => String(text || '').toLowerCase().includes(keyword));
    });
  }, [channels, group, query]);

  const groupCount = (name) => (name === '全部'
    ? channels.length
    : channels.filter((item) => item.group === name).length);

  const openChannel = channels.find((item) => item.id === openId) || null;
  const ready = !loading && !loadError;

  return html`<div class="page">
    <div class="push-hero">
      <div class="push-hero-main">
        <span class="push-hero-icon"><${Icon} name="bell-ring" size=${20} /></span>
        <div>
          <div class="push-hero-title">推送中心</div>
          <div class="push-hero-sub">
            ${ready
              ? `已接通 ${summary.configured} / ${summary.total} 个渠道 · 保存后下一次运行生效`
              : '运行进度、脚本失败与整轮汇总，都可以推到手机上'}
          </div>
        </div>
      </div>
      <div class="push-hero-metrics">
        <span class="chip">
          <${Icon} name="radio" size=${13} />${ready ? `已配置 ${summary.configured}` : '渠道加载中'}
        </span>
        <span class="chip">
          <${Icon} name="image" size=${13} />${prefs.send_image ? '附带截图' : '纯文本'}
        </span>
        <button class="btn" onClick=${testAll} disabled=${testingAll || !ready || summary.configured === 0}>
          <${Icon} name=${testingAll ? 'loader-2' : 'send'} className=${testingAll ? 'spinner' : ''} />全部测试
        </button>
      </div>
    </div>

    ${allResult ? html`<div class=${'test-result ' + (allResult.ok ? 'ok' : 'err')}>
      <${Icon} name=${allResult.ok ? 'check-circle-2' : 'alert-circle'} size=${15} />
      <span class="grow">${allResult.ok ? '全部可用渠道已收到测试消息' : allResult.message}</span>
    </div>` : null}

    <div class="grid-2">
      <${Card}
        title="基础设置"
        sub="通知标题与总开关"
        icon="bell"
        actions=${html`<button class="btn btn-primary btn-sm" onClick=${saveBasics} disabled=${saving}>
          <${Icon} name="save" size=${13} />保存
        </button>`}
      >
        <${Field} label="通知标题" hint="推送消息的标题前缀，建议写清是哪台机器">
          <input class="input" value=${form.title} onInput=${(e) => setForm({ ...form, title: e.target.value })} placeholder="一条龙运行通知" />
        <//>
        <div class="mt">
          <${ToggleRow}
            title="启用推送"
            desc="关闭后不再发送任何通知"
            on=${form.enable_notify}
            onChange=${(value) => setForm({ ...form, enable_notify: value })}
          />
          <${ToggleRow}
            title="启动前通知"
            desc="每轮计划开始前先推一条，方便判断有没有跑起来"
            on=${form.enable_before_notify}
            onChange=${(value) => setForm({ ...form, enable_before_notify: value })}
          />
          <${ToggleRow}
            title="消息附带截图"
            desc="渠道不支持图片时会自动降级为纯文本"
            on=${prefs.send_image}
            onChange=${(value) => setPrefs({ ...prefs, send_image: value })}
          />
        </div>
        <${Field} label="网络代理" hint="渠道在国内无法直连时改用它">
          <select class="input" value=${prefs.proxy} onChange=${(e) => setPrefs({ ...prefs, proxy: e.target.value })}>
            ${prefs.proxy_options.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>
      <//>

      <${Card} title="什么时候推" sub="实际推送时机由运行器决定" icon="clock">
        <div class="push-timing">
          ${[
            ['bell-ring', '计划开始前', '开关打开时，每轮计划启动先发一条'],
            ['triangle-alert', '脚本判失败', '命中错误规则后立刻推送，不必等整轮结束'],
            ['clipboard-list', '每轮结束汇总', '链跑完后推送退出码、失败脚本与耗时'],
            ['terminal', '运行过程日志', '按脚本的「日志推送间隔」推送，0 表示关闭'],
          ].map(([icon, title, desc]) => html`<div class="push-timing-row" key=${title}>
            <span class="push-timing-icon"><${Icon} name=${icon} size=${14} /></span>
            <span class="grow">
              <span class="push-timing-title">${title}</span>
              <span class="push-timing-desc" style=${{ display: 'block' }}>${desc}</span>
            </span>
          </div>`)}
        </div>
        <div class="notice hint" style=${{ marginTop: '10px' }}>
          <${Icon} name="info" size=${14} />
          <span class="grow">单脚本的「启动/完成通知」与「日志间隔」在 脚本链 → 脚本 → 高级 中配置。</span>
        </div>
      <//>
    </div>

    <${Card}
      title="推送渠道"
      sub=${ready ? `共 ${summary.total} 个渠道，可同时启用多个` : '正在加载渠道配置…'}
      icon="send"
      bodyClass="tight"
      actions=${html`
        <div class="search">
          <${Icon} name="search" size=${14} />
          <input placeholder="搜索渠道" value=${query} onInput=${(e) => setQuery(e.target.value)} />
        </div>
      `}
    >
      <div class="tabs" style=${{ padding: '2px 2px 12px' }}>
        <div class="seg">
          ${CHANNEL_GROUPS.map((name) => html`<button
            key=${name}
            class=${'seg-item' + (group === name ? ' on' : '')}
            onClick=${() => setGroup(name)}
          >${name}${ready ? html`<span class="muted" style=${{ marginLeft: '4px' }}>${groupCount(name)}</span>` : null}</button>`)}
        </div>
        <div class="grow"></div>
        <button class="btn btn-ghost btn-sm" onClick=${loadChannels} disabled=${loading}>
          <${Icon} name="refresh-cw" size=${13} />刷新
        </button>
      </div>

      ${loading
        ? html`<div class="chan-grid">
            ${[0, 1, 2, 3].map((index) => html`<${SkelCard} key=${index} />`)}
          </div>`
        : loadError
          ? html`<${EmptyState} icon="plug-zap" title="推送渠道加载失败" desc=${loadError} />`
          : visible.length === 0
            ? html`<${EmptyState} icon="search" title="没有匹配的渠道" desc="换个关键词，或切到「全部」分组" />`
            : html`<div class="chan-grid">
                ${visible.map((channel) => html`<${ChannelTile}
                  key=${channel.id}
                  channel=${channel}
                  onOpen=${() => setOpenId(channel.id)}
                />`)}
              </div>`}

      <div class="notice hint" style=${{ marginTop: '12px' }}>
        <${Icon} name="info" size=${14} />
        <span class="grow">配置写入 config/push.yml；只要必填项齐全，该渠道就会参与推送。测试推送不会影响正在运行的脚本链。</span>
      </div>
    <//>

    ${openChannel ? html`<${ChannelDrawer}
      key=${openChannel.id}
      channel=${openChannel}
      values=${valuesOf(openChannel)}
      dirty=${isDirty(openChannel)}
      saving=${savingId === openChannel.id}
      testing=${testingId === openChannel.id}
      result=${results[openChannel.id]}
      onChange=${(key, value) => patchChannel(openChannel, key, value)}
      onSave=${() => saveChannel(openChannel)}
      onTest=${() => testChannel(openChannel)}
      onClose=${() => setOpenId('')}
    />` : null}
  </div>`;
}

/* ------------------------------ 日志规则 ------------------------------ */

const RULE_GROUPS = [
  ['fatal', '致命', '出现即整轮无效，直接终止'],
  ['task', '任务级', '当前任务失败，跳过继续下一个'],
  ['ignore', '忽略', '噪音，或框架已自愈的失败，命中不终止'],
];

const LOG_FORMAT_LABEL = {
  maa: 'MAA（MaaFW）',
  one_dragon: '一条龙（one_dragon）',
  bettergi: 'BetterGI',
  mixed: '混合格式',
  plain: '纯文本',
};

function RuleRow({ rule, hit }) {
  const [open, setOpen] = useState(false);
  const hasHint = !!rule.hint;
  return html`<div
    class=${'rule-row' + (open ? ' open' : '') + (hasHint ? ' clickable' : '')}
    onClick=${hasHint ? () => setOpen(!open) : null}
  >
    <div class="rule-head">
      <span class=${'rule-tag ' + rule.level}>${rule.level_label}</span>
      <span class="rule-name">${rule.name}</span>
      <span class="rule-action">${rule.action_label}</span>
      <span class="rule-hits">
        ${hit != null
          ? html`<span class=${hit > 0 ? 'hit-on' : ''}>试跑 ${hit}</span>`
          : (rule.hits != null ? `样本 ${rule.hits}` : '')}
        ${hasHint ? html`<${Icon} name="chevron-down" size=${12} className="rule-chevron" />` : null}
      </span>
    </div>
    <code class="rule-match">${rule.match}</code>
    ${open && hasHint ? html`<div class="rule-hint">${rule.hint}</div>` : null}
  </div>`;
}

function ParamGrid({ items }) {
  return html`<div class="param-grid">
    ${items.map(([label, value]) => html`<div class="param" key=${label}>
      <div class="param-value">${value}</div>
      <div class="param-label">${label}</div>
    </div>`)}
  </div>`;
}

function RulesPage({ data, notify }) {
  const [profiles, setProfiles] = useState([]);
  const [ruleDir, setRuleDir] = useState('');
  const [game, setGame] = useState('');
  const [activeId, setActiveId] = useState('');
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [logPath, setLogPath] = useState('');
  const [probe, setProbe] = useState(null);
  const [probing, setProbing] = useState(false);
  const [chainName, setChainName] = useState('');
  const [chainScripts, setChainScripts] = useState([]);
  const [scriptIndex, setScriptIndex] = useState(0);
  const [applying, setApplying] = useState(false);

  const gameOptions = data.options.game || [];
  const gameLabel = (value) => {
    const hit = gameOptions.find((item) => item.value === value);
    return hit ? hit.label : value;
  };
  // 游戏选择器只列出「确实有规则集支持」的游戏，避免一堆空选项
  const supportedGames = useMemo(() => {
    const used = [];
    profiles.forEach((item) => (item.games || []).forEach((value) => {
      if (!used.includes(value)) used.push(value);
    }));
    const known = gameOptions.filter((item) => used.includes(item.value));
    const unknown = used.filter((value) => !gameOptions.some((item) => item.value === value));
    return [...known, ...unknown.map((value) => ({ label: value, value }))];
  }, [profiles, data.options.game]);
  const visibleProfiles = game
    ? profiles.filter((item) => profileSupportsGame(item, game))
    : profiles;

  const loadProfile = async (id) => {
    if (!id) return;
    setActiveId(id);
    setLoading(true);
    setProbe(null);
    try {
      const payload = await api('/api/rules/' + encodeURIComponent(id));
      setProfile(payload);
      // 切规则集时把日志目录跟着换过去，省得每次手填
      const logDir = payload.source && payload.source.log_dir;
      if (logDir) setLogPath(logDir);
    } catch (e) {
      notify(e.message, true);
      setProfile(null);
    } finally {
      setLoading(false);
    }
  };

  const pickGame = (value) => {
    setGame(value);
    const next = value
      ? profiles.filter((item) => profileSupportsGame(item, value))
      : profiles;
    if (next.some((item) => item.id === activeId)) return;
    if (next.length) {
      loadProfile(next[0].id);
    } else {
      setActiveId('');
      setProfile(null);
      setProbe(null);
    }
  };

  useEffect(() => {
    (async () => {
      try {
        const payload = await api('/api/rules');
        const list = payload.profiles || [];
        setProfiles(list);
        setRuleDir(payload.dir || '');
        // 先选游戏：默认落到第一个声明了游戏的规则集，再加载该游戏下的第一条
        const withGame = list.find((item) => (item.games || []).length);
        const initialGame = withGame ? withGame.games[0] : '';
        setGame(initialGame);
        const visible = initialGame
          ? list.filter((item) => profileSupportsGame(item, initialGame))
          : list;
        const first = visible[0];
        if (first) {
          const source = first.source || {};
          if (source.log_dir) setLogPath(source.log_dir);
          await loadProfile(first.id);
        } else {
          setLoading(false);
        }
      } catch (e) {
        notify(e.message, true);
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!chainName) {
      setChainScripts([]);
      return;
    }
    (async () => {
      try {
        const payload = await api('/api/chains/' + encodeURIComponent(chainName));
        setChainScripts(payload.scripts || []);
        setScriptIndex(0);
      } catch (e) {
        setChainScripts([]);
      }
    })();
  }, [chainName]);

  const importFile = async () => {
    const path = await pickPath('pick_file', '');
    if (!path) return;
    setBusy(true);
    try {
      const payload = await api('/api/rules/import', {
        method: 'POST',
        body: JSON.stringify({ path, overwrite: true, game: game || null }),
      });
      setProfiles(payload.profiles || []);
      const imported = payload.profile;
      // 导入成功后把游戏切到规则集支持的游戏，保证详情与列表一致
      if ((imported.games || []).length) setGame(imported.games[0]);
      notify(`已加载规则集：${imported.name}`);
      await loadProfile(imported.id);
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const runProbe = async () => {
    if (!profile) return;
    if (!logPath.trim()) {
      notify('先选择日志文件或目录', true);
      return;
    }
    setProbing(true);
    try {
      setProbe(await api('/api/rules/probe', {
        method: 'POST',
        body: JSON.stringify({ profile_id: profile.id, log_path: logPath.trim() }),
      }));
    } catch (e) {
      notify(e.message, true);
    } finally {
      setProbing(false);
    }
  };

  const applyRules = async () => {
    if (!profile || !chainName) {
      notify('先选择规则集与脚本链', true);
      return;
    }
    setApplying(true);
    try {
      const payload = await api('/api/rules/apply', {
        method: 'POST',
        body: JSON.stringify({ profile_id: profile.id, chain: chainName, index: scriptIndex }),
      });
      const a = payload.applied;
      notify(
        `已写入「${a.script}」：${a.error_patterns} 条错误规则、${a.ignore_patterns} 条忽略规则`
        + (a.fail_fast_action === 'stop_task' ? '（命中终止）' : '（命中只登记）')
        + (a.loop_enabled ? '、段内轮转判定' : ''),
      );
    } catch (e) {
      notify(e.message, true);
    } finally {
      setApplying(false);
    }
  };

  const hitMap = useMemo(() => {
    const map = {};
    (probe ? probe.rules : []).forEach((item) => { map[item.id] = item.hits; });
    return map;
  }, [probe]);

  const chains = data.chains || [];

  if (loading && !profile) {
    return html`<div class="page"><${LoadingBlock} text="读取规则集…" /></div>`;
  }

  return html`<div class="page">
    <${Card}
      title="规则集"
      sub=${ruleDir || '规则文件目录'}
      icon="scroll-text"
    >
      <div class="rule-game-bar">
        <span class="rule-game-label">游戏</span>
        <select class="input rule-game-select" value=${game} onChange=${(e) => pickGame(e.target.value)}>
          <option value="">全部游戏</option>
          ${supportedGames.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
        </select>
        <span class="small muted grow">先选游戏，再加载对应的规则集；未声明游戏的规则集归为「通用」</span>
      </div>

      ${profiles.length === 0
        ? html`<${EmptyState} icon="scroll-text" title="还没有规则文件" desc="在下方加载一个 .yml 规则集，或把文件放进规则目录后刷新" />`
        : visibleProfiles.length === 0
          ? html`<${EmptyState} icon="filter" title="该游戏还没有规则集" desc="换一个游戏，或在下方加载支持该游戏的 .yml 规则集" />`
          : html`<div class="profile-list">
              ${visibleProfiles.map((item) => html`<button
                key=${item.id}
                class=${'profile-item' + (item.id === activeId ? ' active' : '')}
                onClick=${() => loadProfile(item.id)}
              >
                <span class="profile-name">${item.name}</span>
                <span class="profile-meta">
                  ${item.error
                    ? html`<span class="tag tag-empty">文件有误</span>`
                    : html`<span>${(item.games || []).length ? item.games.map(gameLabel).join(' / ') : '通用'} · ${item.total} 条规则</span>`}
                </span>
              </button>`)}
            </div>`}

      ${profile ? html`<div class="profile-desc">
        <div class="small">${profile.description}</div>
        <div class="profile-chips">
          <span class="chip"><${Icon} name="gamepad-2" size=${12} />${(profile.games || []).length
            ? profile.games.map(gameLabel).join(' / ')
            : '通用'}</span>
          ${profile.source && profile.source.files
            ? html`<span class="chip"><${Icon} name="file-code-2" size=${12} />${profile.source.files} 份日志</span>`
            : null}
          ${profile.source && profile.source.records
            ? html`<span class="chip"><${Icon} name="list-checks" size=${12} />${profile.source.records} 条记录</span>`
            : null}
          <span class="chip"><${Icon} name="shield-alert" size=${12} />致命 ${profile.levels.fatal}</span>
          <span class="chip"><${Icon} name="circle-alert" size=${12} />任务级 ${profile.levels.task}</span>
          <span class="chip"><${Icon} name="bug" size=${12} />忽略 ${profile.levels.ignore}</span>
        </div>
      </div>` : null}

      <div class="rule-import-row">
        <span class="small muted grow">导入 .yml 规则集文件，会按当前所选游戏校验</span>
        <button class="btn" onClick=${importFile} disabled=${busy}>
          <${Icon} name=${busy ? 'loader-2' : 'upload'} className=${busy ? 'spinner' : ''} />加载规则文件
        </button>
      </div>
    <//>

    ${profile && profile.error
      ? html`<${Card} title="文件有问题" icon="circle-alert">
          <div class="notice danger">${profile.error}</div>
        <//>`
      : null}

    ${profile && !profile.error ? html`<${Card}
      title="任务级错误规则"
      sub=${(profile.fail_fast && profile.fail_fast.action === 'stop_task'
        ? '命中错误日志并过宽限后终止当前脚本'
        : '命中错误日志只登记、不终止，登记项写进运行报告')
        + '；ignore 规则参与过滤但不终止'}
      icon="circle-alert"
    >
      <div class="rule-groups">
        ${RULE_GROUPS.map(([level, label, desc]) => {
          const items = (profile.rules || []).filter((rule) => rule.level === level);
          if (!items.length) return null;
          return html`<div class="rule-group" key=${level}>
            <div class="rule-group-title">${label} · ${desc}<span class="rule-count">${items.length}</span></div>
            <div class="rule-list">
              ${items.map((rule) => html`<${RuleRow} key=${rule.id} rule=${rule} hit=${hitMap[rule.id]} />`)}
            </div>
          </div>`;
        })}
      </div>
    <//>` : null}

    ${profile && !profile.error ? html`<${Card}
      title="卡死打转规则"
      sub="同一行高频重复、同一节点原地轮转"
      icon="repeat"
    >
      <div class="grid-2">
        <div>
          <div class="rule-group-title">同行高频重复<span class="rule-count">stall</span></div>
          <${ParamGrid} items=${[
            ['判定窗口', (profile.stall.window_minutes || 0) + ' 分钟'],
            ['最少重复', (profile.stall.min_repeats || 0) + ' 次'],
            ['宽限', (profile.stall.grace_seconds || 0) + ' 秒'],
          ]} />
          <div class="field-hint" style=${{ marginTop: '8px' }}>
            同一行在窗口内堆到阈值即判打转；
            ${(profile.stall.ignore_levels || []).length
              ? `${profile.stall.ignore_levels.join(' / ')} 级不计入；`
              : ''}
            ignore 规则也会一并过滤掉噪音。
          </div>
        </div>
        <div>
          <div class="rule-group-title">段内原地轮转<span class="rule-count">loop</span></div>
          <${ParamGrid} items=${[
            ['步进阈值', (profile.loop.min_iterations || 0) + ' 次'],
            ['推进上限', (profile.loop.max_progress || 0) + ' 次'],
            ['连续重复', (profile.loop.same_state_repeats || 0) + ' 次'],
            ['运行时终止', profile.loop.runtime ? '已开启' : '仅试跑'],
          ]} />
          <div class="field-hint" style=${{ marginTop: '8px' }}>
            ${profile.loop.segment_label
              ? `按${profile.loop.segment_label}切段，`
              : `按「${profile.loop.segment_start || '段开始'} → ${profile.loop.segment_end || '段结束'}」切段，`}
            段内步进多但推进少，或同一步骤连续重复超过阈值，即视为卡住。
            ${profile.loop.runtime
              ? `运行时命中后给 ${profile.loop.grace_seconds || 0} 秒，期间段内继续推进就撤销，仍无进展才终止。`
              : '当前只在试跑里判定，不参与运行时终止。'}
          </div>
        </div>
      </div>
    <//>` : null}

    ${profile && !profile.error ? html`<${Card}
      title="试跑"
      sub="拿真实日志验证规则：命中数、首次样例、打转结论"
      icon="flask-conical"
      actions=${html`<button class="btn btn-primary" onClick=${runProbe} disabled=${probing}>
        <${Icon} name=${probing ? 'loader-2' : 'play'} className=${probing ? 'spinner' : ''} />
        ${probing ? '试跑中…' : '开始试跑'}
      </button>`}
    >
      <${PathInput}
        label="日志文件或目录"
        hint="支持单份日志或整个日志目录；规则集可用 probe.include 只挑框架日志"
        value=${logPath}
        onChange=${setLogPath}
        placeholder=${(profile && profile.source && profile.source.log_dir) || '选择日志文件或目录'}
        kind="pick_dir"
        pickLabel="选择目录"
      />

      ${probe ? html`<div class="probe-result">
        <${ParamGrid} items=${[
          ['日志文件', probe.files],
          ['记录数', probe.records + (probe.truncated ? '（已截断）' : '')],
          ['日志格式', LOG_FORMAT_LABEL[probe.log_format] || probe.log_format],
          ['命中规则', (probe.hit_rules.fatal + probe.hit_rules.task + probe.hit_rules.ignore) + ' / ' + profile.total],
          ['耗时', probe.elapsed_ms + ' ms'],
        ]} />

        <div class="probe-cards">
          ${probe.fail_fast ? html`<div class=${'probe-card' + (probe.fail_fast.triggered ? ' bad' : ' ok')}>
            <div class="probe-title">
              <${Icon} name=${probe.fail_fast.triggered ? 'circle-alert' : 'check'} size=${14} />
              ${`任务级错误 ${probe.fail_fast.triggered ? `命中 ${probe.fail_fast.hits} 条` : '未命中'}`}
            </div>
            <div class="small">
              ${`${probe.fail_fast.rules} 种规则命中 · `
                + (probe.fail_fast.action === 'stop_task'
                  ? `宽限 ${probe.fail_fast.grace_seconds} 秒后终止`
                  : '只登记，不终止')}
            </div>
            ${probe.fail_fast.top
              ? html`<code class="rule-match">${probe.fail_fast.top.time} ${probe.fail_fast.top.text}</code>`
              : null}
          </div>` : null}

          <div class=${'probe-card' + (probe.stall.triggered ? ' bad' : ' ok')}>
            <div class="probe-title">
              <${Icon} name=${probe.stall.triggered ? 'circle-alert' : 'check'} size=${14} />
              同行高频重复 ${probe.stall.triggered ? '已触发' : '未触发'}
            </div>
            <div class="small">
              最高重复 ${probe.stall.max_repeats} 次（阈值 ${probe.stall.min_repeats}）
              ${probe.stall.file ? html` · ${probe.stall.file}` : null}
            </div>
            ${probe.stall.key ? html`<code class="rule-match">${probe.stall.key}</code>` : null}
          </div>

          <div class=${'probe-card' + (probe.loop.triggered ? ' bad' : ' ok')}>
            <div class="probe-title">
              <${Icon} name=${probe.loop.triggered ? 'circle-alert' : 'check'} size=${14} />
              段内原地轮转 ${probe.loop.triggered ? '已触发' : '未触发'}
            </div>
            <div class="small">
              ${probe.loop.segments} 段运行 · ${probe.loop.iterations} 次步进 ·
              推进 ${probe.loop.transitions}${probe.loop.progress_mode === 'distinct' ? ' 种不同步骤' : ' 次'}
              ${probe.loop.incomplete ? ` · ${probe.loop.incomplete} 段未结束` : ''}
            </div>
            <div class="small">
              ${profile.loop.runtime
                ? `运行时会终止：命中后 ${profile.loop.grace_seconds || 0} 秒内仍无推进`
                : '仅试跑诊断，不参与运行时终止'}
            </div>
            ${probe.loop.stalled.length
              ? html`<code class="rule-match">最差：${probe.loop.stalled[0].iterations} 次步进仅 ${probe.loop.stalled[0].progress} 次推进</code>`
              : null}
            ${probe.loop.same_state.length
              ? html`<code class="rule-match">连续重复：${probe.loop.same_state[0].state} × ${probe.loop.same_state[0].count}（阈值 ${probe.loop.same_state_repeats}）</code>`
              : probe.loop.max_run && probe.loop.max_run.count
                ? html`<code class="rule-match">最长连续：${probe.loop.max_run.state} × ${probe.loop.max_run.count}（阈值 ${probe.loop.same_state_repeats}）</code>`
                : null}
          </div>
        </div>

        <div class="probe-hits">
          <div class="rule-group-title">命中明细<span class="rule-count">${probe.rules.filter((r) => r.hits > 0).length}</span></div>
          <div class="rule-list">
            ${probe.rules.filter((r) => r.hits > 0)
              .sort((a, b) => b.hits - a.hits)
              .map((rule) => html`<div class="rule-row" key=${rule.id}>
                <div class="rule-head">
                  <span class=${'rule-tag ' + rule.level}>${rule.level_label}</span>
                  <span class="rule-name">${rule.name}</span>
                  <span class="rule-action">${rule.action_label}</span>
                  <span class="rule-hits"><span class="hit-on">${rule.hits}</span></span>
                </div>
                <code class="rule-match">${rule.match}</code>
                ${rule.samples && rule.samples.length
                  ? html`<div class="rule-sample">
                      ${rule.samples.map((sample, index) => html`<div class="sample-line" key=${index}>
                        <span class="sample-time">${sample.time}</span>${sample.text}
                      </div>`)}
                    </div>`
                  : null}
              </div>`)}
          </div>
        </div>
      </div>` : html`<div class="notice hint" style=${{ marginTop: '12px' }}>
        <${Icon} name="info" size=${14} />
        <span class="grow">试跑不会改动任何配置，只是拿日志跑一遍规则，看看命中和阈值是否符合预期。</span>
      </div>`}
    <//>` : null}

    ${profile && !profile.error ? html`<${Card}
      title="应用到脚本"
      sub="把规则写进脚本配置的 fail_fast / stall / loop，运行时生效"
      icon="file-input"
      actions=${html`<button class="btn btn-primary" onClick=${applyRules} disabled=${applying || !chainName}>
        <${Icon} name=${applying ? 'loader-2' : 'check-check'} className=${applying ? 'spinner' : ''} />写入配置
      </button>`}
    >
      <div class="grid-2">
        <${Field} label="脚本链">
          <select class="input" value=${chainName} onChange=${(e) => setChainName(e.target.value)}>
            <option value="">选择脚本链…</option>
            ${chains.map((chain) => html`<option value=${chain.name} key=${chain.name}>${chain.name}</option>`)}
          </select>
        <//>
        <${Field} label="脚本">
          <select
            class="input"
            value=${scriptIndex}
            onChange=${(e) => setScriptIndex(Number(e.target.value))}
            disabled=${chainScripts.length === 0}
          >
            ${chainScripts.length === 0
              ? html`<option value="0">先选脚本链</option>`
              : chainScripts.map((script, index) => html`<option value=${index} key=${index}>
                  ${index + 1}. ${script.script_display_name || script.display_name}
                </option>`)}
          </select>
        <//>
      </div>
      <div class="notice hint" style=${{ marginTop: '12px' }}>
        <${Icon} name="info" size=${14} />
        <span class="grow">
          当前规则集会生成 ${profile.levels.fatal + profile.levels.task} 条错误规则与 ${profile.levels.ignore} 条忽略规则；
          命中后${profile.fail_fast && profile.fail_fast.action === 'stop_task' ? '终止当前脚本' : '只登记、不终止'}。
          忽略规则同时会合并进 stall 的过滤条件，避免「窗口失焦」这类噪音被当成卡死。
          ${profile.loop.runtime
            ? '段内轮转判定会一并写入，命中后按宽限期决定是否终止。'
            : '段内轮转判定只用于试跑，不写入运行时终止条件。'}
        </span>
      </div>
    <//>` : null}
  </div>`;
}

/* ------------------------------ 设置 ------------------------------ */

function SettingsPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.settings);
  const [busy, setBusy] = useState(false);
  const [update, setUpdate] = useState(null);
  const [updateBusy, setUpdateBusy] = useState(false);

  useEffect(() => { setForm(data.settings); }, [data.settings]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({
          repository_id: form.repository.primary,
          resource_source: form.resource_source,
        }),
      });
      setForm(payload);
      notify('下载源设置已保存，代码同步时生效');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const checkUpdate = async (channel) => {
    setUpdateBusy(true);
    setUpdate({ channel, message: '正在检查…', can_update: false });
    try {
      const payload = await api('/api/update/check', {
        method: 'POST',
        body: JSON.stringify({ channel }),
      });
      setUpdate(payload);
    } catch (e) {
      setUpdate({ channel, message: e.message, can_update: false });
    } finally {
      setUpdateBusy(false);
    }
  };

  const applyUpdate = async () => {
    if (!update || !update.can_update) return;
    setUpdateBusy(true);
    setUpdate({ ...update, message: '正在下载更新包…' });
    try {
      const payload = await api('/api/update/apply', {
        method: 'POST',
        body: JSON.stringify({ channel: update.channel }),
      });
      setUpdate({ ...update, can_update: false, message: payload.message });
      notify(payload.message);
    } catch (e) {
      setUpdate({ ...update, can_update: false, message: e.message });
      notify(e.message, true);
    } finally {
      setUpdateBusy(false);
    }
  };

  return html`<div class="page">
    <${Card}
      title="下载源"
      sub="影响代码同步与资源更新使用的优先来源"
      icon="cloud-download"
      actions=${html`<button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>`}
    >
      <div class="grid-2">
        <${Field} label="代码主仓库" hint="代码同步与更新检查的优先源">
          <select class="input" value=${form.repository.primary} onChange=${(e) => setForm({ ...form, repository: { ...form.repository, primary: e.target.value } })}>
            ${form.repository.options.map((item) => html`<option value=${item.value} key=${item.value}>${item.label} · ${item.url}</option>`)}
          </select>
        <//>
        <${Field} label="资源下载源" hint="auto 表示按上次成功源 / 语言推荐 / 其余源依次回退">
          <select class="input" value=${form.resource_source} onChange=${(e) => setForm({ ...form, resource_source: e.target.value })}>
            <option value="auto">自动</option>
            ${form.resource_sources.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>
      </div>
      <div class="mt">
        <div class="section-title" style=${{ marginBottom: '8px' }}>当前候选顺序</div>
        <div class="row" style=${{ flexWrap: 'wrap', gap: '6px' }}>
          ${form.resource_source_order.map((source, index) => html`<span class=${'tag ' + (index === 0 ? 'tag-pre' : '')} key=${source}>
            ${index + 1}. ${source}
          </span>`)}
        </div>
      </div>
    <//>

    <div class="grid-2">
      <${Card} title="版本更新" icon="download" sub="更新包按主仓库优先下载，失败自动回退">
        <div class="row" style=${{ flexWrap: 'wrap' }}>
          <button class="btn" onClick=${() => checkUpdate('stable')} disabled=${updateBusy}>
            <${Icon} name="refresh-cw" />检查正式版
          </button>
          <button class="btn" onClick=${() => checkUpdate('beta')} disabled=${updateBusy}>
            <${Icon} name="flask-conical" />检查测试版
          </button>
          ${update && update.can_update
            ? html`<button class="btn btn-primary" onClick=${applyUpdate} disabled=${updateBusy}>
                <${Icon} name="download" />立即更新
              </button>`
            : null}
        </div>
        <div class="small mt" style=${{ color: update && update.can_update ? 'var(--ok)' : 'var(--t2)', whiteSpace: 'pre-wrap' }}>
          ${update ? update.message : `当前版本 ${data.version}`}
        </div>
      <//>

      <${Card} title="运行环境" icon="monitor" sub="当前版本与进程状态">
        <div class="kv"><span class="k">当前版本</span><span class="v mono">${data.version}</span></div>
        <div class="kv"><span class="k">今日状态</span><span class="v">${data.status.daily_label}${data.status.daily_note
          ? html`<span class="small muted" style=${{ marginLeft: '6px' }}>${data.status.daily_note}</span>`
          : null}</span></div>
        <div class="kv"><span class="k">常驻调度</span><span class="v">${data.status.scheduler_pid ? `运行中 (pid=${data.status.scheduler_pid})` : '未运行'}</span></div>
        <div class="kv"><span class="k">运行实例</span><span class="v">${data.status.runner_pid ? `运行中 (pid=${data.status.runner_pid})` : '未运行'}</span></div>
        <div class="kv"><span class="k">配置文件</span><span class="v mono">config/run.yml</span></div>
      <//>
    </div>
  </div>`;
}

/* ------------------------------ 应用 ------------------------------ */

function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [page, setPage] = useState(initialPage);
  const [theme, setTheme] = useState(initialTheme);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

  useEffect(() => {
    try { localStorage.setItem(PAGE_KEY, page); } catch (e) { /* 忽略存储失败 */ }
    if (window.location.hash !== '#' + page) {
      try { window.history.replaceState(null, '', '#' + page); } catch (e) { /* 忽略 */ }
    }
  }, [page]);

  useEffect(() => {
    const onHash = () => {
      const next = (window.location.hash || '').replace('#', '');
      if (PAGES.some((item) => item.key === next)) setPage(next);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* 忽略存储失败 */ }
      return next;
    });
  }, []);

  const notify = useCallback((message, isError = false) => {
    setToast({ message, isError });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4200);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const payload = await api('/api/bootstrap');
      setData(payload);
      setError('');
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const status = await api('/api/status');
      setData((current) => (current ? { ...current, status } : current));
      setError('');
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refreshStatus, 8000);
    return () => clearInterval(timer);
  }, [refresh, refreshStatus]);

  if (error) {
    return html`<div class="app"><div class="shell" style=${{ display: 'grid', placeItems: 'center' }}>
      <div class="empty">
        <div class="empty-icon" style=${{ color: 'var(--fail)' }}><${Icon} name="plug-zap" size=${22} /></div>
        <div class="empty-title">无法连接后端</div>
        <div class="mono small">${error}</div>
      </div>
    </div></div>`;
  }

  if (!data) {
    return html`<div class="app"><div class="shell" style=${{ display: 'grid', placeItems: 'center' }}>
      <div class="empty">加载中…</div>
    </div></div>`;
  }

  const status = data.status;
  const badgeClass = DAILY_BADGE[status.daily_status] || 'badge-miss';
  const meta = PAGES.find((item) => item.key === page) || PAGES[0];

  const pageNode = {
    chains: html`<${ChainsPage} data=${data} refresh=${refresh} notify=${notify} />`,
    schedule: html`<${SchedulePage} data=${data} refresh=${refresh} notify=${notify} />`,
    policy: html`<${PolicyPage} data=${data} refresh=${refresh} notify=${notify} />`,
    report: html`<${ReportPage} data=${data} refresh=${refresh} notify=${notify} />`,
    notify: html`<${NotifyPage} data=${data} refresh=${refresh} notify=${notify} />`,
    rules: html`<${RulesPage} data=${data} refresh=${refresh} notify=${notify} />`,
    settings: html`<${SettingsPage} data=${data} refresh=${refresh} notify=${notify} />`,
  }[page];

  return html`<div class="app">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-logo" src="/static/app_icon.png" alt="OneDragon ScriptChainer Plus" />
        <div class="brand-text">
          <div class="brand-name">ScriptChainer Plus</div>
          <div class="brand-sub"><span class="brand-ver">${data.version}</span>千机链</div>
        </div>
      </div>

      ${PAGE_GROUPS.map((groupName) => html`<div class="nav-group" key=${groupName}>
        <div class="nav-label">${groupName}</div>
        ${PAGES.filter((item) => item.group === groupName).map((item) => html`<button
          key=${item.key}
          class=${'nav-item' + (page === item.key ? ' active' : '')}
          title=${item.label}
          onClick=${() => setPage(item.key)}
        >
          <span class="nav-icon"><${Icon} name=${item.icon} size=${16} /></span>
          <span>${item.label}</span>
        </button>`)}
      </div>`)}

      <div class="sidebar-foot">
        <div class="status-card">
          <div class="status-row">
            <span class="status-k">今日</span>
            <span class=${'badge ' + badgeClass} title=${status.daily_note || status.daily_label}>${status.daily_label}</span>
          </div>
          <div class="status-row">
            <span class="status-k">常驻调度</span>
            <span class="status-v">${status.scheduler_pid ? '运行中' : '未运行'}</span>
          </div>
          <div class="status-row">
            <span class="status-k">今日计划</span>
            <span class="status-v" title=${data.today_plan.join(' → ')}>${data.today_plan.length ? data.today_plan.join(' → ') : '空'}</span>
          </div>
        </div>
      </div>
    </aside>

    <div class="shell">
      <header class="topbar">
        <div class="topbar-left">
          <div class="page-title">${meta.label}</div>
          <div class="page-sub">${meta.desc}</div>
        </div>
        <div class="topbar-right">
          <span class="chip">${status.today} ${status.weekday_label}</span>
          ${status.launched.pid
            ? html`<span class="chip chip-live"><span class="dot"></span>${status.launched.chain} · pid ${status.launched.pid}</span>`
            : null}
          ${status.scheduler_pid ? html`<span class="chip">调度 pid ${status.scheduler_pid}</span>` : null}
          <button class="icon-btn" onClick=${toggleTheme} title=${theme === 'dark' ? '切换到日间模式' : '切换到夜间模式'}>
            <${Icon} name=${theme === 'dark' ? 'sun' : 'moon'} />
          </button>
          <button class="icon-btn" onClick=${refresh} title="刷新"><${Icon} name="refresh-cw" /></button>
        </div>
      </header>
      <main class="content" key=${page}>${pageNode}</main>
    </div>

    ${toast ? html`<div class=${'toast' + (toast.isError ? ' err' : '')}>
      <span class="toast-icon"><${Icon} name=${toast.isError ? 'alert-circle' : 'check-circle-2'} size=${16} /></span>
      <span class="grow">${toast.message}</span>
    </div>` : null}
  </div>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
