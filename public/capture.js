import {captureInput, readShared, makeBookmarklet} from './capture-core.js';
const $ = id => document.getElementById(id);
const base = new URL('./', location.href);
const key = 'xangi-feed-capture:' + base.pathname;
const labels = {adding:'記事を追加中…',fetching:'記事を追加済み・本文を取得中…',sending:'保存AIへ依頼中…',added:'xangi-feedに追加済み',creating:'保存用の会話を準備中',requested:'Notion保存を依頼済み（処理中）',saved:'Notion保存完了',partial:'Notionに一部保存',needs_input:'保存AIが回答を待っています',uncertain:'依頼結果を確認してください',error:'操作を完了できませんでした'};
const pending = ['creating','sending','requested','needs_input','uncertain'];
let job = null, busy = false, checking = false;
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
function persist() { try { sessionStorage.setItem(key, JSON.stringify(job)); } catch { error('このブラウザでは進捗を保持できません。結果を確認するまで画面を閉じないでください。'); } }
function safeLink(id, value, accept) {
  const link = $(id); link.hidden = true; link.removeAttribute('href');
  try { const url = new URL(value); if (accept(url)) { link.href = url.href; link.hidden = false; } } catch {}
}
function render() {
  $('result').hidden = !job?.phase;
  if (!job?.phase) return;
  $('result-title').textContent = job.title || job.url;
  $('status').textContent = labels[job.phase] || '保存状況を確認してください';
  $('summary').textContent = job.summary || '';
  $('request-error').textContent = job.error || '';
  $('capture-refresh').hidden = !job.itemId;
  safeLink('notion', job.pageUrl, u => u.protocol === 'https:' && ['notion.so','www.notion.so','app.notion.com'].includes(u.hostname));
  safeLink('conversation', job.sessionId ? new URL('/chat/' + encodeURIComponent(job.sessionId), location.origin) : null, u => u.origin === location.origin);
}
function record() { persist(); render(); }
async function api(path, data) {
  const response = await fetch(new URL(path, base), {method:data === undefined ? 'GET':'POST',credentials:'same-origin',redirect:'error',headers:data === undefined ? {}:{'Content-Type':'application/json'},body:data === undefined ? undefined:JSON.stringify(data),signal:AbortSignal.timeout(20000)});
  if ([401,403].includes(response.status)) { const e = new Error('xangiにログインしてから、この画面を開き直してください。'); e.rejected = true; throw e; }
  let result;
  try { result = await response.json(); } catch { throw new Error('xangi-feedの応答を確認できません。接続先とログインを確認してください。'); }
  if (!response.ok) {
    const e = new Error(result.error || `接続エラー（HTTP ${response.status}）`);
    e.rejected = response.status >= 400 && response.status < 500 && ![408,429].includes(response.status);
    throw e;
  }
  return result;
}
async function refresh(showError = true) {
  if (busy || checking || !job?.itemId) return;
  checking = true;
  try {
    const previous = job;
    const item = await api(`api/items/${previous.itemId}`), save = item.save_request;
    if (job !== previous) return;
    if (job.save) {
      if (save?.request_id === job.requestId) Object.assign(job,{phase:save.status,pageUrl:save.page_url || null,summary:save.summary || '',error:save.error || '',sessionId:save.session_id || null});
      else { job.phase = 'uncertain'; job.error = 'この保存依頼の結果を確認できません。xangi-feedか保存AIの会話を確認してください。'; }
      record();
    }
  } catch (e) { if (showError) error(e.message); }
  finally { checking = false; }
}
$('capture-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  let input;
  try { input = captureInput($('url').value.trim(), $('title').value); } catch (e) { error(e.message); return; }
  if (job?.url === input.url && job.save && pending.includes(job.phase)) { await refresh(); return; }
  busy = true; error('');
  for (const control of $('capture-form').elements) control.disabled = true;
  job = {...input,save:event.submitter?.value === 'save',requestId:crypto.randomUUID(),phase:'adding'};
  record();
  try {
    const result = await api('api/items', {...input,background:true});
    if (!/^[a-f0-9]{24}$/.test(result.item?.id)) throw new Error('記事の追加結果を確認できませんでした。');
    job.itemId = result.item.id; job.created = result.created;
    if (!job.save) { job.phase = 'added'; record(); return; }
    job.phase = 'fetching'; record();
    let item = result.item;
    const end = Date.now() + 25000;
    while (item.fetch_state === 'fetching' && Date.now() < end) {
      await new Promise(resolve => setTimeout(resolve,500));
      item = await api(`api/items/${job.itemId}`);
    }
    job.phase = 'sending'; record();
    const save = await api(`api/items/${job.itemId}/save`,{confirm:true,request_id:job.requestId});
    Object.assign(job,{phase:save.status,requestId:save.request_id,pageUrl:save.page_url || null,sessionId:save.session_id || null,summary:save.summary || '',error:save.error || ''}); record();
  } catch (e) { job.error = e.message; job.phase = job.phase === 'sending' && !e.rejected ? 'uncertain':'error'; record(); }
  finally { busy = false; for (const control of $('capture-form').elements) control.disabled = false; }
});
$('capture-refresh').addEventListener('click', () => refresh());
function receiveShared() {
  if (!location.hash) return false;
  const hash = location.hash; history.replaceState(null,'',location.pathname);
  if (busy) { error('操作中です。完了後に記事からもう一度送ってください。'); return true; }
  error(''); job = null; $('capture-form').reset();
  try {
    try { sessionStorage.removeItem(key); } catch {}
    job = readShared(hash);
    if (job) { $('url').value = job.url; $('title').value = job.title; persist(); }
  } catch (e) { error(e.message); }
  render(); return true;
}
if (!receiveShared()) {
  try {
    try { job = JSON.parse(sessionStorage.getItem(key)); } catch {}
    if (job) { const input = captureInput(job.url,job.title); job = {...job,...input}; }
    if (job?.phase === 'sending') { job.phase = 'uncertain'; job.error = '保存依頼中に画面を閉じました。状態を確認してください。'; }
    else if (['adding','fetching'].includes(job?.phase)) { job.phase = 'error'; job.error = '操作が中断されました。同じURLで追加をやり直せます。'; }
    if (job) { $('url').value = job.url; $('title').value = job.title; }
  } catch (e) { job = null; error(e.message); }
}
window.addEventListener('hashchange', receiveShared);
render();
$('installation').open = !job;
const code = makeBookmarklet(new URL('capture',base));
$('code').value = code; $('bookmarklet').href = code;
$('bookmarklet').addEventListener('click',event => { event.preventDefault(); $('copy-message').textContent = 'PCではブックマークバーへドラッグ。iPhoneではコードをコピーしてブックマークのURL欄へ登録してください。'; });
$('copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(code); $('copy-message').textContent = 'コピーしました。ブックマークのURL欄に貼り付けてください。'; }
  catch { $('code').focus(); $('code').select(); $('copy-message').textContent = 'コードを選択しました。手動でコピーしてください。'; }
});
setInterval(() => { if (document.visibilityState === 'visible' && pending.includes(job?.phase)) void refresh(false); },5000);
