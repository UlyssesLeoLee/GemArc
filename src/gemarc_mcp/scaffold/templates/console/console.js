/*
 * GemArc MCP console.
 *
 * The panel talks to the server's own MCP endpoint over Streamable HTTP. It
 * invents nothing: the tool list, the parameter forms and the input schemas
 * all come from `tools/list`, and every call goes through `tools/call`. A
 * failure is shown as the raw JSON-RPC error rather than a friendly rewrite.
 *
 * Every word the reader sees comes from the language tables the server injects
 * (`window.GEMARC.i18n`); this file has no text of its own (BD-010 section 12.7).
 * A key that is not in the table returns the key itself and is recorded, so a
 * gap shows up instead of quietly mixing two languages.
 */

const CFG = window.GEMARC || {};
const MCP_PATH = CFG.mcpPath || '/mcp';
const HEALTH_PATH = CFG.healthPath || '/healthz';
const WRITE_TOOLS = new Set(CFG.writeTools || []);
// The write permission of THIS caller, injected by the server from the signed-in session. The
// administrator role and the write permission are two different things: an administrator may still
// be a plain reader, and a reader never gets the write tools.
const MAY_WRITE = CFG.write === true;

const TOKEN_KEY = 'gemarc.mcp.token';

/* The settings of the signed-in account (DD-010). The server holds them, so the panel
   only reads them and writes them back; it never keeps a second copy of the truth. */
const SETTINGS_PATH = CFG.settingsPath || '/account/settings';
const CSRF = CFG.csrf || '';

/* ------------------------------------------------------------------- i18n */

const LANGUAGES = ['zh-CN', 'ja-JP', 'en-US'];
const DEFAULT_LANG = 'zh-CN';
const LANG_KEY = 'gemarc.console.lang';
const I18N = CFG.i18n || {};

/** The gaps: a key that was looked up and not found. The console never mixes languages silently. */
window.__GEMARC_I18N_MISSING__ = [];

/** One of the three languages, or null. `ja`, `ja-JP` and `JA` all mean `ja-JP`. */
function normalizeLang(value) {
  if (!value) return null;
  const text = String(value).trim();
  for (const lang of LANGUAGES) {
    if (text.toLowerCase() === lang.toLowerCase()) return lang;
  }
  const base = text.split('-')[0].toLowerCase();
  for (const lang of LANGUAGES) {
    if (lang.split('-')[0].toLowerCase() === base) return lang;
  }
  return null;
}

/**
 * The language of this panel: the account's own setting, then the choice the reader
 * made, then the language the server used, then the browser.
 *
 * The account's setting comes first (DD-010): it is the same on every machine, and the
 * agent side reads the very same value for the tool descriptions. The local choice only
 * counts while nobody is signed in.
 */
function pickLang() {
  const account = normalizeLang(CFG.accountLang);
  if (account) return account;
  try {
    const stored = normalizeLang(localStorage.getItem(LANG_KEY));
    if (stored) return stored;
  } catch { /* the storage can be blocked; the other sources still work */ }
  return normalizeLang(CFG.lang) || normalizeLang(navigator.language || '') || DEFAULT_LANG;
}

let LANG = pickLang();

/** One string of the current language. A missing key returns the key itself and is recorded. */
function t(key, vars) {
  const table = I18N[LANG] || {};
  const raw = table[key];
  if (typeof raw !== 'string' || !raw) {
    window.__GEMARC_I18N_MISSING__.push(`${LANG}/${key}`);
    return key;
  }
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (m, name) => (
    Object.hasOwn(vars, name) && vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : m));
}

/** Put the language into the page: the text nodes, the placeholders and the switcher. */
function applyLanguage() {
  document.documentElement.lang = LANG;
  for (const node of document.querySelectorAll('[data-i18n]')) {
    const key = node.getAttribute('data-i18n');
    if (key) node.textContent = t(key);
  }
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) {
    const key = node.getAttribute('data-i18n-placeholder');
    if (key) node.setAttribute('placeholder', t(key));
  }
  for (const button of document.querySelectorAll('.lang-btn')) {
    const on = button.dataset.uilang === LANG;
    button.classList.toggle('on', on);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

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
  sessionAuth: false,
  bootSeq: 0,
  settings: null,
  settingsMode: 'all',
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
      ? t('err.token401')
      : (state.sessionAuth
        ? t('err.session401')
        : t('err.needtoken401')));
    err.unauthorized = true;
    err.tokenUsed = tokenUsed;
    throw err;
  }
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    const err = new Error(t('err.notjson', { status: response.status }));
    err.detail = raw.slice(0, 500);
    throw err;
  }
  if (data.error) {
    // JSON-RPC errors are objects; the server's own early answers (503, 405, ...) are {"error": "text"}.
    const message = typeof data.error === 'string'
      ? `${data.error} (HTTP ${response.status})`
      : (data.error.message || t('err.jsonrpc'));
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
      if (value.length > 200) box.append(el('div', 'jv-nil', t('json.moreitems', { count: value.length - 200 })));
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
      if (keys.length > 200) box.append(el('div', 'jv-nil', t('json.morekeys', { count: keys.length - 200 })));
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
    target.append(el('p', 'muted', t('json.empty')));
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
  if (!response.ok) throw new Error(t('err.healthz', { status: response.status }));
  return response.json();
}

function renderStatus(health) {
  state.health = health;
  state.writeEnabled = Boolean(health.writeEnabled);
  // The server's own list wins over the one injected into the page.
  for (const name of health.writeTools || []) WRITE_TOOLS.add(name);
  const bar = $('#statbar');
  bar.textContent = '';

  // The first screen answers one question only: what is the server right now.
  // At most five phrases, each one short; the protocol values are folded into
  // the details panel (BD-010 section 12.6).
  const phrase = (label, value, cls) => {
    const node = el('span', 'fact' + (cls ? ' ' + cls : ''));
    node.append(el('span', '', label));
    node.append(el('b', '', String(value)));
    return node;
  };

  const site = health.staticSiteSource || '';
  const writeable = state.writeEnabled && WRITE_TOOLS.size > 0;
  bar.append(phrase(t('status.tools'), Array.isArray(health.tools) ? health.tools.length : '—'));
  bar.append(phrase(t('status.permission'), writeable ? t('status.write') : t('status.read'), writeable ? 'good' : ''));
  bar.append(phrase(t('status.site'), site === 'not-built-yet' ? t('status.notbuilt') : (site || '—')));
  bar.append(phrase(t('status.login'), health.authRequired ? (health.sessionAuth ? t('status.signedin') : t('status.needtoken')) : t('status.open'),
    health.authRequired ? (health.sessionAuth ? 'good' : 'warn') : 'good'));
  if (health.status && health.status !== 'ok') {
    bar.append(phrase(t('status.state'), health.status, 'bad'));
  }

  // The technical values live in the folded details, not on the first screen.
  const body = $('#facts-detail');
  if (body) {
    body.textContent = '';
    const rows = [
      [t('detail.transport'), health.transport || '—'],
      [t('detail.protocolpath'), health.protocolPath || '—'],
      [t('detail.toolset'), health.toolset || '—'],
      [t('detail.seed'), health.seedProject || '—'],
      [t('detail.source'), site || '—'],
      [t('detail.writetools'), WRITE_TOOLS.size === 0 ? t('detail.nowritetools') : (state.writeEnabled ? t('detail.on') : t('detail.off'))],
    ];
    for (const [label, value] of rows) {
      const row = el('div');
      row.append(el('span', '', label));
      row.append(el('b', '', String(value)));
      body.append(row);
    }
  }

  $('#foot-meta').textContent =
    `${t('foot.server', { server: health.server || 'gemarc-mcp', version: health.version || '' })} · `
    + (state.writeEnabled ? t('foot.writeon') : t('foot.writeoff'));
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

/**
 * The bridge to the crystal scene of the tool surface.
 *
 * The scene is a module of its own: it needs the three-dimensional library,
 * and the console has to keep working when it is missing. So the two never
 * import each other - the panel tells the scene what the server registered,
 * and the scene tells the panel what was picked. One selection, two views.
 */
const spaceBridge = (window.GEMARC_SPACE = window.GEMARC_SPACE || {
  tools: [], scene: null, onPick: null, onHover: null,
});
spaceBridge.onPick = (name) => selectTool(name);
spaceBridge.onHover = (name) => setSpaceTip(name);

/**
 * The name of the crystal under the pointer. The scene never relies on the
 * colour alone, so the words carry the fact as well.
 * @param {string|null} name the tool
 */
function setSpaceTip(name) {
  const tip = $('#space-tip');
  if (!tip) return;
  if (!name) {
    tip.hidden = true;
    tip.textContent = '';
    return;
  }
  tip.textContent = `${name} · ${WRITE_TOOLS.has(name) ? t('legend.write') : t('legend.read')}`;
  tip.hidden = false;
}

/**
 * The crystals of the tool surface: one per registered tool.
 * @returns {Array<{name: string, write: boolean}>} the tools
 */
function spaceTools() {
  return state.tools.map((tool) => ({ name: tool.name, write: WRITE_TOOLS.has(tool.name) }));
}

/**
 * Hand the tool list to the scene (if it is there).
 */
function syncSpaceTools() {
  spaceBridge.tools = spaceTools();
  if (spaceBridge.scene) spaceBridge.scene.setTools(spaceBridge.tools);
}

/**
 * The title and the description of one tool in the language of this panel.
 *
 * The server sends the three languages of every tool in the `_meta` of the tool
 * (`gemarc.dev/descriptions`); the `description` field itself stays English, so
 * a client that reads nothing else still gets the canonical text.
 * @param {object} tool one tool of `tools/list`
 * @returns {{title: string, description: string}} the words
 */
function toolText(tool) {
  const meta = tool._meta && tool._meta['gemarc.dev/descriptions'];
  const entry = meta && typeof meta[LANG] === 'object' ? meta[LANG] : null;
  return {
    title: (entry && entry.title) || tool.title || tool.name,
    description: (entry && entry.description) || tool.description || '',
  };
}

function toolCard(tool) {
  const card = el('button', 'tool-item' + (WRITE_TOOLS.has(tool.name) ? ' write' : ''));
  card.type = 'button';
  const name = el('span', 'tname', tool.name);
  const schema = tool.inputSchema || {};
  const count = Object.keys(schema.properties || {}).length;
  card.append(name);
  card.append(el('span', 'tag', count ? `${count}p` : ''));
  card.append(el('span', 'tdesc', toolText(tool).description));
  card.addEventListener('click', () => selectTool(tool.name));
  return card;
}

/**
 * Whether the WRITE group belongs on screen.
 *
 * The server refuses a write tool to a read scope (403 insufficient_scope), and the panel never
 * offers what the server would refuse. With the login session the answer is the session's own write
 * permission. A pasted bearer token carries its own scope, which the panel cannot read, so the
 * group stays and the server has the last word.
 */
function writeGroupVisible() {
  if (!WRITE_TOOLS.size) return false;
  if (state.token) return true;
  return MAY_WRITE;
}

function renderToolList(filterText) {
  const list = $('#tool-list');
  list.textContent = '';
  const needle = (filterText || '').trim().toLowerCase();
  const match = (tool) => !needle
    || tool.name.toLowerCase().includes(needle)
    || (tool.description || '').toLowerCase().includes(needle)
    || toolText(tool).description.toLowerCase().includes(needle);

  const groups = [
    [t('group.read'), state.tools.filter((t2) => !WRITE_TOOLS.has(t2.name) && match(t2))],
  ];
  if (writeGroupVisible()) {
    groups.push([t('group.write'), state.tools.filter((t2) => WRITE_TOOLS.has(t2.name) && match(t2))]);
  }
  for (const [label, tools] of groups) {
    if (!tools.length) continue;
    list.append(el('p', 'tool-group', label));
    for (const tool of tools) {
      const card = toolCard(tool);
      if (state.selected === tool.name) card.classList.add('active');
      list.append(card);
    }
  }
  if (!list.childElementCount) list.append(el('p', 'empty', t('tool.nomatch')));
}

/* ---------------------------------------------------------------- the form */

/** The call button follows the selected tool: locked for a write tool while writes are off. */
function syncCallButton() {
  const blocked = Boolean(state.selected) && WRITE_TOOLS.has(state.selected) && !state.writeEnabled;
  $('#call-btn').disabled = blocked;
  $('#call-btn').textContent = blocked ? t('call.blocked') : t('call.button');
}

function selectTool(name) {
  state.selected = name;
  const tool = state.tools.find((t2) => t2.name === name);
  if (!tool) return;
  renderToolList($('#tool-filter').value);
  // The crystal of the selected tool is marked too: one selection, two views.
  if (spaceBridge.scene) spaceBridge.scene.setSelected(name);
  $('#tool-name').textContent = tool.name;
  $('#tool-desc').textContent = toolText(tool).description || t('tool.nodesc');

  const kind = $('#tool-kind');
  kind.textContent = WRITE_TOOLS.has(name) ? t('tool.kind.write') : t('tool.kind.read');
  kind.className = 'chip ' + (WRITE_TOOLS.has(name) ? 'write' : 'read');

  syncCallButton();

  renderParams(tool);
  renderJson($('#schema'), tool.inputSchema || {});
  $('#result').className = 'result';
  $('#result').textContent = '';
  $('#result').append(el('p', 'muted', t('result.ready')));
  $('#result-meta').textContent = t('result.idle');
  $('#copy-btn').hidden = true;
}

/**
 * Put the language into the parts the panel fills itself.
 *
 * The status line, the tool list, the tool head and the parameter form are
 * rebuilt; the result stays as it is (it is JSON, not text).
 */
function refreshLanguage() {
  if (state.health) renderStatus(state.health);
  renderToolList($('#tool-filter').value);
  const tool = state.tools.find((t2) => t2.name === state.selected);
  if (tool) {
    $('#tool-desc').textContent = toolText(tool).description || t('tool.nodesc');
    const kind = $('#tool-kind');
    kind.textContent = WRITE_TOOLS.has(tool.name) ? t('tool.kind.write') : t('tool.kind.read');
    renderParams(tool);
  }
  syncCallButton();
  renderHistory();
}

/**
 * Switch the language of the panel and of the account.
 *
 * The panel speaks the new language at once; the account's own setting follows, so the
 * agent side reads the same value for the tool descriptions (DD-010).
 */
async function switchLanguage(lang) {
  if (!normalizeLang(lang) || lang === LANG) return;
  LANG = lang;
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch { /* the choice is not remembered, the panel still speaks the new language */ }
  applyLanguage();
  refreshLanguage();
  if (!CSRF) return;
  const settings = state.settings || (await loadSettings());
  if (!settings) return;
  const tools = settings.tools || [];
  try {
    await saveSettings({
      lang,
      render_profile: settings.render_profile || '',
      tools_mode: tools.length ? 'some' : 'all',
      tools,
    });
    await loadSettings();
  } catch {
    /* the panel keeps the new language; the account keeps the old one until it is saved */
  }
}

function renderParams(tool) {
  const form = $('#params');
  form.textContent = '';
  const schema = tool.inputSchema || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const names = Object.keys(properties);

  if (!names.length) {
    form.append(el('p', 'empty', t('param.none')));
    return;
  }

  for (const name of names) {
    const spec = properties[name] || {};
    const field = el('div', 'field');
    const label = el('label');
    label.append(el('span', 'fname', name));
    if (required.has(name)) label.append(el('span', 'req', t('param.required')));
    label.append(el('span', 'ftype', Array.isArray(spec.type) ? spec.type.join('|') : (spec.type || 'any')));
    field.append(label);
    if (spec.description) field.append(el('span', 'fhint', spec.description));
    field.dataset.name = name;
    field.dataset.kind = 'string';

    if (Array.isArray(spec.enum) && spec.enum.length) {
      const select = el('select');
      select.append(el('option', '', t('param.unset')));
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
      wrap.append(el('span', '', t('param.enable')));
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
      area.placeholder = spec.type === 'array' ? t('param.array') : t('param.object');
      if (spec.default !== undefined) area.value = JSON.stringify(spec.default);
      field.append(area);
      field.dataset.kind = 'json';
    } else {
      const long = (spec.description || '').length > 90 || name.endsWith('_model');
      const input = long ? el('textarea') : el('input');
      if (!long) input.type = 'text';
      input.placeholder = spec.default !== undefined ? t('param.default', { value: JSON.stringify(spec.default) }) : '';
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
        throw new Error(t('err.badjson', { name, message: error.message }));
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
  button.textContent = t('call.running');

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
  $('#result-meta').textContent = meta || t('result.failed');
  $('#copy-btn').hidden = false;
}

function pushHistory(entry) {
  state.history.unshift(entry);
  state.history = state.history.slice(0, 12);
  const list = $('#history');
  list.textContent = '';
  if (!state.history.length) {
    list.append(el('p', 'empty', t('history.empty')));
    return;
  }
  for (const item of state.history) {
    const row = el('div', 'item');
    row.append(el('code', '', item.key ? t(item.key) : item.name));
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
  if (!state.resources.length) res.append(el('p', 'empty', t('resources.empty')));
  for (const resource of state.resources) {
    const row = el('div', 'item');
    row.append(el('code', '', resource.uri || resource.name || '?'));
    const button = el('button', 'mini', t('resources.read'));
    button.type = 'button';
    button.addEventListener('click', () => readResource(resource.uri));
    row.append(button);
    res.append(row);
  }

  const pro = $('#prompts');
  pro.textContent = '';
  if (!state.prompts.length) pro.append(el('p', 'empty', t('prompts.empty')));
  for (const prompt of state.prompts) {
    const row = el('div', 'item');
    row.append(el('code', '', prompt.name || '?'));
    const button = el('button', 'mini', t('prompts.preview'));
    button.type = 'button';
    button.addEventListener('click', () => getPrompt(prompt.name));
    row.append(button);
    pro.append(row);
  }
}

/* ------------------------------------------- the settings of the account (DD-010) */

/**
 * Read the settings of the signed-in account from the server.
 *
 * The server is the only source of truth: the panel shows what it says and writes back
 * what the reader changed. Without a session there is nothing to read.
 */
async function loadSettings() {
  if (!CSRF) return null;
  try {
    const response = await fetch(SETTINGS_PATH, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (!response.ok) return null;
    state.settings = await response.json();
    return state.settings;
  } catch {
    return null;
  }
}

/**
 * Write the settings back. The form is the same one the account page sends: the three
 * values and the token that proves the session.
 */
async function saveSettings(values) {
  const body = new URLSearchParams();
  body.set('csrf', CSRF);
  body.set('lang', values.lang);
  body.set('render_profile', values.render_profile || '');
  body.set('tools_mode', values.tools_mode || 'all');
  if ((values.tools_mode || 'all') === 'some') {
    for (const name of values.tools || []) body.append('tools', name);
  }
  const response = await fetch(SETTINGS_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
    credentials: 'same-origin',
    body: body.toString(),
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* the answer may not be JSON at all */ }
  if (!response.ok) {
    throw new Error((payload && payload.message) || `HTTP ${response.status}`);
  }
  return payload;
}

/** The values of the dialog: the language, the render form and the tools it keeps. */
function settingsValues() {
  const language = $('#settings-lang');
  const profile = $('#settings-profile');
  const tools = [];
  for (const input of document.querySelectorAll('#settings-tools input[type="checkbox"]')) {
    if (input.checked) tools.push(input.value);
  }
  return {
    // An empty language means "follow the reader": the account pins no language (DD-010).
    lang: language ? language.value : LANG,
    render_profile: profile ? profile.value : '',
    tools_mode: state.settingsMode,
    tools,
  };
}

/** Mark the current choice of a segmented switch. */
function markSegment(container, attribute, value) {
  for (const button of document.querySelectorAll(`${container} .seg-btn`)) {
    button.classList.toggle('on', button.getAttribute(attribute) === value);
  }
}

/** Fill the dialog with the settings of the account. */
function renderSettings() {
  const data = state.settings;
  if (!data) return;
  // An empty language in the row means "follow the reader": the account pins no language.
  const language = $('#settings-lang');
  if (language) language.value = data.lang || '';
  const profile = $('#settings-profile');
  if (profile) profile.value = data.render_profile || '';
  const kept = new Set(data.tools || []);
  state.settingsMode = kept.size ? 'some' : 'all';
  markSegment('#settings-mode', 'data-mode', state.settingsMode);
  const box = $('#settings-tools');
  box.textContent = '';
  for (const name of data.available_tools || []) {
    const label = el('label');
    const input = el('input');
    input.type = 'checkbox';
    input.value = name;
    input.checked = state.settingsMode === 'all' || kept.has(name);
    label.append(input);
    label.append(el('code', '', name));
    box.append(label);
  }
}

/** Open the dialog: the values come from the server, never from the panel. */
async function openSettings() {
  await loadSettings();
  renderSettings();
  const dialog = $('#settings');
  if (!dialog) return;
  const message = $('#settings-msg');
  if (message) message.textContent = '';
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

/** Save the dialog and show what the server said. */
async function saveSettingsFromDialog() {
  const button = $('#settings-save');
  const message = $('#settings-msg');
  button.disabled = true;
  message.textContent = t('settings.saving');
  try {
    await saveSettings(settingsValues());
    message.textContent = t('settings.saved');
    await loadSettings();
    const dialog = $('#settings');
    if (dialog && typeof dialog.close === 'function') dialog.close();
    // The tool list of the account may have changed: the panel shows the new one.
    boot();
  } catch (error) {
    message.textContent = t('settings.failed', { message: error.message });
  } finally {
    button.disabled = false;
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
    // The settings of the account (DD-010): the panel shows what the server holds, and the
    // language switcher writes back to the same place.
    await loadSettings();
    // Health first: it says whether a token is needed, so no MCP call is made
    // (and none can 401) before the user has had a chance to supply one.
    const health = await loadHealth();
    if (!current()) return;
    renderStatus(health);
    healthShown = true;
    state.sessionAuth = !!health.sessionAuth;
    if (health.authRequired) {
      if (health.sessionAuth) {
        // The deployment accepts the login session, so the token bar is optional: pasting one
        // acts as another identity. Without a token the session alone is enough.
        showAuthBar(state.token
          ? t('auth.sessionnote')
          : t('auth.sessionalt'), state.token ? 'good' : 'muted');
      } else {
        showAuthBar(state.token ? t('auth.tokensaved') : t('auth.needtokenpaste'),
          state.token ? 'good' : 'warn');
        if (!state.token) {
          dot.className = 'dot off';
          text.textContent = t('conn.needtoken');
          return;
        }
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
    syncSpaceTools();
    renderToolList('');
    renderSideLists();
    pushHistory({ key: 'history.connected', name: '', ms: 0, ok: true });
    dot.className = 'dot on';
    text.textContent = t('conn.ok', {
      tools: state.tools.length,
      resources: state.resources.length,
      prompts: state.prompts.length,
    });
    if (state.tools.length) selectTool(state.tools[0].name);
  } catch (error) {
    if (!current()) return;
    dot.className = 'dot off';
    if (error.unauthorized) {
      if (handleUnauthorized(error)) text.textContent = t('conn.needtoken');
      return;
    }
    text.textContent = t('conn.failed', { message: error.message });
    const bar = $('#statbar');
    if (!healthShown) bar.textContent = '';  // keep the health chips (a degraded state included) once shown
    bar.append(el('span', 'fact bad', error.message));
  }
}

function useToken() {
  const input = $('#token-input');
  const value = input.value.trim();
  input.value = '';
  if (!value) {
    setAuthMessage(t('auth.emptytoken'), 'bad');
    return;
  }
  // A header value is Latin-1 without control characters. A zero-width space or a smart quote that came
  // along with a copy would make every fetch throw before any request is sent, with the token still stored.
  if (!/^[\x20-\x7e]+$/.test(value)) {
    setAuthMessage(t('auth.badchars'), 'bad');
    return;
  }
  state.token = value;
  storeToken(value);
  setAuthMessage(t('auth.connecting'), 'muted');
  boot();
}

function clearToken() {
  state.token = '';
  storeToken('');
  $('#token-input').value = '';
  setAuthMessage(t('auth.cleared'), 'muted');
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
    $('#result-meta').textContent = t('result.copied');
  } catch {
    $('#result-meta').textContent = t('result.copydenied');
  }
});
for (const button of document.querySelectorAll('.lang-btn')) {
  button.addEventListener('click', () => switchLanguage(button.dataset.uilang));
}

// The settings of the account exist only when the deployment has accounts and the reader
// is signed in: the console is behind the login in that case, and the server is the only
// place the settings live.
if (CSRF) {
  const openButton = document.getElementById('settings-open');
  if (openButton) {
    openButton.hidden = false;
    openButton.addEventListener('click', openSettings);
  }
  const closeButton = document.getElementById('settings-close');
  if (closeButton) {
    closeButton.addEventListener('click', () => {
      const dialog = $('#settings');
      if (dialog && typeof dialog.close === 'function') dialog.close();
    });
  }
  const saveButton = document.getElementById('settings-save');
  if (saveButton) saveButton.addEventListener('click', saveSettingsFromDialog);
  for (const button of document.querySelectorAll('#settings-mode .seg-btn')) {
    button.addEventListener('click', () => {
      state.settingsMode = button.dataset.mode === 'some' ? 'some' : 'all';
      markSegment('#settings-mode', 'data-mode', state.settingsMode);
      // The boxes follow the mode: every tool, or the ones the account keeps.
      const kept = new Set((state.settings && state.settings.tools) || []);
      for (const input of document.querySelectorAll('#settings-tools input[type="checkbox"]')) {
        input.checked = state.settingsMode === 'all' || kept.has(input.value);
      }
    });
  }
  // Signing out belongs to the session, so it shows exactly when there is one (BD-010 12.8).
  // The form itself carries the CSRF token and posts to the account route: no script of its own.
  const signout = document.getElementById('signout');
  if (signout) signout.hidden = false;
}

// The account page and the operator area exist only when the deployment has accounts; the
// operator area only for an administrator. The server decides both, so the panel can never
// offer an entry the server would refuse.
if (CFG.accounts) {
  const accountEntry = document.getElementById('account-entry');
  if (accountEntry) accountEntry.hidden = false;
  // The role badge: it states what this caller may do, so a signed-in user can tell the operator
  // role from the plain reader. The administrator role and the write permission are two different
  // things, so an administrator who may only read says so instead of pretending to be a writer.
  const badge = document.getElementById('role-badge');
  if (badge) {
    badge.hidden = false;
    if (CFG.admin) {
      badge.textContent = MAY_WRITE ? t('role.adminwrite') : t('role.adminread');
      badge.className = 'chip admin';
    } else {
      badge.textContent = MAY_WRITE ? t('role.write') : t('role.read');
      badge.className = 'chip ' + (MAY_WRITE ? 'write' : 'read');
    }
  }
} else {
  // Without accounts there is no page that issues a token, so the panel does not point at one.
  const source = document.getElementById('token-source');
  if (source) source.hidden = true;
  const note = document.getElementById('token-note');
  if (note) note.hidden = true;
}
if (CFG.admin) {
  const adminEntry = document.getElementById('admin-entry');
  if (adminEntry) adminEntry.hidden = false;
}

// The language of the page: the server already wrote the text in the language of the request,
// the panel keeps the reader's own choice.
applyLanguage();
boot();
