/*
 * GemArc MCP console.
 *
 * The panel talks to the server's own MCP endpoint over Streamable HTTP. It
 * invents nothing: the tool list, the parameter forms and the input schemas
 * all come from `tools/list`, and every call goes through `tools/call`. A
 * failure is shown as the raw JSON-RPC error rather than a friendly rewrite.
 */

const CFG = window.GEMARC || {};
const MCP_PATH = CFG.mcpPath || '/mcp';
const HEALTH_PATH = CFG.healthPath || '/healthz';
const WRITE_TOOLS = new Set(CFG.writeTools || []);

const state = {
  tools: [],
  resources: [],
  prompts: [],
  selected: null,
  writeEnabled: false,
  health: null,
  history: [],
  seq: 0,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ---------------------------------------------------------------- transport */

async function rpc(method, params) {
  const response = await fetch(MCP_PATH, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: ++state.seq, method, params }),
  });
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    const err = new Error(`服务端返回的不是 JSON (HTTP ${response.status})`);
    err.detail = raw.slice(0, 500);
    throw err;
  }
  if (data.error) {
    const err = new Error(data.error.message || 'JSON-RPC 错误');
    err.detail = data.error;
    throw err;
  }
  return data.result;
}

/** MCP tool results arrive as a content array; ours are JSON in a text part. */
function unwrapToolResult(result) {
  if (!result) return null;
  const parts = Array.isArray(result.content) ? result.content : [];
  for (const part of parts) {
    if (part && part.type === 'text' && typeof part.text === 'string') {
      try {
        return JSON.parse(part.text);
      } catch {
        return part.text;
      }
    }
  }
  return result;
}

/* -------------------------------------------------------------- json render */

function jsonTree(value, key) {
  const frag = document.createDocumentFragment();
  const row = el('div', 'jrow');

  if (value === null) {
    row.append(key ? el('span', 'jk', key + ': ') : document.createTextNode(''));
    row.append(el('span', 'jv-nil', 'null'));
    frag.append(row);
    return frag;
  }
  if (Array.isArray(value)) {
    row.append(key ? el('span', 'jk', key + ': ') : document.createTextNode(''));
    row.append(el('span', 'jv-b', '[' + value.length + ']'));
    frag.append(row);
    if (value.length) {
      const box = el('div', 'j');
      for (const item of value.slice(0, 200)) box.append(jsonTree(item, null));
      if (value.length > 200) box.append(el('div', 'jv-nil', `… 其余 ${value.length - 200} 项已省略`));
      frag.append(box);
    }
    return frag;
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value);
    row.append(key ? el('span', 'jk', key + ': ') : document.createTextNode(''));
    row.append(el('span', 'jv-b', '{' + keys.length + '}'));
    frag.append(row);
    if (keys.length) {
      const box = el('div', 'j');
      for (const k of keys.slice(0, 200)) box.append(jsonTree(value[k], k));
      if (keys.length > 200) box.append(el('div', 'jv-nil', `… 其余 ${keys.length - 200} 个键已省略`));
      frag.append(box);
    }
    return frag;
  }
  row.append(key ? el('span', 'jk', key + ': ') : document.createTextNode(''));
  const cls = typeof value === 'number' ? 'jv-n' : typeof value === 'boolean' ? 'jv-b' : 'jv-s';
  row.append(el('span', cls, JSON.stringify(value)));
  frag.append(row);
  return frag;
}

function renderJson(target, value) {
  target.textContent = '';
  if (value === undefined || value === null) {
    target.append(el('p', 'muted', '（空）'));
    return;
  }
  if (typeof value === 'string') {
    target.append(el('pre', '', value));
    target.firstChild.style.whiteSpace = 'pre-wrap';
    return;
  }
  target.append(jsonTree(value, null));
}

/* ------------------------------------------------------------------- status */

async function loadHealth() {
  const response = await fetch(HEALTH_PATH, { cache: 'no-store' });
  if (!response.ok) throw new Error(`healthz 返回 HTTP ${response.status}`);
  return response.json();
}

function renderStatus(health) {
  state.health = health;
  state.writeEnabled = Boolean(health.writeEnabled);
  const bar = $('#statbar');
  bar.textContent = '';

  const chip = (label, value, cls) => {
    const node = el('span', 'stat' + (cls ? ' ' + cls : ''));
    node.append(el('span', '', label));
    node.append(el('b', '', String(value)));
    return node;
  };

  bar.append(chip('transport', health.transport || '—'));
  bar.append(chip('protocol path', health.protocolPath || '—'));
  bar.append(chip('workspace', health.workspace || '—'));
  bar.append(chip('seed', `${health.seedProject}${health.seeded ? ' ✓' : ''}`));
  bar.append(chip('static site', health.staticSiteSource || '—'));
  bar.append(chip(
    'write tools',
    state.writeEnabled ? 'enabled' : 'disabled',
    state.writeEnabled ? 'good' : 'warn'
  ));
  if (health.sessionManagerError) {
    bar.append(chip('session manager', health.sessionManagerError, 'bad'));
  }

  $('#foot-meta').textContent =
    `server ${health.server || 'gemarc-mcp'} · ${health.writeEnabled ? '写工具已开启' : '写工具默认关闭'}`;
}

/* -------------------------------------------------------------- tool panel */

function toolCard(tool) {
  const card = el('button', 'tool-item' + (WRITE_TOOLS.has(tool.name) ? ' write' : ''));
  card.type = 'button';
  const name = el('span', 'tname', tool.name);
  const schema = tool.inputSchema || {};
  const count = Object.keys(schema.properties || {}).length;
  card.append(name);
  card.append(el('span', 'tag', count ? `${count}p` : ''));
  card.append(el('span', 'tdesc', tool.description || ''));
  card.addEventListener('click', () => selectTool(tool.name));
  return card;
}

function renderToolList(filterText) {
  const list = $('#tool-list');
  list.textContent = '';
  const needle = (filterText || '').trim().toLowerCase();
  const match = (tool) => !needle
    || tool.name.toLowerCase().includes(needle)
    || (tool.description || '').toLowerCase().includes(needle);

  const groups = [
    ['只读 / READ', state.tools.filter((t) => !WRITE_TOOLS.has(t.name) && match(t))],
    ['写入 / WRITE', state.tools.filter((t) => WRITE_TOOLS.has(t.name) && match(t))],
  ];
  for (const [label, tools] of groups) {
    if (!tools.length) continue;
    list.append(el('p', 'tool-group', label));
    for (const tool of tools) {
      const card = toolCard(tool);
      if (state.selected === tool.name) card.classList.add('active');
      list.append(card);
    }
  }
  if (!list.childElementCount) list.append(el('p', 'empty', '没有匹配的工具'));
}

/* ---------------------------------------------------------------- the form */

function selectTool(name) {
  state.selected = name;
  const tool = state.tools.find((t) => t.name === name);
  if (!tool) return;
  renderToolList($('#tool-filter').value);
  $('#tool-name').textContent = tool.name;
  $('#tool-desc').textContent = tool.description || '(该工具没有描述)';

  const kind = $('#tool-kind');
  kind.textContent = WRITE_TOOLS.has(name) ? 'WRITE · 需要写权限' : 'READ · 只读';
  kind.className = 'chip ' + (WRITE_TOOLS.has(name) ? 'write' : 'read');

  const blocked = WRITE_TOOLS.has(name) && !state.writeEnabled;
  $('#call-btn').disabled = blocked;
  $('#call-btn').textContent = blocked ? '写工具已关闭' : '调用';

  renderParams(tool);
  renderJson($('#schema'), tool.inputSchema || {});
  $('#result').className = 'result';
  $('#result').textContent = '';
  $('#result').append(el('p', 'muted', '参数已就绪,点「调用」执行。'));
  $('#result-meta').textContent = '尚未调用';
  $('#copy-btn').hidden = true;
}

function renderParams(tool) {
  const form = $('#params');
  form.textContent = '';
  const schema = tool.inputSchema || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const names = Object.keys(properties);

  if (!names.length) {
    form.append(el('p', 'empty', '该工具没有参数。'));
    return;
  }

  for (const name of names) {
    const spec = properties[name] || {};
    const field = el('div', 'field');
    const label = el('label');
    label.append(el('span', 'fname', name));
    if (required.has(name)) label.append(el('span', 'req', '必填'));
    label.append(el('span', 'ftype', Array.isArray(spec.type) ? spec.type.join('|') : (spec.type || 'any')));
    field.append(label);
    if (spec.description) field.append(el('span', 'fhint', spec.description));
    field.dataset.name = name;
    field.dataset.kind = 'string';

    if (Array.isArray(spec.enum) && spec.enum.length) {
      const select = el('select');
      select.append(el('option', '', '(未设置)'));
      for (const value of spec.enum) {
        const option = el('option', '', String(value));
        option.value = String(value);
        select.append(option);
      }
      field.append(select);
      field.dataset.kind = 'enum';
    } else if (spec.type === 'boolean') {
      const wrap = el('label', 'check');
      const input = el('input');
      input.type = 'checkbox';
      wrap.append(input);
      wrap.append(el('span', '', '启用'));
      field.append(wrap);
      field.dataset.kind = 'boolean';
    } else if (spec.type === 'integer' || spec.type === 'number') {
      const input = el('input');
      input.type = 'number';
      if (typeof spec.default === 'number') input.value = String(spec.default);
      field.append(input);
      field.dataset.kind = 'number';
    } else if (spec.type === 'array' || spec.type === 'object') {
      const area = el('textarea');
      area.placeholder = spec.type === 'array' ? 'JSON 数组,例如 ["a","b"]' : 'JSON 对象';
      if (spec.default !== undefined) area.value = JSON.stringify(spec.default);
      field.append(area);
      field.dataset.kind = 'json';
    } else {
      const long = (spec.description || '').length > 90 || name.endsWith('_model');
      const input = long ? el('textarea') : el('input');
      if (!long) input.type = 'text';
      input.placeholder = spec.default !== undefined ? `默认 ${JSON.stringify(spec.default)}` : '';
      field.append(input);
      field.dataset.kind = 'string';
    }
    form.append(field);
  }
}

function collectParams() {
  const args = {};
  for (const field of $('#params').querySelectorAll('.field')) {
    const name = field.dataset.name;
    const kind = field.dataset.kind;
    if (kind === 'boolean') {
      const input = field.querySelector('input[type=checkbox]');
      if (input.checked) args[name] = true;
      continue;
    }
    const control = field.querySelector('input, select, textarea');
    if (!control) continue;
    const value = control.value;
    if (value === '' || value === undefined) continue;
    if (kind === 'number') {
      const num = Number(value);
      if (!Number.isNaN(num)) args[name] = num;
      continue;
    }
    if (kind === 'json') {
      try {
        args[name] = JSON.parse(value);
      } catch (error) {
        throw new Error(`参数 ${name} 不是合法 JSON: ${error.message}`);
      }
      continue;
    }
    args[name] = value;
  }
  return args;
}

/* ----------------------------------------------------------------- calling */

async function callSelected() {
  const name = state.selected;
  if (!name) return;
  const button = $('#call-btn');
  button.disabled = true;
  button.textContent = '调用中…';

  let args = {};
  try {
    args = collectParams();
  } catch (error) {
    showError(error.message, null);
    button.disabled = false;
    button.textContent = '调用';
    return;
  }

  const started = performance.now();
  try {
    const result = await rpc('tools/call', { name, arguments: args });
    const elapsed = Math.round(performance.now() - started);
    const payload = unwrapToolResult(result);
    const isError = Boolean(result && result.isError);
    showResult(payload, isError, `${name} · ${elapsed}ms`);
    pushHistory({ name, ms: elapsed, ok: !isError });
  } catch (error) {
    const elapsed = Math.round(performance.now() - started);
    showError(error.message, error.detail, `${name} · ${elapsed}ms`);
    pushHistory({ name, ms: elapsed, ok: false });
  } finally {
    button.disabled = false;
    button.textContent = '调用';
  }
}

function showResult(payload, isError, meta) {
  const box = $('#result');
  box.className = 'result' + (isError ? ' error' : '');
  renderJson(box, payload);
  $('#result-meta').textContent = meta;
  $('#copy-btn').hidden = false;
}

function showError(message, detail, meta) {
  const box = $('#result');
  box.className = 'result error';
  box.textContent = '';
  box.append(el('pre', '', message));
  if (detail) box.append(el('pre', '', JSON.stringify(detail, null, 2)));
  $('#result-meta').textContent = meta || '调用失败';
  $('#copy-btn').hidden = false;
}

function pushHistory(entry) {
  state.history.unshift(entry);
  state.history = state.history.slice(0, 12);
  const list = $('#history');
  list.textContent = '';
  if (!state.history.length) {
    list.append(el('p', 'empty', '暂无调用记录'));
    return;
  }
  for (const item of state.history) {
    const row = el('div', 'item');
    row.append(el('code', '', item.name));
    const right = el('span', item.ok ? 'ok' : 'fail', `${item.ms}ms`);
    row.append(right);
    list.append(row);
  }
}

/* ------------------------------------------------------- resources, prompts */

async function readResource(uri) {
  try {
    const result = await rpc('resources/read', { uri });
    const contents = (result && result.contents) || [];
    const text = contents.map((c) => c.text || '').join('\n');
    const box = $('#result');
    box.className = 'result';
    let parsed = text;
    try {
      parsed = JSON.parse(text);
    } catch { /* keep the raw text */ }
    renderJson(box, parsed);
    $('#result-meta').textContent = `resource ${uri}`;
    $('#copy-btn').hidden = false;
  } catch (error) {
    showError(error.message, error.detail, `resource ${uri}`);
  }
}

async function getPrompt(name) {
  try {
    const result = await rpc('prompts/get', { name, arguments: { project: 'demo' } });
    const messages = (result && result.messages) || [];
    const text = messages
      .map((m) => (m.content && m.content.text) || '')
      .join('\n\n');
    const box = $('#result');
    box.className = 'result';
    box.textContent = '';
    box.append(el('pre', '', text));
    $('#result-meta').textContent = `prompt ${name} · project=demo`;
    $('#copy-btn').hidden = false;
  } catch (error) {
    showError(error.message, error.detail, `prompt ${name}`);
  }
}

function renderSideLists() {
  const res = $('#resources');
  res.textContent = '';
  if (!state.resources.length) res.append(el('p', 'empty', '无资源'));
  for (const resource of state.resources) {
    const row = el('div', 'item');
    row.append(el('code', '', resource.uri || resource.name || '?'));
    const button = el('button', 'mini', '读取');
    button.type = 'button';
    button.addEventListener('click', () => readResource(resource.uri));
    row.append(button);
    res.append(row);
  }

  const pro = $('#prompts');
  pro.textContent = '';
  if (!state.prompts.length) pro.append(el('p', 'empty', '无提示词'));
  for (const prompt of state.prompts) {
    const row = el('div', 'item');
    row.append(el('code', '', prompt.name || '?'));
    const button = el('button', 'mini', '预览');
    button.type = 'button';
    button.addEventListener('click', () => getPrompt(prompt.name));
    row.append(button);
    pro.append(row);
  }
}

/* -------------------------------------------------------------------- boot */

async function boot() {
  const dot = $('#conn-dot');
  const text = $('#conn-text');
  try {
    const [health, toolResult, resourceResult, promptResult] = await Promise.all([
      loadHealth(),
      rpc('tools/list', {}),
      rpc('resources/list', {}),
      rpc('prompts/list', {}),
    ]);
    renderStatus(health);
    state.tools = (toolResult && toolResult.tools) || [];
    state.resources = (resourceResult && resourceResult.resources) || [];
    state.prompts = (promptResult && promptResult.prompts) || [];
    renderToolList('');
    renderSideLists();
    pushHistory({ name: '(连接建立)', ms: 0, ok: true });
    dot.className = 'dot on';
    text.textContent = `已连接 · ${state.tools.length} tools / ${state.resources.length} resources / ${state.prompts.length} prompts`;
    if (state.tools.length) selectTool(state.tools[0].name);
  } catch (error) {
    dot.className = 'dot off';
    text.textContent = `连接失败: ${error.message}`;
    $('#statbar').textContent = '';
    $('#statbar').append(el('span', 'stat bad', error.message));
  }
}

$('#call-btn').addEventListener('click', callSelected);
$('#tool-filter').addEventListener('input', (event) => renderToolList(event.target.value));
$('#copy-btn').addEventListener('click', async () => {
  const text = $('#result').innerText;
  try {
    await navigator.clipboard.writeText(text);
    $('#result-meta').textContent = '已复制到剪贴板';
  } catch {
    $('#result-meta').textContent = '浏览器拒绝了剪贴板访问,请手动复制';
  }
});

boot();
