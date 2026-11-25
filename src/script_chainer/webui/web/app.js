/* 千机链 Web GUI —— React + htm，无构建步骤 */
const { useState, useEffect, useMemo, useCallback, useRef } = React;
const html = htm.bind(React.createElement);

const PAGES = [
  { key: 'chains', label: '脚本链', icon: 'list' },
  { key: 'schedule', label: '调度', icon: 'calendar-days' },
  { key: 'policy', label: '运行策略', icon: 'sliders-horizontal' },
  { key: 'report', label: '运行报告', icon: 'file-text' },
  { key: 'notify', label: '推送', icon: 'bell' },
  { key: 'settings', label: '设置', icon: 'settings' },
];

const TYPE_META = {
  external: { label: '外部', cls: 'tag-ext', color: '#409EFF' },
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
    <label>${label}</label>
    ${children}
    ${hint ? html`<div class="small muted">${hint}</div>` : null}
  </div>`;
}

function ToggleRow({ title, desc, on, onChange }) {
  return html`<div class="row" style=${{ padding: '9px 0' }}>
    <div class="grow">
      <div style=${{ fontSize: '12.5px' }}>${title}</div>
      ${desc ? html`<div class="small muted" style=${{ marginTop: '3px' }}>${desc}</div>` : null}
    </div>
    <${Switch} on=${on} onChange=${onChange} title=${title} />
  </div>`;
}

/* ------------------------------ 脚本链 ------------------------------ */

const SCRIPT_DEFAULTS = {
  display_name: '', game_label: '', script_type: 'external', script_path: '',
  computer_operation: '', script_process_name: [], game_process_name: '',
  launcher_mode: false, run_timeout_seconds: 3600, check_done: 'game_or_script_closed',
  kill_script_after_done: true, kill_game_after_done: true, kill_process_tree: true,
  script_arguments: '', notify_start: true, notify_done: true, notify_log_interval: 0,
  enabled: true, attach_direction: '', no_log_timeout_seconds: 300, no_log_max_retries: 3,
};

function scriptTypeMeta(type) {
  return TYPE_META[type] || TYPE_META.external;
}

function ChainsPage({ data, refresh, notify }) {
  const [selected, setSelected] = useState('');
  const [chain, setChain] = useState(null);
  const [scripts, setScripts] = useState([]);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState('');

  useEffect(() => {
    if (data.chains.length === 0) {
      setSelected('');
      setChain(null);
      return;
    }
    if (!data.chains.some((item) => item.name === selected)) {
      setSelected(data.chains[0].name);
    }
  }, [data.chains, selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    api(`/api/chains/${encodeURIComponent(selected)}`)
      .then((payload) => {
        if (cancelled) return;
        setChain(payload);
        setScripts(payload.scripts.map((item) => ({ ...item })));
      })
      .catch((e) => notify(e.message, true));
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
      notify('已保存');
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
      notify(`已启动脚本链 ${selected}`);
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
      refresh();
    } catch (e) {
      notify(e.message, true);
    }
  };

  const addScript = (type) => {
    setScripts((list) => [...list, { ...SCRIPT_DEFAULTS, script_type: type }]);
    setEditing(scripts.length);
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

  const current = editing !== null ? scripts[editing] : null;

  return html`<div class="page">
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>脚本链</h1>
        <div class="desc">
          ${data.chains.length} 条脚本链 · 今日计划 ${data.today_plan.length ? data.today_plan.join(' → ') : '（空）'}
        </div>
      </div>
      <div class="row">
        ${dirty ? html`<span class="tag tag-off">未保存</span>` : null}
        <button class="btn btn-primary" onClick=${save} disabled=${!selected || busy || !dirty}>
          <${Icon} name="save" />保存
        </button>
        <button class="btn" onClick=${() => run(null)} disabled=${!selected}>
          <${Icon} name="play" />运行
        </button>
        <button class="btn btn-danger" onClick=${stop}>
          <${Icon} name="square" />停止
        </button>
      </div>
    </div>

    <div style=${{ display: 'grid', gridTemplateColumns: '240px minmax(0, 1fr)', gap: '16px', alignItems: 'start' }}>
      <div class="card" style=${{ padding: '10px' }}>
        <div class="row" style=${{ padding: '2px 4px 10px' }}>
          <div class="grow section-title">脚本链</div>
        </div>
        ${data.chains.length === 0
          ? html`<div class="empty small">还没有脚本链<br />在下方新建一条</div>`
          : html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              ${data.chains.map((item) => html`<div
                key=${item.name}
                class=${'chain-item' + (item.name === selected ? ' active' : '')}
                onClick=${() => { setSelected(item.name); setEditing(null); }}
              >
                <div class="grow" style=${{ minWidth: 0 }}>
                  <div style=${{ fontSize: '12.5px', fontWeight: 600 }}>${item.name}</div>
                  <div class="small muted">${item.script_count} 个脚本 · ${item.games.join(' / ') || '未设置游戏'}</div>
                </div>
                ${item.invalid_count > 0 ? html`<span class="badge badge-fail">${item.invalid_count}</span>` : null}
              </div>`)}
            </div>`}
        <div class="row" style=${{ marginTop: '10px' }}>
          <input class="input" placeholder="新建脚本链名称" value=${newName} onInput=${(e) => setNewName(e.target.value)} />
          <button class="btn" onClick=${createChain} title="新建"><${Icon} name="plus" /></button>
        </div>
        ${selected ? html`<button class="btn btn-ghost" style=${{ width: '100%', marginTop: '6px', color: '#f87171' }} onClick=${removeChain}>
          <${Icon} name="trash-2" />删除此链
        </button>` : null}
      </div>

      <div style=${{ display: 'flex', flexDirection: 'column', gap: '12px', minWidth: 0 }}>
        ${!chain ? html`<div class="card empty">选择左侧脚本链，或先新建一条</div>` : html`
          ${chain.warnings.map((warning) => html`<div class="card" style=${{ padding: '10px 14px', color: '#f5c063', borderColor: 'rgba(230,162,60,.35)' }}>
            ${warning}
          </div>`)}

          <div class="row">
            <div class="grow section-title">${chain.name} · ${scripts.length} 个脚本</div>
            <button class="btn" onClick=${() => addScript('external')}><${Icon} name="plus" />外部程序</button>
            <button class="btn" onClick=${() => addScript('python')}><${Icon} name="plus" />Python</button>
            <button class="btn" onClick=${() => addScript('computer_op')}><${Icon} name="plus" />电脑操作</button>
          </div>

          ${scripts.length === 0
            ? html`<div class="card empty">这条链还没有脚本</div>`
            : html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                ${scripts.map((script, index) => {
                  const meta = scriptTypeMeta(script.script_type);
                  return html`<div
                    key=${index}
                    class=${'script-card' + (script.enabled ? '' : ' disabled') + (editing === index ? ' editing' : '')}
                    style=${editing === index ? { borderColor: 'rgba(109,110,241,.6)' } : {}}
                  >
                    <div class="type-bar" style=${{ background: meta.color }}></div>
                    <div class="grow" style=${{ minWidth: 0 }} onClick=${() => setEditing(editing === index ? null : index)}>
                      <div class="row" style=${{ gap: '8px' }}>
                        <span class="name">${index + 1}. ${script.script_display_name}</span>
                        <span class=${'tag ' + meta.cls}>${meta.label}</span>
                        ${script.game_display_name ? html`<span class="tag">${script.game_display_name}</span>` : null}
                        ${script.attached_to_prev ? html`<span class="tag tag-off">挂靠</span>` : null}
                        ${script.invalid_message ? html`<span class="badge badge-fail">${script.invalid_message}</span>` : null}
                      </div>
                      <div class="small muted" style=${{ marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        ${script.script_type === 'computer_op'
                          ? (script.computer_operation_display_name || '未选择电脑操作')
                          : (script.script_path || '（未设置脚本路径）')}
                      </div>
                    </div>
                    <${Switch} on=${script.enabled} onChange=${(value) => updateScript(index, { enabled: value })} title="启用" />
                    <button class="btn btn-ghost" title="上移" onClick=${() => move(index, -1)}><${Icon} name="arrow-up" /></button>
                    <button class="btn btn-ghost" title="下移" onClick=${() => move(index, 1)}><${Icon} name="arrow-down" /></button>
                    <button class="btn btn-ghost" title="复制" onClick=${() => duplicate(index)}><${Icon} name="copy" /></button>
                    <button class="btn btn-ghost" title="删除" onClick=${() => removeScript(index)}><${Icon} name="trash-2" /></button>
                    <button class="btn btn-ghost" title="仅调试此脚本" onClick=${() => run(index)}><${Icon} name="bug" /></button>
                  </div>`;
                })}
              </div>`}

          ${current ? html`<${ScriptEditor}
            script=${current}
            options=${data.options}
            onChange=${(patch) => updateScript(editing, patch)}
            onClose=${() => setEditing(null)}
            onRun=${() => run(editing)}
          />` : null}
        `}
      </div>
    </div>
  </div>`;
}

function stripDerived(scripts) {
  return scripts.map((script) => {
    const copy = { ...script };
    delete copy.idx;
    delete copy.script_display_name;
    delete copy.game_display_name;
    delete copy.script_process_display_name;
    delete copy.check_done_display_name;
    delete copy.computer_operation_display_name;
    delete copy.invalid_message;
    delete copy.attached_to_prev;
    return copy;
  });
}

function ScriptEditor({ script, options, onChange, onClose, onRun }) {
  const isOp = script.script_type === 'computer_op';
  const processValue = Array.isArray(script.script_process_name)
    ? script.script_process_name.join(',')
    : '';

  return html`<div class="card" style=${{ padding: '16px' }}>
    <div class="row" style=${{ marginBottom: '12px' }}>
      <div class="grow section-title">编辑脚本</div>
      <button class="btn" onClick=${onRun}><${Icon} name="bug" />仅调试此脚本</button>
      <button class="btn btn-ghost" onClick=${onClose}><${Icon} name="x" /></button>
    </div>

    <div class="grid-2">
      <${Field} label="显示名称">
        <input class="input" value=${script.display_name || ''} onInput=${(e) => onChange({ display_name: e.target.value })} />
      <//>
      <${Field} label="脚本类型">
        <select class="input" value=${script.script_type} onChange=${(e) => onChange({ script_type: e.target.value })}>
          ${options.script_type.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
        </select>
      <//>
    </div>

    ${isOp ? html`<div class="mt">
      <${Field} label="电脑操作">
        <select class="input" value=${script.computer_operation || ''} onChange=${(e) => onChange({ computer_operation: e.target.value })}>
          <option value="">请选择</option>
          ${options.computer_operation.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
        </select>
      <//>
    </div>` : html`
      <div class="mt">
        <${Field} label="脚本路径">
          <input class="input mono" value=${script.script_path || ''} onInput=${(e) => onChange({ script_path: e.target.value })} placeholder="exe / bat / py 的完整路径" />
        <//>
      </div>

      <div class="grid-2 mt">
        <${Field} label="游戏">
          <select class="input" value=${script.game_process_name || ''} onChange=${(e) => onChange({ game_process_name: e.target.value })}>
            <option value="">不指定</option>
            ${options.game.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>
        <${Field} label="检查完成方式">
          <select class="input" value=${script.check_done || ''} onChange=${(e) => onChange({ check_done: e.target.value })}>
            <option value="">请选择</option>
            ${options.check_done.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>
      </div>

      <div class="grid-2 mt">
        <${Field} label="脚本进程名" hint="多个用英文逗号分隔；留空表示自动监控脚本路径">
          <input class="input mono" value=${processValue} onInput=${(e) => onChange({ script_process_name: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })} />
        <//>
        <${Field} label="启动参数">
          <input class="input mono" value=${script.script_arguments || ''} onInput=${(e) => onChange({ script_arguments: e.target.value })} />
        <//>
      </div>
    `}

    <div class="grid-3 mt">
      <${Field} label="挂靠">
        <select class="input" value=${script.attach_direction || ''} onChange=${(e) => onChange({ attach_direction: e.target.value })}>
          ${options.attach_direction.map((item) => html`<option value=${item.value} key=${item.value || 'none'}>${item.label}</option>`)}
        </select>
      <//>
      <${Field} label="运行超时（秒）">
        <input class="input" type="number" value=${script.run_timeout_seconds} onInput=${(e) => onChange({ run_timeout_seconds: Number(e.target.value) })} />
      <//>
      <${Field} label="无日志超时（秒）" hint="0 或空会回落默认 300 秒，不能关闭">
        <input class="input" type="number" value=${script.no_log_timeout_seconds} onInput=${(e) => onChange({ no_log_timeout_seconds: Number(e.target.value) })} />
      <//>
    </div>

    <div class="mt" style=${{ borderTop: '1px solid var(--line)', paddingTop: '4px' }}>
      <${ToggleRow} title="启用" desc="禁用后运行时跳过此脚本" on=${!!script.enabled} onChange=${(value) => onChange({ enabled: value })} />
      <${ToggleRow} title="完成后关闭脚本" on=${!!script.kill_script_after_done} onChange=${(value) => onChange({ kill_script_after_done: value })} />
      <${ToggleRow} title="完成后关闭游戏" on=${!!script.kill_game_after_done} onChange=${(value) => onChange({ kill_game_after_done: value })} />
      <${ToggleRow} title="启动时通知" on=${!!script.notify_start} onChange=${(value) => onChange({ notify_start: value })} />
      <${ToggleRow} title="完成时通知" on=${!!script.notify_done} onChange=${(value) => onChange({ notify_done: value })} />
    </div>
  </div>`;
}

/* ------------------------------ 调度 ------------------------------ */

function SchedulePage({ data, refresh, notify }) {
  const [schedule, setSchedule] = useState(data.run_config.schedule || {});
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setSchedule(data.run_config.schedule || {});
  }, [data.run_config.schedule]);

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
      await api('/api/run-config/schedule', {
        method: 'PUT',
        body: JSON.stringify({ schedule }),
      });
      notify('周计划已保存');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  return html`<div class="page">
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>调度</h1>
        <div class="desc">按星期配置当天要运行的脚本链；未配置的星期回退到「默认」</div>
      </div>
      <div class="row">
        <button class="btn" onClick=${() => api('/api/plan/run', { method: 'POST' }).then(() => { notify('已启动立即补跑'); refresh(); }).catch((e) => notify(e.message, true))}>
          <${Icon} name="play" />立即运行今日计划
        </button>
        <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存周计划</button>
      </div>
    </div>

    <div class="card" style=${{ padding: '8px' }}>
      ${data.options.weekdays.map((day) => {
        const chains = schedule[day.value] || [];
        const open = editing === day.value;
        return html`<div key=${day.value} style=${{ borderBottom: '1px solid var(--line)' }}>
          <div class="row" style=${{ padding: '10px 10px' }}>
            <div style=${{ width: '72px', fontSize: '12.5px', fontWeight: 600 }}>${day.label}</div>
            <div class="grow row" style=${{ flexWrap: 'wrap', gap: '6px' }}>
              ${chains.length === 0
                ? html`<span class="small muted">未配置</span>`
                : chains.map((name) => html`<span class="tag tag-pre" key=${name}>${name}</span>`)}
            </div>
            <button class="btn btn-ghost" onClick=${() => setEditing(open ? null : day.value)}>
              <${Icon} name=${open ? 'chevron-up' : 'chevron-down'} />${open ? '收起' : '编辑'}
            </button>
          </div>
          ${open ? html`<div style=${{ padding: '4px 10px 12px 92px', display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            ${chainNames.length === 0
              ? html`<span class="small muted">还没有脚本链，请先在「脚本链」页新建</span>`
              : chainNames.map((name) => html`<button
                  key=${name}
                  class=${'btn' + (chains.includes(name) ? ' btn-primary' : '')}
                  onClick=${() => toggleChain(day.value, name)}
                >${chains.includes(name) ? '✓ ' : ''}${name}</button>`)}
          </div>` : null}
        </div>`;
      })}
    </div>
  </div>`;
}

/* ------------------------------ 运行策略 ------------------------------ */

function PolicyPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.run_config);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setForm(data.run_config); }, [data.run_config]);

  const patch = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const patchRetry = (key, value) => setForm((current) => ({ ...current, retry: { ...current.retry, [key]: value } }));

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
      notify(payload.issues.length ? `已保存，仍有 ${payload.issues.length} 项配置问题` : '已保存', payload.issues.length > 0);
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
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>运行策略</h1>
        <div class="desc">时间窗、截止、重试、锁屏与账户校验</div>
      </div>
      <div class="row">
        <button class="btn" onClick=${startScheduler}><${Icon} name="power" />启动常驻调度</button>
        <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>
      </div>
    </div>

    ${form.issues && form.issues.length
      ? html`<div class="card" style=${{ padding: '12px 16px', borderColor: 'rgba(230,162,60,.4)' }}>
          <div class="section-title" style=${{ color: '#f5c063', marginBottom: '6px' }}>配置提示</div>
          ${form.issues.map((issue, index) => html`<div class="small" style=${{ color: '#f5c063', whiteSpace: 'pre-wrap', marginTop: index ? '6px' : 0 }} key=${index}>${issue}</div>`)}
        </div>`
      : null}

    <div class="card" style=${{ padding: '16px' }}>
      <div class="section-title" style=${{ marginBottom: '12px' }}>时间与截止</div>
      <div class="grid-3">
        <${Field} label="时间窗开始" hint="留空表示不限时间">
          <input class="input mono" placeholder="04:15" value=${form.run_window.start} onInput=${(e) => patch('run_window', { ...form.run_window, start: e.target.value })} />
        <//>
        <${Field} label="时间窗结束">
          <input class="input mono" placeholder="06:15" value=${form.run_window.end} onInput=${(e) => patch('run_window', { ...form.run_window, end: e.target.value })} />
        <//>
        <${Field} label="全局截止（分钟）" hint="0 表示不限；与时间窗取较早值">
          <input class="input" type="number" value=${form.deadline_minutes} onInput=${(e) => patch('deadline_minutes', e.target.value)} />
        <//>
      </div>
      <div class="grid-2 mt">
        <${Field} label="调度轮询间隔（秒）">
          <input class="input" type="number" value=${form.scheduler_poll_seconds} onInput=${(e) => patch('scheduler_poll_seconds', e.target.value)} />
        <//>
        <${Field} label="跑完关机延迟（秒）" hint="0 表示不关机">
          <input class="input" type="number" value=${form.shutdown_after_run} onInput=${(e) => patch('shutdown_after_run', e.target.value)} />
        <//>
      </div>
      <div class="grid-2 mt">
        <${Field} label="锁屏处理">
          <select class="input" value=${form.on_locked} onChange=${(e) => patch('on_locked', e.target.value)}>
            ${data.options.lock_policies.map((item) => html`<option value=${item.value} key=${item.value}>${item.label}</option>`)}
          </select>
        <//>
        <${Field} label="隐私屏关闭热键" hint="Ctrl+Alt+Shift + 此键">
          <input class="input mono" value=${form.privacy_screen_close_key} onInput=${(e) => patch('privacy_screen_close_key', e.target.value)} />
        <//>
      </div>
    </div>

    <div class="card" style=${{ padding: '16px' }}>
      <div class="section-title" style=${{ marginBottom: '6px' }}>重试</div>
      <div class="grid-3">
        <${Field} label="最大轮数" hint="0 表示不重试">
          <input class="input" type="number" value=${form.retry.max_rounds} onInput=${(e) => patchRetry('max_rounds', e.target.value)} />
        <//>
        <${Field} label="重试间隔（秒）">
          <input class="input" type="number" value=${form.retry.delay_seconds} onInput=${(e) => patchRetry('delay_seconds', e.target.value)} />
        <//>
        <div></div>
      </div>
      <${ToggleRow} title="只重跑失败脚本" on=${form.retry.only_failed} onChange=${(value) => patchRetry('only_failed', value)} />
      <${ToggleRow} title="配置错误不重试" on=${form.retry.skip_on_config_error} onChange=${(value) => patchRetry('skip_on_config_error', value)} />
      <${ToggleRow} title="环境崩溃不重试" on=${form.retry.skip_on_crash} onChange=${(value) => patchRetry('skip_on_crash', value)} />
    </div>

    <div class="card" style=${{ padding: '16px' }}>
      <div class="section-title" style=${{ marginBottom: '6px' }}>运行环境</div>
      <${ToggleRow} title="每日只运行一次" desc="关闭后窗口内会反复执行" on=${form.once_per_day} onChange=${(value) => patch('once_per_day', value)} />
      <${ToggleRow} title="运行期保持唤醒" desc="阻止系统睡眠与熄屏，结束后自动恢复" on=${form.keep_awake} onChange=${(value) => patch('keep_awake', value)} />
      <${ToggleRow} title="启用内置常驻调度" on=${form.scheduler_enabled} onChange=${(value) => patch('scheduler_enabled', value)} />
      <${ToggleRow} title="随系统登录自启" desc="写入当前用户注册表 Run 项" on=${form.autostart_enabled} onChange=${(value) => patch('autostart_enabled', value)} />
      <div class="grid-2 mt">
        <${Field} label="期望运行账户 SID" hint="留空表示不校验；不一致时按下方策略处理">
          <input class="input mono" value=${form.run_as_sid} onInput=${(e) => patch('run_as_sid', e.target.value)} placeholder="S-1-5-21-..." />
        <//>
        <${Field} label="账户不一致时">
          <select class="input" value=${form.run_as_sid_action} onChange=${(e) => patch('run_as_sid_action', e.target.value)}>
            <option value="abort">立即退出（退出码 2）</option>
            <option value="warn">仅告警继续</option>
          </select>
        <//>
      </div>
    </div>
  </div>`;
}

/* ------------------------------ 运行报告 ------------------------------ */

function ReportPage({ data, refresh, notify }) {
  const [payload, setPayload] = useState(data.report);
  const [logs, setLogs] = useState(data.logs);

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

  useEffect(() => { setPayload(data.report); setLogs(data.logs); }, [data.report, data.logs]);

  const report = payload.report;
  const summary = report ? report.summary : null;

  return html`<div class="page">
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>运行报告</h1>
        <div class="desc">${payload.updated_at ? `最近更新 ${payload.updated_at}` : '还没有运行记录'}</div>
      </div>
      <button class="btn" onClick=${reload}><${Icon} name="refresh-cw" />刷新</button>
    </div>

    ${!report
      ? html`<div class="card empty">运行一次脚本链后这里会显示汇总报告</div>`
      : html`<div style=${{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div class="card" style=${{ padding: '16px' }}>
            <div class="row" style=${{ marginBottom: '10px' }}>
              <div class="grow" style=${{ fontSize: '14px', fontWeight: 650 }}>${report.chain}</div>
              <span class=${'badge ' + (report.exit_code === 0 ? 'badge-ok' : 'badge-fail')}>
                退出码 ${report.exit_code}${report.deadline_hit ? ' · 到截止' : ''}
              </span>
            </div>
            <div class="grid-3">
              <div>
                <div class="kv"><span class="k">开始</span><span class="v mono">${report.started_at || '-'}</span></div>
                <div class="kv"><span class="k">结束</span><span class="v mono">${report.ended_at || '-'}</span></div>
                <div class="kv"><span class="k">耗时</span><span class="v">${report.duration_sec}s</span></div>
              </div>
              <div>
                <div class="kv"><span class="k">成功</span><span class="v">${summary.ok || 0}</span></div>
                <div class="kv"><span class="k">失败</span><span class="v">${summary.fail || 0}</span></div>
                <div class="kv"><span class="k">总计</span><span class="v">${summary.total || 0}</span></div>
              </div>
              <div>
                <div class="kv"><span class="k">配置错误</span><span class="v">${summary.config_error || 0}</span></div>
                <div class="kv"><span class="k">环境异常</span><span class="v">${summary.env_error || 0}</span></div>
                <div class="kv"><span class="k">未找到</span><span class="v">${summary.missing || 0}</span></div>
              </div>
            </div>
          </div>

          <div class="card" style=${{ padding: '12px' }}>
            <div class="section-title" style=${{ padding: '2px 4px 10px' }}>脚本明细</div>
            ${report.scripts.map((item) => html`<div class="row" style=${{ padding: '8px 6px', borderTop: '1px solid var(--line)' }} key=${item.index}>
              <div style=${{ width: '28px' }} class="mono muted small">${item.index + 1}</div>
              <div class="grow" style=${{ minWidth: 0 }}>
                <div style=${{ fontSize: '12.5px' }}>${item.display_name || item.script_path || '(未命名)'}</div>
                <div class="small muted">${item.end_reason || ''} ${item.error || ''}</div>
              </div>
              <span class=${'badge ' + (item.verdict === 'ok' ? 'badge-ok' : item.verdict === 'config_error' ? 'badge-cfg' : 'badge-fail')}>
                ${item.verdict}
              </span>
            </div>`) }
          </div>

          ${payload.text ? html`<div class="card" style=${{ padding: '12px' }}>
            <div class="section-title" style=${{ padding: '2px 4px 8px' }}>文本报告</div>
            <div class="log" style=${{ maxHeight: '220px' }}>${payload.text}</div>
          </div>` : null}
        </div>`}

    <div class="card" style=${{ padding: '12px' }}>
      <div class="row" style=${{ padding: '2px 4px 8px' }}>
        <div class="grow section-title">最近日志</div>
        <span class="small muted mono">${logs.file || ''}</span>
      </div>
      <div class="log">${logs.lines.length ? logs.lines.join('\n') : '暂无日志'}</div>
    </div>
  </div>`;
}

/* ------------------------------ 推送 ------------------------------ */

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
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>推送</h1>
        <div class="desc">脚本失败会立即推送一条，整轮结束再推送汇总</div>
      </div>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>
    </div>

    <div class="card" style=${{ padding: '16px' }}>
      <${Field} label="通知标题">
        <input class="input" value=${form.title} onInput=${(e) => setForm({ ...form, title: e.target.value })} />
      <//>
      <div class="mt">
        <${ToggleRow} title="启用推送" on=${form.enable_notify} onChange=${(value) => setForm({ ...form, enable_notify: value })} />
        <${ToggleRow} title="启动前通知" desc="每轮开始前推送一条" on=${form.enable_before_notify} onChange=${(value) => setForm({ ...form, enable_before_notify: value })} />
      </div>
      <div class="small muted mt">推送渠道（企业微信、钉钉、飞书等）在 config/notify.yml 中配置，修改后重启生效。</div>
    </div>
  </div>`;
}

/* ------------------------------ 设置 ------------------------------ */

function SettingsPage({ data, refresh, notify }) {
  const [form, setForm] = useState(data.settings);
  const [busy, setBusy] = useState(false);

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
      notify('下载源设置已保存，重启后用于代码同步');
      refresh();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  return html`<div class="page">
    <div class="row" style=${{ alignItems: 'flex-start' }}>
      <div class="grow">
        <h1>设置</h1>
        <div class="desc">版本 ${data.version} · 国内环境默认使用 CNB 源</div>
      </div>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}><${Icon} name="save" />保存</button>
    </div>

    <div class="card" style=${{ padding: '16px' }}>
      <div class="grid-2">
        <${Field} label="代码主仓库" hint="影响代码同步与更新检查的优先源">
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
        <div class="row" style=${{ flexWrap: 'wrap' }}>
          ${form.resource_source_order.map((source, index) => html`<span class="tag ${index === 0 ? 'tag-pre' : ''}" key=${source}>
            ${index + 1}. ${source}
          </span>`)}
        </div>
      </div>
    </div>

    <div class="card" style=${{ padding: '16px' }}>
      <div class="section-title" style=${{ marginBottom: '8px' }}>运行环境</div>
      <div class="kv"><span class="k">Python</span><span class="v mono">${data.version}</span></div>
      <div class="kv"><span class="k">今日状态</span><span class="v">${data.status.daily_label}</span></div>
      <div class="kv"><span class="k">常驻调度</span><span class="v">${data.status.scheduler_pid ? `运行中 (pid=${data.status.scheduler_pid})` : '未运行'}</span></div>
      <div class="kv"><span class="k">配置文件</span><span class="v mono">config/run.yml</span></div>
    </div>
  </div>`;
}

/* ------------------------------ 应用 ------------------------------ */

function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [page, setPage] = useState('chains');
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

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
    // 轮询只更新状态，避免覆盖正在编辑的表单
    const timer = setInterval(refreshStatus, 8000);
    return () => clearInterval(timer);
  }, [refresh, refreshStatus]);

  if (error) {
    return html`<div class="app"><div class="main" style=${{ display: 'grid', placeItems: 'center' }}>
      <div class="empty">
        <div style=${{ fontSize: '15px', color: '#ff8585', marginBottom: '8px' }}>无法连接后端</div>
        <div class="mono small">${error}</div>
      </div>
    </div></div>`;
  }

  if (!data) {
    return html`<div class="app"><div class="main" style=${{ display: 'grid', placeItems: 'center' }}>
      <div class="empty">加载中…</div>
    </div></div>`;
  }

  const status = data.status;
  const launched = status.launched;
  const badgeClass = DAILY_BADGE[status.daily_status] || 'badge-miss';

  const pageNode = {
    chains: html`<${ChainsPage} data=${data} refresh=${refresh} notify=${notify} />`,
    schedule: html`<${SchedulePage} data=${data} refresh=${refresh} notify=${notify} />`,
    policy: html`<${PolicyPage} data=${data} refresh=${refresh} notify=${notify} />`,
    report: html`<${ReportPage} data=${data} refresh=${refresh} notify=${notify} />`,
    notify: html`<${NotifyPage} data=${data} refresh=${refresh} notify=${notify} />`,
    settings: html`<${SettingsPage} data=${data} refresh=${refresh} notify=${notify} />`,
  }[page];

  return html`<div class="app">
    <nav class="rail">
      <div class="logo">千</div>
      ${PAGES.map((item) => html`<button
        key=${item.key}
        class=${'nav-item' + (page === item.key ? ' active' : '')}
        data-tip=${item.label}
        onClick=${() => setPage(item.key)}
      ><${Icon} name=${item.icon} /></button>`)}
    </nav>

    <div class="main">
      <header class="topbar">
        <span class=${'badge ' + badgeClass}>今日 ${status.daily_label}</span>
        <span class="small muted">${status.today} ${status.weekday_label}</span>
        ${launched.pid
          ? html`<span class="tag tag-pre">运行中：${launched.chain} (pid=${launched.pid})</span>`
          : null}
        ${status.runner_pid ? html`<span class="tag">运行实例 pid=${status.runner_pid}</span>` : null}
        ${status.scheduler_pid ? html`<span class="tag">调度 pid=${status.scheduler_pid}</span>` : null}
        <div class="grow"></div>
        <button class="btn btn-ghost" onClick=${refresh} title="刷新"><${Icon} name="refresh-cw" /></button>
      </header>
      <main class="content">${pageNode}</main>
    </div>

    ${toast ? html`<div class=${'toast' + (toast.isError ? ' err' : '')}>${toast.message}</div>` : null}
  </div>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);

