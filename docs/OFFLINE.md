# Working with a bad connection

The console runs on Render and the workshop is in Msasa. The link goes down, and
when it does the workshop cannot stop — cars are still booked in, parts are still
fitted and money is still taken at the counter. This is what the app does about
that, and what it deliberately does not do.

Three separate problems live under the word "offline", and they were solved in
this order on purpose.

---

## 1. A write must not be lost

Every write the browser sends may carry an `Idempotency-Key`. The server claims
that key, and a repeat of the same key returns **the stored response** instead of
running the handler again.

This is not an optimisation. Without it a queued payment replayed after a
reconnect takes the customer's money twice, so it had to exist *before* anything
was allowed to queue.

- `app/services/idempotency.py`, `app/models.py::IdempotencyKey`
- Hooked on the API blueprint (`before_request` / `after_request`), so all 42
  write endpoints are covered — including any written later. No endpoint knows
  this exists.
- Scoped to `(user_id, key)`: the same key from a different operator is a
  different act, not a replay.
- A **rejected** write (4xx) releases its claim, so the client can fix the
  payload and retry under the same key. A claim that stayed behind would answer
  every later attempt with 409 and the queue would never drain.
- A claim abandoned by a crash expires after 90s rather than wedging that key.
- Requests with no key behave exactly as they did before this existed, which is
  what keeps every existing caller and every `curl` honest.

## 2. A write must survive the link dropping

`app/static/js/core.js` keeps an outbox in IndexedDB. When a **network** failure
is what stopped a write — never a 4xx — it is parked there with a key and sent
again when the connection returns, in the order the work was done.

- Reads are never queued; only writes.
- The replay is **not** gated on `navigator.onLine`. It lies: the embedded
  browser reports offline while the server is perfectly reachable, and gating on
  it means the queue never drains while the operator is told their work is
  waiting. The send is attempted and the fetch outcome decides.
- A write the server *refuses* is surfaced in the top-bar sync panel with a
  Discard button. Queued work nobody can see is worse than work that was refused.
- The count is always visible in the top bar. Amber means waiting, and a refusal
  is flagged more loudly than a wait.

## 3. The app must open

`app/static/sw.js`, served at **`/sw.js`** — not from `/static/`.

A service worker's scope is capped at the path it is served from, so a copy under
`/static/` could only ever control `/static/*`. It could not cache the app shell
or intercept `/api/`, which is the entire point.

| Request | Strategy | Why |
|---|---|---|
| Navigations | network first, fall back to the cached shell | a deploy is picked up on the next load, not whenever a cache expires |
| `/static/*` | cache first | every asset URL carries its file's mtime (`static_url()`), so an edited file is a *different URL* and cannot be served stale |
| `/api/*` GET | network first, fall back to the cached copy | the shell needs to render, and the stale copy is labelled |
| anything else | untouched | includes every write — see below |

**The worker never intercepts a write.** The outbox owns writes. If the worker
also queued POSTs there would be two things racing to send the same payment from
opposite sides of the app, and the duplicate would look like operator error. The
guard is at the top of the `fetch` handler and is pinned by a test.

### The cache is scoped to a session, not to the browser

Cached API responses contain customers, job cards and money. A workshop PC is
shared, so signing out posts `clear-data` to the worker and the API cache is
thrown away. The next person to sign in is never served the previous one's data.

### "Data as of 14:20"

A read answered from the cache is stamped `X-Served-From: cache`, and the top bar
says so. The warning is **sticky for the life of a render** and keeps the
*oldest* timestamp: a fresh response does not clear it, because one figure from
this morning sitting beside a live one with no warning is precisely the case that
misleads somebody into taking money against a stale balance.

### A hanging CDN must not cost us the feature

`cache.add()` has no timeout. A CDN that accepts the connection and never answers
leaves `install` pending for ever — the worker never activates, `register()` never
settles, and the app silently stays online-only with no error anywhere. Every
precache fetch therefore has a deadline and failures are per-asset.

---

## What is **not** covered

- **The WhatsApp bot goes dark.** Meta cannot reach the webhook and replies cannot
  go out. Meta retries its side, so inbound messages tend to arrive late rather
  than vanish. A bot reply that fails to send is logged and **not** retried —
  an outbound retry queue is still outstanding.
- **Two operators editing the same record offline.** The queue is applied in
  order and the server is the last word; there is no conflict resolution, and
  none is pretended.
- **Screens do not yet show queued work optimistically.** A job card created
  offline is saved, but the list will not show it until the replay lands. The
  write is safe; the display catches up.

---

## Verifying it

The behaviour cannot be tested by pytest — a worker runs in a browser. What the
suite pins is everything around it: `tests/test_offline.py` (the idempotency
contract, using real payments) and `tests/test_offline_worker.py` (the worker is
served from the root, never cached, never intercepts a write, and the manifest is
valid).

> **Note for whoever tests this next.** The VS Code embedded browser exposes the
> service-worker API but **does not start service workers** — plain `Worker`
> threads run, the registration appears with the right scope, and the worker is
> never fetched (`scriptResponseTime: 0`, status stuck at `"new"`, no error).
> Offline serving therefore has to be verified in a real Chrome or Edge.

To check by hand:

1. Open the app in Chrome/Edge, sign in, and visit a few screens.
2. DevTools → Application → Service Workers: one active worker at scope `/`.
3. Application → Cache Storage: `topclass-shell-v1`, `topclass-data-v1`.
4. Network → Offline. Reload. The app should open, show the screens you visited,
   and carry a **Data as of …** chip.
5. Make a change while offline: it lands in the sync panel and the top-bar count.
6. Back online. It drains on its own, and the change appears in the database
   **exactly once** — reload mid-replay to prove the key is doing its job.
