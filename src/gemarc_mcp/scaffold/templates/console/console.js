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

const TOKEN_KEY = 'gemarc.mcp.token';

/**
 * The bearer token lives in this tab's sessionStorage only (never
 * localStorage, never a URL). Storage can be blocked or throw, so every access
 * is guarded and the in-memory copy in `state.token` is the working value.
 */
function readStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

function storeToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* the in-memory copy still works for this page load */ }
}

const state = {
  tools: [],
  resources: [],
  prompts: [],
  selected: null,
  writeEnabled: false,
  health: null,
  history: [],
  seq: 0,
  token: readStoredToken(),
  bootSeq: 0,
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
  // The token this request carries: a 401 that arrives late is judged against it, not against
  // whatever token the user has pasted in the meantime.
  const tokenUsed = state.token;
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
  };
  if (tokenUsed) headers.Authorization = `Bearer ${tokenUsed}`;
  const response = await fetch(MCP_PATH, {
    method: 'POST',
    headers,
    body: JSON.stringify({ jsonrpc: '2.0', id: ++state.seq, method, params }),
  });
  if (response.status === 401) {
    const err = new Error(tokenUsed
      ? '令牌无效或已被轮换 (HTTP 401)。请在上方重新粘贴令牌。'
      : '该服务器需要访问令牌 (HTTP 401)。请在上方粘贴令牌。');
    err.unauthorized = true;
    err.tokenUsed = tokenUsed;
    throw err;
  }
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
    // JSON-RPC errors are objects; the server's own early answers (503, 405, ...) are {"error": "text"}.
    const message = typeof data.error === 'string'
      ? `${data.error} (HTTP ${response.status})`
      : (data.error.message || 'JSON-RPC 错误');
    const err = new Error(message);
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
  // The server's own list wins over the one injected into the page.
  for (const name of health.writeTools || []) WRITE_TOOLS.add(name);
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
  bar.append(chip('toolset', health.toolset || '—'));
  bar.append(chip('tools', Array.isArray(health.tools) ? health.tools.length : '—'));
  bar.append(chip('seed', health.seedProject || '—'));
  bar.append(chip('static site', health.staticSiteSource || '—'));
  bar.append(chip(
    'auth',
    health.authRequired ? 'bearer token' : 'open',
    health.authRequired ? 'good' : 'warn'
  ));
  const writeable = state.writeEnabled && WRITE_TOOLS.size > 0;
  bar.append(chip(
    'write tools',
    WRITE_TOOLS.size === 0 ? 'none in this toolset' : (state.writeEnabled ? 'enabled' : 'disabled'),
    writeable ? 'good' : 'warn'
  ));
  if (health.status && health.status !== 'ok') {
    bar.append(chip('status', `${health.status} · session manager ${health.sessionManager || '?'}`, 'bad'));
  }

  $('#foot-meta').textContent =
    `server ${health.server || 'gemarc-mcp'} ${health.version || ''} · ${health.writeEnabled ? '写工具已开启' : '写工具默认关闭'}`;
}

/* --------------------------------------------------------------------- auth */

function setAuthMessage(text, cls) {
  const msg = $('#token-msg');
  msg.textContent = text || '';
  msg.className = cls || 'muted';
}

function showAuthBar(message, cls) {
  $('#authbar').hidden = false;
  setAuthMessage(message, cls);
}

/**
 * A 401 means the token that request carried is wrong or missing: forget it and ask again.
 * A late 401 for an older token (a new one has been pasted since) is ignored and reported as not
 * handled, so it can neither discard the newer token nor flip the status line.
 */
function handleUnauthorized(error) {
  if ((error.tokenUsed || '') !== state.token) return false;
  state.token = '';
  storeToken('');
  showAuthBar(error.message, 'bad');
  $('#token-input').focus();
  return true;
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

/** The call button follows the selected tool: locked for a write tool while writes are off. */
function syncCallButton() {
  const blocked = Boolean(state.selected) && WRITE_TOOLS.has(state.selected) && !state.writeEnabled;
  $('#call-btn').disabled = blocked;
  $('#call-btn').textContent = blocked ? '写工具已关闭' : '调用';
}

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

  syncCallButton();

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
    syncCallButton();
    return;
  }

  const started = performance.now();
  try {
    const result = await rpc('tools/call', { name, arguments: args });
    const elapsed = Math.round(performance.now() - started);
    const payload = unwrapToolResult(result);
    const isError = Boolean(result && result.isError);
    // The user may have selected another tool while this call was in flight.
    if (state.selected === name) showResult(payload, isError, `${name} · ${elapsed}ms`);
    pushHistory({ name, ms: elapsed, ok: !isError });
  } catch (error) {
    const elapsed = Math.round(performance.now() - started);
    if (error.unauthorized) handleUnauthorized(error);
    if (state.selected === name) showError(error.message, error.detail, `${name} · ${elapsed}ms`);
    pushHistory({ name, ms: elapsed, ok: false });
  } finally {
    syncCallButton();
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
    if (error.unauthorized) handleUnauthorized(error);
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
    if (error.unauthorized) handleUnauthorized(error);
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
  // A newer boot() (a token was pasted meanwhile) owns the status line; an older one must not touch it.
  const generation = ++state.bootSeq;
  const current = () => generation === state.bootSeq;
  let healthShown = false;
  try {
    // Health first: it says whether a token is needed, so no MCP call is made
    // (and none can 401) before the user has had a chance to supply one.
    const health = await loadHealth();
    if (!current()) return;
    renderStatus(health);
    healthShown = true;
    if (health.authRequired) {
      showAuthBar(state.token ? '已使用本标签页保存的令牌。' : '该服务器需要访问令牌,请粘贴后点「使用令牌」。',
        state.token ? 'good' : 'warn');
      if (!state.token) {
        dot.className = 'dot off';
        text.textContent = '需要访问令牌';
        return;
      }
    }
    const [toolResult, resourceResult, promptResult] = await Promise.all([
      rpc('tools/list', {}),
      rpc('resources/list', {}),
      rpc('prompts/list', {}),
    ]);
    if (!current()) return;
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
    if (!current()) return;
    dot.className = 'dot off';
    if (error.unauthorized) {
      if (handleUnauthorized(error)) text.textContent = '需要访问令牌';
      return;
    }
    text.textContent = `连接失败: ${error.message}`;
    const bar = $('#statbar');
    if (!healthShown) bar.textContent = '';  // keep the health chips (a degraded state included) once shown
    bar.append(el('span', 'stat bad', error.message));
  }
}

function useToken() {
  const input = $('#token-input');
  const value = input.value.trim();
  input.value = '';
  if (!value) {
    setAuthMessage('令牌为空。', 'bad');
    return;
  }
  // A header value is Latin-1 without control characters. A zero-width space or a smart quote that came
  // along with a copy would make every fetch throw before any request is sent, with the token still stored.
  if (!/^[\x20-\x7e]+$/.test(value)) {
    setAuthMessage('令牌里有不可见字符或非 ASCII 字符(常见于复制时带入的零宽空格、弯引号),请重新复制。', 'bad');
    return;
  }
  state.token = value;
  storeToken(value);
  setAuthMessage('正在用该令牌连接…', 'muted');
  boot();
}

function clearToken() {
  state.token = '';
  storeToken('');
  $('#token-input').value = '';
  setAuthMessage('已清除本标签页里的令牌。', 'muted');
}

$('#token-save').addEventListener('click', useToken);
$('#token-clear').addEventListener('click', clearToken);
$('#token-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') useToken();
});
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
