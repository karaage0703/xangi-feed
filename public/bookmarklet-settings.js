import {makeBookmarklet} from './capture-core.js';
const link = document.getElementById('settings-bookmarklet');
const field = document.getElementById('settings-bookmarklet-code');
const message = document.getElementById('settings-bookmarklet-message');
const code = makeBookmarklet(new URL('capture', document.baseURI));
link.href = code; field.value = code;
link.addEventListener('click', event => { event.preventDefault(); message.textContent = 'PCではブックマークバーへドラッグ。iPhoneではコードをコピーしてブックマークのURL欄へ登録してください。'; });
document.getElementById('settings-bookmarklet-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(code); message.textContent = 'コピーしました。ブックマークのURL欄へ貼り付けてください。'; }
  catch { field.focus(); field.select(); message.textContent = 'コードを選択しました。手動でコピーしてください。'; }
});
