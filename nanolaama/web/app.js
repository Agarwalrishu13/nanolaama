/* nanoLaama front end — plain JavaScript, no build step, no framework.
   The server does the real work; this file turns it into buttons and bubbles. */

'use strict';

// ---------------------------------------------------------------- tiny helpers
const $ = (id) => document.getElementById(id);
const api = {
  async get(url) {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
  async post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
  async del(url) {
    const response = await fetch(url, { method: 'DELETE' });
    return response.json().catch(() => ({}));
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function toast(message, kind) {
  const node = document.createElement('div');
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  $('toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .3s';
    setTimeout(() => node.remove(), 300);
  }, kind === 'bad' ? 9000 : 4500);
}

function show(id) { $(id).classList.add('open'); }
function hide(id) { $(id).classList.remove('open'); }

/* server-sent events over fetch, so we can also abort the stream */
async function stream(url, body, onEvent, signal) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of raw.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') return;
        try { onEvent(JSON.parse(payload)); } catch (err) { /* partial line, ignore */ }
      }
    }
  }
}

// --------------------------------------------------------------- markdown-lite
function inlineMd(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*\w])\*([^*\n]+)\*(?![\w*])/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return out;
}

/* Enough markdown for a chat: headings, lists, quotes, code, bold, links.
   Everything is escaped first, so a model can never inject HTML into the page. */
function mdToHtml(source) {
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let inFence = false;
  let fence = [];
  let listTag = null;
  let paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html += paragraph.map(inlineMd).join('\n') + '\n';
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listTag) { html += '</' + listTag + '>'; listTag = null; }
  };

  for (const line of lines) {
    if (inFence) {
      if (/^\s*```/.test(line)) {
        html += '<pre><code>' + escapeHtml(fence.join('\n')) + '</code></pre>';
        inFence = false; fence = [];
      } else {
        fence.push(line);
      }
      continue;
    }
    if (/^\s*```/.test(line)) { flushParagraph(); closeList(); inFence = true; continue; }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); closeList();
      const level = Math.min(heading[1].length, 3);
      html += '<h' + level + '>' + inlineMd(heading[2]) + '</h' + level + '>';
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const want = bullet ? 'ul' : 'ol';
      if (listTag !== want) { closeList(); html += '<' + want + '>'; listTag = want; }
      html += '<li>' + inlineMd((bullet || numbered)[1]) + '</li>';
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushParagraph(); closeList();
      html += '<blockquote>' + inlineMd(line.replace(/^\s*>\s?/, '')) + '</blockquote>';
      continue;
    }
    if (!line.trim()) { flushParagraph(); closeList(); html += '\n'; continue; }
    paragraph.push(line);
  }
  if (inFence && fence.length) html += '<pre><code>' + escapeHtml(fence.join('\n')) + '</code></pre>';
  flushParagraph(); closeList();
  return html;
}

// ---------------------------------------------------------------------- state
const state = {
  status: null,
  settings: {},
  engines: [],
  catalog: [],
  chats: [],
  chat: null,          // { id, title, messages: [{role, content, stats}] }
  streaming: null,     // AbortController while an answer is arriving
};

const STARTERS = [
  'Explain what a computer program is, like I am ten.',
  'Rewrite this message so it sounds friendlier: ',
  'Give me a 7-day plan to learn something new.',
  'What can you do?',
];

function engineById(id) {
  return state.engines.find((engine) => engine.id === id) || null;
}
function runningEngines() {
  return state.engines.filter((engine) => engine.running);
}
function currentEngine() {
  const chosen = engineById(state.settings.engine);
  if (chosen && chosen.running) return chosen;
  return runningEngines()[0] || chosen || null;
}
function currentModel() {
  return state.settings.model || '';
}
function modelBelongsToEngine(model) {
  const engine = currentEngine();
  return Boolean(engine && engine.models.some((entry) => entry.name === model));
}

// -------------------------------------------------------------------- render
function renderAllModels() {
  // Flatten every model from every running engine into one list.
  const out = [];
  for (const engine of state.engines) {
    for (const model of engine.models || []) {
      out.push({ engine, model });
    }
  }
  return out;
}

function renderHeader() {
  const engine = currentEngine();
  const label = $('engineLabel');
  const dot = $('engineDot');
  if (!engine) {
    dot.className = 'dot dead';
    label.textContent = 'No AI found yet — click to set one up';
    return;
  }
  const models = engine.models || [];
  dot.className = 'dot live';
  const parts = [engine.name];
  if (currentModel()) parts.push(currentModel());
  else if (models.length === 0) parts.push('no models yet');
  label.textContent = parts.join('  ·  ');
  $('brandSub').textContent = engine.name + (currentModel() ? ' · ' + currentModel() : '');
}

function renderMessages() {
  const box = $('messages');
  box.innerHTML = '';
  const chat = state.chat;

  if (!chat || chat.messages.length === 0) {
    box.appendChild(renderEmptyState());
    return;
  }
  for (const message of chat.messages) {
    box.appendChild(bubble(message));
  }
  scrollDown();
}

function renderEmptyState() {
  const holder = document.createElement('div');
  holder.className = 'empty';

  const title = document.createElement('h1');
  const engine = currentEngine();
  if (engine) {
    title.textContent = 'Ask it anything.';
  } else {
    title.textContent = 'Let’s get an AI running.';
  }
  holder.appendChild(title);

  const sub = document.createElement('p');
  sub.textContent = engine
    ? `Ready, using ${engine.name}. Your words never leave this computer.`
    : 'It takes about three minutes, once. You do not need to know anything about programming.';
  holder.appendChild(sub);

  if (!engine) {
    holder.appendChild(setupCard());
  } else {
    const chips = document.createElement('div');
    chips.className = 'chips';
    for (const starter of STARTERS) {
      const chip = document.createElement('button');
      chip.className = 'chip';
      chip.textContent = starter;
      chip.onclick = () => {
        $('input').value = starter;
        $('input').focus();
        autoGrow();
      };
      chips.appendChild(chip);
    }
    holder.appendChild(chips);
  }
  return holder;
}

function setupCard() {
  const card = document.createElement('div');
  card.className = 'setup-card';
  card.innerHTML = `
    <h2>Pick how you would like to run an AI</h2>
    <p>Any of these works. nanoLaama only ever talks to your own computer.</p>
    <div class="setup-options"></div>`;

  const options = card.querySelector('.setup-options');

  const addOption = ({ icon, title, body, buttons }) => {
    const row = document.createElement('div');
    row.className = 'setup-option';
    row.innerHTML = `
      <div class="icon">${icon}</div>
      <div style="flex:1;min-width:0">
        <h3>${escapeHtml(title)}</h3>
        <p>${body}</p>
        <div class="go"></div>
      </div>`;
    const go = row.querySelector('.go');
    for (const button of buttons) {
      const node = document.createElement('button');
      node.className = 'btn small' + (button.primary ? ' primary' : '');
      node.textContent = button.label;
      node.onclick = button.action;
      go.appendChild(node);
    }
    options.appendChild(row);
  };

  const ollama = state.engines.find((engine) => engine.id === 'ollama');
  addOption({
    icon: '⚡',
    title: ollama && ollama.running ? 'Ollama is running' : 'Install Ollama — the easy route',
    body: escaped(ollama && ollama.running
      ? 'Ollama is running on this computer. Download a small model and start chatting.'
      : 'One free program that does the hard part. nanoLaama can install it for you, or you can get it from ollama.com/download.',
    ),
    buttons: [
      ollama && ollama.running
        ? { label: 'Choose a model', primary: true, action: () => { openModels(); } }
        : { label: 'Install it for me', primary: true, action: () => installOllama() },
      { label: 'Open the download page', action: () => window.open('https://ollama.com/download', '_blank') },
      { label: 'I installed it — look again', action: refresh },
    ],
  });

  const native = state.status && state.status.native;
  addOption({
    icon: '🧠',
    title: 'Use your own engine (nanollama.c)',
    body: native && native.found
      ? `Found it${native.built ? ' and it is built' : ''} in <code>${escapeHtml(native.path)}</code>.`
      : 'A from-scratch C engine that runs models trained by nanobrain. nanoLaama can find the folder, build it and start it.',
    buttons: [
      {
        label: native && native.found ? (native.built ? 'Start it' : 'Build it') : 'Set it up',
        action: () => show('settingsModal'),
      },
    ],
  });

  addOption({
    icon: '🔑',
    title: 'Use a paid AI service you already have',
    body: 'If you have an API key from a service like OpenAI or Groq, point nanoLaama at it in Settings.',
    buttons: [{ label: 'Open Settings', action: () => show('settingsModal') }],
  });

  return card;
}

function escaped(text) { return escapeHtml(text); }

function bubble(message) {
  const wrap = document.createElement('div');
  const kind = message.role === 'user' ? 'user' : (message.error ? 'error' : (message.note ? 'note' : 'assistant'));
  wrap.className = 'msg ' + kind;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = kind === 'user' ? '🙂' : (kind === 'error' ? '⚠️' : '🦙');
  wrap.appendChild(avatar);

  const body = document.createElement('div');
  body.className = 'body';

  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = kind === 'user' ? 'You' : (message.engine ? message.engine : 'nanoLaama');
  body.appendChild(who);

  const text = document.createElement('div');
  text.className = 'text';
  if (kind === 'user') text.textContent = message.content;
  else text.innerHTML = mdToHtml(message.content);
  body.appendChild(text);

  const meta = document.createElement('div');
  meta.className = 'meta';
  if (message.stats && message.stats.seconds) {
    const speed = message.stats.tokens_per_second ? ` · ${message.stats.tokens_per_second} words/second` : '';
    const tag = document.createElement('span');
    tag.className = 'expert-only';
    tag.textContent = `${message.stats.seconds}s${speed}`;
    meta.appendChild(tag);
  }
  if (kind !== 'user') {
    const copy = document.createElement('button');
    copy.textContent = 'Copy';
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        toast('Copied.', 'good');
      } catch (err) {
        toast('Your browser would not let me copy. Select the text instead.', 'bad');
      }
    };
    meta.appendChild(copy);
  }
  body.appendChild(meta);
  wrap.appendChild(body);
  return wrap;
}

function scrollDown() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

function renderChatList() {
  const list = $('chatList');
  list.innerHTML = '';
  if (state.chats.length === 0) {
    const empty = document.createElement('div');
    empty.style.cssText = 'padding:8px 10px;font-size:13px;color:var(--faint)';
    empty.textContent = 'No saved chats yet.';
    list.appendChild(empty);
    return;
  }
  for (const chat of state.chats) {
    const item = document.createElement('button');
    item.className = 'chat-item' + (state.chat && state.chat.id === chat.id ? ' active' : '');
    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = chat.title || 'Untitled chat';
    item.appendChild(title);
    const kill = document.createElement('span');
    kill.className = 'kill';
    kill.textContent = '✕';
    kill.title = 'Forget this chat';
    kill.onclick = async (event) => {
      event.stopPropagation();
      const data = await api.del('/api/chats/' + encodeURIComponent(chat.id));
      state.chats = data.chats || [];
      if (state.chat && state.chat.id === chat.id) newChat();
      renderChatList();
    };
    item.appendChild(kill);
    item.onclick = () => openChat(chat);
    list.appendChild(item);
  }
}

// ------------------------------------------------------------------- actions
function newChat() {
  state.chat = { id: newId(), title: 'New chat', messages: [] };
  renderMessages();
  renderChatList();
  $('input').focus();
}

function newId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function openChat(chat) {
  state.chat = {
    id: chat.id,
    title: chat.title || 'New chat',
    messages: (chat.messages || []).map((message) => ({ ...message })),
  };
  renderMessages();
  renderChatList();
}

async function saveChat() {
  if (!state.chat) return;
  const firstUser = state.chat.messages.find((message) => message.role === 'user');
  if (firstUser) {
    state.chat.title = firstUser.content.slice(0, 60) + (firstUser.content.length > 60 ? '…' : '');
  }
  try {
    const data = await api.post('/api/chats', state.chat);
    state.chats = data.chats || state.chats;
    renderChatList();
  } catch (err) { /* a chat that cannot be saved is not worth interrupting for */ }
}

async function send() {
  const input = $('input');
  const text = input.value.trim();
  if (!text || state.streaming) return;

  const engine = currentEngine();
  if (!engine) { toast('Set up an AI first — it takes three minutes.', 'bad'); return; }
  if (!currentModel()) { openModels(); toast('Choose a model first.'); return; }

  if (!state.chat) newChat();
  state.chat.messages.push({ role: 'user', content: text });
  input.value = '';
  autoGrow();
  renderMessages();

  const placeholder = { role: 'assistant', content: '', engine: engine.name };
  state.chat.messages.push(placeholder);
  renderMessages();
  setStreaming(true);

  const controller = new AbortController();
  state.streaming = controller;
  let answer = '';

  try {
    await stream('/api/chat', {
      engine: engine.id,
      model: currentModel(),
      messages: state.chat.messages
        .filter((message) => !message.error && !message.note)
        .map((message) => ({ role: message.role, content: message.content })),
      temperature: Number(state.settings.temperature),
      max_tokens: Number(state.settings.max_tokens),
      system: state.settings.system_prompt || '',
    }, (event) => {
      if (event.type === 'delta') {
        answer += event.text;
        placeholder.content = answer;
        const target = $('messages').lastElementChild;
        if (target) {
          const textNode = target.querySelector('.text');
          if (textNode) textNode.innerHTML = mdToHtml(answer) + '<span class="cursor-blink"></span>';
          scrollDown();
        }
      } else if (event.type === 'stats') {
        placeholder.stats = event;
        if (event.note) toast(event.note, 'bad');
        if (event.salvaged) {
          toast('That engine wrote its reply slightly incorrectly — a bug in the engine, not in your question. '
              + 'nanoLaama recovered the text above anyway.');
        }
      } else if (event.type === 'error') {
        placeholder.error = true;
        placeholder.content = event.message;
      }
    }, controller.signal);
  } catch (err) {
    if (err.name === 'AbortError') {
      placeholder.content = answer + '\n\n_(stopped)_';
    } else {
      placeholder.error = true;
      placeholder.content = err.message;
    }
  } finally {
    state.streaming = null;
    setStreaming(false);
    renderMessages();
    saveChat();
  }
}

function setStreaming(isStreaming) {
  const button = $('sendBtn');
  button.textContent = isStreaming ? '■' : '↑';
  button.title = isStreaming ? 'Stop' : 'Send';
  button.disabled = false;
  $('composerStats').textContent = '';
}

function autoGrow() {
  const input = $('input');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
}

// ---------------------------------------------------------------- modals: models
async function openModels() {
  show('modelsModal');
  renderInstalled();
  await loadCatalog();
}

function renderInstalled() {
  const holder = $('installedList');
  holder.innerHTML = '';
  const engines = runningEngines().filter((engine) => (engine.models || []).length);
  if (engines.length === 0) {
    holder.innerHTML = `<div class="model-card"><div class="info">
      <h4>No models on this computer yet</h4>
      <p>Download one below — the small ones take a minute and work on any laptop.</p>
    </div></div>`;
    return;
  }
  for (const engine of engines) {
    for (const model of engine.models) {
      const card = document.createElement('div');
      card.className = 'model-card' + (state.settings.model === model.name ? ' installed' : '');
      const size = model.size_gb ? ` · ${model.size_gb} GB` : '';
      const params = model.params ? ` · ${model.params}` : '';
      card.innerHTML = `<div class="info">
          <h4>${escapeHtml(model.name)}</h4>
          <div class="sub">${escapeHtml(engine.name)}${size}${params}</div>
        </div>`;
      const use = document.createElement('button');
      use.className = 'btn small' + (state.settings.model === model.name ? '' : ' primary');
      use.textContent = state.settings.model === model.name ? 'In use' : 'Use it';
      use.onclick = async () => {
        state.settings = await api.post('/api/settings', { engine: engine.id, model: model.name });
        renderHeader();
        renderInstalled();
        toast(`Now talking to ${model.name}.`, 'good');
        hide('modelsModal');
      };
      card.appendChild(use);
      holder.appendChild(card);
    }
  }
}

async function loadCatalog() {
  const data = await api.get('/api/models');
  state.catalog = data.catalog || [];
  state.engines = data.engines || state.engines;

  const row = $('downloadEngineRow');
  row.innerHTML = '';
  const downloaders = state.engines.filter((engine) => engine.kind === 'ollama');
  if (downloaders.length === 0) {
    row.innerHTML = `<span class="hint" style="font-size:13px;color:var(--muted)">
      Downloads need Ollama. Install it from
      <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a>
      and this list becomes one-click.</span>`;
  }

  const holder = $('catalogList');
  holder.innerHTML = '';
  const installedNames = new Set(renderAllModels().map((entry) => entry.model.name));
  const budget = data.memory_budget_gb || 0;

  for (const model of state.catalog) {
    const card = document.createElement('div');
    card.className = 'model-card' + (model.fits ? '' : ' blocked');
    const have = installedNames.has(model.name);
    const tags = [`${model.size_gb} GB`, model.level];
    card.innerHTML = `<div class="info">
        <h4>${escapeHtml(model.label)} ${have ? '<span class="badge live">on this computer</span>' : ''}</h4>
        <div class="sub">${escapeHtml(model.name)} · ${tags.map(escapeHtml).join(' · ')}</div>
        <p>${escapeHtml(model.blurb)}${model.fits ? '' : ` Needs about ${model.ram_gb} GB of memory; this computer has ${budget} GB to spare.`}</p>
      </div>`;
    if (!have && downloaders.length && model.fits) {
      const button = document.createElement('button');
      button.className = 'btn small';
      button.textContent = 'Download';
      button.onclick = () => downloadModel(model, downloaders[0], button);
      card.appendChild(button);
    }
    holder.appendChild(card);
  }
}

async function downloadModel(model, engine, button) {
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>';
  const log = $('downloadLog');
  log.textContent = '';
  const bar = $('downloadBar');
  bar.style.display = 'block';
  bar.querySelector('span').style.width = '0%';

  try {
    await stream('/api/pull', { engine: engine.id, model: model.name }, (event) => {
      if (event.type === 'log') {
        log.textContent += event.message + '\n';
        log.scrollTop = log.scrollHeight;
      } else if (event.type === 'progress') {
        bar.querySelector('span').style.width = event.percent + '%';
      } else if (event.type === 'done') {
        toast(event.message, 'good');
      } else if (event.type === 'error') {
        toast(event.message, 'bad');
        log.textContent += event.message + '\n';
      }
    });
    await refresh();
    state.settings = await api.post('/api/settings', { engine: engine.id, model: model.name });
    renderHeader();
    await loadCatalog();
    renderInstalled();
  } catch (err) {
    toast(err.message, 'bad');
  } finally {
    button.disabled = false;
    button.textContent = 'Download';
    bar.style.display = 'none';
  }
}

// --------------------------------------------------------------- modals: settings
function fillSettings() {
  const settings = state.settings || {};
  $('tempInput').value = settings.temperature ?? 0.7;
  $('maxTokensInput').value = settings.max_tokens ?? 512;
  $('systemInput').value = settings.system_prompt || '';
  $('expertToggle').checked = Boolean(settings.expert_mode);
  document.body.classList.toggle('expert', Boolean(settings.expert_mode));

  const list = $('engineList');
  list.innerHTML = '';
  for (const engine of state.engines) {
    const row = document.createElement('div');
    row.className = 'engine-row';
    const badge = engine.running ? '<span class="badge live">running</span>' : '<span class="badge">not running</span>';
    const best = engine.best_for ? `<span class="badge best">${escapeHtml(engine.best_for)}</span>` : '';
    row.innerHTML = `<div class="info">
        <h4>${escapeHtml(engine.name)} ${badge} ${best}</h4>
        <p>${escapeHtml(engine.blurb || '')} ${engine.detail ? '— ' + escapeHtml(engine.detail) : ''}</p>
      </div>`;
    if (!engine.running && engine.site) {
      const link = document.createElement('a');
      link.className = 'btn small';
      link.href = engine.site;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Get it';
      row.appendChild(link);
    }
    if (engine.custom) {
      const remove = document.createElement('button');
      remove.className = 'btn small';
      remove.textContent = 'Remove';
      remove.onclick = async () => {
        const data = await api.post('/api/engines/remove', { id: engine.id });
        state.engines = data.engines;
        fillSettings();
        renderHeader();
      };
      row.appendChild(remove);
    }
    list.appendChild(row);
  }
  renderNativeCard();
  if (state.status && state.status.hardware_sentence) {
    $('aboutInfo').textContent = state.status.hardware_sentence +
      ' Your chats, settings and any model files you drop here are stored in your home folder ' +
      'inside “.nanolaama”. Nothing is uploaded anywhere.';
  }
}

async function renderNativeCard() {
  let native;
  try {
    native = await api.get('/api/native');
  } catch (err) {
    return;
  }
  state.status = state.status || {};
  state.status.native = native;
  const holder = $('nativeCard');
  holder.innerHTML = '';
  const card = document.createElement('div');
  card.className = 'model-card';

  if (!native.found) {
    card.innerHTML = `<div class="info">
      <h4>Not found on this computer</h4>
      <p>Clone it with <code>git clone ${escapeHtml(native.engine_home)}</code>, or tell me where it is below.</p>
      <p>${native.can_build ? 'A C compiler is available, so building will work.' : 'No C compiler found — that is needed to build it.'}</p>
    </div>`;
    holder.appendChild(card);
    return;
  }

  const modelRows = (native.models || []).slice(0, 6).map((model) => {
    const usable = model.tokenizer ? '' : ' <span class="badge">needs its tokenizer file</span>';
    return `<p>· ${escapeHtml(model.name)} — ${model.size_gb} GB${usable}
      <button class="btn small" data-model="${escapeHtml(model.path)}" data-tok="${escapeHtml(model.tokenizer)}">Run it</button></p>`;
  }).join('') || '<p>No .bin models in its folder yet. Train one with nanobrain and drop it in.</p>';

  card.innerHTML = `<div class="info">
      <h4>${native.running ? '<span class="badge live">running</span>' : ''}
          ${native.built ? '<span class="badge live">built</span>' : '<span class="badge">not built yet</span>'}</h4>
      <div class="sub">${escapeHtml(native.path)}</div>
      <p>${escapeHtml(native.how)}${native.can_build ? '' : ' · no C compiler found'}</p>
      ${modelRows}
    </div>`;
  holder.appendChild(card);

  holder.querySelectorAll('button[data-model]').forEach((button) => {
    button.onclick = () => runNativeModel(button.dataset.model, button.dataset.tok);
  });
}

async function runNativeModel(modelPath, tokenizer) {
  const log = $('nativeLog');
  log.textContent = '';
  toast('Starting your engine…');
  try {
    await stream('/api/native/serve', { model: modelPath, tokenizer }, (event) => {
      if (event.type === 'log') {
        log.textContent += event.message + '\n';
        log.scrollTop = log.scrollHeight;
      } else if (event.type === 'error') {
        log.textContent += event.message + '\n';
        toast('Could not start the engine.', 'bad');
      } else if (event.type === 'done') {
        toast(event.message, 'good');
      }
    });
    await refresh();
    // Point nanoLaama at it now that it is up.
    const native = state.engines.find((engine) => engine.id === 'nanollama');
    if (native && native.running && native.models.length) {
      state.settings = await api.post('/api/settings', { engine: 'nanollama', model: native.models[0].name });
      renderHeader();
    }
  } catch (err) {
    toast(err.message, 'bad');
    log.textContent += err.message + '\n';
  }
}

async function installOllama() {
  const log = $('installLog');
  log.textContent = '';
  show('settingsModal');
  toast('Installing Ollama — this can take a few minutes.');
  try {
    await stream('/api/install', { engine: 'ollama' }, (event) => {
      if (event.type === 'log') {
        log.textContent += event.message + '\n';
        log.scrollTop = log.scrollHeight;
      } else if (event.type === 'error') {
        log.textContent += event.message + '\n';
        toast(event.message, 'bad');
      } else if (event.type === 'done') {
        toast(event.message, 'good');
      } else if (event.type === 'ready') {
        toast('Ollama is running. Now download a model.', 'good');
      }
    });
    await refresh();
    fillSettings();
    openModels();
  } catch (err) {
    toast(err.message, 'bad');
    log.textContent += err.message + '\n';
  }
}

// ------------------------------------------------------------------- drop files
function wireDropZone() {
  const zone = $('dropzone');
  let depth = 0;

  window.addEventListener('dragenter', (event) => {
    if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes('Files')) return;
    depth += 1;
    zone.classList.add('open');
  });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) zone.classList.remove('open');
  });
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('drop', async (event) => {
    event.preventDefault();
    depth = 0;
    zone.classList.remove('open');
    const files = Array.from(event.dataTransfer.files || []);
    if (files.length) await handleFiles(files);
  });
}

async function handleFiles(files) {
  for (const file of files) {
    const lower = file.name.toLowerCase();
    if (!/\.(gguf|bin|safetensors)$/.test(lower)) {
      toast(`“${file.name}” is not a model file. I can take .gguf or .bin.`, 'bad');
      continue;
    }
    try {
      const saved = await uploadFile(file);
      toast(saved.message, 'good');

      if (saved.kind === 'gguf') {
        const ollama = state.engines.find((engine) => engine.id === 'ollama' && engine.running);
        if (!ollama) {
          toast('Saved. To chat with it, install Ollama or run llama.cpp — either can load a .gguf.', 'bad');
          continue;
        }
        const log = $('downloadLog');
        log.textContent = '';
        show('modelsModal');
        let modelName = '';
        await stream('/api/import', { engine: 'ollama', path: saved.path, name: file.name.replace(/\.gguf$/i, '') },
          (event) => {
            if (event.type === 'log') { log.textContent += event.message + '\n'; log.scrollTop = log.scrollHeight; }
            else if (event.type === 'error') { toast(event.message, 'bad'); log.textContent += event.message + '\n'; }
            else if (event.type === 'done') { modelName = event.model || ''; toast(event.message, 'good'); }
          });
        if (modelName) {
          state.settings = await api.post('/api/settings', { engine: 'ollama', model: modelName });
        }
        await refresh();
        await loadCatalog();
        renderInstalled();
        renderHeader();
      } else {
        toast('Saved. Use it from Settings → your own engine.', 'good');
        await renderNativeCard();
      }
    } catch (err) {
      toast('The upload failed: ' + err.message, 'bad');
    }
  }
}

/* Upload in slices so a multi-gigabyte file does not have to fit in memory. */
async function uploadFile(file) {
  const zone = $('dropzone');
  const inner = zone.querySelector('p');
  zone.classList.add('open');

  const start = await api.post('/api/upload/start', { name: file.name });
  const chunkSize = start.chunk_bytes || (8 * 1024 * 1024);
  let offset = 0;

  while (offset < file.size) {
    const slice = file.slice(offset, Math.min(offset + chunkSize, file.size));
    const response = await fetch(`/api/upload/chunk?id=${encodeURIComponent(start.id)}&offset=${offset}`, {
      method: 'POST',
      body: slice,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'The transfer stopped.');
    }
    offset += slice.size;
    const percent = Math.round((offset / file.size) * 100);
    if (inner) inner.textContent = `Copying ${file.name} — ${percent}%  (${(offset / 1048576).toFixed(0)} MB)`;
  }
  const result = await api.post('/api/upload/finish', { id: start.id });
  zone.classList.remove('open');
  if (inner) inner.textContent = '.gguf files run with Ollama · .bin files run with your own engine';
  return result;
}

// ---------------------------------------------------------------------- export
function exportChat() {
  if (!state.chat || state.chat.messages.length === 0) {
    toast('Nothing to save yet.');
    return;
  }
  const lines = [`# ${state.chat.title || 'nanoLaama chat'}`, ''];
  for (const message of state.chat.messages) {
    lines.push(message.role === 'user' ? '## You' : '## AI', '', message.content, '');
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = (state.chat.title || 'chat').replace(/[^\w\- ]+/g, '').trim().slice(0, 50) + '.md';
  link.click();
  URL.revokeObjectURL(link.href);
  toast('Saved to your downloads.', 'good');
}

// ------------------------------------------------------------------------ boot
async function refresh() {
  try {
    state.status = await api.get('/api/status');
  } catch (err) {
    toast('The app stopped answering. Restart it.', 'bad');
    return;
  }
  state.settings = state.status.settings || {};
  state.engines = state.status.engines || [];
  if (!state.settings.model) {
    const engine = currentEngine();
    if (engine && engine.models.length) state.settings.model = engine.models[0].name;
  }
  document.body.classList.toggle('expert', Boolean(state.settings.expert_mode));
  $('expertToggle').checked = Boolean(state.settings.expert_mode);
  renderHeader();
  renderMessages();
  fillSettings();
  if (state.status.hardware_sentence) {
    $('footWhere').textContent = state.status.hardware_sentence;
  }
}

function closeWithSave(id) {
  hide(id);
  if (id === 'settingsModal') {
    api.post('/api/settings', {
      temperature: Number($('tempInput').value) || 0.7,
      max_tokens: Number($('maxTokensInput').value) || 512,
      system_prompt: $('systemInput').value || '',
    }).then((settings) => { state.settings = settings; });
  }
}

function wire() {
  $('newChatBtn').onclick = newChat;
  $('modelsBtn').onclick = openModels;
  $('enginePill').onclick = openModels;
  $('settingsBtn').onclick = async () => { await refresh(); show('settingsModal'); };
  $('exportBtn').onclick = exportChat;
  $('sendBtn').onclick = () => { if (state.streaming) { state.streaming.abort(); } else { send(); } };
  $('rescanBtn').onclick = async () => { await refresh(); toast('Looked again.', 'good'); };
  $('installBtn').onclick = installOllama;

  $('expertToggle').onchange = async (event) => {
    document.body.classList.toggle('expert', event.target.checked);
    state.settings = await api.post('/api/settings', { expert_mode: event.target.checked });
  };

  $('input').addEventListener('input', autoGrow);
  $('input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  document.querySelectorAll('[data-close]').forEach((button) => {
    button.onclick = () => closeWithSave(button.dataset.close);
  });
  document.querySelectorAll('.backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) closeWithSave(backdrop.id);
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      document.querySelectorAll('.backdrop.open').forEach((backdrop) => closeWithSave(backdrop.id));
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      newChat();
    }
  });

  $('nativeFindBtn').onclick = async () => {
    try {
      const summary = await api.post('/api/native/locate', { path: $('nativePath').value.trim() });
      toast(`Found it in ${summary.path}`, 'good');
      await renderNativeCard();
    } catch (err) { toast(err.message, 'bad'); }
  };
  $('nativeBuildBtn').onclick = async () => {
    const log = $('nativeLog');
    log.textContent = '';
    toast('Building the engine…');
    try {
      await stream('/api/native/build', { path: $('nativePath').value.trim() }, (event) => {
        if (event.type === 'log') { log.textContent += event.message + '\n'; log.scrollTop = log.scrollHeight; }
        else if (event.type === 'error') { log.textContent += event.message + '\n'; toast('The build failed.', 'bad'); }
        else if (event.type === 'done') { toast(event.message, 'good'); }
      });
      await renderNativeCard();
    } catch (err) { toast(err.message, 'bad'); }
  };
  $('nativeStopBtn').onclick = async () => {
    const result = await api.post('/api/native/stop', {});
    toast(result.message, result.stopped ? 'good' : undefined);
    await refresh();
    await renderNativeCard();
  };
  $('customAddBtn').onclick = async () => {
    try {
      await api.post('/api/engines/add', {
        name: $('customName').value.trim(),
        url: $('customUrl').value.trim(),
        api_key: $('customKey').value.trim(),
      });
      $('customName').value = $('customUrl').value = $('customKey').value = '';
      await refresh();
      toast('Added. If it is running, it will show up now.', 'good');
    } catch (err) { toast(err.message, 'bad'); }
  };

  if (window.innerWidth <= 860) $('menuBtn').style.display = 'inline-flex';
  wireDropZone();
}

(async function main() {
  wire();
  try {
    const data = await api.get('/api/chats');
    state.chats = data.chats || [];
  } catch (err) { /* first run, no chats yet */ }
  await refresh();
  renderChatList();
  if (state.chats.length) openChat(state.chats[0]); else newChat();
  // Keep an eye out for an engine being started outside this window.
  setInterval(async () => {
    if (state.streaming) return;
    const before = runningEngines().map((engine) => engine.id).join(',');
    try {
      const data = await api.get('/api/engines');
      state.engines = data.engines || [];
      const after = runningEngines().map((engine) => engine.id).join(',');
      if (before !== after) { renderHeader(); renderMessages(); }
    } catch (err) { /* server busy; try again later */ }
  }, 8000);
})();
