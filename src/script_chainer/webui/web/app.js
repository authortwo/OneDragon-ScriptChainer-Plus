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
  { key: 'notify', label: '消息推送', icon: 'bell', group: '配置', desc: '运行进度与失败结果的通知' },
  { key: 'settings', label: '设置', icon: 'settings', group: '配置', desc: '下载源、版本更新与运行环境' },
  { key: 'report', label: '运行报告', icon: 'file-text', group: '诊断', desc: '最近一次运行的汇总、明细与日志' },
];

const PAGE_GROUPS = ['运行', '配置', '诊断'];

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
  fail_fast: {}, stall: {},
};

const FAIL_FAST_DEFAULTS = { enabled: false, grace_seconds: 240, error_patterns: [], ignore_patterns: [] };
const STALL_DEFAULTS = { enabled: true, window_minutes: 8, min_repeats: 200, grace_seconds: 60, ignore_pattern: '' };

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

function Icon({ name, size = 16 }) {
  const pascal = String(name).split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('');
  const icons = (window.lucide && window.lucide.icons) || {};
  const node = icons[pascal];
  if (!node) return null;
  return React.createElement(
    node[0],
    _iconProps(node[1], { width: size, height: size }),
    (node[2] || []).map((child, index) => React.createElement(
      child[0],
      _iconProps(child[1], { key: index }),
    )),
  );
}

function Switch({ on, onChange, title }) {
  return html`<button
    class=${'switch' + (on ? ' on' : '')}
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
      ${icon ? html`<span class="muted"><${Icon} name=${icon} size=${16} /></span>` : null}
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

/* ------------------------------ 脚本链 ------------------------------ */

function scriptTypeMeta(type) {
  return TYPE_META[type] || TYPE_META.external;
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
                  onClick=${() => setSelected(item.name)}
                >
                  <div class="grow" style=${{ minWidth: 0 }}>
                    <div class="chain-item-name">${item.name}</div>
                    <div class="chain-item-meta">${item.script_count} 个脚本 · ${item.games.join(' / ') || '未设置游戏'}</div>
                  </div>
                  ${item.invalid_count > 0 ? html`<span class="badge badge-fail">${item.invalid_count}</span>` : null}
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
                ${confirmingDelete
                  ? html`<button class="btn btn-danger" onClick=${removeChain}>确认删除</button>`
                  : html`<button class="icon-btn danger" title="删除脚本链" onClick=${() => setConfirmingDelete(true)}><${Icon} name="trash-2" /></button>`}
              </div>
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
                            return html`<div
                              key=${index}
                              class=${'script-card' + (script.enabled ? '' : ' disabled') + (editing === index ? ' editing' : '')}
                              onClick=${() => setEditing(editing === index ? null : index)}
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
                                  ${script.invalid_message ? html`<span class="badge badge-fail">${script.invalid_message}</span>` : null}
                                </div>
                                <div class="script-path mono">
                                  ${isOp
                                    ? (script.computer_operation_display_name || '未选择电脑操作')
                                    : (script.script_path || '未设置脚本路径')}
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
  const processNames = Array.isArray(script.script_process_name) ? script.script_process_name : [];

  const patchFailFast = (patch) => onChange({ fail_fast: { ...failFast, ...patch } });
  const patchStall = (patch) => onChange({ stall: { ...stall, ...patch } });
  const patchProcessNames = (text) => onChange({
    script_process_name: text.split(',').map((item) => item.trim()).filter(Boolean),
  });
  const patchPatterns = (key, text) => patchFailFast({
    [key]: text.split('\n').map((item) => item.trim()).filter(Boolean),
  });

  return html`<div class="drawer-backdrop" onClick=${onClose}></div>
  <aside class="drawer" onClick=${(e) => e.stopPropagation()}>
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
              <div class="rule-title">任务级错误规则</div>
              <div class="rule-desc">命中错误日志后先宽限一段时间，期间脚本自行恢复则不算失败</div>
            </div>
            <${Switch} on=${!!failFast.enabled} onChange=${(value) => patchFailFast({ enabled: value })} />
          </div>
          <${Field} label="宽限时间（秒）">
            <input class="input" type="number" value=${failFast.grace_seconds} onInput=${(e) => patchFailFast({ grace_seconds: Number(e.target.value) })} />
          <//>
          <${Field} label="错误正则" hint="每行一条，例如 ${'\\[ERROR\\]\\[task_flow\\.py\\]'}">
            <textarea class="input mono" rows="3" value=${(failFast.error_patterns || []).join('\n')} onInput=${(e) => patchPatterns('error_patterns', e.target.value)} placeholder=${'\\[ERROR\\]'}></textarea>
          <//>
          <${Field} label="忽略正则" hint="每行一条，命中后不参与错误判定">
            <textarea class="input mono" rows="2" value=${(failFast.ignore_patterns || []).join('\n')} onInput=${(e) => patchPatterns('ignore_patterns', e.target.value)} placeholder="DEBUG"></textarea>
          <//>
        </div>

        <div class="rule-box">
          <div class="rule-head">
            <div class="grow">
              <div class="rule-title">卡死 / 打转检测</div>
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
        const chains = schedule[day.value] || [];
        const open = editing === day.value;
        return html`<div key=${day.value}>
          <div class="weekday-row">
            <div class="weekday-name">${day.label}</div>
            <div class="weekday-chips">
              ${chains.length === 0
                ? html`<span class="small muted">未配置</span>`
                : chains.map((name) => html`<span class="tag tag-pre" key=${name}>${name}</span>`)}
            </div>
            <button class="btn btn-ghost btn-sm" onClick=${() => setEditing(open ? null : day.value)}>
              <${Icon} name=${open ? 'chevron-up' : 'chevron-down'} />${open ? '收起' : '编辑'}
            </button>
          </div>
          ${open ? html`<div class="weekday-edit">
            ${chainNames.length === 0
              ? html`<span class="small muted">还没有脚本链，请先在「脚本链」页新建</span>`
              : chainNames.map((name) => html`<button
                  key=${name}
                  class=${'chip-toggle' + (chains.includes(name) ? ' on' : '')}
                  onClick=${() => toggleChain(day.value, name)}
                >${chains.includes(name) ? '✓ ' : ''}${name}</button>`)}
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

function ReportPage({ data, refresh, notify }) {
  const [payload, setPayload] = useState(data.report);
  const [logs, setLogs] = useState(data.logs);

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

  const report = payload.report;
  const summary = (report && report.summary) || {};

  const stats = [
    ['总计', summary.total || 0],
    ['成功', summary.ok || 0],
    ['失败', summary.fail || 0],
    ['配置错误', summary.config_error || 0],
    ['环境异常', summary.env_error || 0],
    ['未找到', summary.missing || 0],
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
              ${stats.map(([label, value]) => html`<div class="stat" key=${label}>
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
      sub=${logs.file || ''}
      icon="terminal"
      actions=${html`<button class="btn btn-ghost btn-sm" onClick=${reload}><${Icon} name="refresh-cw" />刷新</button>`}
      bodyClass="tight"
    >
      <div class="log">${logs.lines.length ? logs.lines.join('\n') : '暂无日志'}</div>
    <//>
  </div>`;
}

/* ------------------------------ 消息推送 ------------------------------ */

function NotifyPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.notify);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setForm(data.notify); }, [data.notify]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = await api('/api/notify', {
        method: 'PUT',
        body: JSON.stringify({
          title: form.title,
          enable_notify: form.enable_notify,
          enable_before_notify: form.enable_before_notify,
        }),
      });
      setForm(payload);
      notify('推送配置已保存');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  return html`<div class="page">
    <${Card}
      title="消息推送"
      sub="脚本失败会立即推送一条，整轮结束再推送汇总"
      icon="bell"
      actions=${html`<button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>`}
    >
      <${Field} label="通知标题">
        <input class="input" value=${form.title} onInput=${(e) => setForm({ ...form, title: e.target.value })} />
      <//>
      <div class="mt">
        <${ToggleRow} title="启用推送" desc="关闭后不再发送任何通知" on=${form.enable_notify} onChange=${(value) => setForm({ ...form, enable_notify: value })} />
        <${ToggleRow} title="启动前通知" desc="每轮计划开始前推送一条" on=${form.enable_before_notify} onChange=${(value) => setForm({ ...form, enable_before_notify: value })} />
      </div>
      <div class="notice hint" style=${{ marginTop: '16px' }}>
        <${Icon} name="info" size=${15} />
        <span class="grow">推送渠道（企业微信、钉钉、飞书等）在 config/notify.yml 中配置，修改后重启生效。</span>
      </div>
    <//>
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
        <div class="kv"><span class="k">今日状态</span><span class="v">${data.status.daily_label}</span></div>
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
  const [page, setPage] = useState('chains');
  const [theme, setTheme] = useState(initialTheme);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

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
    settings: html`<${SettingsPage} data=${data} refresh=${refresh} notify=${notify} />`,
  }[page];

  return html`<div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-logo">千</div>
        <div class="brand-text">
          <div class="brand-name">千机链</div>
          <div class="brand-sub">ScriptChainer ${data.version}</div>
        </div>
      </div>

      ${PAGE_GROUPS.map((groupName) => html`<div class="nav-group" key=${groupName}>
        <div class="nav-label">${groupName}</div>
        ${PAGES.filter((item) => item.group === groupName).map((item) => html`<button
          key=${item.key}
          class=${'nav-item' + (page === item.key ? ' active' : '')}
          onClick=${() => setPage(item.key)}
        >
          <${Icon} name=${item.icon} size=${17} />
          <span>${item.label}</span>
        </button>`)}
      </div>`)}

      <div class="sidebar-foot">
        <div class="status-card">
          <div class="status-row">
            <span class="status-k">今日</span>
            <span class=${'badge ' + badgeClass}>${status.daily_label}</span>
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
