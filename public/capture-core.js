export function captureInput(url, title = '') {
  let parsed;
  try { parsed = new URL(url); } catch { throw new Error('記事のURLを入力してください。'); }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || String(url).length > 4000) {
    throw new Error('認証情報を含まないHTTP/HTTPSの記事URLを指定してください。');
  }
  if (parsed.port && !['80', '443'].includes(parsed.port)) throw new Error('記事URLのポートは80または443のみ対応しています。');
  return {url: parsed.href, title: String(title).slice(0, 1000)};
}
export function readShared(hash) {
  if (!hash || hash === '#') return null;
  if (hash.length > 20000) throw new Error('受け取ったURLが長すぎます。記事URLを直接入力してください。');
  const params = new URLSearchParams(hash.slice(1));
  if (!params.has('url')) throw new Error('記事URLがありません。');
  if (params.getAll('url').length !== 1 || params.getAll('title').length > 1) throw new Error('記事URLを一つだけ指定してください。');
  return captureInput(params.get('url'), params.get('title') || '');
}
export function makeBookmarklet(target) {
  const url = new URL(target);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !url.pathname.endsWith('/capture')) {
    throw new Error('受け取り画面のURLが不正です。');
  }
  const bridge = new URL(url.href);
  const managed = bridge.pathname.match(/^(.*\/api\/extensions\/[^/]+)\/service\/capture$/);
  bridge.pathname = managed ? managed[1] + '/ui' : bridge.pathname.replace(/capture$/, 'entry');
  const code = `void(function(){if(!/^https?:$/.test(location.protocol)){alert('記事のページで実行してください。');return;}location.href=${JSON.stringify(bridge.href)}+'#'+new URLSearchParams({url:location.href,title:document.title.slice(0,1000)}).toString();})()`;
  return 'javascript:' + code;
}
