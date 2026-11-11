'use strict';
const $ = selector => document.querySelector(selector);
const themeMedia = matchMedia('(prefers-color-scheme: dark)');
function applyTheme(value) {
  const selected = ['system', 'light', 'dark'].includes(value) ? value : 'system';
  document.documentElement.dataset.theme = selected === 'dark' || selected === 'system' && themeMedia.matches ? 'dark' : 'light';
  $('#theme-select').value = selected;
}
try { applyTheme(localStorage.getItem('xangi-theme') || 'system'); } catch { applyTheme('system'); }
$('#theme-select').addEventListener('change', event => {
  applyTheme(event.target.value);
  try { localStorage.setItem('xangi-theme', event.target.value); } catch {}
});
themeMedia.addEventListener('change', () => applyTheme($('#theme-select').value));
window.addEventListener('storage', event => { if (event.key === 'xangi-theme') applyTheme(event.newValue); });
const state = { tab: 'recommended', items: [], total: 0, sources: [], selectedKinds: new Set(), selectedTopics: new Set(), selectedTags: new Set(), topicOptions: [], tagOptions: [], available: false, loading: false, lastFinished: null, polled: false, refreshPending: false };
let settingsAgents = [];
const sortNames = { manual_added_at: '個別に追加した順（新しい順）', updated: '更新日が新しい順', published: '公開日が新しい順', preference: '好み順（Good／Bad）', recommended: '好み・新しさ順', read_at: '読了に追加した順（新しい順）' };
function sortStorageKey() {
  return state.tab === 'read' ? 'xangi-feed-sort-read' : state.tab === 'manual' ? 'xangi-feed-sort-manual' : 'xangi-feed-sort';
}
function restoreSort() {
  const read = state.tab === 'read', manual = state.tab === 'manual';
  for (const [value, visible] of [['read_at', read], ['manual_added_at', manual]]) {
    const option = $(`#sort option[value=${value}]`); option.hidden = option.disabled = !visible;
  }
  let savedSort;
  try { savedSort = localStorage.getItem(sortStorageKey()); } catch {}
  const valid = Object.hasOwn(sortNames, savedSort) && (read || savedSort !== 'read_at') && (manual || savedSort !== 'manual_added_at');
  $('#sort').value = valid ? savedSort : read ? 'read_at' : manual ? 'manual_added_at' : 'updated';
}
restoreSort();
let discussionItem = null, discussionRequestId = null, discussionBusy = false, discussionReady = false;
const noteDrafts = new Map();
const notionBusy = new Set(), notionMessages = new Map();
let notionQueue = Promise.resolve();
const saveRequestIds = new Map();
const embedded = /^\/api\/extensions\/[^/]+\/(?:ui|service)(?:\/|$)/.test(location.pathname);
const base = new URL(document.baseURI);
base.pathname = base.pathname.replace(/\/?$/, '/'); base.search = ''; base.hash = '';
if (embedded) $('#home').hidden = false;
const kinds = { rss: '記事', tech: 'テック', blog: 'ブログ', news: 'ニュース', comic: '漫画', podcast: 'Podcast', paper: '論文', video: 'YouTube' };
const dateFormatter = new Intl.DateTimeFormat('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const date = value => {
  if (value == null) return '公開日不明';
  const timestamp = new Date(value * 1000);
  return Number.isNaN(timestamp.getTime()) ? 'Invalid Date' : dateFormatter.format(timestamp);
};

function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
async function api(path, data, keepalive = false) {
  const response = await fetch(new URL(path, base), { method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data), keepalive });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function notice(message) { $('#notice').textContent = message; $('#notice').hidden = !message; }
function button(label, action, className = '') { const b = el('button', className, label); b.addEventListener('click', async () => { b.disabled = true; try { await action(); } catch (e) { notice(e.message); } finally { b.disabled = false; } }); return b; }
function link(label, url) { const a = el('a', '', label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }

const safariPreferenceKey = 'xangi-feed-ios-external-safari';
let externalSafari = false;
try { externalSafari = localStorage.getItem(safariPreferenceKey) === 'on'; } catch {}
function isIOSStandalone() {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  return ios && (navigator.standalone === true || matchMedia('(display-mode: standalone)').matches);
}
function safariAppURL(url) {
  if (!externalSafari || !isIOSStandalone()) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
    // Pass the public HTTPS destination directly to Safari's OS URL handler.
    return 'x-safari-https:' + parsed.href.slice('https:'.length);
  } catch { return null; }
}
function configureArticleLink(control, url) {
  const safari = safariAppURL(url);
  control.dataset.articleUrl = url;
  control.href = safari || url; control.target = safari ? '_self' : '_blank';
  control.title = safari ? 'Safariアプリで開く' : '別タブ／ウィンドウで開く';
  if (control.id === 'article-link-open') control.textContent = safari ? 'Safariアプリで原文を開く' : '別ウィンドウで原文を開く';
}
$('#ios-external-safari').addEventListener('change', event => {
  externalSafari = event.target.checked;
  try {
    localStorage.setItem(safariPreferenceKey, externalSafari ? 'on' : 'off');
    $('#ios-link-status').textContent = 'この端末に保存しました。';
  } catch { $('#ios-link-status').textContent = '端末に保存できないため、今回はこの画面だけに適用します。'; }
  for (const control of document.querySelectorAll('a[data-article-url]')) configureArticleLink(control, control.dataset.articleUrl);
});

function recordLinkActivation(event, item) {
  if (!item || event.defaultPrevented || event.altKey || (event.type === 'click' ? event.button !== 0 : event.button !== 1)) return;
  // Do not await or cancel native navigation or the external-app handoff.
  void recordArticleOpen(item);
}
function articleLink(item, label, url = item.url) {
  const control = link(label, url);
  configureArticleLink(control, url);
  control.addEventListener('click', event => recordLinkActivation(event, item));
  control.addEventListener('auxclick', event => recordLinkActivation(event, item));
  return control;
}
async function recordArticleOpen(item) {
  try {
    const result = await api(`api/items/${item.id}/open`, {}, true);
    if (!result.marked_read) return;
    applyItem(item, result.item);
    if (state.tab === 'recommended') {
      const index = state.items.findIndex(current => current.id === item.id);
      if (index >= 0) { state.items.splice(index, 1); state.total = Math.max(0, state.total - 1); }
    }
    render();
    if ($('#detail').open && $('#detail').dataset.id === item.id) renderDetail(item);
    notice('リンクを開いた記事を読了にしました。');
  } catch {
    const message = '読了を保存できませんでした。「読んだ／視聴済み」で再試行できます。';
    notice(message);
    if ($('#detail').open && $('#detail').dataset.id === item.id) {
      const status = el('p', 'source-error', message); status.setAttribute('role', 'alert'); $('#detail-body').prepend(status);
    }
  }
}
let linkOptionsItem = null;

function showArticleLinkOptions(item) {
  linkOptionsItem = item;
  $('#article-link-name').textContent = item.title;
  configureArticleLink($('#article-link-open'), item.url); $('#article-link-url').value = item.url;
  $('#article-link-copy-fallback').hidden = true; $('#article-link-message').textContent = '';
  $('#article-link-share').hidden = typeof navigator.share !== 'function';
  $('#article-link').showModal();
}
$('#article-link-close').addEventListener('click', () => $('#article-link').close());
$('#article-link-open').addEventListener('click', event => { recordLinkActivation(event, linkOptionsItem); $('#article-link').close(); });
$('#article-link-open').addEventListener('auxclick', event => recordLinkActivation(event, linkOptionsItem));
$('#article-link-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('#article-link-url').value);
    $('#article-link-message').textContent = 'URLをコピーしました。';
  } catch {
    $('#article-link-copy-fallback').hidden = false; $('#article-link-url').focus(); $('#article-link-url').select();
    $('#article-link-message').textContent = 'URL欄を長押しなどでコピーできます。';
  }
});
$('#article-link-share').addEventListener('click', async () => {
  try {
    await navigator.share({ title: $('#article-link-name').textContent, url: $('#article-link-url').value });
    $('#article-link-message').textContent = '共有しました。';
  } catch (error) { $('#article-link-message').textContent = error.name === 'AbortError' ? '共有をキャンセルしました。' : '共有できませんでした。URLのコピーも使えます。'; }
});
function renderRecommendations(settings) {
  const form = $('#recommendation-form');
  form.elements.recommendation_min_preference.value = settings.recommendation_min_preference ?? '';
  for (const key of ['recommendation_days', 'recommendation_priority']) form.elements[key].value = settings[key];
  for (const key of ['recommendation_hide_bad', 'recommendation_mix_types']) form.elements[key].checked = settings[key];
  $('#recommendation-fields').disabled = false;
}
async function loadRecommendations(request = api('api/recommendations')) {
  try {
    renderRecommendations(await request);
    $('#recommendation-message').textContent = '条件を変えると自動で保存して、この一覧に反映します。';
    $('#recommendation-retry').hidden = true;
  } catch (e) { $('#recommendation-message').textContent = '条件を読み込めませんでした。もう一度試してください。'; $('#recommendation-retry').hidden = false; }
}

async function react(item, data) {
  const active = document.activeElement;
  const focusLabel = active?.getAttribute('aria-label');
  const focusText = active?.textContent;
  const inDetail = $('#detail').open;
  const updated = await api(`api/items/${item.id}/react`, data);
  Object.assign(item, updated);
  const itemIndex = state.items.findIndex(i => i.id === item.id);
  const visibleItem = state.items.find(i => i.id === item.id);
  if (visibleItem && visibleItem !== item) Object.assign(visibleItem, updated);
  if (updated.preference_weights) {
    const weights = new Map(updated.preference_weights.map(row => [row.name, row.weight]));
    for (const visible of state.items) {
      const own = ['duplicate', 'summary'].includes(visible.reason) ? 0 : visible.rating;
      visible.preference_score = visible.topics.reduce((sum, topic) => sum + Math.max(-3, Math.min(3, weights.get(topic) || 0)), own);
      visible.preference_known = own !== 0 || visible.topics.some(topic => weights.has(topic));
    }
  }
  if ('muted' in data || (state.tab === 'recommended' && 'read' in data) || (state.tab === 'read' && 'read' in data)) {
    const remove = 'muted' in data ? item.muted !== (state.tab === 'muted') : state.tab === 'recommended' ? !!item.read : !item.read;
    if (remove && visibleItem) {
      state.items = state.items.filter(i => i.id !== item.id); state.total = Math.max(0, state.total - 1);
    } else if (!remove) await load();
  }
  if (state.tab === 'recommended' && 'rating' in data && $('#recommendation-form').elements.recommendation_min_preference.value !== '') await load();
  // Keep current ordering while reading; explicit navigation applies new ranking.
  render();
  if ($('#detail').open && $('#detail').dataset.id === item.id) renderDetail(item);
  const scope = inDetail ? $('#detail') : document.querySelector(`article[data-id="${item.id}"]`);
  const target = scope && [...scope.querySelectorAll('button,select,input,summary')].find(b => ('read' in data && (inDetail ? b.classList.contains('read-control') : b.matches('.more-actions>summary'))) || ('muted' in data && b.classList.contains('mute-control')) || ('saved' in data && b.classList.contains('bookmark-control')) || (focusLabel ? b.getAttribute('aria-label') === focusLabel : b.textContent === focusText || ('saved' in data && ['ブックマーク', 'ブックマーク済み'].includes(b.textContent))));
  (target || ('muted' in data ? scope?.querySelector('.more-actions>summary') : null))?.focus({ preventScroll: true });
  if (!scope && ('read' in data || 'muted' in data) && !inDetail) {
    const next = state.items[Math.min(itemIndex, state.items.length - 1)];
    const nextControl = 'muted' in data ? '.mute-control' : '.more-actions>summary';
    const fallback = 'muted' in data ? (state.tab === 'muted' ? '[data-tab="all"]' : '[data-tab="muted"]') : state.tab === 'read' ? '[data-tab="recommended"]' : '[data-tab="read"]';
    (next ? document.querySelector(`article[data-id="${next.id}"] ${nextControl}`) : document.querySelector(fallback))?.focus({ preventScroll: true });
  }
  if ('read' in data) notice(item.read ? '読了に移しました。カスタムには表示されません。' : '未読に戻しました。カスタムの条件に合えば再表示されます。');
  if ('muted' in data) notice(item.muted ? 'ミュートしました。「ミュート」から確認・解除できます。' : 'ミュートを解除しました。元の状態と各一覧の条件に合わせて表示されます。');
}
let detailOpening = 0;
async function openDetail(item, target = '') {
  const opening = ++detailOpening;
  const updated = await api(`api/items/${item.id}`);
  if (opening !== detailOpening) return;
  Object.assign(item, updated);
  renderDetail(item);
  if (!$('#detail').open) $('#detail').showModal();
  if (target === 'tags') { $('#manual-tag-input').scrollIntoView({ block: 'center' }); $('#manual-tag-input').focus({ preventScroll: true }); }
  if (target === 'note') {
    $('#article-note').scrollIntoView({ block: 'center' });
    $('#article-note').focus({ preventScroll: true });
  }
}

function ratingIcon(down = false) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false'); svg.classList.add('rating-icon');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', 'M3 10h4v11H3z M7 11l6-8c2 0 3 1 2 4l-1 3h5c2 0 2 1 2 2l-2 7c0 1-1 2-3 2H7z');
  if (down) path.setAttribute('transform', 'rotate(180 12 12)');
  svg.append(path); return svg;
}
function icon(name) {
  const paths = {
    bookmark: 'M6 3h12v18l-6-4-6 4z',
    note: 'M4 4h16v16H4z M8 9h8 M8 13h6',
    more: 'M5 12h1 M11.5 12h1 M18 12h1',
    mute: 'M3 9h4l5-4v14l-5-4H3z M17 9l5 6 M22 9l-5 6',
    restore: 'M9 5l-5 5 5 5 M4 10h9a6 6 0 0 1 0 12',
  };
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('action-icon');
  const path = document.createElementNS(ns, 'path'); path.setAttribute('d', paths[name]);
  svg.append(path); return svg;
}
function iconButton(label, action, graphic, className = '') {
  const control = button('', action, 'icon-button ' + className);
  control.setAttribute('aria-label', label); control.title = label;
  control.append(graphic); return control;
}
function applyItem(item, updated) {
  Object.assign(item, updated);
  const visible = state.items.find(i => i.id === item.id); if (visible) Object.assign(visible, updated);
  if (state.tab === 'notes' && !updated.note?.trim() && visible) {
    state.items = state.items.filter(i => i.id !== item.id); state.total = Math.max(0, state.total - 1);
  }
}
function refreshItem(item) {
  render();
  if ($('#detail').open && $('#detail').dataset.id === item.id) renderDetail(item);
}
async function saveToNotion(item) {
  if (notionBusy.has(item.id)) return;
  const previous = notionQueue;
  let release; notionQueue = new Promise(resolve => { release = resolve; });
  notionBusy.add(item.id); notionMessages.set(item.id, {text: '保存AIへの依頼を準備中…'}); refreshItem(item);
  try {
    await previous;
    if (noteDrafts.has(item.id)) {
      const updated = await api(`api/items/${item.id}/note`, {note: noteDrafts.get(item.id)});
      noteDrafts.delete(item.id); applyItem(item, updated);
    }
    if (!saveRequestIds.has(item.id)) saveRequestIds.set(item.id, crypto.randomUUID());
    const request = await api(`api/items/${item.id}/save`, {confirm: true, request_id: saveRequestIds.get(item.id)});
    saveRequestIds.delete(item.id);
    applyItem(item, {...item, save_request: request});
    notionMessages.delete(item.id);
  } catch (error) {
    notionMessages.set(item.id, {text: error.message, error: true});
    try { applyItem(item, await api(`api/items/${item.id}`)); } catch {}
  } finally { notionBusy.delete(item.id); release(); refreshItem(item); }
}
function notionButton(item) {
  const busy = notionBusy.has(item.id), request = item.save_request;
  const pending = request && ['creating','sending','requested','needs_input','uncertain'].includes(request.status);
  const control = button(busy ? '依頼中…' : pending ? '保存依頼済み' : '保存をAIに依頼', () => saveToNotion(item), 'notion-action');
  control.disabled = busy;
  control.setAttribute('aria-label', busy ? '保存AIへ依頼中' : 'Notionへの保存をAIに依頼');
  control.title = '保存担当AIが、利用者の保存スキルに従って保存します';
  control.replaceChildren(el('span', 'notion-label', control.textContent), el('span', 'notion-mark', 'N'));
  control.lastElementChild.setAttribute('aria-hidden', 'true');
  control.dataset.action = 'notion'; return control;
}
function exportStatus(item) {
  const message = notionMessages.get(item.id), request = item.save_request;
  const labels = {creating:'保存用の会話を作成中', sending:'保存AIへ依頼中', requested:'保存AIへ依頼済み', needs_input:'保存AIから確認があります', uncertain:'保存依頼の結果を会話で確認してください', saved:'AIから保存完了の報告', partial:'AIから一部保存の報告', error:'AIから保存未完了の報告'};
  const warning = message?.error || request && ['uncertain','partial','error','needs_input'].includes(request.status);
  const box = el('p', 'notion-status' + (warning ? ' source-error' : '')); box.setAttribute('role', 'status');
  if (message) box.append(document.createTextNode(message.text));
  else if (request) box.append(document.createTextNode([labels[request.status] || '保存状態を確認してください', request.summary, request.error].filter(Boolean).join(' / ')));
  if (request?.session_id) box.append(document.createTextNode(' '), link('保存用の会話を開く', new URL(`/chat/${encodeURIComponent(request.session_id)}`, location.origin).href));
  const pageUrl = request?.page_url;
  if (pageUrl) box.append(document.createTextNode(' '), link('Notionで開く', pageUrl));
  return box;
}
function actions(item, inDetail = false) {
  const group = el('div', 'actions compact-actions');
  const good = iconButton(`${item.title}を好きと評価`, () => react(item, { rating: item.rating === 1 ? 0 : 1 }), ratingIcon(), 'rating-control');
  const bad = iconButton(`${item.title}を興味なしと評価`, () => react(item, { rating: item.rating === -1 ? 0 : -1, reason: '' }), ratingIcon(true), 'rating-control bad');
  good.title = 'Good · 好みに合う'; bad.title = 'Bad · 好みに合わない';
  good.setAttribute('aria-pressed', item.rating === 1); bad.setAttribute('aria-pressed', item.rating === -1);
  const save = iconButton(item.saved ? 'ブックマーク済み' : 'ブックマーク', () => react(item, { saved: !item.saved }), icon('bookmark'), 'bookmark-control');
  save.setAttribute('aria-pressed', !!item.saved);
  group.append(good, bad, save);
  if (!inDetail) {
    group.append(iconButton(item.note?.trim() ? 'メモを編集' : 'メモを書く', () => openDetail(item, 'note'), icon('note'), 'note-control'), notionButton(item));
  }
  group.append(muteControl(item));
  const secondary = el('div', inDetail ? 'detail-actions' : 'action-menu');
  if (!inDetail) secondary.append(button('記事の詳細', () => openDetail(item)), button('タグを編集', () => openDetail(item, 'tags')));
  secondary.append(button('リンクを共有・コピー', () => showArticleLinkOptions(item)));
  const readLabel = item.kind === 'video' ? (item.read ? '未視聴に戻す' : '視聴済み') : (item.read ? '未読に戻す' : '読んだ');
  const read = button(readLabel, () => react(item, { read: !item.read }), 'read-control');
  read.setAttribute('aria-label', `${item.title}を${readLabel}`);
  secondary.append(read);
  if (inDetail) {
    const analysis = item.analysis, running = analysis && ['pending', 'processing'].includes(analysis.status);
    const analyze = button(running ? '解説を準備中' : analysis?.status === 'done' ? 'AI解説を作り直す' : '日本語の解説を作る', async () => {
      await api(`api/items/${item.id}/analyze`, {});
      notice('日本語の要点とAIコメントを準備しています。');
      applyItem(item, await api(`api/items/${item.id}`)); refreshItem(item);
    });
    analyze.disabled = running || !state.available; secondary.append(analyze);
  }
  if (item.audio_url) secondary.append(articleLink(item, '音声を聴く', item.audio_url));
  const discuss = button(item.session_id ? '前の会話を開く' : 'xangiと話す', () => showDiscussion(item), 'discuss');
  discuss.disabled = !state.available; secondary.append(discuss);
  if (inDetail) group.append(secondary);
  else {
    const more = el('details', 'more-actions'), summary = el('summary', 'icon-button');
    summary.setAttribute('aria-label', 'その他の操作'); summary.title = '詳細・読了・xangiとの会話'; summary.append(icon('more'));
    more.append(summary, secondary); group.append(more);
    secondary.addEventListener('click', () => { more.open = false; });
    more.addEventListener('keydown', event => { if (event.key === 'Escape') { more.open = false; summary.focus(); } });
    more.addEventListener('toggle', () => { if (more.open) document.querySelectorAll('.more-actions[open]').forEach(other => { if (other !== more) other.open = false; }); });
  }
  return group;
}

function muteControl(item) {
  const group = el('div', 'article-mute');
  const label = item.muted ? 'ミュートを解除' : 'ミュート';
  const control = iconButton(item.muted ? `${item.title}のミュートを解除` : `${item.title}をミュート`, () => react(item, { muted: !item.muted }), icon(item.muted ? 'restore' : 'mute'), 'mute-control');
  control.append(el('span', 'sr-only', label));
  control.setAttribute('aria-pressed', !!item.muted);
  control.title = item.muted ? 'ミュートを解除して通常の一覧へ戻す' : '読了状態や評価を変えず、通常の一覧から非表示にする';
  group.append(control); return group;
}

let discussionOpening = 0;
async function showDiscussion(item) {
  discussionItem = item; discussionRequestId = null; discussionReady = false;
  const opening = ++discussionOpening, form = $('#discussion-form');
  form.reset();
  $('#discussion-title').textContent = item.title;
  $('#discussion-message').textContent = 'エージェント候補を確認しています…';
  form.elements.new_conversation.closest('label').hidden = !item.session_id;
  $('#discussion-help').textContent = item.session_id ? '追記が空なら既存の会話を開くだけです。追記はその会話へ送信します。別担当なら新しい会話を選びます。会話は新しいタブで開きます。' : '記事の文脈と追記を渡し、新しいタブで開きます。プロジェクトは指定せず作成します。';
  form.elements.agent_id.replaceChildren(new Option(item.session_id ? '既存の会話の担当' : 'xangiの既定', ''));
  form.elements.agent_id.disabled = true;
  form.querySelector('[type=submit]').disabled = true;
  $('#discussion').showModal();
  try {
    const options = await api('api/discussion-options');
    if (opening !== discussionOpening || !$('#discussion').open) return;
    for (const agent of options.agents) {
      const option = new Option([agent.name, agent.model].filter(Boolean).join(' / ') + (agent.available === false ? '（利用不可）' : ''), agent.id);
      option.disabled = agent.available === false; form.elements.agent_id.append(option);
    }
    form.elements.agent_id.disabled = false;
    discussionReady = true; form.querySelector('[type=submit]').disabled = false;
    $('#discussion-message').textContent = 'プロジェクトは指定しません。送信すると選択したAIで処理します。';
  } catch (e) { if (opening === discussionOpening) $('#discussion-message').textContent = '議論の準備に失敗しました。画面を再読込して試してください。'; }
}
$('#discussion-close').addEventListener('click', () => { if (!discussionBusy) $('#discussion').close(); });
$('#discussion').addEventListener('cancel', event => { if (discussionBusy) event.preventDefault(); });
$('#discussion-form').addEventListener('input', () => { if (!discussionBusy) discussionRequestId = null; });
$('#discussion-form [name=agent_id]').addEventListener('change', event => {
  if (discussionItem?.session_id && event.target.value) $('#discussion-form').elements.new_conversation.checked = true;
});
$('#discussion-form').addEventListener('submit', async event => {
  event.preventDefault(); if (discussionBusy || !discussionReady || !discussionItem) return;
  const form = event.target;
  // Reserve the popup inside the user gesture; awaiting the API first causes
  // popup blockers to discard it. Never start an AI turn when opening failed.
  const popup = window.open('about:blank', '_blank');
  if (!popup) { $('#discussion-message').textContent = '新しいタブがブロックされました。このサイトのポップアップを許可して再試行してください。'; return; }
  popup.opener = null; popup.document.title = 'xangiの会話を準備中'; popup.document.body.textContent = '会話を準備しています…';
  discussionBusy = true; const controls = [...form.elements], disabledBefore = controls.map(control => control.disabled); controls.forEach(control => { control.disabled = true; }); $('#discussion-close').disabled = true;
  discussionRequestId ||= crypto.randomUUID();
  try {
    const result = await api(`api/items/${discussionItem.id}/discuss`, { prompt: form.elements.prompt.value, agent_id: form.elements.agent_id.value, new_conversation: form.elements.new_conversation.checked, request_id: discussionRequestId });
    const url = new URL(`/chat/${encodeURIComponent(result.session_id)}`, location.origin);
    if (!popup.closed) popup.location.replace(url.href);
    discussionItem.session_id = result.session_id;
    $('#discussion-message').replaceChildren(document.createTextNode(result.error || '会話を開きました。'), link('会話を別タブで開く', url.href));
    if (!result.error) { $('#discussion').close(); notice(result.sent ? '記事と追記を送信し、会話を別タブで開きました。' : '既存の会話を別タブで開きました。'); discussionRequestId = null; }
    render(); if ($('#detail').open) renderDetail(discussionItem);
  } catch (e) { if (!popup.closed) popup.close(); $('#discussion-message').textContent = e.message; }
  finally { discussionBusy = false; controls.forEach((control, index) => { control.disabled = disabledBefore[index]; }); $('#discussion-close').disabled = false; }
});

function reasonControl(item) {
  const label = el('label', 'reason', '興味なしの理由（任意）');
  const select = el('select');
  for (const [value, text] of [['', '選択なし'], ['interest', '興味がない'], ['duplicate', '既読・重複'], ['summary', '要約・内容の問題']]) {
    const option = el('option', '', text); option.value = value; select.append(option);
  }
  select.value = item.reason;
  select.addEventListener('change', () => react(item, { reason: select.value }).catch(e => notice(e.message)));
  label.append(select); return label;
}
function preferenceLabel(item) {
  const known = item.preference_known === true && Number.isFinite(item.preference_score);
  const value = known ? `${item.preference_score > 0 ? '+' : ''}${item.preference_score}` : '未評価';
  const badge = el('span', 'preference-score', '好み ' + value);
  badge.title = 'Good／Badの話題別集計とこの記事への評価。0は評価が相殺された状態、未評価は根拠となる評価がない状態です。';
  return badge;
}
let showLinkPreviews = false;
const previewData = new Map(), previewRequests = new Map();
const previewObserver = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) {
    previewObserver.unobserve(entry.target);
    void hydrateLinkCard(entry.target);
  }
}, {rootMargin: '150px'});
function fillLinkCard(card, data) {
  card.replaceChildren();
  if (data.has_image) {
    const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
    image.src = new URL(`api/items/${card.dataset.itemId}/preview/image`, base).href;
    image.addEventListener('error', () => image.remove(), {once: true}); card.append(image);
  }
  const text = el('span', 'link-card-text');
  text.append(el('strong', 'link-card-title', data.title || card.dataset.title), el('span', 'link-card-site', data.site_name || card.dataset.site));
  if (data.description) text.append(el('span', 'link-card-description', data.description));
  card.append(text);
}
async function hydrateLinkCard(card) {
  if (!showLinkPreviews || !card.isConnected) return;
  const id = card.dataset.itemId;
  if (!previewRequests.has(id)) previewRequests.set(id, (async () => {
    for (let attempt = 0; attempt < 30 && showLinkPreviews; attempt++) {
      const result = await api(`api/items/${id}/preview`);
      if (['done', 'error', 'disabled'].includes(result.status)) return result;
      await new Promise(resolve => setTimeout(resolve, attempt < 4 ? 250 : 1500));
    }
    return {status: 'pending'};
  })().finally(() => previewRequests.delete(id)));
  try {
    const result = await previewRequests.get(id);
    if (['done', 'error'].includes(result.status)) previewData.set(id, result);
    if (showLinkPreviews && card.isConnected) fillLinkCard(card, result);
  } catch { /* Keep the article title and site when a preview is unavailable. */ }
}
function linkCard(item) {
  if (!showLinkPreviews) return document.createDocumentFragment();
  const card = articleLink(item, ''); card.className = 'link-card';
  card.dataset.itemId = item.id; card.dataset.title = item.title; card.dataset.site = new URL(item.url).hostname;
  card.setAttribute('aria-label', `リンクカード: ${item.title}`);
  if (item.link_preview) previewData.set(item.id, item.link_preview);
  fillLinkCard(card, previewData.get(item.id) || {});
  if (!previewData.has(item.id)) previewObserver.observe(card);
  return card;
}
function meta(item) {
  const row = el('div', 'news-top');
  if (item.manually_added) row.append(el('span', 'kind', '個別追加'));
  const label = item.updated != null ? '更新' : item.published != null ? '公開' : '取得';
  const publisher = el('span', 'publisher', item.publisher_label || item.sources.map(s => s.name).join(' / ') || new URL(item.url).hostname);
  row.append(el('span', 'kind', kinds[item.kind]), publisher,
    el('span', '', label + ' ' + date(item.updated ?? item.published ?? item.fetched)), preferenceLabel(item));
  return row;
}
function topicNodes(item) { const row = el('div', 'topics'); for (const topic of item.topics) { const control = button(topic, async () => { state.selectedTopics.add(topic); if ($('#detail').open) $('#detail').close(); updateSelections(); await load(); $('#active-filters button')?.focus(); }, 'topic'); control.setAttribute('aria-label', `話題タグ「${topic}」で絞り込む`); row.append(control); } return row; }
function evidence(item) { if (item.content_kind === 'youtube_transcript') return '動画の音声をtranscriber_toolで文字起こしした本文です。認識誤りを含む場合があります。'; if (item.content_kind === 'youtube_captions') return 'YouTubeの公開字幕（自動生成を含む）を取得しました。動画・音声本編は未取得。'; if (item.content_kind === 'youtube_description') return 'YouTubeの動画情報と説明欄を取得しました。字幕本文・動画・音声本編は未取得。'; if (item.content_text) return '直接追加した公開ページから取得した本文です（最大50,000文字）。画像・動的表示・動画や音声本編は未取得。'; if (item.content_origin === 'manual') return '個別追加の記事。入力したタイトル・抜粋が根拠です。記事全文は未取得。'; return item.kind === 'video' ? (item.content_origin === 'page' ? 'YouTubeの公開一覧のタイトル・抜粋が根拠です。公開日不明の場合は取得順で表示。動画・音声・字幕は未確認。' : 'YouTubeのタイトル・説明欄が根拠です。動画・音声・字幕は未確認。') : item.kind === 'paper' ? '論文要旨からの抜粋。本文・査読状況は未確認。' : item.kind === 'podcast' ? '番組説明からの抜粋。音声は未確認。' : item.content_origin === 'page' ? 'ページの見出し・抜粋。記事全文は未確認。' : 'RSS本文からの抜粋。記事全文は未確認。'; }
function commentBubble(item, result) {
  const bubble = el('aside', 'ai-comment');
  bubble.setAttribute('role', 'note'); bubble.setAttribute('aria-label', 'AIコメント');
  bubble.append(el('p', 'ai-comment-label', 'AIコメント'));
  const analysis = item.analysis;
  if (result?.comment_version === 1 && result.comment) {
    bubble.append(el('p', 'ai-comment-text', result.comment));
    const context = result.comment_context || {}, sources = context.sources || [];
    const basis = sources.length ? '関連する記録を参考にしたコメント' :
      context.status === 'empty' ? '関連する記録なし・記事内容に基づくコメント' :
      ['error', 'unavailable'].includes(context.status) ? '関連する記録を取得できず・記事内容に基づくコメント' :
      '記事内容に基づくコメント（関連する記録は未参照）';
    bubble.append(el('p', 'ai-comment-basis', basis + (context.degraded ? '（キーワード検索）' : '')));
    if (sources.length) {
      const records = el('details', 'ai-comment-records');
      records.append(el('summary', '', `コメント生成時に参照した記録（${sources.length}件）`));
      for (const source of sources) {
        const record = el('div', 'recommendation-source');
        record.append(el('p', 'evidence', `${source.id}: ${source.path}`), el('p', 'excerpt', source.excerpt));
        records.append(record);
      }
      bubble.append(records);
    }
    if (['pending', 'processing'].includes(analysis?.status)) bubble.append(el('p', 'ai-comment-basis', 'コメントを更新中です。前回の内容を表示しています。'));
    if (analysis?.status === 'error') bubble.append(el('p', 'source-error', '更新に失敗しました。前回のコメントを表示しています。'));
  } else {
    const text = ['pending', 'processing'].includes(analysis?.status) ? 'AIコメントを準備中です。' :
      analysis?.status === 'error' ? 'AIコメントを生成できませんでした。15分後の再表示・再起動時に再試行します。記事詳細からすぐ作り直すこともできます。' :
      'AIコメントはまだありません。記事詳細から生成できます。';
    bubble.append(el('p', 'ai-comment-basis', text));
  }
  const running = ['pending', 'processing'].includes(analysis?.status);
  const regenerate = button(running ? 'AIコメントを準備中' : result?.comment_version === 1 ? 'AIコメントを作り直す' : 'AIコメントを作る', async () => {
    await api(`api/items/${item.id}/analyze`, {});
    applyItem(item, await api(`api/items/${item.id}`)); refreshItem(item);
    notice('関連する記録を確認し、日本語の要点とAIコメントを準備しています。');
  }, 'ai-comment-regenerate');
  regenerate.disabled = running || !state.available;
  bubble.append(regenerate);
  return bubble;
}
function editorial(item) {
  const box = el('div', 'editorial');
  const analysis = item.analysis;
  const result = analysis?.result || null;
  if (result) {
    box.append(el('p', 'excerpt', result.summary_ja));
    box.append(el('p', 'evidence', 'AIによる日本語の要点（取得した抜粋に基づく）'));
  } else {
    box.append(el('p', 'excerpt', item.summary || '本文の抜粋はありません。原文をご覧ください。'));
    box.append(el('p', analysis?.status === 'error' ? 'source-error' : 'evidence', analysis?.status === 'error' ? `解説を作れませんでした: ${analysis.error}` : '日本語の要点・解説は準備待ちです。原文を表示しています。'));
  }
  box.append(commentBubble(item, result));
  return box;
}
function render() {
  $('#recommendation-panel').hidden = state.tab !== 'recommended';
  previewObserver.disconnect();
  const feed = $('#feed'); feed.replaceChildren(); feed.setAttribute('aria-busy', 'false');
  $('#result-count').textContent = `${state.total}件${state.items.length < state.total ? `のうち${state.items.length}件を表示` : ''}`;
  $('#more').hidden = state.items.length >= state.total;
  $('#view-description').textContent = (state.tab === 'all' ? 'ミュートを除く取得済みの記事。' : state.tab === 'manual' ? 'URLから個別に追加した記事。読了後や配信元からの再取得後も残ります。' : state.tab === 'muted' ? 'ミュートした記事。解除すると元の状態と各一覧の条件に合わせて表示されます。' : state.tab === 'notes' ? '自分のメモを残した記事。メモの内容も検索できます。' : state.tab === 'recommended' ? 'カスタム条件に合う未読の記事。' : state.tab === 'saved' ? 'ブックマークした記事。読了後も残ります。' : state.tab === 'read' ? '読み終えた記事・視聴済みの動画。「未読に戻す」で読了から外せます。過去の読了日時が不明な記事は後ろに表示します。' : '議論した記事。') + sortNames[$('#sort').value] + 'で表示します。';
  if (!state.items.length) {
    if (state.tab === 'recommended') {
      const box = el('div', 'empty');
      box.append(el('h3', '', '条件に合う記事がありません'), el('p', '', 'カスタムの条件を緩めるか、「すべて」で取得済みの記事を確認できます。'), button('カスタムの条件', () => { $('#recommendation-toggle').open = true; $('#recommendation-title').scrollIntoView({ block: 'center' }); $('#recommendation-form').elements.recommendation_days.focus(); }), button('すべてを見る', () => changeTab('all')));
      feed.append(box); return;
    }
    if (state.tab === 'muted') {
      const box = el('div', 'empty');
      box.append(el('h3', '', 'ミュートした記事が見つかりません'), el('p', '', '各記事のミュート操作でここへ移せます。検索や分類の条件も確認してください。'), button('すべてを見る', () => changeTab('all')));
      feed.append(box); return;
    }
    if (state.tab === 'manual') {
      const box = el('div', 'empty');
      box.append(el('h3', '', '個別追加の記事が見つかりません'), el('p', '', '「記事を追加」から登録した記事を表示します。検索や分類の条件も確認してください。'), button('記事を追加', () => $('#article-open').click()));
      feed.append(box); return;
    }
    if (state.tab === 'read') {
      const box = el('div', 'empty');
      box.append(el('h3', '', '読了した記事はまだありません'), el('p', '', '記事の「読んだ」を押すと、ここに入ります。検索や絞り込みをしている場合は条件も確認してください。'), button('カスタムを見る', () => changeTab('recommended')));
      feed.append(box); return;
    }
    const box = el('div', 'empty'); box.append(el('h3', '', state.tab === 'saved' ? '残しておきたい記事をブックマークしよう' : state.tab === 'discussed' ? '気になる記事から、話してみよう' : '記事はまだありません'), el('p', '', '配信元と取得状態を確認するか、検索条件を変えてみてください。'));
    box.append(button('配信元を確認', showSettings)); feed.append(box); return;
  }
  for (const item of state.items) {
    const article = el('article', 'news'); article.dataset.id = item.id;
    const result = item.analysis?.result || null;
    const title = articleLink(item, result?.title_ja || item.title);
    title.className = 'news-title';
    article.append(meta(item));
    if (state.tab === 'manual') article.append(el('p', 'muted', item.manual_added_at == null ? '追加日時不明（取得 ' + date(item.fetched) + '）' : '追加 ' + date(item.manual_added_at)));
    if (state.tab === 'read') article.append(el('p', 'muted', item.read_at == null ? '読了日時不明' : '読了 ' + date(item.read_at)));
    article.append(title, linkCard(item), editorial(item), topicNodes(item), manualTagNodes(item), actions(item), exportStatus(item));
    if (item.note?.trim()) article.append(el('p', 'note-preview', '自分のメモ: ' + item.note));
    if (item.rating === -1) article.append(reasonControl(item));
    if (state.tab === 'recommended') article.append(el('p', 'recommendation', item.recommendation));
    feed.append(article);
  }
}
const recommendationRecords = new Map();
function recommendationEvidence(item) {
  const section = el('section', 'recommendation-evidence');
  section.append(el('h3', '', '推薦根拠'), el('p', 'muted', 'xangi-searchで関連する記録を探せます。AIコメントで使った記録は吹き出し内に表示します。'));
  const key = JSON.stringify([item.id, item.title]);
  const record = recommendationRecords.get(key);
  if (record) {
    const status = el('p', record.status === 'error' ? 'source-error' : 'evidence', record.message);
    status.setAttribute('role', 'status'); section.append(status);
    for (const source of record.sources || []) {
      const entry = el('div', 'recommendation-source');
      entry.append(el('p', 'evidence', source.path), el('p', 'excerpt', source.excerpt)); section.append(entry);
    }
  }
  const search = button(record?.status === 'loading' ? '関連する記録を検索中…' : record ? '推薦根拠をもう一度探す' : '推薦根拠を探す', async () => {
    recommendationRecords.set(key, {status: 'loading', message: '関連する記録を検索中です。', sources: []});
    renderDetail(item);
    let result;
    try { result = await api(`api/items/${item.id}/evidence`); }
    catch { result = {status: 'error', message: '推薦根拠を取得できませんでした。もう一度お試しください。', sources: []}; }
    recommendationRecords.set(key, result);
    if ($('#detail').open && $('#detail').dataset.id === item.id) {
      renderDetail(item); $('#detail .recommendation-evidence button').focus({preventScroll: true});
    }
  });
  search.disabled = record?.status === 'loading';
  section.append(search); return section;
}
function renderDetail(item) {
  const body = $('#detail-body'); body.replaceChildren(); $('#detail').dataset.id = item.id;
  const result = item.analysis?.result || null;
  body.append(meta(item), el('h2', 'detail-title', result?.title_ja || item.title), articleLink(item, item.kind === 'video' ? 'YouTubeで見る' : '原文を読む'), el('p', 'evidence', evidence(item)), linkCard(item), editorial(item), recommendationEvidence(item), topicNodes(item), actions(item, true));
  if (result) { const original = el('details'); original.append(el('summary', '', '原文のタイトル・抜粋'), el('h3', '', item.title), el('p', 'excerpt', item.summary)); body.append(original); }
  if (item.content_origin === 'manual') {
    if (item.content_text) {
      const captured = el('details'); captured.className = 'captured-article';
      captured.append(el('summary', '', item.content_kind === 'youtube_transcript' ? '音声から文字起こしした本文' : item.content_kind === 'youtube_captions' ? '取得したYouTube字幕（自動生成を含む）' : item.content_kind === 'youtube_description' ? '取得したYouTube説明欄' : '取得した本文（最大50,000文字）'), el('p', 'captured-body', item.content_text)); body.append(captured);
    }
    if (item.fetch_state === 'fetching') body.append(el('p', 'muted', '本文を取得中です。字幕が取得できない動画は音声から文字起こしします。'));
    if (item.fetch_error) body.append(el('p', 'source-error', item.fetch_error));
    const retry = button(item.content_text ? '本文を再取得' : '本文を取得', async () => {
      retry.disabled = true; retry.textContent = '本文を取得中…';
      try {
        let updated = await api(`api/items/${item.id}/fetch`, {background: true});
        updated = await waitForArticle(updated);
        applyItem(item, updated); refreshItem(item);
        notice(updated.fetch_error ? '本文を取得できませんでした。詳細の表示を確認してください。' : '記事本文を取得しました。');
      } catch (error) { notice(error.message); }
      finally { retry.disabled = false; }
    });
    body.append(retry);
  }
  if (item.rating === -1) body.append(reasonControl(item));
  if (item.discussion_error) body.append(el('p', 'source-error', item.discussion_error));
  body.append(manualTagEditor(item), noteEditor(item));
}
function manualTagNodes(item) {
  const row = el('div', 'manual-tags');
  if (item.tags?.length) row.append(el('span', 'tag-caption', '自分のタグ'));
  for (const name of item.tags || []) {
    const control = button(name, async () => { state.selectedTags.add(name); updateSelections(); await load(); $('#active-filters button')?.focus(); }, 'topic');
    control.setAttribute('aria-label', `自分のタグ「${name}」で絞り込む`); row.append(control);
  }
  return row;
}
async function changeManualTag(item, operation, name) {
  try {
    const updated = await api(`api/items/${item.id}/tags`, { [operation]: name });
    applyItem(item, updated); await loadTagOptions(); await load();
    if ($('#detail').open) { renderDetail(item); $('#manual-tag-input').focus(); }
  } catch (e) { const status = $('#tag-message'); if (status) status.textContent = e.message; else notice(e.message); }
}
function manualTagEditor(item) {
  const section = el('section', 'manual-tag-editor');
  section.append(el('h3', '', '自分のタグ'));
  const assigned = el('div', 'manual-tag-assigned');
  for (const name of item.tags || []) {
    const remove = button(name + ' ×', () => changeManualTag(item, 'remove', name), 'filter-chip');
    remove.setAttribute('aria-label', `この記事からタグ「${name}」を外す`); assigned.append(remove);
  }
  const form = el('form'), label = el('label', '', 'タグ名（1〜40文字）'), input = el('input');
  input.id = 'manual-tag-input'; input.maxLength = 40; input.required = true; input.autocomplete = 'off';
  input.setAttribute('list', 'manual-tag-suggestions'); label.append(input);
  const suggestions = el('datalist'); suggestions.id = 'manual-tag-suggestions';
  for (const tag of state.tagOptions) suggestions.append(new Option(tag.name));
  const add = el('button', '', 'タグを追加'); add.type = 'submit';
  form.addEventListener('submit', async event => { event.preventDefault(); add.disabled = true; await changeManualTag(item, 'add', input.value); add.disabled = false; });
  form.append(label, suggestions, add);
  const message = el('p'); message.id = 'tag-message'; message.setAttribute('role', 'status');
  section.append(assigned, form, message, el('p', 'muted', '×でこの記事から外します。タグ自体の削除は分類パネルの「タグの管理」で行います。'));
  return section;
}
async function loadTagOptions() {
  const options = await api('api/filter-options'); state.tagOptions = options.tags || [];
  renderManualTagChoices(); updateSelections();
}
function renderManualTagChoices() {
  const choices = $('#manual-tag-choices'), management = $('#tag-management-list'); choices.replaceChildren(); management.replaceChildren();
  if (!state.tagOptions.length) choices.append(el('p', 'muted', '記事の詳細からタグを追加できます。'));
  for (const tag of state.tagOptions) {
    const control = button(`${tag.name} (${tag.count})`, async () => { state.selectedTags.has(tag.name) ? state.selectedTags.delete(tag.name) : state.selectedTags.add(tag.name); updateSelections(); await load(); }, 'topic');
    control.dataset.tag = tag.name; choices.append(control);
    const row = el('div', 'tag-management-row'); row.append(el('span', '', tag.name));
    const remove = button('削除', async () => {
      if (!confirm(`タグ「${tag.name}」を削除しますか？\n${tag.count}件の記事からタグを外します。記事・メモは残ります。`)) return;
      try {
        await api('api/tags/delete', { name: tag.name, confirm: true });
        state.selectedTags.delete(tag.name); await loadTagOptions(); await load();
        $('#tag-management-message').textContent = 'タグを削除しました。記事は残っています。';
      } catch (e) { $('#tag-management-message').textContent = e.message; }
    });
    remove.setAttribute('aria-label', `タグ「${tag.name}」をすべての記事から削除`); row.append(remove); management.append(row);
  }
}
function noteEditor(item) {
  const section = el('section', 'note-editor');
  const form = el('form'), label = el('label', '', '感想・メモ（任意）'), input = el('textarea');
  input.id = 'article-note'; input.maxLength = 4000; input.rows = 5;
  input.value = noteDrafts.get(item.id) ?? item.note ?? '';
  const help = el('p', 'muted', '4000文字まで。メモを保存すると、このアプリで後から検索できます。');
  help.id = 'note-help'; input.setAttribute('aria-describedby', help.id);
  const message = el('p', 'note-message'); message.setAttribute('role', 'status');
  const save = el('button', '', 'メモを保存'); save.type = 'submit';
  input.addEventListener('input', () => { noteDrafts.set(item.id, input.value); message.textContent = '未保存の変更があります'; });
  label.append(input); form.append(label, help, save, message);
  const apply = updated => { applyItem(item, updated); render(); };
  const persist = async () => {
    const updated = await api(`api/items/${item.id}/note`, { note: input.value });
    noteDrafts.delete(item.id); apply(updated);
  };
  form.addEventListener('submit', async event => {
    event.preventDefault(); input.disabled = save.disabled = true;
    try {
      await persist();
      if ($('#detail').open && $('#detail').dataset.id === item.id) { renderDetail(item); $('#detail .note-message').textContent = 'メモを保存しました'; }
    }
    catch (e) { message.textContent = e.message; }
    finally { input.disabled = save.disabled = false; }
  });
  input.disabled = save.disabled = notionBusy.has(item.id);
  form.append(notionButton(item), exportStatus(item)); section.append(el('h3', '', '振り返り'), form); return section;
}
const classificationTabs = new Set(['recommended', 'all', 'manual', 'saved', 'notes', 'read', 'discussed', 'muted']);
const classificationMemory = new Map();
let classificationRestored = false, classificationReady = false;
function classificationPreferenceKey() { return `xangi-feed-classification:${state.tab}`; }
function saveClassification() {
  if (!classificationRestored || !classificationTabs.has(state.tab)) return;
  const saved = JSON.stringify({
    kinds: [...state.selectedKinds], topics: [...state.selectedTopics], tags: [...state.selectedTags],
    source: $('#source').value, search: $('#search').value, sourceSearch: $('#source-search').value,
  });
  classificationMemory.set(state.tab, saved);
  try { localStorage.setItem(classificationPreferenceKey(), saved); } catch {}
}
function restoreClassification() {
  if (!classificationReady || !classificationTabs.has(state.tab)) return;
  classificationRestored = false;
  let saved = {};
  try {
    let raw = classificationMemory.get(state.tab);
    if (raw === undefined) raw = localStorage.getItem(classificationPreferenceKey());
    const parsed = JSON.parse(raw || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed;
  } catch {}
  const selection = (values, allowed) => new Set(Array.isArray(values) ? values.filter(value => allowed.includes(value)) : []);
  state.selectedKinds = selection(saved.kinds, Object.keys(kinds));
  state.selectedTopics = selection(saved.topics, state.topicOptions);
  state.selectedTags = selection(saved.tags, state.tagOptions.map(tag => tag.name));
  $('#kind').value = state.selectedKinds.size === 1 ? [...state.selectedKinds][0] : '';
  $('#search').value = typeof saved.search === 'string' ? saved.search.slice(0, 200) : '';
  $('#source-search').value = typeof saved.sourceSearch === 'string' ? saved.sourceSearch.slice(0, 100) : '';
  const source = typeof saved.source === 'string' && state.sources.some(source => String(source.id) === saved.source) ? saved.source : '';
  renderSourceChoices(source);
  classificationRestored = true;
  updateSelections();
}
let loadVersion = 0;
function showDiscoveryPanel() {
  const discovery = state.tab === 'discovery';
  $('#discovery-panel').hidden = !discovery;
  for (const selector of ['.filters', '#active-filters', '.list-info', '#feed']) $(selector).hidden = discovery;
  if (discovery) { $('#more').hidden = true; $('#recommendation-panel').hidden = true; $('#view-description').textContent = '好みと検索条件に合う記事を見つけて読む場所です。'; }
}
async function load(more = false) {
  showDiscoveryPanel();
  if (state.tab === 'discovery') { ++loadVersion; state.loading = false; return loadDiscovery(); }
  saveClassification();
  if (more && state.loading) return;
  const version = ++loadVersion;
  state.loading = true; $('#feed').setAttribute('aria-busy', 'true');
  $('#more').disabled = true;
  try {
    const params = new URLSearchParams({ tab: state.tab, source: $('#source').value, q: $('#search').value, sort: $('#sort').value, limit: 50, offset: more ? state.items.length : 0 });
    for (const kind of state.selectedKinds) params.append('kind', kind);
    for (const topic of state.selectedTopics) params.append('topic', topic);
    for (const tag of state.selectedTags) params.append('tag', tag);
    const result = await api('api/items?' + params);
    if (version !== loadVersion) return;
    state.items = more ? [...state.items, ...result.items.filter(i => !state.items.some(x => x.id === i.id))] : result.items; state.total = result.total;
    render(); updateSelections(); $('#new-items').hidden = true;
  } catch (e) { if (version === loadVersion) notice(e.message); } finally { if (version === loadVersion) { state.loading = false; $('#more').disabled = false; $('#feed').setAttribute('aria-busy', 'false'); } }
}
async function loadSources() {
  state.sources = (await api('api/sources')).sources;
  renderSourceChoices();
}
function renderSourceChoices(selected = $('#source').value) {
  const select = $('#source');
  const query = $('#source-search').value.trim().toLocaleLowerCase();
  const matches = state.sources.filter(source => source.name.toLocaleLowerCase().includes(query));
  select.replaceChildren(new Option('すべての配信元', ''));
  for (const source of state.sources) if (!query || matches.includes(source) || String(source.id) === selected) select.append(new Option(source.name, source.id));
  select.value = state.sources.some(source => String(source.id) === selected) ? selected : '';
  $('#source-match').textContent = `${matches.length}件の配信元`;
}
function updateSelections() {
  saveClassification();
  for (const control of $('#kind-choices').children) control.setAttribute('aria-pressed', control.dataset.kind ? state.selectedKinds.has(control.dataset.kind) : state.selectedKinds.size === 0);
  for (const control of $('#topic-choices').children) control.setAttribute('aria-pressed', state.selectedTopics.has(control.dataset.topic));
  for (const control of $('#manual-tag-choices').querySelectorAll('[data-tag]')) control.setAttribute('aria-pressed', state.selectedTags.has(control.dataset.tag));
  const active = $('#active-filters'); active.replaceChildren();
  const chip = (label, clear) => {
    const control = button(label + ' ×', async () => { clear(); updateSelections(); await load(); }, 'filter-chip');
    control.setAttribute('aria-label', `${label}の絞り込みを解除`); active.append(control);
  };
  for (const kind of state.selectedKinds) chip(kinds[kind], () => state.selectedKinds.delete(kind));
  for (const topic of state.selectedTopics) chip(topic, () => state.selectedTopics.delete(topic));
  for (const tag of state.selectedTags) chip('自分のタグ: ' + tag, () => state.selectedTags.delete(tag));
  if ($('#source').value) {
    const source = state.sources.find(source => String(source.id) === $('#source').value);
    if (source) chip(source.name, () => { $('#source').value = ''; });
  }
  if ($('#search').value) chip('検索: ' + $('#search').value, () => { $('#search').value = ''; });
  if (active.children.length) active.append(button('すべて解除', async () => { clearClassification(); await load(); }, 'clear-filters'));
}
function clearClassification() {
  $('#kind').value = ''; $('#source').value = ''; $('#search').value = ''; $('#source-search').value = '';
  state.selectedKinds.clear(); state.selectedTopics.clear(); state.selectedTags.clear(); renderSourceChoices(); updateSelections();
}
async function initClassification(options) {
  state.topicOptions = options.topics; state.tagOptions = options.tags || []; renderManualTagChoices();
  for (const option of $('#kind').options) {
    const control = button(option.textContent, async () => {
      if (!option.value) state.selectedKinds.clear();
      else if (state.selectedKinds.has(option.value)) state.selectedKinds.delete(option.value);
      else state.selectedKinds.add(option.value);
      updateSelections(); await load();
    }, 'classification-choice');
    control.dataset.kind = option.value; $('#kind-choices').append(control);
  }
  for (const topic of state.topicOptions) {
    const control = button(topic, async () => { state.selectedTopics.has(topic) ? state.selectedTopics.delete(topic) : state.selectedTopics.add(topic); updateSelections(); await load(); }, 'topic');
    control.dataset.topic = topic; $('#topic-choices').append(control);
  }
  updateSelections();
}
const classificationMobile = matchMedia('(max-width: 900px)');
function placeClassification() {
  const wasOpen = $('#classification-dialog').open;
  if (wasOpen) $('#classification-dialog').close();
  (classificationMobile.matches ? $('#classification-mobile-slot') : $('#classification')).append($('#classification-controls'));
  if (wasOpen && !classificationMobile.matches) $('#kind-choices button[aria-pressed=true]')?.focus();
}
classificationMobile.addEventListener('change', placeClassification); placeClassification();

// Width is local to this browser. Responsive clamping never changes the saved preference.
const classificationLayout = $('.reading-layout');
const classificationHandle = $('#resize-classification');
const classificationWidthKey = 'xangi-feed-classification-width';
let classificationWidth = null, classificationPointer = null;
try {
  const saved = Number(localStorage.getItem(classificationWidthKey));
  if (Number.isFinite(saved) && saved > 0) classificationWidth = saved;
} catch {}
function layoutClassification() {
  if (classificationMobile.matches) { finishClassificationResize(true); return; }
  const max = Math.max(180, Math.min(480, classificationLayout.clientWidth - 56 - 28 - 360));
  const width = Math.round(Math.max(180, Math.min(classificationWidth ?? 240, max)));
  classificationLayout.style.setProperty('--classification-width', `${width}px`);
  for (const [key, value] of Object.entries({ min: 180, max, now: width, text: `${width}px` })) {
    classificationHandle.setAttribute(`aria-value${key}`, value);
  }
}
function setClassificationWidth(value) {
  classificationWidth = Math.max(Number(classificationHandle.getAttribute('aria-valuemin')), Math.min(value, Number(classificationHandle.getAttribute('aria-valuemax'))));
  layoutClassification();
}
function persistClassificationWidth() {
  try {
    if (classificationWidth === null) localStorage.removeItem(classificationWidthKey);
    else localStorage.setItem(classificationWidthKey, classificationWidth);
  } catch {}
}
function finishClassificationResize(cancelled) {
  if (!classificationPointer) return;
  const previous = classificationPointer;
  classificationPointer = null;
  if (cancelled) { classificationWidth = previous.saved; layoutClassification(); }
  else persistClassificationWidth();
  document.body.classList.remove('resizing-classification');
  if (classificationHandle.hasPointerCapture(previous.id)) classificationHandle.releasePointerCapture(previous.id);
}
classificationHandle.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !event.isPrimary || classificationMobile.matches || classificationPointer) return;
  layoutClassification();
  classificationPointer = { id: event.pointerId, x: event.clientX, width: Number(classificationHandle.getAttribute('aria-valuenow')), saved: classificationWidth };
  classificationHandle.setPointerCapture(event.pointerId);
  classificationHandle.focus({ preventScroll: true });
  document.body.classList.add('resizing-classification');
  event.preventDefault();
});
classificationHandle.addEventListener('pointermove', event => {
  if (classificationPointer?.id === event.pointerId) setClassificationWidth(classificationPointer.width + event.clientX - classificationPointer.x);
});
classificationHandle.addEventListener('pointerup', event => { if (classificationPointer?.id === event.pointerId) finishClassificationResize(false); });
classificationHandle.addEventListener('pointercancel', () => finishClassificationResize(true));
classificationHandle.addEventListener('lostpointercapture', () => finishClassificationResize(true));
classificationHandle.addEventListener('dblclick', () => { classificationWidth = null; persistClassificationWidth(); layoutClassification(); });
classificationHandle.addEventListener('keydown', event => {
  if (event.key === 'Escape' && classificationPointer) { event.preventDefault(); event.stopPropagation(); finishClassificationResize(true); return; }
  if (classificationMobile.matches || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  layoutClassification();
  setClassificationWidth(event.key === 'Home' ? Number(classificationHandle.getAttribute('aria-valuemin')) : event.key === 'End' ? Number(classificationHandle.getAttribute('aria-valuemax')) : Number(classificationHandle.getAttribute('aria-valuenow')) + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 40 : 10));
  persistClassificationWidth();
});
new ResizeObserver(layoutClassification).observe(classificationLayout);
window.addEventListener('resize', layoutClassification);
layoutClassification();

$('#classification-open').addEventListener('click', () => $('#classification-dialog').showModal());
for (const id of ['#classification-close', '#classification-done']) $(id).addEventListener('click', () => $('#classification-dialog').close());
$('#source-search').addEventListener('input', () => { renderSourceChoices(); saveClassification(); });
async function showSettings() {
  await loadSources();
  const adapterData = await api('api/source-adapters');
  $('#source-adapter-help').textContent = '';
  $('#source-form').elements.adapter.replaceChildren(...adapterData.adapters.map(a => new Option(a.name, a.id)));
  $('#source-form').elements.adapter.onchange = () => {
    const selected = adapterData.adapters.find(a => a.id === $('#source-form').elements.adapter.value);
    $('#source-adapter-help').textContent = selected.urls.length ? '対応URL: ' + selected.urls.join(' / ') : '';
    if (selected.urls.length) $('#source-form').elements.url.value = selected.urls[0];
  };
  const settings = await api('api/settings');
  const saveForm = $('#save-form');
  saveForm.elements.save_skill_name.value = settings.save_skill_name;
  saveForm.elements.save_agent_id.replaceChildren(new Option('xangiの既定', ''), ...settings.agents.map(agent => {
    const option = new Option(agent.name, agent.id); option.disabled = agent.available === false; return option;
  }));
  if (settings.save_agent_id && !settings.agents.some(agent => agent.id === settings.save_agent_id)) saveForm.elements.save_agent_id.append(new Option('現在の保存担当', settings.save_agent_id));
  saveForm.elements.save_agent_id.value = settings.save_agent_id;
  $('#save-message').textContent = settings.save_available ? '' : '保存AIへの接続が未設定です';
  $('#ios-link-settings').hidden = !isIOSStandalone();
  $('#ios-external-safari').checked = externalSafari;
  $('#preview-form').elements.show_link_previews.checked = settings.show_link_previews;
  $('#reading-form').elements.mark_read_on_open.checked = settings.mark_read_on_open;
  const form = $('#ai-form');
  form.elements.enabled.value = String(settings.enabled);
  form.elements.automatic_limit.value = settings.automatic_limit;
  form.elements.timeout_seconds.value = settings.timeout_seconds;
  settingsAgents = settings.agents;
  form.elements.agent_id.replaceChildren(new Option('選択してください', ''));
  for (const agent of settings.agents) {
    const option = new Option(agent.name + (agent.available === false ? '（利用不可）' : ''), agent.id);
    option.disabled = agent.available === false;
    form.elements.agent_id.append(option);
  }
  if (settings.agent_id && !settings.agents.some(a => a.id === settings.agent_id)) form.elements.agent_id.append(new Option('現在の担当', settings.agent_id));
  form.elements.agent_id.value = settings.agent_id;
  renderAgentModel();
  if (settings.agent_error) $('#ai-message').textContent = settings.agent_error;
  const profile = await api('api/profile');
  const stats = el('div', 'stats');
  for (const [label, value] of [['Good', profile.good], ['Bad', profile.bad], ['保存', profile.saved]]) { const block = el('p'); block.append(el('strong', '', value), document.createTextNode(` ${label}`)); stats.append(block); }
  const topics = el('div', 'profile-topics');
  for (const t of profile.topics) topics.append(el('span', 'topic', `${t.name} ${t.weight > 0 ? '+' : ''}${t.weight}`));
  if (!profile.topics.length) topics.append(el('p', 'muted', '評価が増えると、ここに話題ごとの好みが表示されます。'));
  $('#profile').replaceChildren(stats, topics);
  const sources = $('#sources'); sources.replaceChildren();
  for (const source of state.sources) {
    const row = el('div', 'source-row'), heading = el('div', 'source-heading');
    const toggle = button(source.enabled ? '取得を停止' : '取得を有効に', async () => { await api(`api/sources/${source.id}`, { enabled: !source.enabled }); await showSettings(); });
    const remove = button('削除', async () => {
      if (!confirm(`「${source.name}」の配信元登録を削除しますか？\n取得済みの記事・評価・メモ・会話は残ります。再開する場合は配信元を追加し直してください。`)) return;
      try {
        await api(`api/sources/${source.id}/delete`, { confirm: true });
        await showSettings(); await load();
        $('#source-message').textContent = '配信元を削除しました。取得済みの記事・評価・メモ・会話は残っています。';
        $('#source-message').focus();
      } catch (e) { $('#source-message').textContent = e.message; }
    });
    const controls = el('div', 'source-controls'); controls.append(toggle, remove);
    heading.append(el('strong', '', source.name), controls);
    row.append(heading, el('p', 'source-info', `${kinds[source.kind]} / ${source.enabled ? '有効' : '停止中'} / 最終成功 ${source.succeeded ? date(source.succeeded) : 'まだ取得していません'}`), link(source.adapter && source.adapter !== 'rss' ? 'ページ' : 'フィード', source.url));
    if (source.error) row.append(el('p', 'source-error', `取得失敗: ${source.error}`));
    sources.append(row);
  }
  if (!$('#settings').open) { $('#settings').showModal(); $('#settings').scrollTop = 0; }
}
async function poll() {
  try {
    if (state.tab === 'discovery') await loadDiscovery();
    const health = await api('api/health'); state.available = health.discussion_available;
    if (showLinkPreviews !== health.show_link_previews) {
      showLinkPreviews = health.show_link_previews;
      if (state.items.length) render();
    }
    const status = health.collector;
    $('#sync-status').textContent = status.running ? '配信元を取得中…' : status.last_finished ? `最終取得 ${date(status.last_finished)}` : '次の取得を待っています';
    $('#refresh').disabled = status.running;
    if (state.polled && status.last_finished !== state.lastFinished) {
      await loadSources();
      if (status.added) { $('#new-items').textContent = `新着${status.added}件を表示`; $('#new-items').hidden = false; }
      if (state.refreshPending || !state.items.length) { state.refreshPending = false; await load(); }
      const failed = state.sources.filter(s => s.enabled && s.error);
      if (failed.length) notice(`${failed.length}件の配信元で取得に失敗しました。「設定」で確認できます。`);
    }
    state.lastFinished = status.last_finished;
    state.polled = true;
    if (state.saveRevision !== undefined && health.save_revision !== state.saveRevision) {
      notionMessages.clear(); await load();
      if ($('#detail').open) await openDetail({id: $('#detail').dataset.id});
    }
    state.saveRevision = health.save_revision;
    const enrichment = health.enrichment;
    if (enrichment) {
      if (enrichment.running || enrichment.queued) $('#sync-status').textContent += ` / 日本語解説 ${enrichment.queued}件待ち`;
      if (state.analysisRevision !== undefined && enrichment.revision !== state.analysisRevision) {
        $('#new-items').textContent = '日本語の要点・解説を反映'; $('#new-items').hidden = false;
        if (enrichment.error) notice(enrichment.error);
      }
      state.analysisRevision = enrichment.revision;
    }
  } catch (e) { $('#sync-status').textContent = '接続を確認してください'; notice(e.message); }
}
async function changeTab(tab) {
  saveClassification();
  state.tab = tab;
  restoreClassification();
  restoreSort();
  $('#recommendation-panel').hidden = tab !== 'recommended';
  clearTimeout(searchTimer);
  document.querySelectorAll('[data-tab]').forEach(t => t.setAttribute('aria-pressed', t.dataset.tab === tab));
  await load();
}
document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => changeTab(b.dataset.tab)));
$('#kind').addEventListener('change', () => { state.selectedKinds.clear(); if ($('#kind').value) state.selectedKinds.add($('#kind').value); updateSelections(); load(); }); $('#source').addEventListener('change', () => load());
$('#sort').addEventListener('change', () => { try { localStorage.setItem(sortStorageKey(), $('#sort').value); } catch {} load(); });
let searchTimer; $('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => load(), 300); });
$('#more').addEventListener('click', () => load(true)); $('#new-items').addEventListener('click', () => load());
$('#refresh').addEventListener('click', async () => { try { state.refreshPending = true; await api('api/refresh', {}); await poll(); } catch (e) { notice(e.message); } });
$('#settings-open').addEventListener('click', () => showSettings().catch(e => notice(e.message)));
$('#detail-close').addEventListener('click', () => $('#detail').close()); $('#settings-close').addEventListener('click', () => $('#settings').close());
$('#reset').addEventListener('click', async () => { if (!confirm('Good／Badと好みの学習をリセットします。保存記事と議論は残ります。')) return; try { await api('api/profile/reset', { confirm: true }); await showSettings(); await load(); } catch (e) { notice(e.message); } });
$('#source-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try { await api('api/sources', Object.fromEntries(new FormData(form))); form.reset(); $('#source-error').textContent = ''; await showSettings(); notice('配信元を追加しました。「今すぐ取得」で読み込めます。'); }
  catch (e) { $('#source-error').textContent = e.message; } finally { submit.disabled = false; }
});
$('#ai-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, b = form.querySelector('[type=submit]'); b.disabled = true;
  try {
    await api('api/settings', { enabled: form.elements.enabled.value === 'true', agent_id: form.elements.agent_id.value, automatic_limit: Number(form.elements.automatic_limit.value), timeout_seconds: Number(form.elements.timeout_seconds.value) });
    $('#ai-message').textContent = '保存しました。エージェント・待ち時間は次の生成から反映します。既存の解説は記事の詳細にある「AI解説を作り直す」で更新できます。';
  } catch (e) { $('#ai-message').textContent = e.message; } finally { b.disabled = false; }
});
$('#save-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, submit = form.querySelector('button'); submit.disabled = true;
  try {
    await api('api/settings', {save_skill_name: form.elements.save_skill_name.value.trim(), save_agent_id: form.elements.save_agent_id.value});
    $('#save-message').textContent = '保存担当とスキルを設定しました';
  } catch (error) { $('#save-message').textContent = error.message; } finally { submit.disabled = false; }
});
window.addEventListener('beforeunload', event => { if (noteDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
function renderAgentModel() {
  const form = $('#ai-form');
  const agent = settingsAgents.find(a => a.id === form.elements.agent_id.value);
  $('#agent-model').textContent = agent ? [agent.name, agent.backend, agent.model].filter(Boolean).join(' / ') : '利用するエージェントを選択してください。';
  form.elements.agent_id.required = form.elements.enabled.value === 'true';
}
$('#ai-form [name=agent_id]').addEventListener('change', renderAgentModel);
$('#ai-form [name=enabled]').addEventListener('change', renderAgentModel);
$('#preview-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try {
    const enabled = form.elements.show_link_previews.checked;
    await api('api/settings', {show_link_previews: enabled});
    showLinkPreviews = enabled; render();
    $('#preview-message').textContent = 'リンクカードの設定を保存しました。';
  } catch (e) { $('#preview-message').textContent = e.message; } finally { submit.disabled = false; }
});
$('#reading-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try {
    await api('api/settings', { mark_read_on_open: form.elements.mark_read_on_open.checked });
    $('#reading-message').textContent = '読了の設定を保存しました。';
  } catch (e) { $('#reading-message').textContent = e.message; } finally { submit.disabled = false; }
});
let recommendationSave = Promise.resolve(), recommendationVersion = 0, recommendationTimer;
function saveRecommendations() {
  clearTimeout(recommendationTimer);
  const form = $('#recommendation-form');
  if ($('#recommendation-fields').disabled) return;
  const version = ++recommendationVersion;
  if (!form.checkValidity()) {
    $('#recommendation-message').textContent = '表示期間は0〜365日、好みの下限は-100〜100の整数で指定してください。まだ保存していません。';
    return;
  }
  const data = {};
  data.recommendation_days = Number(form.elements.recommendation_days.value);
  data.recommendation_min_preference = form.elements.recommendation_min_preference.value === '' ? null : Number(form.elements.recommendation_min_preference.value);
  for (const key of ['recommendation_hide_bad', 'recommendation_mix_types']) data[key] = form.elements[key].checked;
  data.recommendation_priority = form.elements.recommendation_priority.value;
  $('#recommendation-message').textContent = '保存して反映しています…';
  $('#recommendation-retry').hidden = true;
  recommendationSave = recommendationSave.then(async () => {
    if (version !== recommendationVersion) return;
    try {
      await api('api/settings', data);
      if (version !== recommendationVersion) return;
      $('#recommendation-message').textContent = '保存して反映しました。';
      if (state.tab === 'recommended') await load();
    } catch (e) {
      if (version !== recommendationVersion) return;
      $('#recommendation-message').textContent = '保存できませんでした。条件はまだ反映されていません。' + e.message;
      $('#recommendation-retry').hidden = false;
      if (state.tab !== 'recommended') notice('カスタム条件を保存できませんでした。カスタム一覧でもう一度試してください。');
    }
  });
}
$('#recommendation-form').addEventListener('change', saveRecommendations);
$('#recommendation-form').addEventListener('input', event => {
  if (event.isComposing) { clearTimeout(recommendationTimer); return; }
  if (event.target.type === 'number') { clearTimeout(recommendationTimer); recommendationTimer = setTimeout(saveRecommendations, 300); }
});
$('#recommendation-form').addEventListener('compositionend', () => { clearTimeout(recommendationTimer); recommendationTimer = setTimeout(saveRecommendations, 300); });
$('#recommendation-form').addEventListener('submit', event => { event.preventDefault(); saveRecommendations(); });
$('#recommendation-retry').addEventListener('click', () => { if ($('#recommendation-fields').disabled) void loadRecommendations(); else saveRecommendations(); });

(async () => {
  try {
    // Fetch settings early, but enable their controls only after restoring filters.
    const recommendations = api('api/recommendations');
    const [, , options] = await Promise.all([
      poll(), loadSources(), api('api/filter-options'), recommendations.catch(() => {}),
    ]);
    await initClassification(options);
    classificationReady = true;
    restoreClassification();
    await loadRecommendations(recommendations);
    await load();
  } catch (e) { notice(e.message); }
  setInterval(poll, 5000);
})();

async function waitForArticle(item) {
  while (item.fetch_state === 'fetching') {
    await new Promise(resolve => setTimeout(resolve, 1000));
    item = await api(`api/items/${item.id}`);
  }
  return item;
}
$('#article-open').addEventListener('click', () => { $('#article-error').textContent = ''; $('#article-add').showModal(); });
$('#article-close').addEventListener('click', () => $('#article-add').close());
$('#article-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.target, submit = form.querySelector('[type=submit]'); submit.disabled = true; const previousLabel = submit.textContent; submit.textContent = '本文を取得中…';
  try {
    const result = await api('api/items', {...Object.fromEntries(new FormData(form)), background: true});
    result.item = await waitForArticle(result.item);
    form.reset(); $('#article-add').close();
    await load();
    await openDetail(result.item);
    notice(result.item.fetch_error ? '記事は登録しましたが本文を取得できませんでした。詳細から再取得できます。' : result.created ? '記事を追加しました。' : '登録済みの記事を開きました。');
  } catch (e) { $('#article-error').textContent = e.message; } finally { submit.disabled = false; submit.textContent = previousLabel; }
});

// Discovery has its own list; registered-feed filters never hide its candidates.
let discoveryLoaded = false, discoverySaved = null, discoveryVersion = 0, discoveryTimer, discoveryComposing = false;
let discoverySave = Promise.resolve(), discoveryRetryRefresh = false;
async function loadDiscovery() {
  try {
    const result = await api('api/discovery');
    if (state.tab !== 'discovery') return;
    const form = $('#discovery-form');
    if (!discoveryLoaded) {
      form.elements.discovery_enabled.checked = result.enabled;
      form.elements.discovery_interval_hours.value = result.interval_hours;
      form.elements.discovery_prompt.value = result.prompt;
      discoveryLoaded = true;
      discoverySaved = JSON.stringify(discoveryValues());
      $('#discovery-fields').disabled = false;
      $('#discovery-save-message').textContent = '入力すると自動保存して検索します。';
    }
    $('#discovery-message').textContent = result.running ? '検索しています。入力を変更した場合は、最後の条件で検索し直します。' : result.error || `${result.updated ? '最終更新 ' + date(result.updated) + '。' : '探索済みの候補はまだありません。'}${result.enabled ? '次回: ' + (result.next_run ? date(result.next_run) : '起動後に探索します') : '定期探索は停止中です。'}`;
    if (result.diagnostics) $('#discovery-message').textContent += `（検索成功${result.diagnostics.searches_ok ?? 0}件・サイト確認${result.diagnostics.sites_checked ?? 0}件・フィード確認${result.diagnostics.feeds_checked ?? 0}件）`;
    if (result.plan?.notice && !result.running) $('#discovery-message').textContent += ' ' + result.plan.notice;
    if (result.plan && !result.running) $('#discovery-message').textContent += ` 検索条件: ${result.plan.language === 'ja' ? '日本語のみ・' : ''}直近${result.plan.max_age_days}日 / ${result.plan.queries.join('、')}${result.plan.exclude_terms.length ? ' / 除外: ' + result.plan.exclude_terms.join('、') : ''}`;
    const box = $('#discovery-results');
    const active = document.activeElement;
    const focused = box.contains(active) ? active.dataset.candidate : null;
    box.replaceChildren();
    for (const item of result.items) {
      const card = el('article', 'news discovery-card');
      const title = link(item.title, item.url);
      title.className = 'news-title'; title.dataset.candidate = item.id;
      configureArticleLink(title, item.url);
      const heading = el('h3'); heading.append(title);
      card.append(el('p', 'publisher-label', item.source_name), heading, el('p', 'muted', '公開 ' + date(item.published)), el('p', '', item.reason), el('p', 'muted', '関心: ' + item.matched_topics.join('、')));
      box.append(card);
    }
    if (!result.items.length) box.append(el('p', 'muted', '条件に合う記事はまだありません。Goodで学習した話題と検索条件で、最近の記事を探します。候補がない場合や失敗した場合は15分後に再試行します。'));
    if (focused && document.activeElement === document.body) [...box.querySelectorAll('[data-candidate]')].find(b => b.dataset.candidate === focused)?.focus({preventScroll:true});
  } catch (error) { $('#discovery-message').textContent = 'おすすめを読み込めませんでした。' + error.message; if (!discoveryLoaded) $('#discovery-retry').hidden = false; }
}
function discoveryValues() {
  const form = $('#discovery-form');
  return {discovery_enabled: form.elements.discovery_enabled.checked,
    discovery_interval_hours: Number(form.elements.discovery_interval_hours.value),
    discovery_prompt: form.elements.discovery_prompt.value};
}
function scheduleDiscoverySave(delay = 650) {
  clearTimeout(discoveryTimer);
  if (!discoveryLoaded || discoveryComposing) return;
  const version = ++discoveryVersion;
  $('#discovery-save-message').textContent = '入力中…';
  $('#discovery-retry').hidden = true;
  discoveryTimer = setTimeout(() => saveDiscovery(version), delay);
}
function saveDiscovery(version, refresh = false) {
  const form = $('#discovery-form');
  if (version !== discoveryVersion || discoveryComposing) return;
  if (!form.checkValidity()) {
    $('#discovery-save-message').textContent = '探索間隔は6〜168時間、検索指示は2000文字以内です。まだ保存していません。';
    return;
  }
  const data = discoveryValues(), encoded = JSON.stringify(data);
  discoverySave = discoverySave.then(async () => {
    if (version !== discoveryVersion) return;
    if (encoded === discoverySaved && !refresh) {
      $('#discovery-save-message').textContent = '自動保存済みです。';
      return;
    }
    $('#discovery-save-message').textContent = '保存して反映しています…';
    try {
      const promptChanged = !discoverySaved || JSON.parse(discoverySaved).discovery_prompt !== data.discovery_prompt;
      if (encoded !== discoverySaved) {
        await api('api/settings', data);
        discoverySaved = encoded;
      }
      // Saving a changed prompt already starts a search on the server.
      if (refresh && !promptChanged) await api('api/discovery/refresh', {});
      if (version !== discoveryVersion) return;
      discoveryRetryRefresh = false;
      $('#discovery-retry').hidden = true;
      $('#discovery-save-message').textContent = refresh ? '検索をリクエストしました。' : '自動保存して反映しました。';
      if (state.tab === 'discovery') await loadDiscovery();
    } catch (error) {
      if (version !== discoveryVersion) return;
      discoveryRetryRefresh = refresh;
      $('#discovery-save-message').textContent = (refresh ? '検索できませんでした。入力内容は保持しています。' : '保存できませんでした。入力はまだ反映されていません。') + error.message;
      $('#discovery-retry').textContent = refresh ? '検索を再試行' : '保存を再試行';
      $('#discovery-retry').hidden = false;
      if (state.tab !== 'discovery') notice('おすすめの検索指示を保存できませんでした。おすすめで再試行してください。');
    }
  });
  return discoverySave;
}
$('#discovery-form').addEventListener('input', event => { if (!event.isComposing) scheduleDiscoverySave(); });
$('#discovery-form').addEventListener('change', () => scheduleDiscoverySave(0));
$('#discovery-form').addEventListener('compositionstart', () => {
  discoveryComposing = true; clearTimeout(discoveryTimer); ++discoveryVersion;
  $('#discovery-save-message').textContent = '入力中…';
});
$('#discovery-form').addEventListener('compositionend', () => { discoveryComposing = false; scheduleDiscoverySave(); });
$('#discovery-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#discovery-refresh');
  if (!discoveryLoaded || discoveryComposing || button.disabled) return;
  clearTimeout(discoveryTimer);
  const version = ++discoveryVersion;
  button.disabled = true;
  try { await saveDiscovery(version, true); } finally { button.disabled = false; }
});
$('#discovery-retry').addEventListener('click', () => {
  if (!discoveryLoaded) void loadDiscovery();
  else if (discoveryRetryRefresh) $('#discovery-form').requestSubmit();
  else scheduleDiscoverySave(0);
});
