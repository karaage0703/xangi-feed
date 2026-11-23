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
window.addEventListener('storage', event => { if (event.key === 'xangi-theme' || event.key === null) { try { applyTheme(localStorage.getItem('xangi-theme') || 'system'); } catch { applyTheme('system'); } } });
const state = { tab: 'recommended', items: [], total: 0, sources: [], selectedKinds: new Set(), selectedTopics: new Set(), selectedTags: new Set(), topicOptions: [], tagOptions: [], available: false, loading: false, lastFinished: null, polled: false, refreshPending: false };
let settingsAgents = [];
function sortStorageKey() {
  return state.tab === 'read' ? 'xangi-feed-sort-read' : state.tab === 'manual' ? 'xangi-feed-sort-manual' : 'xangi-feed-sort';
}
function restoreSort() {
  for (const option of $('#sort').options) option.hidden = option.disabled = false;
  $('#sort').value = 'updated';
}
restoreSort();
const listDisplay = $('#list-display');
const listDisplayKey = 'xangi-feed-list-display';
try {
  const saved = localStorage.getItem(listDisplayKey);
  if ([...listDisplay.options].some(option => option.value === saved)) listDisplay.value = saved;
} catch {}
listDisplay.addEventListener('change', () => {
  try { localStorage.setItem(listDisplayKey, listDisplay.value); } catch {}
  render();
});
let detailItem = null;
const detailDialog = $('#detail');
const readerToggle = $('#reader-toggle');
const readerMedia = matchMedia('(min-width: 1200px)');
const readerPreferenceKey = 'xangi-feed-desktop-reader';
let readerPreference = false;
try { readerPreference = localStorage.getItem(readerPreferenceKey) === 'on'; } catch {}
const readerEnabled = () => readerMedia.matches && readerPreference;
let discussionItem = null, discussionRequestId = null, discussionBusy = false, discussionReady = false;
const noteDrafts = new Map();
const notionBusy = new Set(), notionMessages = new Map();
let notionQueue = Promise.resolve();
const saveRequestIds = new Map();
const embedded = /^\/api\/extensions\/[^/]+\/(?:ui|service)(?:\/|$)/.test(location.pathname);
const base = new URL(document.baseURI);
base.pathname = base.pathname.replace(/\/?$/, '/'); base.search = ''; base.hash = '';
if (embedded && window.self === window.top) $('#home').hidden = false;
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
function listTitleLink(item, label) {
  const control = articleLink(item, label);
  control.className = 'news-title';
  if (readerEnabled()) control.title = '右ペインで読む';
  control.addEventListener('click', event => {
    if (!readerEnabled() || event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    showArticleDetail(item);
    if (detailDialog.open && detailDialog.dataset.id === item.id) control.focus({preventScroll: true});
  }, {capture: true});
  return control;
}
async function recordArticleOpen(item) {
  const version = loadVersion, view = activeCustomFilter;
  try {
    const result = await api(`api/items/${item.id}/open`, {}, true);
    if (!result.marked_read) return;
    applyItem(item, result.item);
    if (version !== loadVersion || view !== activeCustomFilter) return;
    if (activeCustomFilter) { retainedForReading.add(item.id); patchReaction(item); }
    else if (['recommended', 'discovery'].includes(state.tab)) {
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
let customFilters = [], activeCustomFilter = '', customFilterBusy = false;
let sidebarSource = null;
const viewRssMenus = new Map();

function currentView() { return customFilters.find(item => item.id === activeCustomFilter); }
function isDiscoveryView() { return state.tab === 'recommended' && currentView()?.conditions.origin === 'discovery'; }
function readingTitle() {
  const view = currentView()?.name || '一覧';
  if (!sidebarSource) return view;
  return state.tab === 'recommended' ? view + ' / ' + sidebarSource.name : sidebarSource.name;
}
function viewConditions() {
  const form = $('#view-state-form');
  return Object.fromEntries(['read', 'muted', 'saved', 'noted', 'discussed', 'origin'].map(key => [key, form.elements[key].value]).concat([
    ['max_preference', form.elements.max_preference.value === '' ? null : Number(form.elements.max_preference.value)],
  ]));
}
let defaultCustomClassification = null, customClassificationReady = false;
let classificationSave = Promise.resolve(true);
function customClassificationValues(value = null) {
  return value ? {kinds: value.kinds, topics: value.topics, tags: value.tags, source: value.source, search: value.search, sort: value.sort, conditions: value.conditions, recommendations: Object.fromEntries(['recommendation_min_preference', 'recommendation_days', 'recommendation_priority', 'recommendation_hide_bad', 'recommendation_mix_types'].map(key => [key, value.recommendations[key]]))} : {
    kinds: [...state.selectedKinds], topics: [...state.selectedTopics], tags: [...state.selectedTags],
    source: $('#source').value, search: $('#saved-search').value, sort: $('#sort').value, conditions: viewConditions(), recommendations: recommendationData(),
  };
}
function setViewSaveMessage(message) {
  for (const id of ['classification-save-message', 'settings-save-message', 'filter-save-message']) $('#' + id).textContent = message;
}
function setViewSaveRetry(visible) {
  for (const id of ['classification-save-retry', 'settings-save-retry', 'filter-save-retry']) $('#' + id).hidden = !visible;
}
function persistCustomClassification() {
  if (!activeCustomFilter || !customClassificationReady || !classificationReady || state.tab !== 'recommended' || $('#recommendation-fields').disabled) return classificationSave;
  if (!$('#view-state-form').checkValidity() || !$('#recommendation-form').checkValidity()) {
    setViewSaveMessage('数値の範囲を確認してください。まだ保存していません。'); return Promise.resolve(false);
  }
  const id = activeCustomFilter, values = customClassificationValues();
  classificationSave = classificationSave.then(async () => {
    const record = id ? customFilters.find(item => item.id === id) : defaultCustomClassification;
    if (record && JSON.stringify(customClassificationValues(record)) === JSON.stringify(values)) {
      if (!$('#classification-save-retry').hidden) { setViewSaveRetry(false); setViewSaveMessage('フィルターは保存済みです。'); notice(''); }
      return true;
    }
    setViewSaveMessage('フィルターをサーバに保存中…');
    try {
      const result = await api('api/views/save', {id, ...values});
      if (id) {
        customFilters = customFilters.map(item => item.id === id ? result.filter : item);
        const menu = viewRssMenus.get(id);
        if (menu && !menu.hidden) void loadViewRss(id, menu);
      }
      else defaultCustomClassification = result.classification;
      setViewSaveMessage('フィルターをサーバに保存しました。');
      if (!$('#classification-save-retry').hidden) notice('');
      setViewSaveRetry(false);
      return true;
    } catch (error) {
      setViewSaveMessage('フィルターを保存できませんでした。画面の条件は残っています。' + error.message);
      setViewSaveRetry(true);
      notice('フィルターを保存できませんでした。再試行してからタブを切り替えてください。');
      return false;
    }
  });
  return classificationSave;
}
for (const id of ['classification-save-retry', 'settings-save-retry', 'filter-save-retry']) $('#' + id).addEventListener('click', async () => { if (await persistCustomClassification()) await load(); });
function recommendationData() {
  const form = $('#recommendation-form');
  return {
    recommendation_min_preference: form.elements.recommendation_min_preference.value === '' ? null : Number(form.elements.recommendation_min_preference.value),
    recommendation_days: Number(form.elements.recommendation_days.value),
    recommendation_priority: form.elements.recommendation_priority.value,
    recommendation_hide_bad: form.elements.recommendation_hide_bad.checked,
    recommendation_mix_types: form.elements.recommendation_mix_types.checked,
  };
}
let draggedCustomFilter = '', pointerViewDrag = null;
function clearViewDrag() {
  for (const row of $('#custom-filter-tabs').querySelectorAll('.view-row')) { row.classList.remove('is-dragging'); delete row.dataset.dropPosition; }
}
function markViewDrop(row, y) {
  clearViewDrag();
  if (!row || row.dataset.viewId === draggedCustomFilter) return null;
  const position = y < row.getBoundingClientRect().top + row.offsetHeight / 2 ? 'before' : 'after';
  row.dataset.dropPosition = position;
  $('#custom-filter-tabs').querySelector(`[data-view-id="${draggedCustomFilter}"]`)?.classList.add('is-dragging');
  return {id: row.dataset.viewId, position};
}
function dropView(id, target) {
  if (!id || !target || target.id === id) return;
  const ids = customFilters.map(item => item.id).filter(value => value !== id);
  const index = ids.indexOf(target.id);
  if (index < 0) return;
  ids.splice(index + (target.position === 'after' ? 1 : 0), 0, id);
  if (ids.some((value, position) => value !== customFilters[position].id)) void reorderCustomFilters(ids);
}
function startViewPointer(event, id) {
  if (event.button !== 0 || customFilterBusy) return;
  event.preventDefault();
  const handle = event.currentTarget;
  handle.setPointerCapture(event.pointerId);
  const drag = pointerViewDrag = {id, x: event.clientX, y: event.clientY, active: false, target: null};
  const move = current => {
    if (Math.hypot(current.clientX - drag.x, current.clientY - drag.y) < 5 && !drag.active) return;
    drag.active = true; draggedCustomFilter = id;
    drag.target = markViewDrop(document.elementFromPoint(current.clientX, current.clientY)?.closest('.view-row'), current.clientY);
  };
  const finish = current => {
    handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', finish); handle.removeEventListener('pointercancel', finish);
    if (handle.hasPointerCapture(current.pointerId)) handle.releasePointerCapture(current.pointerId);
    pointerViewDrag = null; draggedCustomFilter = ''; clearViewDrag();
    if (current.type === 'pointerup' && drag.active) dropView(id, drag.target);
  };
  handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish);
}
function renderCustomFilters() {
  const disabled = customFilterBusy || $('#recommendation-fields').disabled;
  const classification = $('#custom-filter-tabs');
  const focused = document.activeElement;
  const currentFocus = focused?.dataset.customFilter;
  const focusedToggle = focused?.dataset.viewRssToggle;
  const hasRssSources = state.sources.some(source => source.adapter === 'rss');
  const tabs = customFilters.map(item => {
    const tab = button(item.name, () => selectCustomFilter(item.id));
    tab.dataset.customFilter = item.id; tab.title = item.name;
    if (item.id.startsWith('preset-')) tab.dataset.tab = item.id.slice(7);
    tab.setAttribute('aria-pressed', state.tab === 'recommended' && activeCustomFilter === item.id && !sidebarSource);
    tab.disabled = disabled; tab.draggable = !disabled;
    const more = button('…', () => openViewSettings(item.id));
    more.className = 'view-settings-button'; more.disabled = disabled; more.setAttribute('aria-label', item.name + 'の設定'); more.title = item.name + 'の設定';
    const row = el('div', 'view-row'); row.dataset.viewId = item.id;
    const handle = button('⠿', () => {}, 'view-drag-handle'); handle.disabled = disabled;
    handle.setAttribute('aria-label', item.name + 'を並べ替え'); handle.title = 'ドラッグで並べ替え（Alt+↑/↓でも移動できます）';
    handle.addEventListener('pointerdown', event => startViewPointer(event, item.id));
    handle.addEventListener('keydown', event => {
      if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const ids = customFilters.map(view => view.id), index = ids.indexOf(item.id), target = index + (event.key === 'ArrowUp' ? -1 : 1);
      if (target < 0 || target >= ids.length) return;
      [ids[index], ids[target]] = [ids[target], ids[index]]; void reorderCustomFilters(ids);
    });
    row.addEventListener('dragstart', event => { draggedCustomFilter = item.id; row.classList.add('is-dragging'); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', item.id); });
    row.addEventListener('dragend', () => { draggedCustomFilter = ''; clearViewDrag(); });
    row.addEventListener('dragover', event => { if (draggedCustomFilter && !customFilterBusy) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; markViewDrop(row, event.clientY); } });
    row.addEventListener('drop', event => { event.preventDefault(); const id = draggedCustomFilter, target = markViewDrop(row, event.clientY); draggedCustomFilter = ''; clearViewDrag(); dropView(id, target); });
    row.append(handle, tab);
    const group = el('div', 'view-group');
    group.append(row);
    if (hasRssSources) {
      const menu = viewRssMenus.get(item.id) || el('div', 'rss-links view-rss-links');
      if (!viewRssMenus.has(item.id)) {
        menu.id = 'view-rss-' + item.id;
        menu.hidden = true;
        viewRssMenus.set(item.id, menu);
      }
      const toggle = el('button', 'view-rss-toggle');
      toggle.type = 'button'; toggle.dataset.viewRssToggle = item.id;
      toggle.setAttribute('aria-label', item.name + 'のRSS配信元');
      toggle.setAttribute('aria-controls', menu.id);
      toggle.setAttribute('aria-expanded', String(!menu.hidden));
      toggle.addEventListener('click', () => {
        menu.hidden = !menu.hidden;
        toggle.setAttribute('aria-expanded', String(!menu.hidden));
        if (!menu.hidden) void loadViewRss(item.id, menu);
      });
      row.append(toggle);
      group.append(menu);
    }
    row.append(more);
    return group;
  });
  for (const id of viewRssMenus.keys()) {
    if (!customFilters.some(view => view.id === id)) viewRssMenus.delete(id);
  }
  classification.replaceChildren(...tabs);
  updateRssSelection();
  if (focusedToggle) classification.querySelector(`[data-view-rss-toggle="${focusedToggle}"]`)?.focus({preventScroll: true});
  if (focused?.dataset.rssSource && classification.contains(focused)) focused.focus({preventScroll: true});
  if (currentFocus) $('#custom-filter-tabs').querySelector(`[data-custom-filter="${currentFocus}"]`)?.focus({preventScroll: true});
  $('#current-view-title').textContent = readingTitle();
  $('#view-settings-title').textContent = (currentView()?.name || '一覧') + 'の設定';
  $('#custom-filter-plus').disabled = disabled;
  $('#custom-filter-controls').disabled = disabled;
  $('#custom-filter-update').disabled = $('#custom-filter-delete').disabled = !activeCustomFilter;
  const index = customFilters.findIndex(item => item.id === activeCustomFilter);
  $('#custom-filter-left').disabled = index <= 0;
  $('#custom-filter-right').disabled = index < 0 || index === customFilters.length - 1;
}
async function reorderCustomFilters(ids) {
  const focusedRow = document.activeElement?.closest('.view-row')?.dataset.viewId;
  const focusedHandle = document.activeElement?.classList.contains('view-drag-handle');
  if (customFilterBusy || !await persistCustomClassification()) return;
  customFilterBusy = true; renderCustomFilters();
  try {
    customFilters = (await api('api/custom-filters/reorder', {ids})).filters;
    $('#view-order-message').textContent = '一覧の並び順をサーバに保存しました。';
  } catch (error) {
    $('#custom-filter-message').textContent = '並べ替えを保存できませんでした。' + error.message;
    notice($('#custom-filter-message').textContent);
    $('#custom-filter-retry').hidden = false;
  } finally {
    customFilterBusy = false; renderCustomFilters();
    if (focusedRow) $('#custom-filter-tabs').querySelector(`[data-view-id="${focusedRow}"] ${focusedHandle ? '.view-drag-handle' : '[data-custom-filter]'}`)?.focus({preventScroll: true});
  }
}
function moveCustomFilter(direction) {
  const ids = customFilters.map(item => item.id), index = ids.indexOf(activeCustomFilter);
  if (index < 0 || index + direction < 0 || index + direction >= ids.length) return;
  [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
  void reorderCustomFilters(ids);
}
$('#custom-filter-left').addEventListener('click', () => moveCustomFilter(-1));
$('#custom-filter-right').addEventListener('click', () => moveCustomFilter(1));
async function loadCustomFilters() {
  try {
    const result = await api('api/views');
    customFilters = result.filters;
    defaultCustomClassification = result.default_classification;
    customClassificationReady = true;
    if (!customFilters.some(item => item.id === activeCustomFilter)) activeCustomFilter = customFilters[0]?.id || '';
    renderCustomFilters(); $('#custom-filter-retry').hidden = true;
  } catch (error) {
    $('#custom-filter-message').textContent = '項目を読み込めませんでした。' + error.message;
    $('#custom-filter-retry').hidden = false;
  }
}
function applyCustomClassification(item, clearSourceSearch = true) {
  state.selectedKinds = new Set(item.kinds.filter(kind => Object.hasOwn(kinds, kind)));
  state.selectedTopics = new Set(item.topics.filter(topic => state.topicOptions.includes(topic)));
  state.selectedTags = new Set(item.tags.filter(tag => state.tagOptions.some(option => option.name === tag)));
  $('#kind').value = item.kinds.length === 1 ? item.kinds[0] : '';
  $('#saved-search').value = item.search; if (clearSourceSearch) $('#source-search').value = '';
  renderSourceChoices(item.source); updateSelections();
  $('#sort').value = item.sort;
}
function applyCustomFilter(item) {
  renderRecommendations(item.recommendations);
  applyCustomClassification(item);
  $('#custom-filter-name').value = item.name;
  const form = $('#view-state-form');
  for (const key of ['read', 'muted', 'saved', 'noted', 'discussed', 'origin']) form.elements[key].value = item.conditions[key];
  form.elements.max_preference.value = item.conditions.max_preference ?? '';
  $('#view-state-fields').disabled = false;
}
async function writeCustomFilter(update) {
  if (customFilterBusy) return;
  if (!$('#custom-filter-name').value.trim()) {
    $('#custom-filter-message').textContent = '一覧名を入力してください。'; $('#custom-filter-name').focus(); return;
  }
  if (update) {
    customFilterBusy = true; renderCustomFilters();
    try {
      const saved = (await api('api/views/rename', {id: activeCustomFilter, name: $('#custom-filter-name').value})).filter;
      customFilters = customFilters.map(item => item.id === saved.id ? saved : item);
      $('#settings-save-message').textContent = '名前を保存しました。';
      return true;
    } catch (error) { $('#settings-save-message').textContent = '名前を保存できませんでした。' + error.message; return false; }
    finally { customFilterBusy = false; renderCustomFilters(); }
  }
  if (!$('#recommendation-form').reportValidity() || !await persistCustomClassification()) return false;
  customFilterBusy = true; renderCustomFilters();
  clearTimeout(recommendationTimer); await recommendationSave;
  try {
    const data = {name: $('#custom-filter-name').value, recommendations: recommendationData(), kinds: [...state.selectedKinds], topics: [...state.selectedTopics], tags: [...state.selectedTags], source: $('#source').value, search: $('#saved-search').value, sort: $('#sort').value, conditions: viewConditions()};
    if (update) data.id = activeCustomFilter;
    const saved = await api('api/custom-filters', data);
    activeCustomFilter = saved.id; sidebarSource = null;
    if (!update) { $('#search').value = ''; $('#search-clear').hidden = true; }
    customFilters = customFilters.some(item => item.id === saved.id)
      ? customFilters.map(item => item.id === saved.id ? saved : item)
      : [...customFilters, saved];
    await loadCustomFilters();
    $('#custom-filter-message').textContent = 'サーバに保存しました。別PCでもこの項目を選べます。';
    $('#recommendation-message').textContent = '';
    await load();
    return true;
  } catch (error) { $('#custom-filter-message').textContent = '保存できませんでした。' + error.message; return false; }
  finally { customFilterBusy = false; renderCustomFilters(); }
}
$('#custom-filter-plus').addEventListener('click', async () => {
  if (state.tab !== 'recommended') await changeTab('recommended');
  $('#custom-filter-new-name').value = ''; $('#custom-filter-new-message').textContent = '';
  $('#custom-filter-new').showModal(); $('#custom-filter-new-name').focus();
});
$('#custom-filter-new-cancel').addEventListener('click', () => $('#custom-filter-new').close());
$('#custom-filter-new-form').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('#custom-filter-new-name').value.trim();
  if (!name) { $('#custom-filter-new-message').textContent = '一覧名を入力してください。'; return; }
  const submit = event.target.querySelector('[type=submit]'); submit.disabled = true;
  const previousName = $('#custom-filter-name').value;
  $('#custom-filter-name').value = name;
  try {
    if (await writeCustomFilter(false)) {
      $('#custom-filter-new').close();
      if ($('#view-settings').open) $('#view-settings').close();
      if (classificationMobile.matches && $('#classification-dialog').open) $('#classification-dialog').close();
      $('#custom-filter-tabs').querySelector(`[data-custom-filter="${activeCustomFilter}"]`)?.focus();
    } else {
      $('#custom-filter-new-message').textContent = $('#custom-filter-message').textContent || '条件を確認して再試行してください。';
      $('#custom-filter-name').value = previousName;
    }
  } finally { submit.disabled = false; }
});
async function openViewSettings(id) {
  if (!id) return;
  if (id !== activeCustomFilter || state.tab !== 'recommended') await selectCustomFilter(id);
  if (id !== activeCustomFilter || state.tab !== 'recommended') return;
  if ($('#classification-dialog').open) $('#classification-dialog').close();
  $('#view-meta-group').open = true;
  if (!$('#view-settings').open) $('#view-settings').showModal();
}
function openFilters(section = 'types') {
  if (!activeCustomFilter || state.tab !== 'recommended') return;
  if ($('#classification-dialog').open) $('#classification-dialog').close();
  $('#filter-toggle').open = true;
  $('#view-state-group').open = section === 'conditions';
  $('#view-classification-group').open = section === 'classification';
  $('#recommendation-toggle').open = section === 'preference';
  if (!$('#filter-dialog').open) $('#filter-dialog').showModal();
}
$('#view-filter-open').addEventListener('click', () => openFilters());
$('#filter-dialog-close').addEventListener('click', async () => { if (await persistCustomClassification()) $('#filter-dialog').close(); });
$('#filter-dialog').addEventListener('close', () => $('#view-filter-open').focus({preventScroll: true}));
$('#view-settings-close').addEventListener('click', () => $('#view-settings').close());
$('#view-settings').addEventListener('close', () => {
  if (classificationMobile.matches) $('#classification-open').focus({preventScroll: true});
  else $('#custom-filter-tabs').querySelector(`[data-custom-filter="${activeCustomFilter}"]`)?.focus({preventScroll: true});
});
$('#custom-filter-duplicate').addEventListener('click', () => {
  $('#custom-filter-new-name').value = [...(currentView()?.name || '一覧')].slice(0, 76).join('') + 'のコピー';
  $('#custom-filter-new-message').textContent = ''; $('#custom-filter-new').showModal(); $('#custom-filter-new-name').focus();
});
$('#custom-filter-update').addEventListener('click', () => writeCustomFilter(true));
$('#custom-filter-retry').addEventListener('click', async () => { await loadCustomFilters(); await load(); });
async function selectCustomFilter(selected, source = null) {
  if (customFilterBusy) return;
  if (!await persistCustomClassification()) {
    if (classificationMobile.matches && $('#classification-dialog').open) $('#classification-dialog').close();
    openFilters('conditions'); return;
  }
  if (customFilterBusy) return;
  const restoreFocus = document.activeElement?.dataset.customFilter === selected;
  customFilterBusy = true; renderCustomFilters();
  clearTimeout(recommendationTimer); ++recommendationVersion; await recommendationSave;
  try {
    // Re-read the server copy so changes made on another PC are available on selection.
    const latest = await api('api/views');
    customFilters = latest.filters; defaultCustomClassification = latest.default_classification;
    const item = customFilters.find(item => item.id === selected);
    if (selected && !item) throw new Error('この項目は削除されています。項目を再読み込みしてください。');
    const changed = selected !== activeCustomFilter || state.tab !== 'recommended' || sidebarSource?.id !== source?.id;
    if (changed) { $('#search').value = ''; $('#search-clear').hidden = true; }
    activeCustomFilter = selected; sidebarSource = source;
    await setViewTab('recommended', false, false);
    if (item) applyCustomFilter(item);
    else { $('#custom-filter-name').value = ''; await loadRecommendations(); }
    $('#custom-filter-message').textContent = ''; $('#recommendation-message').textContent = '';
    const loaded = await load();
    if (classificationMobile.matches && $('#classification-dialog').open) $('#classification-dialog').close();
    if (changed && loaded) window.scrollTo({top: 0, left: 0, behavior: 'instant'});
  } catch (error) { $('#custom-filter-message').textContent = error.message; $('#custom-filter-retry').hidden = false; }
  finally {
    customFilterBusy = false; renderCustomFilters();
    if (!classificationMobile.matches) {
      if (source) viewRssMenus.get(selected)?.querySelector(`[data-rss-source="${source.id}"]`)?.focus({preventScroll: true});
      else if (restoreFocus) $('#custom-filter-tabs').querySelector(`[data-custom-filter="${selected}"]`)?.focus({preventScroll: true});
    }
  }
}
$('#custom-filter-delete').addEventListener('click', async () => {
  if (!activeCustomFilter || !confirm('このカスタム項目を削除しますか？記事は残ります。')) return;
  customFilterBusy = true; renderCustomFilters();
  try {
    await api('api/custom-filters/delete', {id: activeCustomFilter, confirm: true});
    activeCustomFilter = ''; sidebarSource = null; $('#custom-filter-name').value = '';
    classificationSave = Promise.resolve(true);
    await loadCustomFilters();
    if (currentView()) applyCustomFilter(currentView());
    await load();
    $('#custom-filter-message').textContent = '一覧を削除しました。';
    $('#view-settings').close();
  } catch (error) { $('#custom-filter-message').textContent = '削除できませんでした。' + error.message; }
  finally { customFilterBusy = false; renderCustomFilters(); }
});
function renderRecommendations(settings) {
  const form = $('#recommendation-form');
  form.elements.recommendation_min_preference.value = settings.recommendation_min_preference ?? '';
  for (const key of ['recommendation_days', 'recommendation_priority']) form.elements[key].value = settings[key];
  for (const key of ['recommendation_hide_bad', 'recommendation_mix_types']) form.elements[key].checked = settings[key];
  $('#recommendation-fields').disabled = false;
  renderCustomFilters();
}
async function loadRecommendations(request = api('api/recommendations')) {
  try {
    renderRecommendations(await request);
    $('#recommendation-message').textContent = '';
    $('#recommendation-retry').hidden = true;
  } catch (e) { $('#recommendation-message').textContent = '条件を読み込めませんでした。もう一度試してください。'; $('#recommendation-retry').hidden = false; }
}

function captureReadingAnchor(exclude = '') {
  const rows = [...$('#feed').querySelectorAll('article.news')];
  const row = rows.find(row => row.dataset.id !== exclude && row.getBoundingClientRect().bottom > 0 && row.getBoundingClientRect().top < innerHeight);
  return row ? {id: row.dataset.id, top: row.getBoundingClientRect().top} : null;
}
function restoreReadingAnchor(anchor) {
  const row = anchor && [...$('#feed').children].find(row => row.dataset.id === anchor.id);
  if (row) { const delta = row.getBoundingClientRect().top - anchor.top; if (Math.abs(delta) > 0.5) scrollBy(0, delta); }
}
const pendingReactions = new Map();
const retainedForReading = new Set();
let reactionRevision = 0;
let reactionWeights = null;
function patchReaction(item, busy = pendingReactions.has(item.id)) {
  const row = [...$('#feed').children].find(row => row.dataset.id === item.id);
  const detail = $('#detail').open && $('#detail').dataset.id === item.id ? $('#detail-body') : null;
  for (const scope of [row, detail].filter(Boolean)) {
    const oldMeta = scope.querySelector('.news-top'); if (oldMeta) oldMeta.replaceWith(meta(item));
    const readLabel = item.kind === 'video' ? (item.read ? '未視聴に戻す' : '視聴済みにする') : (item.read ? '未読に戻す' : '読了にする');
    for (const control of scope.querySelectorAll('.read-control, .bookmark-control, .mute-control, .rating-control')) {
      control.disabled = busy;
      if (control.classList.contains('rating-control')) control.setAttribute('aria-pressed', item.rating === (control.classList.contains('bad') ? -1 : 1));
      if (control.classList.contains('read-control')) { control.setAttribute('aria-pressed', !!item.read); control.title = item.title + 'を' + readLabel; control.setAttribute('aria-label', control.title); }
      if (control.classList.contains('bookmark-control')) { control.setAttribute('aria-pressed', !!item.saved); control.title = item.saved ? 'ブックマーク済み' : 'ブックマーク'; control.setAttribute('aria-label', control.title); }
      if (control.classList.contains('mute-control')) control.setAttribute('aria-pressed', !!item.muted);
    }
    let reason = scope.querySelector('.reason');
    if (item.rating === -1) {
      if (!reason) { reason = reasonControl(item); scope.append(reason); }
      reason.querySelector('select').value = item.reason;
      reason.querySelector('select').disabled = busy;
    } else reason?.remove();
  }
}
function applyPreferenceWeights(topics) {
  reactionWeights = topics;
  const weights = new Map(topics.map(row => [row.name, row.weight]));
  for (const visible of state.items) {
    const own = ['duplicate', 'summary'].includes(visible.reason) ? 0 : visible.rating;
    const score = visible.topics.reduce((sum, topic) => sum + Math.max(-3, Math.min(3, weights.get(topic) || 0)), own);
    const known = own !== 0 || visible.topics.some(topic => weights.has(topic));
    if (score === visible.preference_score && known === visible.preference_known) continue;
    visible.preference_score = score; visible.preference_known = known;
    const row = [...$('#feed').children].find(row => row.dataset.id === visible.id);
    row?.querySelector('.preference-score')?.replaceWith(preferenceLabel(visible));
  }
}
async function react(item, data) {
  if (pendingReactions.has(item.id)) return;
  const tab = state.tab, view = tab === 'recommended' ? activeCustomFilter : '';
  const source = sidebarSource?.id, version = loadVersion;
  const stillCurrent = () => tab === state.tab && (!view || view === activeCustomFilter) &&
    source === sidebarSource?.id && version === loadVersion;
  const optimistic = {...data};
  if ('rating' in data && data.rating !== -1) optimistic.reason = '';
  const previous = Object.fromEntries(Object.keys(optimistic).map(key => [key, item[key]]));
  const params = new URLSearchParams({search: $('#search').value});
  if (view) {
    params.set('custom_filter', view);
    if (source) params.set('sidebar_source', source);
  }
  const syncItem = update => {
    Object.assign(item, update);
    const visible = state.items.find(current => current.id === item.id);
    if (visible && visible !== item) Object.assign(visible, update);
    if (detailItem?.id === item.id && detailItem !== item) Object.assign(detailItem, update);
    return visible;
  };
  pendingReactions.set(item.id, {optimistic});
  syncItem(optimistic); patchReaction(item);
  let saved = false;
  try {
    const updated = await api(`api/items/${item.id}/react?` + params, data);
    saved = true; ++reactionRevision;
    const visible = syncItem(updated);
    if (updated.preference_weights) applyPreferenceWeights(updated.preference_weights);
    // A response from another view must not remove rows or move focus here.
    if (!stillCurrent()) return;
    if (view && ('read' in data || 'muted' in data || 'rating' in data || 'reason' in data)) retainedForReading.add(item.id);
    const remove = view ? updated.view_matches === false && !retainedForReading.has(item.id) :
      ('muted' in data ? item.muted !== (state.tab === 'muted') :
       'read' in data ? (['recommended', 'discovery'].includes(state.tab) ? !!item.read : state.tab === 'read' && !item.read) :
       'saved' in data && state.tab === 'saved' && !item.saved);
    if (remove && visible) {
      const anchor = captureReadingAnchor(item.id), index = state.items.indexOf(visible);
      const row = [...$('#feed').children].find(row => row.dataset.id === item.id);
      state.items.splice(index, 1); state.total = Math.max(0, state.total - 1); row?.remove();
      if (!state.items.length) render();
      else { updateResultCount(); restoreReadingAnchor(anchor); }
      if (!$('#detail').open) {
        const next = state.items[Math.min(index, state.items.length - 1)];
        const control = next && [...$('#feed').children].find(row => row.dataset.id === next.id)?.querySelector('read' in data ? '.read-control' : 'saved' in data ? '.bookmark-control' : '.mute-control');
        (control || $('#classification-open')).focus({preventScroll: true});
      }
    }
    if ('rating' in data || 'reason' in data) {
      $('#new-items').textContent = '好みを反映して一覧を更新'; $('#new-items').hidden = false;
    }
    $('#reading-action-message').textContent = 'rating' in data || 'reason' in data ? '評価を保存しました。' : 'saved' in data ? (item.saved ? 'ブックマークしました。' : 'ブックマークを外しました。') : 'read' in data ? (item.read ? '読了にしました。' : '未読に戻しました。') : 'スキップの状態を保存しました。';
  } catch (error) {
    if (!saved) {
      syncItem(previous);
      if (reactionWeights) applyPreferenceWeights(reactionWeights);
      notice('保存できませんでした。操作前の状態へ戻しました。' + error.message);
    } else notice('保存済みですが表示を更新できませんでした。一覧を更新してください。' + error.message);
  } finally {
    pendingReactions.delete(item.id);
    const anchor = captureReadingAnchor();
    patchReaction(state.items.find(current => current.id === item.id) || item, false); restoreReadingAnchor(anchor);
    if (stillCurrent() && document.activeElement === document.body) {
      const scope = $('#detail').open && $('#detail').dataset.id === item.id ? $('#detail-body') : [...$('#feed').children].find(row => row.dataset.id === item.id);
      scope?.querySelector('rating' in data ? '.rating-control' + (data.rating === -1 ? '.bad' : ':not(.bad)') : 'read' in data ? '.read-control' : 'saved' in data ? '.bookmark-control' : 'reason' in data ? '.reason select' : '.mute-control')?.focus({preventScroll: true});
    }
  }
}
// Observe actual visibility, without the prefetch margin used by link previews.
const scrollSkipKey = 'xangi-feed-scroll-skip';
let scrollSkipEnabled = false, scrollSkipY = window.scrollY, scrollSkipFrame = 0;
try { scrollSkipEnabled = localStorage.getItem(scrollSkipKey) === 'on'; } catch {}
$('#scroll-skip').checked = scrollSkipEnabled;
let scrollSkipSeen = new WeakSet();
const scrollSkipPending = new Set();
function scrollSkipActive() {
  return scrollSkipEnabled && !state.loading && !document.hidden &&
    !document.querySelector('dialog:modal') && currentView()?.conditions.read !== 'read' && currentView()?.conditions.muted !== 'only';
}
const scrollSkipObserver = new IntersectionObserver(entries => {
  if (!scrollSkipActive()) return;
  for (const entry of entries) {
    if (entry.isIntersecting && entry.intersectionRect.height > 0) scrollSkipSeen.add(entry.target);
  }
});
function observeScrollSkip() {
  scrollSkipObserver.disconnect();
  if (scrollSkipEnabled) $('#feed').querySelectorAll('article.news').forEach(row => scrollSkipObserver.observe(row));
}
$('#scroll-skip').addEventListener('change', event => {
  scrollSkipEnabled = event.target.checked;
  scrollSkipY = window.scrollY;
  scrollSkipSeen = new WeakSet();
  $('#scroll-skip-message').textContent = scrollSkipEnabled ? 'スクロールでスキップをONにしました。' : 'スクロールでスキップをOFFにしました。';
  try { localStorage.setItem(scrollSkipKey, scrollSkipEnabled ? 'on' : 'off'); }
  catch { $('#scroll-skip-message').textContent += ' ブラウザに保存できないため、この画面でのみ有効です。'; }
  observeScrollSkip();
});
async function skipScrolledItem(row, item) {
  scrollSkipPending.add(item.id);
  scrollSkipSeen.delete(row);
  const version = loadVersion;
  try {
    const updated = await api(`api/items/${item.id}/react`, { muted: true });
    Object.assign(item, updated);
    // Keep both the item and its DOM so Good and later renders remain available.
    if (version === loadVersion && row.isConnected) {
      retainedForReading.add(item.id);
      row.querySelector('.article-mute')?.replaceWith(muteControl(item));
      row.dataset.scrollSkipped = 'true';
      updateResultCount();
    }
  } catch (error) { notice('スクロールでのスキップを保存できませんでした。' + error.message); }
  finally { scrollSkipPending.delete(item.id); }
}
window.addEventListener('scroll', () => {
  if (scrollSkipFrame) return;
  scrollSkipFrame = requestAnimationFrame(() => {
    scrollSkipFrame = 0;
    const down = window.scrollY > scrollSkipY;
    scrollSkipY = window.scrollY;
    if (!down || !scrollSkipActive()) return;
    for (const row of $('#feed').querySelectorAll('article.news')) {
      const rect = row.getBoundingClientRect();
      if (rect.top < innerHeight && rect.bottom > 0) scrollSkipSeen.add(row);
      if (!scrollSkipSeen.has(row) || rect.bottom > 0) continue;
      const item = state.items.find(current => current.id === row.dataset.id);
      if (!item || item.muted || item.saved || item.read || scrollSkipPending.has(item.id)) continue;
      void skipScrolledItem(row, item);
    }
  });
}, { passive: true });
let detailOpening = 0;
function showDetailDialog() {
  if (readerEnabled()) detailDialog.show();
  else detailDialog.showModal();
}
function syncReaderSelection() {
  const selected = readerEnabled() && detailDialog.open ? detailDialog.dataset.id : null;
  $('#reader-empty').hidden = !readerEnabled() || !!selected;
  for (const title of $('#feed').querySelectorAll('.news-title')) {
    if (title.closest('article').dataset.id === selected) title.setAttribute('aria-current', 'true');
    else title.removeAttribute('aria-current');
  }
}
function syncReaderLayout() {
  const enabled = readerEnabled();
  readerToggle.hidden = !readerMedia.matches;
  readerToggle.setAttribute('aria-pressed', enabled);
  $('.reading-layout').classList.toggle('reader-enabled', enabled);
  if (detailDialog.open && enabled === detailDialog.matches(':modal')) {
    detailDialog.close();
    if (detailItem && !$('#detail-body .detail-loading')) renderDetail(detailItem);
    showDetailDialog();
  }
  layoutClassification();
  syncReaderSelection();
}
async function openDetail(item, target = '') {
  const opening = ++detailOpening;
  const previous = detailDialog.open ? detailDialog.dataset.id : null;
  const body = $('#detail-body');
  const preview = !target && item.title && (previous !== item.id || body.querySelector('.detail-loading'));
  let status;
  if (preview) {
    detailItem = item;
    for (const card of body.querySelectorAll('.link-card')) previewObserver.unobserve(card);
    detailDialog.dataset.id = item.id;
    const result = item.analysis?.result;
    status = el('p', 'detail-loading muted', '詳細を読み込み中…');
    status.setAttribute('role', 'status');
    body.replaceChildren(
      meta(item),
      el('h2', 'detail-title', result?.title_ja || item.title),
      el('p', 'excerpt', result?.summary_ja || item.summary || '本文の抜粋はありません。'),
      status,
    );
    if (!detailDialog.open) showDetailDialog();
    detailDialog.scrollTop = 0;
    syncReaderSelection();
  }
  const obsolete = () => opening !== detailOpening || ((previous || preview) && !detailDialog.open);
  const initial = {...item};
  let updated;
  try {
    updated = await api(`api/items/${item.id}`);
  } catch (error) {
    if (obsolete()) return;
    if (!status?.isConnected) throw error;
    status.classList.replace('muted', 'source-error');
    status.textContent = '詳細を読み込めませんでした。';
    status.after(button('再試行', () => openDetail(item)));
    return;
  }
  if (obsolete()) return;
  // Keep local changes made while details were loading, including completed reactions.
  for (const key of Object.keys(updated)) {
    if (item[key] === initial[key]) item[key] = updated[key];
  }
  Object.assign(item, pendingReactions.get(item.id)?.optimistic);
  renderDetail(item);
  if (!detailDialog.open) showDetailDialog();
  if (!preview && previous !== item.id) detailDialog.scrollTop = 0;
  syncReaderSelection();
  if (target === 'tags') { $('#manual-tag-input').scrollIntoView({ block: 'center' }); $('#manual-tag-input').focus({ preventScroll: true }); }
  if (target === 'note') {
    $('#article-note').scrollIntoView({ block: 'center' });
    $('#article-note').focus({ preventScroll: true });
  }
}
function showArticleDetail(item) {
  void openDetail(item).catch(error => notice(error.message));
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
    read: 'M5 12l4 4L19 6',
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
  if (activeCustomFilter && currentView()?.conditions.noted !== 'any') void load();
  else if (state.tab === 'notes' && !updated.note?.trim() && visible) {
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
  if (!inDetail) secondary.append(button('記事の詳細', () => showArticleDetail(item)), button('タグを編集', () => openDetail(item, 'tags')));
  secondary.append(button('リンクを共有・コピー', () => showArticleLinkOptions(item)));
  const readLabel = item.kind === 'video' ? (item.read ? '未視聴に戻す' : '視聴済みにする') : (item.read ? '未読に戻す' : '読了にする');
  const read = iconButton(`${item.title}を${readLabel}`, () => react(item, { read: !item.read }), icon('read'), 'read-control');
  read.setAttribute('aria-pressed', !!item.read);
  group.prepend(read);
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
  if (pendingReactions.has(item.id)) for (const control of group.querySelectorAll('.read-control, .bookmark-control, .mute-control, .rating-control')) control.disabled = true;
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
  select.disabled = pendingReactions.has(item.id);
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
  if (card.classList.contains('list-thumbnail')) return;
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
  finally { delete card.dataset.previewPending; }
}
function linkCard(item, thumbnailOnly = false) {
  if (!showLinkPreviews) return document.createDocumentFragment();
  const card = thumbnailOnly ? el('span', 'list-thumbnail') : articleLink(item, '');
  card.dataset.itemId = item.id;
  if (!thumbnailOnly) {
    card.className = 'link-card';
    card.dataset.title = item.title;
    card.dataset.site = new URL(item.url).hostname;
    card.setAttribute('aria-label', `リンクカード: ${item.title}`);
  }
  if (item.link_preview) previewData.set(item.id, item.link_preview);
  fillLinkCard(card, previewData.get(item.id) || {});
  if (!previewData.has(item.id)) {
    if (thumbnailOnly) card.dataset.previewPending = '';
    previewObserver.observe(card);
  }
  return card;
}
function publisherLabel(item) {
  return item.publisher_label || item.sources.map(s => s.name).join(' / ') || new URL(item.url).hostname;
}
function meta(item) {
  const row = el('div', 'news-top');
  const status = item.kind === 'video' ? (item.read ? '視聴済み' : '未視聴') : (item.read ? '読了' : '未読');
  row.append(el('span', 'reading-state ' + (item.read ? 'is-read' : 'is-unread'), status));
  if (item.discovery) row.append(el('span', 'kind', 'おすすめ'));
  if (item.manually_added) row.append(el('span', 'kind', '個別追加'));
  const label = item.updated != null ? '更新' : item.published != null ? '公開' : '取得';
  const publisher = el('span', 'publisher', publisherLabel(item));
  row.append(el('span', 'kind', kinds[item.kind]), publisher,
    el('span', '', label + ' ' + date(item.updated ?? item.published ?? item.fetched)), preferenceLabel(item));
  return row;
}
function topicNodes(item) { const row = el('div', 'topics'); for (const topic of item.topics) { const control = button(topic, async () => { state.selectedTopics.add(topic); if ($('#detail').open) $('#detail').close(); updateSelections(); await load(); focusClassification(); }, 'topic'); control.setAttribute('aria-label', `話題タグ「${topic}」で絞り込む`); row.append(control); } return row; }
function evidence(item) { if (item.content_kind === 'youtube_transcript') return '動画の音声をtranscriber_toolで文字起こしした本文です。認識誤りを含む場合があります。'; if (item.content_kind === 'youtube_captions') return 'YouTubeの公開字幕（自動生成を含む）を取得しました。動画・音声本編は未取得。'; if (item.content_kind === 'youtube_description') return 'YouTubeの動画情報と説明欄を取得しました。字幕本文・動画・音声本編は未取得。'; if (item.content_text) return '直接追加した公開ページから取得した本文です（最大50,000文字）。画像・動的表示・動画や音声本編は未取得。'; if (item.content_origin === 'manual') return '個別追加の記事。入力したタイトル・抜粋が根拠です。記事全文は未取得。'; return item.kind === 'video' ? (item.content_origin === 'page' ? 'YouTubeの公開一覧のタイトル・抜粋が根拠です。公開日不明の場合は取得順で表示。動画・音声・字幕は未確認。' : 'YouTubeのタイトル・説明欄が根拠です。動画・音声・字幕は未確認。') : item.kind === 'paper' ? '論文要旨からの抜粋。本文・査読状況は未確認。' : item.kind === 'podcast' ? '番組説明からの抜粋。音声は未確認。' : item.content_origin === 'page' ? 'ページの見出し・抜粋。記事全文は未確認。' : 'RSS本文からの抜粋。記事全文は未確認。'; }
function commentBubble(item, result) {
  const bubble = el('aside', 'ai-comment');
  bubble.setAttribute('role', 'note'); bubble.setAttribute('aria-label', 'AIコメント');
  bubble.append(el('p', 'ai-comment-label', 'AIコメント'));
  const analysis = item.analysis;
  const context = result?.comment_context || {}, sources = context.sources || [];
  if (result?.comment_version === 1 && result.comment) {
    bubble.append(el('p', 'ai-comment-text', result.comment));
    const basis = sources.length ? '関連する記録を参考にしたコメント' :
      context.status === 'article_only' ? '関連する記録を利用できず・記事内容に基づくコメント' :
      context.status === 'empty' ? '関連する記録なし・記事内容に基づくコメント' :
      ['error', 'unavailable'].includes(context.status) ? '関連する記録を取得できず・記事内容に基づくコメント' :
      '記事内容に基づくコメント（関連する記録は未参照）';
    bubble.append(el('p', 'ai-comment-basis', basis + (context.degraded ? '（キーワード検索）' : '')));
    if (['pending', 'processing'].includes(analysis?.status)) bubble.append(el('p', 'ai-comment-basis', 'コメントを更新中です。前回の内容を表示しています。'));
    if (analysis?.status === 'error') bubble.append(el('p', 'source-error', '更新に失敗しました。前回のコメントを表示しています。'));
  } else {
    const text = result?.comment_needs_rewrite ? (['pending', 'processing'].includes(analysis?.status) ? '記事の使い道が分かるコメントに書き直しています。' : analysis?.status === 'error' ? 'コメントを書き直せませんでした。記事詳細から再試行できます。' : '記事の使い道が分かるコメントに作り直してください。') : ['pending', 'processing'].includes(analysis?.status) ? 'AIコメントを準備中です。' :
      analysis?.status === 'error' ? 'AIコメントを生成できませんでした。15分後の再表示・再起動時に再試行します。記事詳細からすぐ作り直すこともできます。' :
      'AIコメントはまだありません。記事詳細から生成できます。';
    bubble.append(el('p', 'ai-comment-basis', text));
  }
  if (sources.length) {
    const records = el('details', 'ai-comment-records');
    records.append(el('summary', '', `コメント生成時に参照した記録（${sources.length}件）`));
    for (const source of sources) {
      const record = el('div', 'recommendation-source');
      record.append(el('p', 'evidence', source.path), el('p', 'excerpt', source.excerpt));
      records.append(record);
    }
    bubble.append(records);
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
function editorialErrorMessage(error) {
  // Older saved errors may contain schema keys; keep those out of the reader UI.
  const labels = {title_ja: '見出し', summary_ja: '要点', comment: 'AIコメント'};
  const match = String(error || '').match(/^(日本語の解説が必要です|AIの解説の項目が不正です): (title_ja|summary_ja|comment)$/);
  if (match) return match[1] === '日本語の解説が必要です' ? `AIが${labels[match[2]]}を日本語で生成できませんでした。` : `AIが生成した${labels[match[2]]}の形式が正しくありません。`;
  return error || 'AIから解説を取得できませんでした。';
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
    box.append(el('p', analysis?.status === 'error' ? 'source-error' : 'evidence', analysis?.status === 'error' ? `${editorialErrorMessage(analysis.error)} 原文を表示しています。` : '日本語の要点・解説は準備待ちです。原文を表示しています。'));
  }
  box.append(commentBubble(item, result));
  return box;
}
function discoveryContext(item) {
  const data = item.discovery, box = el('section', 'discovery-context');
  if (data.reason) box.append(el('p', 'evidence', 'おすすめの理由: ' + data.reason));
  if (data.matched_query) box.append(el('p', 'evidence', '検索テーマ: ' + data.matched_query));
  if (data.personal_reason) box.append(el('p', '', '自分とのつながり: ' + data.personal_reason));
  if (data.personal_sources?.length) {
    const records = el('details', 'ai-comment-records');
    records.append(el('summary', '', '検索テーマに使った記録'));
    for (const source of data.personal_sources) {
      const record = el('div', 'recommendation-source');
      record.append(el('p', 'evidence', source.path), el('p', 'excerpt', source.excerpt));
      records.append(record);
    }
    box.append(records);
  }
  return box;
}
function updateResultCount() {
  $('#result-count').textContent = `${state.total}件${state.items.length < state.total ? `のうち${state.items.length}件を表示` : ''}`;
  $('#more').hidden = state.items.length >= state.total;
}
function render(preservePosition = true) {
  const anchor = preservePosition ? captureReadingAnchor() : null;
  try { renderFeed(); } finally { restoreReadingAnchor(anchor); }
}
function renderFeed() {
  $('#recommendation-panel').hidden = state.tab !== 'recommended';
  $('#view-filter-open').disabled = !activeCustomFilter || state.tab !== 'recommended';
  $('#search-clear').hidden = !$('#search').value;
  const feed = $('#feed');
  for (const preview of feed.querySelectorAll('.link-card, .list-thumbnail')) previewObserver.unobserve(preview);
  const openedRecords = new Set([...feed.querySelectorAll('article:has(.discovery-context details[open])')].map(article => article.dataset.id));
  feed.replaceChildren(); feed.setAttribute('aria-busy', 'false');
  updateResultCount();
  $('#current-view-title').textContent = readingTitle();
  if (!sidebarSource && !customFilters.length && customClassificationReady) {
    const box = el('div', 'empty'); box.append(el('h3', '', '保存した一覧はありません'), button('一覧を追加', () => $('#custom-filter-plus').click()));
    feed.append(box); return;
  }
  if (!state.items.length && activeCustomFilter && state.tab === 'recommended') {
    const box = el('div', 'empty');
    box.append(el('h3', '', '条件に合う記事がありません'), button('条件を編集', () => openFilters('conditions')));
    feed.append(box); return;
  }
  if (!state.items.length) {
    if (isDiscoveryView()) {
      const box = el('div', 'empty');
      box.append(el('h3', '', 'おすすめの未読記事はまだありません'), el('p', '', 'おすすめの設定から記事を探せます。読了した記事は「すべて」「読了」で確認できます。'));
      feed.append(box); return;
    }
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
  const display = listDisplay.value;
  for (const item of state.items) {
    const article = el('article', 'news'); article.dataset.id = item.id;
    const result = item.analysis?.result || null;
    const title = listTitleLink(item, result?.title_ja || item.title);
    if (display !== 'standard') {
      const text = el('div', 'compact-news-text');
      text.append(title);
      article.classList.add('compact-news');
      if (display === 'summary') {
        const summary = result?.summary_ja || item.summary;
        if (summary) text.append(el('p', 'compact-news-summary', summary));
        const publisher = publisherLabel(item);
        const source = el('p', 'compact-news-source', publisher);
        source.title = publisher;
        text.append(source);
        article.classList.add('with-summary');
      }
      article.append(text);
      if (display === 'summary') article.append(linkCard(item, true));
      const detail = iconButton('記事の詳細', () => showArticleDetail(item), icon('more'), 'compact-news-detail');
      detail.setAttribute('aria-haspopup', 'dialog');
      article.append(detail);
      feed.append(article);
      continue;
    }
    article.append(meta(item));
    if (currentView()?.conditions.origin === 'manual') article.append(el('p', 'muted', item.manual_added_at == null ? '追加日時不明（取得 ' + date(item.fetched) + '）' : '追加 ' + date(item.manual_added_at)));
    if (currentView()?.conditions.read === 'read') article.append(el('p', 'muted', item.read_at == null ? '読了日時不明' : '読了 ' + date(item.read_at)));
    article.append(title, linkCard(item), editorial(item));
    if (item.discovery) {
      article.append(discoveryContext(item));
      const records = article.querySelector('.discovery-context details');
      if (records) records.open = openedRecords.has(item.id);
    }
    article.append(topicNodes(item), manualTagNodes(item), actions(item), exportStatus(item));
    if (item.note?.trim()) article.append(el('p', 'note-preview', '自分のメモ: ' + item.note));
    if (item.rating === -1) article.append(reasonControl(item));
    if (state.tab === 'recommended') article.append(el('p', 'recommendation', item.recommendation));
    feed.append(article);
  }
  syncReaderSelection();
  observeScrollSkip();
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
  detailItem = item;
  const body = $('#detail-body');
  for (const preview of body.querySelectorAll('.link-card')) previewObserver.unobserve(preview);
  body.replaceChildren(); detailDialog.dataset.id = item.id;
  const result = item.analysis?.result || null;
  body.append(meta(item), el('h2', 'detail-title', result?.title_ja || item.title), articleLink(item, item.kind === 'video' ? 'YouTubeで見る' : '原文を読む'), el('p', 'evidence', evidence(item)), linkCard(item), editorial(item), recommendationEvidence(item), topicNodes(item), actions(item, true));
  if (item.discovery) body.append(discoveryContext(item));
  if (result) {
    const original = el('details');
    original.append(el('summary', '', '原文のタイトル・抜粋'), el('h3', '', item.title), el('p', 'excerpt', item.summary));
    if (readerEnabled() && item.content_origin === 'rss' && item.summary) {
      original.open = true;
      body.insertBefore(original, body.querySelector('.editorial'));
    } else body.append(original);
  }
  if (['manual', 'discovery'].includes(item.content_origin)) {
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
    const control = button(name, async () => { state.selectedTags.add(name); updateSelections(); await load(); focusClassification(); }, 'topic');
    control.setAttribute('aria-label', `自分のタグ「${name}」で絞り込む`); row.append(control);
  }
  return row;
}
async function changeManualTag(item, operation, name) {
  try {
    const updated = await api(`api/items/${item.id}/tags`, { [operation]: name });
    applyItem(item, updated); await loadTagOptions(); await load(false, true, true);
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
  section.append(assigned, form, message, el('p', 'muted', '×でこの記事から外します。タグ自体の削除は絞り込み内の「タグの管理」で行います。'));
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
const classificationTabs = new Set(['discovery', 'recommended', 'all', 'manual', 'saved', 'notes', 'read', 'discussed', 'muted']);
const classificationMemory = new Map();
let classificationRestored = false, classificationReady = false;
function classificationPreferenceKey() { return `xangi-feed-classification:${state.tab}`; }
function saveClassification() {
  if (!classificationRestored || !classificationTabs.has(state.tab)) return;
  const saved = JSON.stringify({
    kinds: [...state.selectedKinds], topics: [...state.selectedTopics], tags: [...state.selectedTags],
    source: $('#source').value, search: $('#saved-search').value, sourceSearch: $('#source-search').value,
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
  $('#saved-search').value = typeof saved.search === 'string' ? saved.search.slice(0, 200) : '';
  $('#source-search').value = typeof saved.sourceSearch === 'string' ? saved.sourceSearch.slice(0, 100) : '';
  const source = typeof saved.source === 'string' && state.sources.some(source => String(source.id) === saved.source) ? saved.source : '';
  renderSourceChoices(source);
  classificationRestored = true;
  updateSelections();
}
let loadVersion = 0;
function showDiscoveryPanel() {
  const discovery = isDiscoveryView();
  $('#discovery-panel').hidden = !discovery;

}
async function load(more = false, preservePosition = false, retainCurrent = false) {
  showDiscoveryPanel();
  saveClassification();
  if (more && state.loading) return;
  if (!sidebarSource && customClassificationReady && !activeCustomFilter) { ++loadVersion; state.loading = false; state.items = []; state.total = 0; render(); return; }
  const version = ++loadVersion;
  if (!await persistCustomClassification()) {
    if (version === loadVersion) { state.loading = false; $('#feed').setAttribute('aria-busy', 'false'); $('#more').disabled = false; }
    return;
  }
  if (version !== loadVersion) return;
  state.loading = true; $('#feed').setAttribute('aria-busy', 'true');
  $('#more').disabled = true;
  try {
    if (isDiscoveryView() && !more) {
      await loadDiscovery();
      if (version !== loadVersion) return;
    }
    const params = new URLSearchParams({ tab: state.tab, source: $('#source').value, q: $('#saved-search').value, search: $('#search').value, sort: $('#sort').value, limit: 50, offset: 0 });
    if (more) for (const item of state.items) params.append('exclude_id', item.id);
    if (state.tab === 'all' && sidebarSource) {
      params.set('source', sidebarSource.id);
      params.delete('q');
    } else {
      if (state.tab === 'recommended' && activeCustomFilter) params.set('custom_filter', activeCustomFilter);
      if (sidebarSource) params.set('sidebar_source', sidebarSource.id);
      for (const kind of state.selectedKinds) params.append('kind', kind);
      for (const topic of state.selectedTopics) params.append('topic', topic);
      for (const tag of state.selectedTags) params.append('tag', tag);
    }
    const revision = reactionRevision;
    const result = await api('api/items?' + params);
    if (version === loadVersion && revision !== reactionRevision) {
      state.loading = false;
      return load(more, preservePosition, retainCurrent);
    }
    if (version !== loadVersion) return;
    for (const item of result.items) {
      const pending = pendingReactions.get(item.id);
      if (pending) Object.assign(item, pending.optimistic);
    }
    const previousCount = state.items.length;
    if (more) state.items = [...state.items, ...result.items.filter(i => !state.items.some(x => x.id === i.id))];
    else if (retainCurrent && previousCount) {
      const updates = new Map(result.items.map(item => [item.id, item]));
      state.items = state.items.map(item => updates.get(item.id) || item);
    } else { retainedForReading.clear(); state.items = result.items; }
    state.total = more ? previousCount + result.total : retainCurrent ? Math.max(previousCount, result.total) : result.total;
    render(more || preservePosition);
    for (const item of state.items) if (pendingReactions.has(item.id)) patchReaction(item);
    updateSelections(); $('#new-items').hidden = true;
    return true;
  } catch (e) { if (version === loadVersion) notice(e.message); } finally { if (version === loadVersion) { state.loading = false; $('#more').disabled = false; $('#feed').setAttribute('aria-busy', 'false'); } }
}
async function loadSources() {
  state.sources = (await api('api/sources')).sources;
  const failed = state.sources.filter(source => source.enabled && source.error).length;
  const failures = $('#source-failures');
  failures.textContent = failed ? `取得失敗 ${failed}件` : '';
  failures.hidden = !failed;
  renderSourceChoices();
  renderRssLinks($('#rss-sources'), rssSources(), '');
  renderCustomFilters();
  for (const [id, menu] of viewRssMenus) {
    if (!menu.hidden && menu.isConnected) void loadViewRss(id, menu);
  }
}
function rssSources() {
  return state.sources.filter(source => source.adapter === 'rss')
    .sort((a, b) => a.name.localeCompare(b.name, 'ja'));
}
function renderRssLinks(menu, sources, viewId) {
  const focusedId = menu.contains(document.activeElement) && document.activeElement.dataset.rssSource;
  const links = sources.map(source => {
    const control = el('button', 'rss-source', source.name);
    control.type = 'button'; control.dataset.rssSource = source.id; control.dataset.rssView = viewId;
    control.addEventListener('click', () => {
      if (viewId) void selectCustomFilter(viewId, source);
      else void selectRssSource(source);
    });
    return control;
  });
  menu.replaceChildren(...(links.length ? links : [el('p', 'muted', viewId ? '該当するRSS配信元はありません' : 'RSS配信元が登録されていません')]));
  updateRssSelection();
  if (focusedId) menu.querySelector(`[data-rss-source="${focusedId}"]`)?.focus({preventScroll: true});
}
function updateRssSelection() {
  const viewId = state.tab === 'recommended' ? activeCustomFilter : '';
  for (const control of document.querySelectorAll('[data-rss-source]')) {
    control.setAttribute('aria-pressed', control.dataset.rssSource === String(sidebarSource?.id) && control.dataset.rssView === viewId);
  }
}
async function loadViewRss(viewId, menu) {
  const request = String(Number(menu.dataset.request || 0) + 1);
  menu.dataset.request = request;
  menu.setAttribute('aria-busy', 'true');
  if (!menu.hasChildNodes()) menu.append(el('p', 'muted', '配信元を読み込み中…'));
  try {
    const {source_ids} = await api('api/views/source-ids?' + new URLSearchParams({id: viewId}));
    if (menu.dataset.request !== request) return;
    const matching = new Set(source_ids);
    renderRssLinks(menu, rssSources().filter(source => matching.has(source.id)), viewId);
  } catch {
    if (menu.dataset.request !== request) return;
    menu.replaceChildren(button('配信元を再読み込み', () => loadViewRss(viewId, menu)));
  } finally {
    if (menu.dataset.request === request) menu.setAttribute('aria-busy', 'false');
  }
}
async function selectRssSource(source) {
  if (customFilterBusy) return;
  if (!await persistCustomClassification()) {
    openFilters('conditions'); return;
  }
  if (customFilterBusy) return;
  customFilterBusy = true;
  try {
    const changed = state.tab !== 'all' || sidebarSource?.id !== source.id;
    if (changed) $('#search').value = '';
    sidebarSource = source;
    if (state.tab !== 'all') await setViewTab('all', false, false);
    else updateRssSelection();
    const loaded = await load();
    if ($('#classification-dialog').open) $('#classification-dialog').close();
    if (changed && loaded) window.scrollTo({top: 0, left: 0, behavior: 'instant'});
  } finally {
    customFilterBusy = false; renderCustomFilters();
  }
}
function renderSourceChoices(selected = $('#source').value) {
  const select = $('#source');
  const query = $('#source-search').value.trim().toLocaleLowerCase();
  const matches = state.sources.filter(source => source.name.toLocaleLowerCase().includes(query));
  select.replaceChildren(new Option('すべての配信元', ''), ...state.sources.map(source => new Option(source.name, source.id)));
  select.value = state.sources.some(source => String(source.id) === selected) ? selected : '';
  const current = state.sources.find(source => String(source.id) === select.value);
  $('#source-picker-open').textContent = current?.name || 'すべての配信元';
  const choose = source => {
    select.value = source ? String(source.id) : ''; renderSourceChoices(select.value);
    $('#source-picker').close(); updateSelections(); void load();
  };
  const all = button('すべての配信元', () => choose(null)); all.setAttribute('aria-pressed', !select.value);
  const choices = matches.slice(0, 50).map(source => {
    const control = button(source.name, () => choose(source));
    control.setAttribute('aria-pressed', String(source.id) === select.value); return control;
  });
  $('#source-results').replaceChildren(all, ...choices);
  $('#source-match').textContent = matches.length > 50 ? `${matches.length}件。先頭50件を表示しています。名前で絞り込めます。` : `${matches.length}件の配信元`;
}
$('#source-picker-open').addEventListener('click', () => { $('#source-search').value = ''; renderSourceChoices(); $('#source-picker').showModal(); $('#source-search').focus(); });
$('#source-picker-close').addEventListener('click', () => $('#source-picker').close());
$('#source-picker').addEventListener('close', () => $('#source-picker-open').focus({preventScroll: true}));
function focusClassification() {
  openFilters('classification');
}
function updateSelections() {
  saveClassification();
  $('#topic-no-filter').setAttribute('aria-pressed', state.selectedTopics.size === 0);
  $('#tag-no-filter').setAttribute('aria-pressed', state.selectedTags.size === 0);
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
  if ($('#saved-search').value) chip('検索: ' + $('#saved-search').value, () => { $('#saved-search').value = ''; });
  const count = active.children.length;
  $('#classification-count').textContent = String(count);
  $('#classification-count').hidden = count === 0;
  $('#classification-open').setAttribute('aria-label', '一覧を選ぶ' + (count ? `（${count}件選択中）` : ''));
  if (count) active.append(button('分類を解除', async () => { clearClassification(); await load(); }, 'clear-filters'));
}
$('#topic-no-filter').addEventListener('click', async () => { state.selectedTopics.clear(); updateSelections(); await load(); });
$('#tag-no-filter').addEventListener('click', async () => { state.selectedTags.clear(); updateSelections(); await load(); });
function clearClassification() {
  $('#kind').value = ''; $('#source').value = ''; $('#saved-search').value = ''; $('#source-search').value = '';
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
  if (wasOpen && !classificationMobile.matches) $('#classification-controls button[aria-pressed=true]')?.focus();
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
  if (readerEnabled()) {
    const top = classificationLayout.getBoundingClientRect().top + window.scrollY;
    classificationLayout.style.setProperty('--reader-start', `${Math.max(16, top)}px`);
  }
  const max = Math.max(180, Math.min(readerEnabled() ? 320 : 480, classificationLayout.clientWidth - 56 - 28 - 360));
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
  form.elements.agent_id.replaceChildren(new Option('xangi本体の既定', ''));
  for (const agent of settings.agents) {
    const option = new Option(agent.name + (agent.available === false ? '（利用不可）' : ''), agent.id);
    option.disabled = agent.available === false;
    form.elements.agent_id.append(option);
  }
  if (settings.agent_id && !settings.agents.some(a => a.id === settings.agent_id)) form.elements.agent_id.append(new Option('現在の担当', settings.agent_id));
  form.elements.agent_id.value = settings.agent_id;
  renderAgentModel();
  form.dataset.defaultBackend = [settings.default_backend?.backend, settings.default_backend?.model].filter(Boolean).join(' / ');
  form.dataset.destination = settings.host_url || '';
  form.dataset.mode = settings.ai_mode === 'direct' ? 'local-llmの直接接続を許可（本体の監視・停止・利用量記録の対象外）' : '本体経由';
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
async function refreshAnalyses() {
  const anchor = captureReadingAnchor();
  const version = loadVersion;
  const ids = state.items.map(item => item.id);
  if ($('#detail').open && !ids.includes($('#detail').dataset.id)) ids.push($('#detail').dataset.id);
  for (let offset = 0; offset < ids.length; offset += 100) {
    const params = new URLSearchParams();
    for (const id of ids.slice(offset, offset + 100)) params.append('id', id);
    const {analyses} = await api('api/analyses?' + params);
    if (version !== loadVersion) return;
    for (const item of state.items) {
      if (!Object.hasOwn(analyses, item.id) || JSON.stringify(item.analysis) === JSON.stringify(analyses[item.id])) continue;
      item.analysis = analyses[item.id];
      const article = [...$('#feed').children].find(row => row.dataset.id === item.id);
      if (article) {
        article.querySelector('.news-title').textContent = item.analysis?.result?.title_ja || item.title;
        replaceEditorial(article, item);
      }
    }
    restoreReadingAnchor(anchor);
    if ($('#detail').open && Object.hasOwn(analyses, $('#detail').dataset.id)) {
      const body = $('#detail-body'), analysis = analyses[$('#detail').dataset.id];
      const item = state.items.find(item => item.id === $('#detail').dataset.id) || detailItem;
      if (item?.id === $('#detail').dataset.id) {
        item.analysis = analysis;
        body.querySelector('.detail-title').textContent = analysis?.result?.title_ja || item.title;
        const excerpt = body.querySelector('.detail-loading') && body.querySelector('.excerpt');
        if (excerpt) excerpt.textContent = analysis?.result?.summary_ja || item.summary || '本文の抜粋はありません。';
        replaceEditorial(body, item);
      }
    }
  }
}
function replaceEditorial(container, item) {
  const old = container.querySelector('.editorial');
  if (!old) return;
  const next = editorial(item);
  if (old.querySelector('.ai-comment-records')?.open && next.querySelector('.ai-comment-records')) next.querySelector('.ai-comment-records').open = true;
  const focused = old.contains(document.activeElement) ? document.activeElement.tagName.toLowerCase() : null;
  old.replaceWith(next);
  if (focused) next.querySelector(focused)?.focus({preventScroll: true});
}
async function poll() {
  try {
    if (isDiscoveryView()) {
      const previous = state.discoveryUpdated;
      const current = await loadDiscovery();
      if (current && current.updated !== previous) await load(false, true, true);
    }
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
      if (state.refreshPending || !state.items.length) { const manual = state.refreshPending; state.refreshPending = false; await load(false, !manual, !manual); }
    }
    state.lastFinished = status.last_finished;
    state.polled = true;
    if (state.saveRevision !== undefined && health.save_revision !== state.saveRevision) {
      notionMessages.clear(); await load(false, true, true);
      if ($('#detail').open) await openDetail({id: $('#detail').dataset.id});
    }
    state.saveRevision = health.save_revision;
    const enrichment = health.enrichment;
    if (enrichment) {
      if (enrichment.running || enrichment.queued) $('#sync-status').textContent += ` / 日本語解説 ${enrichment.queued}件待ち`;
      if (state.analysisRevision !== undefined && enrichment.revision !== state.analysisRevision) {
        await refreshAnalyses();
        if (enrichment.error) notice('一部の記事の日本語解説を作れませんでした。原文で読めます。記事の「…」から再試行できます。');
      }
      state.analysisRevision = enrichment.revision;
    }
  } catch (e) { $('#sync-status').textContent = '接続を確認してください'; notice(e.message); }
}
async function setViewTab(tab, loadItems = true, persistBefore = true) {
  if (persistBefore && !await persistCustomClassification()) return;
  saveClassification();
  state.tab = tab;
  restoreClassification();
  restoreSort();
  if (tab === 'recommended' && activeCustomFilter) {
    const item = customFilters.find(item => item.id === activeCustomFilter);
    if (item) applyCustomFilter(item);
  } else if (tab === 'recommended' && defaultCustomClassification) {
    applyCustomClassification(defaultCustomClassification, false);
  }
  $('#recommendation-panel').hidden = tab !== 'recommended';
  clearTimeout(searchTimer);
  document.querySelectorAll('[data-tab]').forEach(t => t.setAttribute('aria-pressed', t.dataset.tab === tab));
  renderCustomFilters();
  if (loadItems) await load();
}
async function changeTab(tab) {
  const id = 'preset-' + tab;
  await selectCustomFilter(customFilters.some(item => item.id === id) ? id : customFilters[0]?.id || '');
}

$('#kind').addEventListener('change', () => { state.selectedKinds.clear(); if ($('#kind').value) state.selectedKinds.add($('#kind').value); updateSelections(); load(); }); $('#source').addEventListener('change', () => load());
$('#sort').addEventListener('change', () => { try { localStorage.setItem(sortStorageKey(), $('#sort').value); } catch {} load(); });
let searchTimer; $('#search').addEventListener('input', () => { $('#search-clear').hidden = !$('#search').value; clearTimeout(searchTimer); searchTimer = setTimeout(() => load(), 300); });
$('#search-clear').addEventListener('click', () => { $('#search').value = ''; $('#search-clear').hidden = true; void load(); $('#search').focus(); });
$('#saved-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => load(), 300); });
$('#more').addEventListener('click', () => load(true)); $('#new-items').addEventListener('click', () => load());
$('#refresh').addEventListener('click', async () => { try { state.refreshPending = true; await api('api/refresh', {}); await poll(); } catch (e) { notice(e.message); } });
$('#settings-open').addEventListener('click', () => showSettings().catch(e => notice(e.message)));
$('#source-failures').addEventListener('click', async () => {
  try {
    await showSettings();
    $('#sources').closest('section').scrollIntoView({block: 'start'});
    $('#source-message').focus({preventScroll: true});
  } catch (error) { notice(error.message); }
});
$('#detail-close').addEventListener('click', () => { ++detailOpening; detailDialog.close(); });
detailDialog.addEventListener('close', syncReaderSelection);
readerToggle.addEventListener('click', () => {
  readerPreference = !readerPreference;
  try { localStorage.setItem(readerPreferenceKey, readerPreference ? 'on' : 'off'); } catch {}
  syncReaderLayout();
  render();
});
readerMedia.addEventListener('change', () => { syncReaderLayout(); if (readerPreference) render(); });
syncReaderLayout();
$('#settings-close').addEventListener('click', () => $('#settings').close());
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
  const label = agent ? [agent.name, agent.backend, agent.model].filter(Boolean).join(' / ') : `本体の既定 / ${form.dataset.defaultBackend || '取得できませんでした'}`;
  $('#agent-model').textContent = [label, form.dataset.mode, form.dataset.destination].filter(Boolean).join(' — ');
  form.elements.agent_id.required = false;
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
  if (activeCustomFilter) { void load(); return; }
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
let viewFilterTimer;
$('#view-state-form').addEventListener('change', () => { clearTimeout(viewFilterTimer); void load(); });
$('#view-state-form').addEventListener('input', event => { if (event.target.type === 'number') { clearTimeout(viewFilterTimer); viewFilterTimer = setTimeout(() => load(), 300); } });
$('#view-state-form').addEventListener('submit', event => { event.preventDefault(); void load(); });
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
    await loadCustomFilters();
    if (currentView()) applyCustomFilter(currentView());
    renderCustomFilters();
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
    if (!isDiscoveryView()) return;
    const form = $('#discovery-form');
    if (!discoveryLoaded) {
      form.elements.discovery_enabled.checked = result.enabled;
      form.elements.discovery_use_personal_context.checked = result.use_personal_context ?? false;
      form.elements.discovery_interval_hours.value = result.interval_hours;
      form.elements.discovery_prompt.value = result.prompt;
      discoveryLoaded = true;
      discoverySaved = JSON.stringify(discoveryValues());
      $('#discovery-fields').disabled = false;
      $('#discovery-save-message').textContent = '入力すると自動保存して検索します。';
    }
    const status = $('#discovery-message');
    status.textContent = result.running ? '検索中…' : result.error || '';
    status.hidden = !status.textContent;
    const details = $('#discovery-details');
    details.replaceChildren();
    details.append(el('p', 'muted', `${result.updated ? '最終更新 ' + date(result.updated) + '。' : '探索済みの候補はまだありません。'}${result.enabled ? '次回: ' + (result.next_run ? date(result.next_run) : '起動後に探索します') : '定期探索は停止中です。'}`));
    if (result.plan && !result.running) {
      details.append(el('p', 'muted', `検索条件: ${result.plan.language === 'ja' ? '日本語のみ・' : ''}直近${result.plan.max_age_days}日 / ${result.plan.queries.join('、')}${result.plan.exclude_terms.length ? ' / 除外: ' + result.plan.exclude_terms.join('、') : ''}`));
      if (result.plan.notice) details.append(el('p', 'muted', result.plan.notice));
    }
    if (result.context?.message && !result.running) details.append(el('p', 'muted', result.context.message));
    state.discoveryUpdated = result.updated;
    return result;
  } catch (error) { $('#discovery-message').hidden = false; $('#discovery-message').textContent = 'おすすめを読み込めませんでした。' + error.message; if (!discoveryLoaded) { $('#discovery-toggle').open = true; $('#discovery-retry').hidden = false; } }
}
function discoveryValues() {
  const form = $('#discovery-form');
  return {discovery_enabled: form.elements.discovery_enabled.checked,
    discovery_use_personal_context: form.elements.discovery_use_personal_context.checked,
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
      const promptChanged = !discoverySaved || ['discovery_prompt', 'discovery_use_personal_context'].some(key => JSON.parse(discoverySaved)[key] !== data[key]);
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
      if (isDiscoveryView()) await load();
    } catch (error) {
      if (version !== discoveryVersion) return;
      discoveryRetryRefresh = refresh;
      $('#discovery-toggle').open = true;
      $('#discovery-save-message').textContent = (refresh ? '検索できませんでした。入力内容は保持しています。' : '保存できませんでした。入力はまだ反映されていません。') + error.message;
      $('#discovery-retry').textContent = refresh ? '検索を再試行' : '保存を再試行';
      $('#discovery-retry').hidden = false;
      if (!isDiscoveryView()) notice('おすすめの検索指示を保存できませんでした。おすすめで再試行してください。');
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
