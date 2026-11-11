import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {captureInput, readShared, makeBookmarklet} from '../public/capture-core.js';

test('bookmarklet hands off URL and title as fragment data without HTML or fetch', () => {
  const target='https://xangi.test:18888/api/extensions/xangi-feed/service/capture';
  const url='https://example.org/read?q=a%26b#section',title='日本語 <img onerror=alert(1)> "quote"';
  const context={location:{href:url,protocol:'https:'},document:{title},URLSearchParams,alert:()=>assert.fail('unexpected alert')};
  vm.runInNewContext(makeBookmarklet(target).slice('javascript:'.length),context);
  const result=new URL(context.location.href);
  assert.equal(result.origin,'https://xangi.test:18888');assert.equal(result.search,'');
  assert.equal(result.pathname,'/api/extensions/xangi-feed/ui');
  assert.deepEqual(readShared(result.hash),{url,title});
});
test('invalid source schemes, credentials and ports are rejected without a fetch', () => {
  for(const url of ['javascript:alert(1)','data:text/html,hello','file:///secret','https://secret@example.org','https://example.org:8888/a']) assert.throws(()=>captureInput(url));
  assert.throws(()=>readShared('#url=https://a.test&url=https://b.test'));
  assert.throws(()=>readShared('#url='+ 'x'.repeat(20001)));
  assert.equal(readShared(''),null);
});
test('action flags from an external URL are ignored; only URL and title are accepted',()=>{
  assert.deepEqual(readShared('#url=https%3A%2F%2Fexample.org%2F&title=hello&save=true&confirm=true&request_id=attacker'),{url:'https://example.org/',title:'hello'});
});
test('generated code cannot target credential URLs or non-HTTP destinations',()=>{
  for(const target of ['javascript:alert(1)','https://secret@xangi.test/capture','https://xangi.test/capture?token=secret','https://xangi.test/settings']) assert.throws(()=>makeBookmarklet(target));
  const context={location:{href:'chrome://settings',protocol:'chrome:'},document:{title:'settings'},alert:()=>{context.alerted=true;}};
  vm.runInNewContext(makeBookmarklet('https://xangi.test/capture').slice(11),context);
  assert.equal(context.location.href,'chrome://settings');assert.equal(context.alerted,true);
});

test('standalone capture also uses its trusted entry document',()=>{
  const context={location:{href:'https://example.org/',protocol:'https:'},document:{title:'Article'},URLSearchParams};
  vm.runInNewContext(makeBookmarklet('https://feed.test/capture').slice(11),context);
  assert.equal(new URL(context.location.href).pathname,'/entry');
});
