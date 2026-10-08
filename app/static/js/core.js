/* ==========================================================================
   Topclass Auto Body — front-end micro-framework
   A tiny hyperscript + store + hash-router + API client, so the app behaves
   like a React SPA without a build step or extra dependencies.
   ========================================================================== */
(function () {
  'use strict';
  const TCA = (window.TCA = window.TCA || {});

  /* ── hyperscript ─────────────────────────────────────────────────── */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  /**
   * Parse a tag shorthand into `{ tag, classes, id }`.
   *
   * Accepts the dotted form (`div.card.p-3#main`), space-separated classes
   * (`div.card is-active`), or a mix. Empty class fragments — the kind produced
   * by a template literal like `` `div.item.${maybeEmpty}` `` — are dropped
   * rather than becoming a literal garbage class name.
   */
  function parseTag(tag) {
    const out = { tag: 'div', classes: [], id: null };
    const parts = String(tag).trim().split(/\s+/).filter(Boolean);

    parts.forEach((part, partIndex) => {
      part.split(/(?=[.#])/).filter(Boolean).forEach((token, tokenIndex) => {
        if (token.startsWith('#')) {
          out.id = token.slice(1);
        } else if (token.startsWith('.')) {
          const cls = token.slice(1);
          if (cls) out.classes.push(cls);
        } else if (partIndex === 0 && tokenIndex === 0 && /^[a-zA-Z][a-zA-Z0-9-]*$/.test(token)) {
          out.tag = token;
        } else if (token) {
          out.classes.push(token);
        }
      });
    });

    return out;
  }

  function appendChild(parent, child) {
    if (child === null || child === undefined || child === false || child === true) return;
    if (Array.isArray(child)) { child.forEach((c) => appendChild(parent, c)); return; }
    if (child instanceof Node) { parent.appendChild(child); return; }
    if (typeof child === 'function') { appendChild(parent, child()); return; }
    if (typeof child === 'object' && child.__html !== undefined) {
      parent.insertAdjacentHTML('beforeend', child.__html);
      return;
    }
    parent.appendChild(document.createTextNode(String(child)));
  }

  function h(tag, props, ...children) {
    const { tag: name, classes, id } = parseTag(tag);
    const el = document.createElement(name);

    // Anything that isn't a plain props object is really the first child.
    const isProps = props !== null && typeof props === 'object'
      && !Array.isArray(props) && !(props instanceof Node);
    if (!isProps) {
      children.unshift(props);
      props = {};
    }

    if (classes.length) el.className = classes.join(' ');
    if (id) el.id = id;

    Object.entries(props).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === 'class' || key === 'className') {
        el.className = ((el.className ? el.className + ' ' : '') + value).trim();
      } else if (key === 'style') {
        if (typeof value === 'string') el.style.cssText = value;
        else Object.assign(el.style, value);
      } else if (key === 'dataset') {
        Object.entries(value).forEach(([k, v]) => { el.dataset[k] = v; });
      } else if (key === 'html' || key === '__html') {
        el.innerHTML = value;
      } else if (key.startsWith('on') && typeof value === 'function') {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === 'value' && (name === 'input' || name === 'textarea' || name === 'select')) {
        el.value = value;
      } else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'readonly') {
        el[key] = !!value;
      } else if (key.startsWith('data-') || key.startsWith('aria-') || key === 'role'
                 || key === 'type' || key === 'href' || key === 'target'
                 || key === 'colspan' || key === 'rowspan' || key === 'placeholder'
                 || key === 'title' || key === 'name' || key === 'for') {
        /* `aria-*` must go through setAttribute. Only `aria-label` used to be
           listed here, so every other ARIA attribute — aria-expanded,
           aria-haspopup, aria-modal, aria-selected, aria-required — fell through
           to `el[key] = value`, which creates a JS property on a dashed name and
           no attribute at all. Silently: no exception, no attribute, no state for
           a screen reader anywhere in the app. */
        el.setAttribute(key, value);
      } else {
        try { el[key] = value; } catch (e) { el.setAttribute(key, value); }
      }
    });

    children.forEach((c) => appendChild(el, c));
    return el;
  }

  function frag(...children) { const f = document.createDocumentFragment(); children.forEach((c) => appendChild(f, c)); return f; }
  function mount(target, node) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return null;
    el.innerHTML = '';
    appendChild(el, node);
    return el;
  }
  function html(markup) { return { __html: markup }; }
  function icon(name, cls) { return h('i', { class: `bi bi-${name} ${cls || ''}` }); }

  /* ── store ───────────────────────────────────────────────────────── */
  function createStore(initial) {
    let state = initial || {};
    const subs = new Set();
    return {
      get: (key) => (key === undefined ? state : state[key]),
      set(patch) {
        state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
        subs.forEach((fn) => fn(state));
        return state;
      },
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    };
  }

  /* ── the offline outbox ──────────────────────────────────────────────
     A write the network refused is not lost — it is parked here and sent again
     when the link returns.

     Anything queued carries an `Idempotency-Key`, and the server remembers the
     answer it gave for that key, so a replay that the server has already seen
     returns the first outcome instead of performing the action a second time.
     Without that, "record this payment" queued and replayed takes the money
     twice — which is why the key exists and why it is minted here, at the moment
     the write is first attempted, not when it is replayed.
     ──────────────────────────────────────────────────────────────────── */
  const OUTBOX_DB = 'topclass-outbox';
  const OUTBOX_STORE = 'queue';
  const WRITE_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);
  // A write that keeps failing on the server is retried, but not for ever —
  // past this many attempts it is surfaced to the operator to deal with.
  const OUTBOX_MAX_ATTEMPTS = 6;

  let outboxDb = null;
  function openOutbox() {
    if (outboxDb) return Promise.resolve(outboxDb);
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) return reject(new Error('no indexDB'));
      const req = indexedDB.open(OUTBOX_DB, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(OUTBOX_STORE)) {
          const store = db.createObjectStore(OUTBOX_STORE, { keyPath: 'id', autoIncrement: true });
          store.createIndex('queued_at', 'queued_at');
        }
      };
      req.onsuccess = () => { outboxDb = req.result; resolve(outboxDb); };
      req.onerror = () => reject(req.error);
    });
  }

  function tx(mode, fn) {
    return openOutbox().then((db) => new Promise((resolve, reject) => {
      const t = db.transaction(OUTBOX_STORE, mode);
      const store = t.objectStore(OUTBOX_STORE);
      let out;
      try { out = fn(store); } catch (e) { reject(e); return; }
      t.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    }));
  }

  const outbox = {
    add: (entry) => tx('readwrite', (s) => s.add(entry)),
    all: () => tx('readonly', (s) => s.getAll()).then((r) => r || []),
    get: (id) => tx('readonly', (s) => s.get(id)),
    update: (id, patch) => tx('readwrite', (s) => new Promise((res, rej) => {
      const g = s.get(id);
      g.onsuccess = () => { Object.assign(g.result, patch); res(s.put(g.result)); };
      g.onerror = () => rej(g.error);
    })),
    remove: (id) => tx('readwrite', (s) => s.delete(id)),
    count: () => tx('readonly', (s) => s.count()),
  };

  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `k-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  }

  /** Everything still waiting, oldest first — the order it was done in is the
      order it must be applied in, or a stage change can land after the next. */
  async function outboxPending() {
    const items = await outbox.all().catch(() => []);
    return items.sort((a, b) => a.queued_at - b.queued_at);
  }
  TCA.outboxPending = outboxPending;

  async function outboxRemove(id) {
    await outbox.remove(id).catch(() => {});
    // Everything that displays the queue reads the same cache, so this is the
    // single place a removal has to be announced from.
    announceOutbox();
  }

  async function outboxRetryNow() {
    await replayOutbox({ announce: true });
  }
  TCA.outboxRetryNow = outboxRetryNow;

  async function outboxDrop(id) {
    await outboxRemove(id);
  }
  TCA.outboxDrop = outboxDrop;

  let replaying = false;

  /* ── pending changes, as something a screen can read ───────────────────
     A queued write is invisible to every list on the app: the server has not
     been told yet, so a re-fetch returns the old data and the operator watches
     their own change disappear. That is not only confusing — it is dangerous.
     Somebody who cannot see that they already recorded a payment will record it
     again, and a second attempt is a new action with a new key, so nothing
     server-side will stop it.

     So the queue is exposed reactively: screens read it, render what is waiting,
     and re-render when it changes.
     ──────────────────────────────────────────────────────────────────── */
  let pendingCache = [];
  const pendingListeners = [];

  async function refreshPending() {
    let items = [];
    try { items = await outboxPending(); } catch (e) { items = []; }
    pendingCache = items;
    pendingListeners.forEach((fn) => { try { fn(items); } catch (e) { /* ignore */ } });
    return items;
  }

  /** Everything queued, oldest first. Synchronous — a screen can read it while
      rendering rather than awaiting in the middle of building a table. */
  TCA.pending = () => pendingCache;

  /** Items still waiting or already refused, filtered by URL. */
  TCA.pendingFor = (test) => pendingCache.filter((i) => test(String(i.url || ''), i));

  TCA.onPending = (fn) => {
    pendingListeners.push(fn);
    try { fn(pendingCache); } catch (e) { /* ignore */ }
    return () => {
      const i = pendingListeners.indexOf(fn);
      if (i >= 0) pendingListeners.splice(i, 1);
    };
  };

  /** Turn "POST /api/invoices/4/payment" into something a foreman would say.
   *
   * Lives here rather than in the top bar because every screen that shows a
   * queued change has to name it the same way, or the same action reads as two
   * different things depending on where you look. */
  TCA.describeChange = function describeChange(item) {
    const path = String((item && item.url) || '');
    const verb = item && item.method === 'DELETE' ? 'Remove'
      : item && item.method === 'PATCH' ? 'Update' : 'Add';
    const known = [
      [/\/invoices\/\d+\/payment/, 'Record a payment'],
      [/\/invoices\/\d+\/issue/, 'Issue an invoice'],
      [/\/invoices$/, 'Raise an invoice'],
      [/\/jobs\/\d+\/stage/, 'Move a job card'],
      [/\/jobs\/\d+\/advance/, 'Advance a job card'],
      [/\/jobs\/\d+\/qc/, 'Record a quality check'],
      [/\/jobs\/\d+\/estimate/, 'Save an estimate'],
      [/\/jobs\/\d+\/parts/, 'Fit a part'],
      [/\/jobs\/\d+\/photos/, 'Add a job photo'],
      [/\/jobs\/\d+\/documents/, 'Add a job document'],
      [/\/jobs\/\d+/, 'Update a job card'],
      [/\/jobs$/, 'Open a job card'],
      [/\/part[s]?\/\d+\/movement/, 'Record a stock movement'],
      [/\/parts\/\d+/, 'Update a stock item'],
      [/\/parts$/, 'Add a stock item'],
      [/\/tasks\/\d+$/, `${verb} a to-do`],
      [/\/tasks$/, 'Add a to-do'],
      [/\/customers/, 'Update a customer'],
      [/\/vehicles/, 'Update a vehicle'],
      [/\/bookings\/\d+\/reschedule/, 'Reschedule an appointment'],
      [/\/bookings/, 'Update an enquiry'],
      [/\/estimates/, 'Update an estimate'],
    ];
    for (const [re, label] of known) if (re.test(path)) return label;
    return `${verb} — ${path.replace('/api/', '').replace(/\/\d+/g, '')}`;
  };

  /** The job id a queued change is about, or null. */
  TCA.pendingJobId = (item) => {
    const m = String((item && item.url) || '').match(/\/jobs\/(\d+)/);
    return m ? Number(m[1]) : null;
  };

  /**
   * "2 changes on this screen are not saved yet", with a Try now button.
   *
   * One component for every screen, so the same queued action is described the
   * same way wherever it is seen, and a screen cannot quietly forget to mention
   * it. Returns a host element that fills itself in — and empties itself out —
   * as the queue changes, because a change made *while you are looking at the
   * screen* is exactly the case where somebody would otherwise repeat it.
   *
   * `match` decides what belongs here — a screen must only claim changes it
   * actually shows, or the operator learns to ignore the strip.
   */
  TCA.pendingStrip = function pendingStrip({ match, label = 'change' } = {}) {
    const test = match || (() => false);
    const host = h('div.tc-pending-host');
    let wasConnected = false;
    let stop = null;

    function paint(items) {
      // Once the route that owned this has gone, so has the reason to listen.
      if (wasConnected && !document.contains(host)) {
        if (stop) stop();
        stop = null;
        return;
      }
      if (document.contains(host)) wasConnected = true;

      const relevant = (items || []).filter((i) => test(String(i.url || ''), i));
      if (!relevant.length) {
        TCA.mount(host, []);
        host.hidden = true;
        return;
      }
      const refused = relevant.filter((i) => i.state === 'failed');
      TCA.mount(host, h('div.tc-pending-strip' + (refused.length ? '.is-refused' : ''), [
        h('span.tc-pending-icon',
          icon(refused.length ? 'exclamation-octagon' : 'cloud-arrow-up')),
        h('div.tc-pending-text', [
          h('div.tc-pending-title', relevant.length === 1
            ? `1 ${label} on this screen has not been saved yet`
            : `${relevant.length} ${label}s on this screen have not been saved yet`),
          h('div.tc-pending-list', relevant.map((i) => h('span.tc-pending-item',
            (i.state === 'failed' ? 'Could not save: ' : 'Waiting: ')
            + TCA.describeChange(i)))),
        ]),
        h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button',
          onclick: async (e) => {
            e.target.disabled = true;
            await TCA.replayOutbox({ announce: true });
            e.target.disabled = false;
          },
        }, 'Try now'),
      ]));
      host.hidden = false;
    }

    stop = TCA.onPending(paint);
    return host;
  };

  /* ── where you came from ────────────────────────────────────────────────
     Opening a job card from a filtered list and coming back used to land you on
     a cold, unfiltered list — the back link was a hardcoded `#/jobs`. So the
     search you had just done had to be done again, every time.

     Two halves fix it:

     * a list screen writes its filters into the address bar as they change, so
       the URL always describes what is on screen and the browser's own Back
       works without any help from us;
     * it also leaves that URL behind for its detail screens, so an in-app Back
       button can return to *that* list rather than the default one.

     Kept in sessionStorage, not localStorage: coming back to yesterday's filter
     would be stranger than coming back to nothing.
     ─────────────────────────────────────────────────────────────────────── */
  const ORIGIN_KEY = 'topclass.origin.';

  /** Record what a list screen is currently showing.
   *
   * `params` is a URLSearchParams. Rewrites the hash with replaceState, which
   * deliberately does NOT fire `hashchange` — the router must not re-run while
   * somebody is typing in a filter box. */
  TCA.listState = function listState(section, params) {
    const query = params ? params.toString() : '';
    const url = `#/${section}${query ? `?${query}` : ''}`;
    try {
      window.history.replaceState(null, '', url);
      sessionStorage.setItem(ORIGIN_KEY + section, url);
    } catch (e) { /* private mode, or no history — the filter still works */ }
    return url;
  };

  /** Where a detail screen's Back should go. Falls back to the plain list. */
  TCA.listOrigin = function listOrigin(section, fallback) {
    const plain = fallback || `#/${section}`;
    try { return sessionStorage.getItem(ORIGIN_KEY + section) || plain; }
    catch (e) { return plain; }
  };

  function announceOutbox() {
    const badge = document.getElementById('outboxCount');
    // refreshPending() is what keeps the badge and every screen's pending strip
    // in step — they read the same cache, so they cannot disagree.
    return refreshPending().then((items) => {
      const failed = items.filter((i) => i.state === 'failed').length;
      // The panel is optional — the console works with no top bar in tests.
      if (badge) {
        badge.textContent = String(items.length);
        badge.hidden = !items.length;
      }
      const wrapBtn = document.getElementById('outboxBtn');
      if (wrapBtn) {
        wrapBtn.classList.toggle('has-items', !!items.length);
        wrapBtn.classList.toggle('has-failed', failed > 0);
        wrapBtn.title = items.length
          ? `${items.length} change${items.length === 1 ? '' : 's'} waiting to sync`
            + (failed ? ` — ${failed} could not be saved` : '')
          : 'Nothing waiting to sync';
      }
      return items.length;
    });
  }
  TCA.announceOutbox = announceOutbox;

  /** Send everything that was parked while the connection was down. */
  async function replayOutbox({ announce = false } = {}) {
    if (replaying) return { synced: 0, failed: 0, deferred: 0 };
    /* Deliberately NOT gated on `navigator.onLine`. It lies — the embedded
       browser reports offline while the server is perfectly reachable, and a
       gate on it means the queue never drains and the operator is told their
       work is waiting when it could have gone. Attempt the send and let the
       fetch outcome decide, which is how `setConnectionState` already works. */
    replaying = true;
    const result = { synced: 0, failed: 0, deferred: 0 };
    try {
      const items = await outboxPending();
      for (const item of items) {
        if (item.state === 'failed') { result.failed += 1; continue; }
        try {
          await request(item.method, item.url, item.body, {
            noQueue: true, silent: true, idempotencyKey: item.key,
          });
          await outbox.remove(item.id);
          result.synced += 1;
        } catch (err) {
          if (err.status === 409) {
            // The server is still settling the original. Leave it queued rather
            // than deciding on its behalf.
            result.deferred += 1;
            continue;
          }
          if (err.status >= 400 && err.status < 500) {
            // The server understood and refused. Retrying cannot fix it, so it
            // becomes the operator's problem and is shown, not swallowed.
            await outbox.update(item.id, { state: 'failed', error: err.message });
            result.failed += 1;
          } else {
            const attempts = (item.attempts || 0) + 1;
            await outbox.update(item.id, {
              attempts,
              state: attempts >= OUTBOX_MAX_ATTEMPTS ? 'failed' : 'queued',
              error: err.message,
            });
            result.failed += attempts >= OUTBOX_MAX_ATTEMPTS ? 1 : 0;
            // Network or server trouble: stop here so the queue keeps its order.
            break;
          }
        }
      }
    } finally {
      replaying = false;
      await announceOutbox();
    }

    if (result.synced) {
      // The screen on show was rendered from data that predates these changes,
      // so the app refreshes the route. Announced rather than called directly so
      // core.js stays free of knowledge about routing.
      try {
        window.dispatchEvent(new CustomEvent('topclass:synced', { detail: result }));
      } catch (e) { /* older browsers — the toast still fires */ }
    }

    if (announce && (result.synced || result.failed)) {
      if (result.failed) {
        TCA.toast(`${result.synced} change(s) saved. ${result.failed} could not be `
          + 'saved — open the sync panel to see why.', 'warning');
      } else {
        TCA.toast(`${result.synced} offline change(s) saved.`, 'success');
      }
    }
    return result;
  }
  TCA.replayOutbox = replayOutbox;

  let outboxTimersBound = false;
  function bindOutbox() {
    if (outboxTimersBound) return;
    outboxTimersBound = true;
    window.addEventListener('online', () => replayOutbox({ announce: true }));
    // Also poll, because the browser often does not announce the return of a
    // flaky link at all — and because `online` is not a reliable signal here.
    window.setInterval(() => replayOutbox({ announce: true }), 45000);
    announceOutbox();
  }
  TCA.bindOutbox = bindOutbox;

  /* ── api client ──────────────────────────────────────────────────── */
  /**
   * Connection state is driven by real evidence — failed fetches and the
   * browser's offline event — not by an optimistic navigator.onLine read, which
   * is unreliable inside embedded browsers.
   */
  let connectionDown = false;
  function setConnectionState(online) {
    if (online === !connectionDown) return;
    connectionDown = !online;
    const banner = document.getElementById('connBanner');
    if (banner) banner.classList.toggle('show', !online);
  }

  /* ── how old is what you are looking at ────────────────────────────────
     When the worker answers a read from its cache it stamps the response, and
     the UI says so. A figure on screen that is two hours old is fine as long as
     nobody believes it is live — the danger is a balance that looks current and
     is not, because that is what someone takes money against.
     ──────────────────────────────────────────────────────────────────── */
  let staleSince = null;
  const staleListeners = [];

  function emitFreshness() {
    staleListeners.forEach((fn) => { try { fn(staleSince); } catch (e) { /* ignore */ } });
  }

  /** A read that came off this device rather than the network.
   *
   * Sticky for the rest of the render, and it keeps the OLDEST stamp, so the
   * warning describes the stalest thing on the screen. A later fresh response
   * must not clear it — that would mean one figure from this morning sits beside
   * a live one with no warning at all, which is the case that actually misleads
   * somebody.
   */
  function markStale(when) {
    const next = when || new Date().toISOString();
    if (staleSince && new Date(staleSince) <= new Date(next)) return;
    staleSince = next;
    emitFreshness();
  }

  /** Start of a render: the screen is about to re-fetch everything it needs, so
      it gets the benefit of the doubt until something comes back cached. */
  function resetFreshness() {
    if (staleSince === null) return;
    staleSince = null;
    emitFreshness();
  }

  TCA.staleSince = () => staleSince;
  TCA.onFreshness = (fn) => {
    staleListeners.push(fn);
    try { fn(staleSince); } catch (e) { /* ignore */ }
    return () => {
      const i = staleListeners.indexOf(fn);
      if (i >= 0) staleListeners.splice(i, 1);
    };
  };

  const store = createStore({
    user: (window.__BOOTSTRAP__ || {}).user,
    meta: (window.__BOOTSTRAP__ || {}).meta || {},
    offline: false,
  });
  TCA.store = store;

  class ApiError extends Error {
    constructor(message, status, body) { super(message); this.status = status; this.body = body; }
  }

  async function request(method, path, body, opts = {}) {
    const init = {
      method,
      headers: {
        Accept: 'application/json',
        'X-Requested-With': 'fetch',
        'X-CSRFToken': window.__CSRF__ || '',
      },
      credentials: 'same-origin',
    };
    // A queued write already has a key — reuse it on every replay, so the server
    // can recognise the repeat. A fresh write gets one only if it ends up queued.
    if (opts.idempotencyKey) init.headers['Idempotency-Key'] = opts.idempotencyKey;
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (err) {
      setConnectionState(false);
      /* The network refused it. For a write that means "not yet", not "no" — so
         it is parked with a key and replayed once the link is back. A read
         cannot be parked, and a queued item being replayed must not re-queue
         itself, hence `noQueue`. */
      if (WRITE_METHODS.has(method) && !opts.noQueue) {
        const key = opts.idempotencyKey || newKey();
        try {
          await outbox.add({
            method, url: path, body: body === undefined ? null : body,
            key, queued_at: Date.now(), attempts: 0, state: 'queued',
            label: opts.queueLabel || `${method} ${path}`,
          });
        } catch (e) {
          // No IndexedDB (private mode, or a browser refusing storage). Say so
          // rather than pretending the change was kept.
          if (!opts.silent) {
            TCA.toast('Could not save that change offline — it has been lost. '
              + 'Reconnect and try again.', 'danger');
          }
          throw new ApiError('Network error', 0, null);
        }
        announceOutbox();
        if (!opts.silent) {
          TCA.toast('Offline — that change is saved and will go through when the '
            + 'connection returns.', 'warning');
        }
        // Shaped like a normal response so a caller reading `.message` says the
        // right thing; `queued` is the flag a screen can check if it must.
        return { queued: true, message: 'Saved offline. It will sync automatically.' };
      }
      if (!opts.silent) TCA.toast('Network problem — check your connection.', 'danger');
      throw new ApiError('Network error', 0, null);
    }
    setConnectionState(true);
    /* The worker stamps a cached read. Anything unstamped is live, and a live
       read deliberately does NOT clear the warning — see markStale(). */
    if (res.headers.get('X-Served-From') === 'cache') {
      markStale(res.headers.get('X-Cached-At'));
    }
    if (res.status === 401 && !opts.silent) {
      window.location.href = '/login';
      throw new ApiError('Signed out', 401, null);
    }
    const text = await res.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch (e) { json = { raw: text }; }
    if (!res.ok) {
      const msg = (json && (json.message || json.error)) || `Request failed (${res.status})`;
      throw new ApiError(msg, res.status, json);
    }
    return json;
  }

  const api = {
    get: (p, o) => request('GET', p, undefined, o),
    post: (p, b, o) => request('POST', p, b === undefined ? {} : b, o),
    patch: (p, b, o) => request('PATCH', p, b === undefined ? {} : b, o),
    del: (p, o) => request('DELETE', p, undefined, o),
  };
  TCA.api = api;

  /* ── formatting helpers ──────────────────────────────────────────── */
  function money(value, currency) {
    const n = Number(value || 0);
    const s = n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return currency ? `${currency} ${s}` : s;
  }
  /* ── the workshop clock ──────────────────────────────────────────────
     Timestamps are stored in UTC and cross the API without a suffix, which a
     browser reads as *its own* local time — so a job checked in at 14:05 showed
     as 12:05. `parseStamp` marks a bare stamp as UTC, and every formatter then
     renders it in Harare, so the reading is right even on a laptop set to
     another timezone. Harare is the clock the paperwork, the end-of-day sheet
     and the customer's own message all agree on. */
  const TZ_NAME = 'Africa/Harare';

  function parseStamp(value) {
    if (!value) return null;
    if (value instanceof Date) return isNaN(value) ? null : value;
    const s = String(value);
    const bare = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s) && !/(Z|[+-]\d{2}:?\d{2})$/.test(s);
    const d = new Date(bare ? `${s}Z` : s);
    return isNaN(d) ? null : d;
  }

  /** The Harare calendar day of a stamp, as YYYY-MM-DD, for grouping and matching. */
  function dateKey(value) {
    const d = parseStamp(value);
    if (!d) return '';
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: TZ_NAME, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d);
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  }

  function dateShort(value) {
    if (!value) return '—';
    const d = parseStamp(value);
    if (!d) return String(value);
    return d.toLocaleDateString('en-GB',
      { timeZone: TZ_NAME, day: '2-digit', month: 'short', year: 'numeric' });
  }
  function dateTime(value) {
    if (!value) return '—';
    const d = parseStamp(value);
    if (!d) return String(value);
    return d.toLocaleString('en-GB',
      { timeZone: TZ_NAME, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function timeOnly(value) {
    const d = parseStamp(value);
    return d ? d.toLocaleTimeString('en-GB',
      { timeZone: TZ_NAME, hour: '2-digit', minute: '2-digit' }) : '';
  }
  function relTime(value) {
    const d = parseStamp(value);
    if (!d) return '';
    const diff = (Date.now() - d.getTime()) / 1000;
    if (isNaN(diff)) return '';
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return dateShort(value);
  }
  /** Today's Harare date as YYYY-MM-DD, for date filters and booking keys. */
  function today() { return dateKey(new Date()); }
  function src(obj, path) { return path.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), obj); }
  function debounce(fn, ms) {
    let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms || 300); };
  }

  /* ── Toasts ──────────────────────────────────────────────────────── */
  const TOAST_STYLE = {
    success: { icon: 'check-lg', label: 'Done' },
    danger: { icon: 'exclamation-octagon', label: 'Problem' },
    warning: { icon: 'exclamation-triangle', label: 'Heads up' },
    info: { icon: 'info-lg', label: 'For your information' },
    brand: { icon: 'bell', label: 'Update' },
  };

  /**
   * Show a toast.
   *
   * @param {string} message  body copy; `**bold**` is rendered
   * @param {string} variant  success | danger | warning | info | brand
   * @param {object|number} opts  { title, timeout, action: { label, run } } or a delay in ms
   */
  function toast(message, variant = 'success', opts = {}) {
    const host = document.getElementById('toastHost');
    if (!host) return null;

    const options = typeof opts === 'number' ? { timeout: opts } : (opts || {});
    const style = TOAST_STYLE[variant] || TOAST_STYLE.info;
    const timeout = options.timeout === undefined ? 4200 : options.timeout;

    const el = h(`div.toast-tc.toast-tc-${variant}`, { role: 'status' }, []);
    el.appendChild(h('span.toast-tc-icon', icon(style.icon)));
    el.appendChild(h('div.toast-tc-body', [
      h('div.toast-tc-title', options.title || style.label),
      h('div.toast-tc-msg', { html: String(message).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') }),
      options.action
        ? h('button.toast-tc-action', {
            type: 'button',
            onclick: () => { options.action.run(); dismiss(); },
          }, options.action.label)
        : null,
    ]));

    const closeBtn = h('button.toast-tc-close', {
      type: 'button', 'aria-label': 'Dismiss notification', onclick: () => dismiss(),
    }, icon('x-lg'));
    el.appendChild(closeBtn);

    let bar = null;
    if (timeout) {
      bar = h('div.toast-tc-bar', { style: `animation-duration:${timeout}ms` });
      el.appendChild(bar);
    }

    host.prepend(el);
    requestAnimationFrame(() => el.classList.add('show'));

    let timer = null;
    let dismissed = false;

    function startTimer() {
      if (!timeout || dismissed) return;
      timer = setTimeout(dismiss, timeout);
      if (bar) bar.style.animationPlayState = 'running';
    }
    function pauseTimer() {
      if (timer) clearTimeout(timer);
      if (bar) bar.style.animationPlayState = 'paused';
    }
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      if (timer) clearTimeout(timer);
      el.classList.remove('show');
      el.classList.add('hide');
      setTimeout(() => el.remove(), 260);
    }

    el.addEventListener('mouseenter', pauseTimer);
    el.addEventListener('mouseleave', startTimer);
    el.addEventListener('focusin', pauseTimer);
    el.addEventListener('focusout', startTimer);

    startTimer();
    // Keep at most 4 on screen so a burst of updates cannot cover the UI.
    Array.from(host.children).slice(4).forEach((old) => {
      old.classList.remove('show');
      setTimeout(() => old.remove(), 260);
    });
    return { dismiss, el };
  }
  TCA.toast = toast;

  /* ── Modals ──────────────────────────────────────────────────────── */
  const SIZES = { sm: 'modal-sm', md: 'modal-md', lg: 'modal-lg', xl: 'modal-xl', full: 'modal-full' };
  const ACCENTS = {
    brand: 'text-bg-brand', danger: 'text-bg-danger', warning: 'text-bg-warning',
    success: 'text-bg-success', info: 'text-bg-info', primary: 'text-bg-primary',
  };

  function modalNode() {
    const el = document.getElementById('tcaModal');
    return { el, instance: bootstrap.Modal.getOrCreateInstance(el, { backdrop: true, keyboard: true }) };
  }

  /**
   * Open the global modal.
   *
   * Every dialog reuses one `#tcaModal` element, so when one closes and another
   * opens straight away (invoice → record payment) the closing animation would
   * otherwise hide the newly opened dialog. A token makes the newest request
   * win and defers the reveal until the outgoing dialog has finished hiding.
   *
   * @param {object} options
   * @param {string} options.title
   * @param {Node}   options.body
   * @param {Node[]} options.footer
   * @param {string} options.size    sm | md | lg | xl | full
   * @param {string} options.icon    bootstrap icon name for the header well
   * @param {string} options.accent  brand | danger | warning | success | info | primary
   * @param {string} options.subtitle
   */
  let modalToken = 0;

  function modal({ title, body, footer, size, icon: iconName, accent, subtitle }) {
    const { el, instance } = modalNode();

    el.querySelector('.modal-dialog').className =
      `modal-dialog modal-dialog-centered modal-dialog-scrollable ${SIZES[size] || SIZES.lg}`;
    el.querySelector('.modal-title').textContent = title || '';

    const subtitleEl = el.querySelector('#tcaModalSubtitle');
    subtitleEl.textContent = subtitle || '';
    subtitleEl.hidden = !subtitle;

    const iconEl = el.querySelector('#tcaModalIcon');
    if (iconName) {
      iconEl.className = `tc-modal-icon ${ACCENTS[accent] || 'text-bg-brand'}`;
      iconEl.innerHTML = '';
      appendChild(iconEl, icon(iconName));
      iconEl.hidden = false;
    } else {
      iconEl.hidden = true;
    }

    const bodyEl = el.querySelector('.modal-body');
    bodyEl.scrollTop = 0;
    mount(bodyEl, body);

    const footerEl = el.querySelector('.modal-footer');
    footerEl.innerHTML = '';
    if (footer && footer.length) {
      appendChild(footerEl, footer);
    } else {
      footerEl.appendChild(h('button.btn.btn-outline-secondary.btn-sm', {
        type: 'button', 'data-bs-dismiss': 'modal',
      }, 'Close'));
    }

    const token = ++modalToken;
    const reveal = () => { if (token === modalToken) instance.show(); };

    // Mid-hide: wait for the outgoing dialog so it cannot hide us on the way out.
    const midHide = instance._isShown === false && instance._isTransitioning === true;
    if (midHide) {
      el.addEventListener('hidden.bs.modal', reveal, { once: true });
    } else {
      reveal();
    }

    return { el, instance, close: () => instance.hide() };
  }

  function closeModal() {
    const el = document.getElementById('tcaModal');
    if (el) bootstrap.Modal.getOrCreateInstance(el).hide();
  }

  /**
   * Confirmation dialog with an icon well and colour-coded action button.
   * Resolves true when confirmed, false otherwise.
   *
   * opts.subtitle  override the caption under the title. When omitted, a
   *                warning is shown only for destructive variants so that
   *                benign actions ("send", "notify") do not look dangerous.
   */
  function confirmDialog({ title, message, confirmLabel = 'Confirm', variant = 'danger',
                           detail, icon: iconName, subtitle }) {
    const warning = subtitle !== undefined
      ? subtitle
      : (['danger', 'warning'].includes(variant) ? 'This action cannot be undone.' : null);
    return new Promise((resolve) => {
      const btn = h(`button.btn.btn-${variant}.btn-sm`, { type: 'button' }, confirmLabel);
      const m = modal({
        title: title || 'Are you sure?',
        subtitle: warning,
        size: 'sm',
        icon: iconName || (variant === 'danger' ? 'exclamation-triangle' : 'patch-question'),
        accent: variant,
        body: h('div', [
          h('p.mb-0', message),
          detail ? h('div.small.text-secondary.mt-2', detail) : null,
        ]),
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm', {
            type: 'button', 'data-bs-dismiss': 'modal',
          }, 'Cancel'),
          btn,
        ],
      });
      let answered = false;
      btn.addEventListener('click', () => { answered = true; m.close(); resolve(true); });
      btn.focus();
      m.el.addEventListener('hidden.bs.modal', () => { if (!answered) resolve(false); }, { once: true });
    });
  }

  /**
   * A select with an "Other…" escape hatch.
   *
   * Picking "Other" swaps in a free-text input, so an unlisted make or colour
   * is never a dead end. A hidden input carries the value, which keeps
   * `FormData` seeing exactly one field with the expected name.
   *
   * @returns {{node: Node, read: Function, select: Node, setOptions: Function}}
   */
  function comboField({ name, options = [], value = '', placeholder, otherLabel,
                        small = true, disabled = false, swatch = null, onChange,
                        chooseLabel = '— Choose —' } = {}) {
    const hidden = h('input', { type: 'hidden', name, value: value || '' });
    const free = h(`input.form-control${small ? '.form-control-sm' : ''}`, {
      hidden: true, 'aria-label': `${name} (other)`,
      placeholder: placeholder || 'Type it in',
    });
    const dot = swatch ? h('span.tc-swatch') : null;

    const paint = () => {
      if (!dot) return;
      const key = hidden.value;
      const found = (options || []).find((o) => String(o.value ?? o) === String(key));
      dot.style.background = (found && found.swatch) || '#e5ebf5';
      dot.style.visibility = found ? 'visible' : 'hidden';
    };

    const select = h(`select.form-select${small ? '.form-select-sm' : ''}`, {
      disabled,
      'aria-label': name,
      onchange: (e) => {
        if (e.target.value === '__other__') {
          free.hidden = false;
          free.value = '';
          hidden.value = '';
          free.focus();
        } else {
          free.hidden = true;
          free.value = '';
          hidden.value = e.target.value;
        }
        paint();
        if (onChange) onChange(hidden.value);
      },
    }, buildOptions(options, value, otherLabel, chooseLabel));

    free.addEventListener('input', () => {
      hidden.value = free.value;
      if (onChange) onChange(hidden.value);
    });

    const picker = searchableSelect(select, {
      placeholder: swatch ? 'Search colour…' : 'Search…',
      ariaLabel: `Search ${name}`,
      lead: dot,
    });
    const node = h('div.tc-combo', [picker.node, free, hidden]);
    paint();

    return {
      node,
      select,
      read: () => hidden.value,
      /** Swap the option list (used for a Make → Model cascade). */
      setOptions(list, { keep = false } = {}) {
        const current = keep ? hidden.value : '';
        select.innerHTML = '';
        appendChild(select, buildOptions(list, current, otherLabel, chooseLabel));
        /* The select now holds the full new list, so re-read it before filtering. */
        if (picker.search.value) picker.search.value = '';
        picker.resync();
        if (current && !select.value) { free.hidden = false; free.value = current; hidden.value = current; }
        paint();
      },
    };
  }

  function buildOptions(options, value, otherLabel, chooseLabel) {
    const nodes = [h('option', { value: '' }, chooseLabel)];
    (options || []).forEach((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      nodes.push(h('option', {
        value: opt.value, selected: String(opt.value) === String(value),
      }, opt.label ?? opt.value));
    });
    nodes.push(h('option', { value: '__other__' }, otherLabel || 'Other (type it in)'));
    return nodes;
  }

  /* ── searchable dropdowns ────────────────────────────────────────────
     A real combobox: the visible control is a text box, and typing filters the
     option list *in the list itself*. An earlier version put a filter row above
     a native <select>, which silently did nothing on a phone — a native select
     opens the OS picker, where an inline filter cannot reach. Here the options
     are ordinary buttons, so search works with a finger, a mouse or a keyboard.

     The native <select> is kept in the form but hidden: it stays the single
     source of value, so `name`, `FormData`, `formValue()` and the required-field
     validation all keep working untouched. Picking dispatches a real `change`
     event on it, which is what the intake's customer handler, the Make → Model
     cascade and the form's error-clearing all listen for.
     ─────────────────────────────────────────────────────────────────── */

  /** Read a <select> into plain specs, keeping any <optgroup> structure. */
  function readSelectSpecs(select) {
    const groups = [];
    let current = null;
    Array.from(select.children).forEach((child) => {
      if (child.tagName === 'OPTGROUP') {
        current = { label: child.label, options: [] };
        groups.push(current);
      } else if (child.tagName === 'OPTION') {
        if (!current) { current = { label: null, options: [] }; groups.push(current); }
        current.options.push({
          value: child.value, label: child.textContent, disabled: !!child.disabled,
        });
      }
    });
    return groups;
  }

  /**
   * Turn a <select> into a searchable combobox.
   *
   * @returns {{node: Node, search: Node, inputId: string, resync: Function}}
   */
  function searchableSelect(select, { placeholder = 'Search…', ariaLabel, lead } = {}) {
    let groups = readSelectSpecs(select);

    /* Value carrier only. `display:none` still submits, and still reads through
       formValue(), so nothing downstream has to know this is a combobox. */
    select.classList.add('d-none');
    select.setAttribute('tabindex', '-1');

    const flat = () => groups.flatMap((g) => g.options);
    const labelOf = (value) => {
      const found = flat().find((o) => String(o.value) === String(value));
      return found ? found.label : '';
    };

    const input = h('input.form-control.form-control-sm.tc-cbo-input', {
      type: 'text', autocomplete: 'off', spellcheck: 'false',
      role: 'combobox', 'aria-expanded': 'false', 'aria-autocomplete': 'list',
      'aria-label': ariaLabel || 'Choose an option',
      placeholder,
    });
    if (select.id) input.id = `${select.id}__combo`;

    const list = h('div.tc-cbo-list', { hidden: true, role: 'listbox' });
    const node = h(`div.tc-cbo${lead ? '.has-lead' : ''}`, [
      h('div.tc-cbo-control', [lead || null, input,
        h('i.bi.bi-chevron-expand.tc-cbo-caret')]),
      list,
      select,
    ]);

    let items = [];
    let active = -1;

    const showLabel = () => { input.value = labelOf(select.value); };

    function highlight(index) {
      items.forEach((it) => it.el.classList.remove('is-active'));
      if (index < 0 || index >= items.length) return;
      items[index].el.classList.add('is-active');
      items[index].el.scrollIntoView({ block: 'nearest' });
    }

    function render(term) {
      const needle = (term || '').trim().toLowerCase();
      const matches = (o) => !needle
        || String(o.label).toLowerCase().includes(needle)
        || String(o.value).toLowerCase().includes(needle);
      list.innerHTML = '';
      items = [];

      groups.forEach((group) => {
        const opts = group.options.filter(matches);
        if (!opts.length) return;
        if (group.label) list.appendChild(h('div.tc-cbo-group', group.label));
        opts.forEach((o) => {
          const chosen = String(o.value) === String(select.value);
          const el = h('button.tc-cbo-opt', {
            type: 'button', role: 'option', disabled: o.disabled,
            'aria-selected': chosen ? 'true' : 'false',
            class: chosen ? 'is-selected' : null,
          }, o.label);
          /* Keep focus on the input so the blur-close does not fire mid-pick. */
          el.addEventListener('mousedown', (e) => e.preventDefault());
          el.addEventListener('click', () => pick(o.value));
          list.appendChild(el);
          items.push({ value: o.value, disabled: o.disabled, el });
        });
      });

      if (!items.length) list.appendChild(h('div.tc-cbo-empty', 'No match'));
      active = items.findIndex((it) => String(it.value) === String(select.value));
      highlight(active);
    }

    function open() {
      if (!list.hidden) return;
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      render('');
      input.select();
    }

    function close() {
      if (list.hidden) return;
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      showLabel();
    }

    function pick(value) {
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      close();
    }

    input.addEventListener('focus', open);
    input.addEventListener('click', open);
    input.addEventListener('input', () => { if (list.hidden) open(); render(input.value); });
    /* A blur can land after a click on an option, so let the click win first. */
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { close(); input.blur(); return; }
      if (e.key === 'Enter') {
        /* Enter belongs to the dropdown, not to the surrounding form. */
        e.preventDefault();
        if (!list.hidden && items[active]) pick(items[active].value);
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) { open(); return; }
        const step = e.key === 'ArrowDown' ? 1 : -1;
        let next = active + step;
        while (next >= 0 && next < items.length && items[next].disabled) next += step;
        if (next >= 0 && next < items.length) { active = next; highlight(next); }
      }
    });

    /* A value set from elsewhere (a cascade rebuilding the list) must not leave
       the panel open over stale options. */
    select.addEventListener('change', () => { if (!list.hidden) close(); });

    showLabel();
    return {
      node,
      search: input,
      inputId: input.id,
      /** Re-read the option list after the caller has rebuilt it. */
      resync() {
        groups = readSelectSpecs(select);
        if (!list.hidden) render('');
        showLabel();
      },
    };
  }

  /**
   * A phone number: a country-code picker beside the national part.
   *
   * The desk types the number the way it is written on a job card ("077 555
   * 0555") and the code comes from the dropdown, so what is stored — and what
   * the WhatsApp bot dials — is always a full international number. Before
   * this, a number saved as "0775550555" reached the API as-is and could not be
   * dialled at all: the message was rejected and the desk saw only silence.
   *
   * The codes come from /api/meta, so the dropdown and the bot's dialling rule
   * are the same list, read from the same config on the server.
   */
  function telField({ name, value, placeholder, disabled, id, onInput }) {
    const meta = TCA.store.get('meta') || {};
    const countries = (meta.countries || []).length
      ? meta.countries
      : [{ code: '263', iso: 'ZW', name: 'Zimbabwe' }];
    const fallback = meta.default_country_code || countries[0].code;
    const parts = splitPhoneNumber(value, countries, fallback);

    const select = h('select.form-select.form-select-sm.tc-tel-cc', {
      /* Named only when the caller asked for it: a hand-built form reads this
         control with `.read()` instead, and a stray `...__cc` key would post a
         country code as though it were a field of its own. */
      name: name ? `${name}__cc` : undefined,
      disabled: !!disabled, 'aria-label': 'Country code',
      onchange: () => onInput && onInput(),
    }, countries.map((c) => h('option', {
      value: c.code, selected: c.code === parts.country_code,
      title: `${c.name} +${c.code}`,
    }, `+${c.code}`)));

    const number = h('input.form-control.form-control-sm', {
      id, name: name || undefined, type: 'tel', value: parts.national,
      disabled: !!disabled, placeholder: placeholder || '77 000 0000',
      inputmode: 'tel', autocomplete: 'tel-national',
      oninput: () => onInput && onInput(),
    });

    return {
      node: h('div.input-group.input-group-sm.tc-tel', [select, number]),
      select, number,
      focus: () => number.focus(),
      /* One value, always international: the API and the bot both want the number
         they can dial, not two fields they have to know how to join. */
      read() {
        const national = number.value.replace(/\D/g, '').replace(/^0+/, '');
        return national ? `+${select.value}${national}` : '';
      },
      /* Fill from a stored number, splitting it back across the two controls so
         that `read()` returns exactly what went in. */
      set(value) {
        const stored = splitPhoneNumber(value, countries, fallback);
        select.value = stored.country_code;
        number.value = stored.national;
      },
    };
  }

  /** Take a stored number apart so the dropdown and box show the right things. */
  function splitPhoneNumber(value, countries, fallback) {
    const raw = String(value == null ? '' : value).trim();
    const digits = raw.replace(/\D/g, '');
    if (!digits) return { country_code: fallback, national: '' };

    const explicit = raw.startsWith('+') || raw.startsWith('00');
    const body = raw.startsWith('00') ? digits.slice(2) : digits;
    /* Longest code first: 263 must be tried before 26, or Zimbabwe becomes
       South Africa and the number gains a digit. */
    const codes = countries.map((c) => c.code).sort((a, b) => b.length - a.length);

    if (explicit) {
      for (const code of codes) {
        if (body.startsWith(code)) {
          return { country_code: code, national: body.slice(code.length) };
        }
      }
      return { country_code: fallback, national: body };
    }
    if (body.startsWith('0')) {
      return { country_code: fallback, national: body.replace(/^0+/, '') };
    }
    for (const code of codes) {
      if (body.startsWith(code) && body.length - code.length >= 5) {
        return { country_code: code, national: body.slice(code.length) };
      }
    }
    return { country_code: fallback, national: body };
  }

  /** Standard helpers for the vehicle reference lists served by /api/meta. */
  function vehicleOptions() {
    const meta = TCA.store.get('meta') || {};
    return {
      makes: (meta.vehicle_makes || []).map((m) => ({ value: m, label: m })),
      modelsFor: (make) => {
        const table = meta.vehicle_models || {};
        const list = table[make];
        const source = list && list.length ? list : (meta.vehicle_models_common || []);
        return source.map((m) => ({ value: m, label: m }));
      },
      colours: (meta.vehicle_colours || []).map((c) => ({
        value: c.value, label: c.value, swatch: c.swatch,
      })),
    };
  }

  /**
   * Build a form inside a modal.
   *
   * fields: [{
   *   name, label, type, options, required, value, placeholder, col, help,
   *   icon, affix, min, max, step, rows, disabled, hint
   * }]
   *
   * type — text · email · tel · url · number · money · password · date · time
   *        search · textarea · select · combo · checkbox · switch · radio
   *        segmented · options · static
   *
   * `tel` — a country-code picker beside the national number, submitted as one
   *         international string (`+263775550555`). The codes come from
   *         /api/meta, so the dropdown and the bot's dialling rule cannot drift.
   *
   * `optgroups` — for a grouped (native) select.
   * `combo`     — a select with an "Other…" escape hatch, plus an optional
   *               `swatch` colour dot. `onComboChange(value, name)` fires on
   *               every change, which is how Make → Model cascades.
   */
  function formModal({ title, fields, values = {}, submitLabel = 'Save', size, intro,
                       icon: headerIcon, accent, validate }) {
    return new Promise((resolve) => {
      const refs = {};
      const gates = {};
      const combos = {};
      /* Handed to `onComboChange` so one combo can repopulate another. */
      const comboApi = {
        setOptions: (name, list) => { if (combos[name]) combos[name].setOptions(list); },
        get: (name) => (combos[name] ? combos[name].read() : ''),
      };
      const initial = (f) => (values[f.name] !== undefined ? values[f.name]
        : (f.value !== undefined ? f.value : ''));
      const optionList = (f) => (f.options || []).map(
        (o) => (typeof o === 'string' ? { value: o, label: o } : o));
      /* Labels may arrive as "Name *" from older call sites; the required
         marker is rendered as its own element so it can be styled. */
      const labelText = (f) => String(f.label || f.name || '').replace(/\s*\*\s*$/, '');

      /** Wrap a control with its label, hint and inline error text. */
      function shell(f, control, { labelFor, bare } = {}) {
        const required = !!f.required;
        const hint = f.help || f.hint;
        const field = h('div.tc-field', [
          f.label && !bare
            ? h('label.tc-label', { for: labelFor || null }, labelText(f),
                required ? h('span.req', { title: 'Required' }, '*') : null)
            : null,
          control,
          hint && !bare ? h('div.tc-hint', hint) : null,
          h('div.tc-error', [h('i.bi.bi-exclamation-circle'), h('span.err-text')]),
        ]);
        const slot = h(`div.col-${f.col || 6}`, field);
        gates[f.name] = {
          field,
          slot,
          fail(message) {
            field.classList.add('has-error');
            field.querySelector('.err-text').textContent =
              message || `${labelText(f) || 'This field'} is required.`;
          },
          clear() { field.classList.remove('has-error'); },
        };
        return slot;
      }

      const rows = fields.map((f) => {
        const start = initial(f);
        const common = {
          id: `f_${f.name}`, name: f.name, disabled: !!f.disabled,
          placeholder: f.placeholder || '',
          'aria-required': f.required ? 'true' : undefined,
        };
        /* A leading icon and/or a trailing affix ("USD", "hrs") shares a wrapper. */
        const iconWrap = (input) => {
          if (!f.icon && !f.affix) return input;
          return h('div.tc-input-icon', {
            class: [f.icon ? 'has-icon' : null, f.affix ? 'has-affix' : null,
                    f.affixAlign === 'left' ? 'affix-left' : null].filter(Boolean).join(' '),
          }, [
            f.icon ? h(`i.bi.bi-${f.icon}`) : null,
            f.affix ? h('span.tc-affix', f.affix) : null,
            input,
          ]);
        };

        /* — readonly presentation — */
        if (f.type === 'static') {
          refs[f.name] = { value: start };
          return shell(f, h('div.form-control-plaintext.py-0.fw-semibold',
            { style: 'min-height:auto' }, start === '' || start == null ? '—' : String(start)));
        }

        /* — switch: a full-width settings row — */
        if (f.type === 'switch') {
          const input = h('input.form-check-input', { ...common, type: 'checkbox', checked: !!start });
          refs[f.name] = input;
          return shell(f, h('label.tc-switch-row.form-switch', { for: common.id }, [
            h('div.tc-switch-text', [
              h('div.tc-switch-title', labelText(f)),
              f.help ? h('div.tc-switch-sub', f.help) : null,
            ]),
            input,
          ]), { bare: true });
        }

        /* — checkbox — */
        if (f.type === 'checkbox') {
          const input = h('input.form-check-input', { ...common, type: 'checkbox', checked: !!start });
          refs[f.name] = input;
          return shell(f, h('div.form-check.pt-1', [
            input,
            h('label.form-check-label.small.fw-semibold', { for: common.id }, labelText(f)),
          ]), { bare: true });
        }

        /* — option cards with real radios underneath — */
        if (f.type === 'options' || f.type === 'radio') {
          const opts = optionList(f);
          const cards = opts.map((o) => {
            const radio = h('input.form-check-input', {
              id: `f_${f.name}_${o.value}`, name: f.name, type: 'radio',
              value: o.value, checked: String(o.value) === String(start), disabled: !!f.disabled,
            });
            refs[f.name] = refs[f.name] || radio;
            const card = h('label.tc-option', {
              class: String(o.value) === String(start) ? 'is-checked' : null,
              for: `f_${f.name}_${o.value}`,
            }, [
              f.type === 'radio' ? null : radio,
              h('div.tc-option-body', [
                h('div.tc-option-title', o.label),
                o.description ? h('div.tc-option-desc', o.description) : null,
              ]),
              f.type === 'options' ? radio : null,
              o.badge ? h(`span.badge.text-bg-${o.badge.tone || 'secondary'}`, o.badge.text) : null,
            ]);
            radio.addEventListener('change', () => {
              cards.forEach((c) => c.classList.remove('is-checked'));
              card.classList.add('is-checked');
            });
            return card;
          });
          return shell(f, h('div.tc-option-list', cards), { bare: true });
        }

        /* — checkboxes as a group: "tick everything that applies" — */
        if (f.type === 'checks') {
          const picked = (Array.isArray(start) ? start : [start]).map(String).filter(Boolean);
          let firstBox = null;
          const cards = optionList(f).map((o) => {
            const box = h('input.form-check-input', {
              id: `f_${f.name}_${o.value}`, name: f.name, type: 'checkbox',
              value: o.value, checked: picked.includes(String(o.value)),
              disabled: !!f.disabled,
            });
            if (!firstBox) firstBox = box;
            const card = h('label.tc-option', {
              class: picked.includes(String(o.value)) ? 'is-checked' : null,
              for: `f_${f.name}_${o.value}`,
            }, [
              h('div.tc-option-body', [
                h('div.tc-option-title', o.label),
                o.description ? h('div.tc-option-desc', o.description) : null,
              ]),
              box,
            ]);
            /* The tick and the card have to agree: `.tc-option` is styled from
               `is-checked`, so a card that misses this reads as unpicked while the
               value it posts says otherwise. */
            box.addEventListener('change', () => card.classList.toggle('is-checked', box.checked));
            return card;
          });
          /* A composite control registers a wrapper, so the required-field
             handling resolves to the first tick rather than the group. */
          refs[f.name] = { el: firstBox };
          return shell(f, h('div.tc-option-list', cards));
        }

        /* — segmented control (still posts a real value) — */
        if (f.type === 'segmented') {
          const hidden = h('input', { type: 'hidden', name: f.name, value: start });
          refs[f.name] = hidden;
          const buttons = optionList(f).map((o) => {
            const btn = h('button', {
              type: 'button', value: o.value,
              class: String(o.value) === String(start) ? 'is-active' : null,
              onclick: () => {
                buttons.forEach((b) => b.classList.remove('is-active'));
                btn.classList.add('is-active');
                hidden.value = o.value;
              },
            }, o.icon ? [h(`i.bi.bi-${o.icon}`), ` ${o.label}`] : o.label);
            return btn;
          });
          return shell(f, h('div.tc-segmented', { role: 'group' }, buttons));
        }

        /* — textarea — */
        if (f.type === 'textarea') {
          const input = h('textarea.form-control.form-control-sm', { ...common, rows: f.rows || 3 }, start);
          refs[f.name] = input;
          return shell(f, input, { labelFor: common.id });
        }

        /* — select with an "Other…" escape hatch — */
        if (f.type === 'combo') {
          const combo = comboField({
            name: f.name,
            options: optionList(f),
            value: start,
            placeholder: f.otherPlaceholder || f.placeholder,
            otherLabel: f.otherLabel,
            disabled: f.disabled,
            swatch: !!f.swatch,
            chooseLabel: f.chooseLabel,
            onChange: (v) => f.onComboChange && f.onComboChange(v, f.name, comboApi),
          });
          refs[f.name] = { value: start, combo, el: combo.select, focus: () => combo.select.focus() };
          combos[f.name] = combo;
          return shell(f, combo.node, { labelFor: null });
        }

        /* — select (flat or grouped) — */
        if (f.type === 'select') {
          const groups = f.optgroups
            ? f.optgroups.map((g) => h('optgroup', { label: g.label },
                optionList(g).map((o) => h('option', {
                  value: o.value, selected: String(o.value) === String(start) }, o.label))))
            : null;
          const input = h('select.form-select.form-select-sm', common,
            f.placeholder ? h('option', { value: '' }, f.placeholder) : null,
            groups || optionList(f).map((o) => h('option', {
              value: o.value, selected: String(o.value) === String(start) }, o.label)));
          refs[f.name] = input;
          const picker = searchableSelect(input, {
            placeholder: f.searchPlaceholder || 'Search…',
            ariaLabel: `Search ${labelText(f)}`,
          });
          return shell(f, iconWrap(picker.node), { labelFor: picker.inputId || common.id });
        }

        /* — phone: country code + national number, posted as one value — */
        if (f.type === 'tel') {
          const tel = telField({
            name: f.name, value: start, placeholder: f.placeholder,
            disabled: f.disabled, id: common.id,
            onInput: () => gates[f.name] && gates[f.name].clear(),
          });
          refs[f.name] = { value: start, tel, el: tel.number, focus: () => tel.focus() };
          return shell(f, tel.node, { labelFor: common.id });
        }

        /* — everything else is an <input> — */
        const isNumber = f.type === 'number' || f.type === 'money';
        const input = h('input.form-control.form-control-sm', {
          ...common,
          type: isNumber ? 'number' : (f.type || 'text'),
          value: start,
          min: f.min, max: f.max, step: f.step || (isNumber ? '0.01' : undefined),
          inputmode: isNumber ? 'decimal' : undefined,
          class: isNumber ? 'form-control form-control-sm.no-spin' : null,
        });
        refs[f.name] = input;
        return shell(f, iconWrap(input), { labelFor: common.id });
      });

      const formId = `tca-form-${Math.random().toString(36).slice(2, 9)}`;
      const form = h('form.row.g-3', { id: formId, novalidate: true }, rows);
      const submit = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'submit' },
        [h('i.bi.bi-check2.me-1'), submitLabel]);
      /* The footer sits outside the <form>, so a submit button there is orphaned
         and clicking it does nothing at all — which made every formModal in the
         app quietly unsubmittable. `form` is a read-only property on
         HTMLButtonElement, so it has to be set as an attribute rather than
         through h()'s property assignment. */
      submit.setAttribute('form', formId);

      const m = modal({
        title,
        subtitle: intro,
        icon: headerIcon || 'pencil-square',
        accent: accent || 'brand',
        size: size || 'lg',
        body: form,
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm', { type: 'button', 'data-bs-dismiss': 'modal' }, 'Cancel'),
          submit,
        ],
      });

      /* Clear a field's error the moment the operator fixes it. */
      Object.entries(refs).forEach(([name, el]) => {
        const gate = gates[name];
        if (!gate) return;
        if (el && el.combo) {
          el.combo.node.addEventListener('change', () => gate.clear());
          el.combo.node.addEventListener('input', () => gate.clear());
          return;
        }
        if (!el || !el.addEventListener) return;
        const evt = el.type === 'checkbox' || el.type === 'radio' || el.tagName === 'SELECT'
          ? 'change' : 'input';
        el.addEventListener(evt, () => gate.clear());
      });

      let answered = false;
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const out = {};
        let firstBad = null;
        let ok = true;

        fields.forEach((f) => {
          const el = refs[f.name];
          let v;
          if (f.type === 'static') v = el.value;
          else if (f.type === 'combo') v = el.combo.read().trim();
          else if (f.type === 'tel') v = el.tel.read();
          else if (f.type === 'switch' || f.type === 'checkbox') v = el.checked;
          else if (f.type === 'options' || f.type === 'radio') {
            v = form.querySelector(`[name="${f.name}"]:checked`)?.value ?? '';
          } else if (f.type === 'number' || f.type === 'money') {
            v = el.value === '' ? null : Number(el.value);
          } else if (f.type === 'checks') {
            /* The group posts a *list*, and none ticked is an empty one — which
               the required check below has to treat as missing, not as filled. */
            v = Array.from(form.querySelectorAll(`input[name="${f.name}"]:checked`))
              .map((box) => box.value);
          } else v = el.value.trim();

          const gate = gates[f.name];
          if (f.required && (v === '' || v === null || v === undefined
                             || (Array.isArray(v) && !v.length))) {
            gate.fail(f.requiredMessage);
            if (!firstBad) firstBad = el;
            ok = false;
          } else {
            gate.clear();
          }
          out[f.name] = v;
        });

        /* Cross-field rules (matching passwords, a date after another…).
           Running here rather than after the dialog closes keeps the
           operator's typing on screen when something is wrong. */
        if (typeof validate === 'function') {
          const errors = validate(out) || {};
          Object.entries(errors).forEach(([name, message]) => {
            if (!message) return;
            if (gates[name]) gates[name].fail(message);
            if (!firstBad) firstBad = refs[name];
            ok = false;
          });
        }

        if (!ok) {
          toast('Please complete the highlighted fields.', 'warning');
          /* A composite control (combo, phone) registers a wrapper rather than the
             element, so resolve to something focusable before asking the DOM about
             it — `firstBad.classList` used to throw for a required combo. */
          const el = firstBad && firstBad.el ? firstBad.el : firstBad;
          const target = el && el.type !== 'hidden'
            && !el.classList.contains('d-none')
            ? el
            : el?.closest('.tc-field')
                ?.querySelector('input:not([type=hidden]),select:not(.d-none),textarea,button');
          target?.focus();
          return;
        }
        answered = true;
        m.close();
        resolve(out);
      });

      m.el.addEventListener('hidden.bs.modal', () => { if (!answered) resolve(null); }, { once: true });
      setTimeout(() => {
        const first = form.querySelector('input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled)');
        if (first) first.focus();
      }, 380);
    });
  }

  function badge(text, colour) { return h(`span.badge.text-bg-${colour || 'secondary'}`, text); }
  function stageBadge(stage, label, colour) {
    const c = colour || (TCA.stageColours || {})[stage] || 'secondary';
    return h(`span.badge.text-bg-${c}`, label || stage);
  }
  function priorityBadge(priority) {
    const c = { LOW: 'secondary', NORMAL: 'info', HIGH: 'warning', URGENT: 'danger' }[priority] || 'secondary';
    return h(`span.badge.text-bg-${c}`, priority);
  }
  /* What a car is in for, as one line.
     A card can be in for more than one service — a panel repair that also wants a
     valet — and every screen that names the service has to name all of them: a
     card showing only its leading line looks like the second one was never asked
     for. A card written before this was possible falls back to its single line. */
  function serviceText(job, fallback) {
    const names = (job && job.services && job.services.length)
      ? job.services
      : [(job && job.service) || ''];
    const text = names.filter(Boolean).join(' + ');
    return text || (fallback === undefined ? '' : fallback);
  }
  function emptyState(title, subtitle, iconName) {
    return h('div.empty-state', [h('div', icon(iconName || 'inbox')),
      h('div.fw-semibold.text-secondary', title),
      subtitle ? h('div.small', subtitle) : null]);
  }
  function skeletonTable(rows = 6, cols = 5) {
    return h('div.p-3', Array.from({ length: rows }, () =>
      h('div.d-flex.gap-3.mb-3', Array.from({ length: cols }, () =>
        h('div.skeleton.flex-fill', { style: 'height:16px' })))));
  }
  function spinner(text) {
    return h('div.d-flex.align-items-center.gap-2.text-secondary.p-4',
      h('div.spinner-border.spinner-border-sm.text-warning', { role: 'status' }), text || 'Loading…');
  }

  /** The standard list-screen filter: a search box with a leading icon. */
  function searchInput({ placeholder, oninput, value = '', width = 300 } = {}) {
    return h('div.tc-input-icon.has-icon',
      { style: `flex:1 1 ${width}px;max-width:${width + 60}px` },
      h('i.bi.bi-search'),
      h('input.form-control.form-control-sm', {
        type: 'search', placeholder, value, oninput, 'aria-label': placeholder || 'Search',
      }));
  }

  /** A filter select with its own icon well, for toolbars. */
  function iconSelect({ options = [], value = '', onChange, icon = 'funnel',
                        width = 160, ariaLabel } = {}) {
    const select = h('select.form-select.form-select-sm', {
      'aria-label': ariaLabel || undefined,
      onchange: (e) => onChange && onChange(e.target.value),
    }, options.map((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      return h('option', { value: opt.value, selected: String(opt.value) === String(value) }, opt.label);
    }));
    const picker = searchableSelect(select, {
      placeholder: ariaLabel || 'Any',
      ariaLabel,
      lead: h(`i.bi.bi-${icon}.tc-cbo-lead`),
    });
    picker.search.style.minWidth = `${width}px`;
    return picker.node;
  }
  function dataTable({ columns, rows, onRowClick, empty, rowLabel }) {
    if (!rows || !rows.length) return empty || emptyState('Nothing here yet');
    return h('div.table-responsive',
      h('table.table.table-tc.table-hover.align-middle.mb-0',
        h('thead', h('tr', columns.map((c) => h('th', { class: c.class || '', scope: 'col' }, c.label)))),
        h('tbody', rows.map((row) => {
          const clickable = !!onRowClick;
          const tr = h('tr', {
            class: clickable ? null : 'row-static',
            role: clickable ? 'button' : null,
            'aria-label': clickable && rowLabel ? rowLabel(row) : null,
            style: clickable ? 'cursor:pointer' : null,
            onclick: clickable ? () => onRowClick(row) : null,
            onkeydown: clickable ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(row); }
            } : null,
          }, columns.map((c) => h('td', { class: c.class || '' }, c.render(row))));

          /* The attribute, not the property, because the DOM spells it `tabIndex`:
             a props key of `tabindex` would be assigned on as a plain JS property
             of that name — no exception, no attribute — leaving a row that says it
             is a button, has an Enter handler, and no keyboard that can reach it.
             Same silent failure as the `aria-*` case above, one letter apart. */
          if (clickable) tr.setAttribute('tabindex', '0');
          return tr;
        }))));
  }

  /**
   * Open a document in a modal that is laid out for paper and print it.
   * Used for job cards and invoices so the shop can hand a customer a real
   * printout instead of a screenshot.
   */
  function printDoc({ title, node, size = 'xl' }) {
    return modal({
      title,
      size,
      body: h('div.print-doc', node),
      footer: [
        h('button.btn.btn-outline-secondary.btn-sm.no-print', {
          type: 'button', 'data-bs-dismiss': 'modal' },
          'Close'),
        h('button.btn.btn-brand.btn-sm.no-print', {
          type: 'button',
          onclick: () => setTimeout(() => window.print(), 150),
        }, icon('printer'), ' Print'),
      ],
    });
  }

  /** Standard letterhead for any printed document. */
  function printHeader(docTitle, metaLines) {
    const company = (window.__BOOTSTRAP__ && window.__BOOTSTRAP__.company) || {};
    return h('div.print-head', [
      h('div', [
        h('h1', company.name || 'Topclass Auto Body'),
        h('div.small', company.address || '23 George Avenue, Msasa, Harare'),
        h('div.small', `${company.tel || ''} · ${company.email || ''}`),
      ]),
      h('div.text-end', [
        h('div.fw-bold', { style: 'font-size:1rem' }, docTitle),
        ...(metaLines || []).map((line) => h('div.small', line)),
      ]),
    ]);
  }

  /* Every tile tone resolves here, so a caller cannot emit a Bootstrap colour
     that is not in the palette — `info` was rendering cyan and `secondary` a
     warm grey, which made a screen of tiles look like several products.
     `tone` is the documented name; `colour` is kept for older call sites. */
  const TILE_TONES = {
    brand: 'bg-brand-subtle text-brand',
    navy: 'bg-primary-subtle text-primary',
    primary: 'bg-primary-subtle text-primary',
    steel: 'bg-info-subtle text-info',
    info: 'bg-info-subtle text-info',
    slate: 'bg-secondary-subtle text-secondary',
    secondary: 'bg-secondary-subtle text-secondary',
    green: 'bg-success-subtle text-success',
    success: 'bg-success-subtle text-success',
    amber: 'bg-warning-subtle text-warning',
    warning: 'bg-warning-subtle text-warning',
    red: 'bg-danger-subtle text-danger',
    danger: 'bg-danger-subtle text-danger',
  };

  function statCard({ label, value, icon: ic, colour = 'brand', tone, sub, onClick }) {
    const iconClass = TILE_TONES[tone || colour] || TILE_TONES.brand;
    const el = h('div.card.stat-card.h-100', { onclick: onClick, style: onClick ? 'cursor:pointer' : null },
      h('div.card-body.d-flex.align-items-center.gap-3',
        h(`div.stat-icon.${iconClass}`, icon(ic || 'graph-up')),
        h('div.flex-fill',
          h('div.text-secondary.small.text-uppercase.fw-semibold', { style: 'font-size:.68rem;letter-spacing:.05em' }, label),
          h('div.stat-value', value),
          sub ? h('div.small.text-secondary', sub) : null)));
    return el;
  }

  function section({ title, actions, body, flush, tools, grow }) {
    // `grow` fills the column so two cards side by side end on the same line
    // instead of leaving a gap under whichever one is shorter.
    return h(`div.card.soft-card.mb-3${grow ? '.h-100' : ''}`,
      title ? h('div.card-header.d-flex.align-items-center.gap-2',
        h('span.flex-fill', title),
        tools || null,
        ...(actions || [])) : null,
      h(`div.card-body${flush ? '.p-0' : ''}`, body));
  }

  function formValue(form, name) {
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'radio') {
      const picked = form.querySelector(`[name="${name}"]:checked`);
      return picked ? picked.value : '';
    }
    return el.value.trim();
  }
  function formData(form) {
    const out = {};
    new FormData(form).forEach((v, k) => { out[k] = typeof v === 'string' ? v.trim() : v; });
    form.querySelectorAll('input[type=checkbox]').forEach((c) => { out[c.name] = c.checked; });
    return out;
  }

  /* ── router ──────────────────────────────────────────────────────── */
  const routes = [];
  function route(pattern, handler) {
    const keys = [];
    const regex = new RegExp('^' + pattern.replace(/:([\w]+)/g, (_, k) => {
      keys.push(k); return '([^/]+)';
    }).replace(/\*/g, '.*') + '$');
    routes.push({ regex, keys, handler, pattern });
  }
  /* Where the app lands when no hash is present. A deploy that ships only the
     board and the chat manager sets window.__HOME__ = '/board' before the router
     starts; the full console keeps the dashboard. */
  function homeRoute() {
    return window.__HOME__ || '/dashboard';
  }
  function currentPath() {
    const hash = window.location.hash.replace(/^#/, '');
    return hash || homeRoute();
  }
  function navigate(path, replace) {
    const target = '#' + path;
    if (replace) window.location.replace(target);
    else window.location.hash = path;
  }
  function resolve() {
    const path = currentPath();
    const query = {};
    const [pure, qs] = path.split('?');
    new URLSearchParams(qs || '').forEach((v, k) => { query[k] = v; });
    for (const r of routes) {
      const m = pure.match(r.regex);
      if (m) {
        const params = {};
        r.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
        return { handler: r.handler, params, query, path: pure };
      }
    }
    return { handler: () => h('div.empty-state', [icon('question-circle'), 'Page not found']), params: {}, query, path: pure };
  }
  let currentCleanup = null;
  async function renderRoute() {
    const { handler, params, query, path } = resolve();
    const outlet = document.getElementById('viewOutlet');
    if (!outlet) return;
    /* A fresh render re-fetches everything this screen needs, so it starts from
       "live" and a cached read puts the warning back. */
    resetFreshness();
    if (typeof currentCleanup === 'function') { try { currentCleanup(); } catch (e) { /* noop */ } currentCleanup = null; }
    outlet.innerHTML = '';
    outlet.appendChild(spinner('Loading…'));
    document.querySelectorAll('.tc-nav-item[data-route]').forEach((a) => {
      const base = a.dataset.route;
      a.classList.toggle('active', path === base || (base !== '/dashboard' && path.startsWith(base)));
    });
    /* The sidebar's active pill is positioned from script now, so it has to be
       told the classes moved — otherwise it stays on the previous row. */
    document.dispatchEvent(new CustomEvent('topclass:nav'));
    const context = {
      params, query, path,
      onCleanup: (fn) => { currentCleanup = fn; },
      navigate,
      refresh: () => renderRoute(),
    };
    try {
      const node = await handler(context);
      if (outlet) mount(outlet, node);
      const crumb = document.getElementById('pageCrumb');
      if (crumb) crumb.textContent = (context.title || path.replace(/^\//, '').replace(/\//g, ' › '));
    } catch (err) {
      if (err && err.status === 401) return;
      console.error(err);
      mount(outlet, h('div.alert.alert-danger', [h('strong', 'Could not load this view. '), err.message]));
    }
  }
  async function startRouter() {
    window.addEventListener('hashchange', renderRoute);
    /* Warm the queue before the first screen renders. It is read synchronously
       while building the tree, so a screen would otherwise draw without its
       "not saved yet" strip and only grow one on the next navigation — which is
       exactly when the operator has stopped looking. */
    try { await refreshPending(); } catch (e) { /* no store, no queue */ }
    if (!window.location.hash) window.location.hash = homeRoute();
    else renderRoute();
  }

  TCA.h = h;
  TCA.frag = frag;
  TCA.mount = mount;
  TCA.html = html;
  TCA.icon = icon;
  TCA.createStore = createStore;
  TCA.modal = modal;
  TCA.closeModal = closeModal;
  /* One viewer for every customer-supplied image the app receives: enquiry
     damage photos and proof-of-payment screenshots. Built as nodes, never
     innerHTML — the files come from outside the app. */
  function photoViewer(items, { title, subtitle } = {}) {
    const photos = items || [];
    const isImage = (url) => /\.(png|jpe?g|webp|gif)$/i.test(url || '');
    return modal({
      title: title || 'Photos',
      subtitle: subtitle || '',
      icon: 'camera',
      size: 'lg',
      body: photos.length
        ? h('div.tc-photo-grid', photos.map((p) => (
            isImage(p.url)
              ? h('a', { href: p.url, target: '_blank', rel: 'noopener' },
                  h('img.tc-photo-thumb', { src: p.url, loading: 'lazy',
                                            alt: p.caption || p.note || 'Attachment' }))
              : h('a.btn.btn-sm.btn-outline-secondary',
                  { href: p.url, target: '_blank', rel: 'noopener' },
                  p.caption || p.note || 'Open attachment')
          )))
        : h('div.text-secondary', 'Nothing was attached.'),
    });
  }

  /* The little camera + count button that opens the viewer. */
  function photoPill(row, { onClick, title, cls } = {}) {
    const count = row.photo_count || row.proof_count || 0;
    if (!count) return null;
    return h(`button.btn.btn-sm.btn-outline-secondary${cls ? ' ' + cls : ''}`, {
      title: title || 'View photos',
      onclick: (e) => { e.stopPropagation(); onClick && onClick(row); },
    }, [icon('camera'), h('span.ms-1', String(count))]);
  }

  TCA.photoViewer = photoViewer;
  TCA.photoPill = photoPill;
  TCA.confirmDialog = confirmDialog;
  TCA.formModal = formModal;
  /** Exposed so a hand-built form can use the same phone control. */
  TCA.telField = telField;
  TCA.comboField = comboField;
  TCA.vehicleOptions = vehicleOptions;
  TCA.badge = badge;
  TCA.stageBadge = stageBadge;
  TCA.priorityBadge = priorityBadge;
  TCA.serviceText = serviceText;
  TCA.emptyState = emptyState;
  TCA.searchInput = searchInput;
  TCA.iconSelect = iconSelect;
  TCA.searchableSelect = searchableSelect;
  TCA.skeletonTable = skeletonTable;
  TCA.spinner = spinner;
  TCA.dataTable = dataTable;
  TCA.printDoc = printDoc;
  TCA.printHeader = printHeader;
  TCA.statCard = statCard;
  TCA.section = section;
  TCA.formData = formData;
  TCA.formValue = formValue;
  TCA.money = money;
  TCA.dateShort = dateShort;
  TCA.dateTime = dateTime;
  TCA.timeOnly = timeOnly;
  TCA.relTime = relTime;
  TCA.today = today;
  /* The workshop's timezone, exposed so view modules format and group on the
     same clock rather than their own. */
  TCA.TZ_NAME = TZ_NAME;
  TCA.parseStamp = parseStamp;
  TCA.dateKey = dateKey;
  TCA.src = src;
  TCA.debounce = debounce;
  TCA.setConnectionState = setConnectionState;
  TCA.route = route;
  TCA.navigate = navigate;
  TCA.startRouter = startRouter;
  TCA.renderRoute = renderRoute;
})();
