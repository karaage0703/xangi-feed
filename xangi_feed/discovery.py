"""Periodic discovery from learned topics; only verified, unregistered feeds are offered."""
import threading
import time

from .feeds import fetch_feed
from .web_discovery import discover, scope, fetch_public
from .discovery_prompt import compile_prompt, PromptError


class Discovery:
    def __init__(self, store, host, fetcher=fetch_feed, clock=time.time, searcher=discover):
        self.store, self.host, self.fetcher, self.clock = store, host, fetch_public if fetcher is fetch_feed else fetcher, clock
        self.searcher = searcher
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.running = False
        self.pending_refresh = False
        self.error = store.discovery_state().get('error')
        self.worker = self.timer = None

    def status(self):
        with self.lock:
            data = self.store.discovery_state()
            settings = self.store.settings()
            excluded = {scope(s['url']) for s in self.store.sources()}
            items = [i for i in data['items'] if scope(i['feed_url']) not in excluded and scope(i['url']) not in excluded]
            if data.get('result_prompt', '') != settings['discovery_prompt']: items = []
            if (data.get('plan') or {}).get('language') == 'ja': items = [i for i in items if i.get('language') == 'ja']
            next_run = data.get('retry_at') or (data['last_attempt'] + settings['discovery_interval_hours'] * 3600 if data['last_attempt'] else None)
            changed = data.get('prompt', '') != settings['discovery_prompt']
            error = None if changed else self.error
            plan = None if changed else data.get('plan')
            if changed: next_run = self.clock()
            if data.get('outcome') == 'running' and not self.running:
                error = '前回の探索は再起動で中断しました。再探索します。'
                next_run = self.clock()
            return {**data, 'items': items, 'plan': plan, 'running': self.running, 'error': error,
                    'prompt': settings['discovery_prompt'], 'enabled': settings['discovery_enabled'], 'interval_hours': settings['discovery_interval_hours'],
                    'next_run': next_run if settings['discovery_enabled'] else None}

    def request_refresh(self):
        with self.lock:
            if self.stop.is_set(): return False
            if self.running:
                self.pending_refresh = True
                return False
            return self.trigger(force=True)

    def trigger(self, force=False):
        with self.lock:
            settings, data = self.store.settings(), self.store.discovery_state()
            if self.stop.is_set() or self.running:
                return False
            next_run = data.get('retry_at') or (data['last_attempt'] + settings['discovery_interval_hours'] * 3600 if data['last_attempt'] else 0)
            interrupted = data.get('outcome') == 'running'
            changed = data.get('prompt', '') != settings['discovery_prompt']
            if not force and (not settings['discovery_enabled'] or (not interrupted and not changed and self.clock() < next_run)):
                return False
            profile = self.store.profile()['topics']
            positive = [r for r in profile if r['weight'] > 0][:12]
            negative = [r for r in profile if r['weight'] < 0][-8:]
            if not positive and not settings['discovery_prompt'].strip():
                self.error = '検索する話題を入力するか、記事にGoodを付けてください。'
                return False
            registered = [s['url'] for s in self.store.sources()]
            data.update(last_attempt=self.clock(), outcome='running', error=None, retry_at=None, prompt=settings['discovery_prompt'])
            self.store.save_discovery(data)
            self.running, self.error = True, None
            self.worker = threading.Thread(target=self._run, args=(positive, negative, registered, settings['discovery_prompt'], settings['agent_id']), daemon=True)
            self.worker.start()
            return True

    def _run(self, positive, negative, registered, instructions, agent_id):
        items, diagnostics = [], {}
        plan = None
        outcome, error = 'failed', None
        try:
            started = time.monotonic()
            plan = compile_prompt(instructions, positive, self.host, agent_id)
            if not plan['queries'] and not positive:
                raise PromptError('検索する話題を入力するか、記事にGoodを付けてください。')
            items, diagnostics = self.searcher(positive, negative, registered, fetcher=self.fetcher, now=self.clock(), stop=self.stop, budget=max(1,90-(time.monotonic()-started)), plan=plan)
            outcome = 'success' if items else 'empty' if diagnostics.get('searches_ok', 1) else 'failed'
            if outcome == 'failed':
                error = '公開検索に接続できませんでした。15分後に再探索します。'
            elif not items:
                error = '公開フィードで確認できる新しい候補がありませんでした。15分後に再探索します。'
        except PromptError as exc:
            error = str(exc)
        except TimeoutError:
            error = '探索が90秒の制限時間を超えました。15分後に再探索します。'
        except Exception:
            error = '公開検索・フィード取得に失敗しました。15分後に再探索します。'
        with self.lock:
            self.running = False
            self.error = error
            if self.stop.is_set(): return
            previous = self.store.discovery_state()
            changed = self.store.settings()['discovery_prompt'] != instructions
            rerun = self.pending_refresh or changed
            self.pending_refresh = False
            if changed:
                previous.update(outcome='superseded', retry_at=self.clock(), error=None)
            else:
                previous.update(outcome=outcome, error=error, plan=plan, diagnostics=diagnostics, last_finished=self.clock(),
                                retry_at=None if items else self.clock()+900)
                if items:
                    previous.update(items=items, updated=self.clock(), result_prompt=instructions)
            self.store.save_discovery(previous)
            if rerun: self.trigger(force=True)

    def start(self):
        def loop():
            while not self.stop.is_set():
                self.trigger()
                self.stop.wait(60)
        self.timer = threading.Thread(target=loop, daemon=True)
        self.timer.start()

    def close(self):
        with self.lock:
            self.stop.set()
        if self.timer:
            self.timer.join(timeout=2)
