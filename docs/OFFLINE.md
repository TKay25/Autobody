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

## 4. You have to be able to see it

A queued change is invisible to every list in the app — the server has not been
told yet, so a re-fetch returns the old data and the operator watches their own
change disappear.

That is not merely confusing. **Somebody who cannot see that they already recorded
a payment will record it again**, and a second attempt is a *new* action with a
*new* key, so nothing server-side stops it. The idempotency work in section 1
protects against a *replay*; it does not protect against a repeat.

So the queue is exposed as a reactive value and every screen that can queue
something says so:

- `T.pending()` — synchronous read, for use while building a tree
- `T.pendingFor(test)` / `T.pendingJobId(item)` — filter by URL or record
- `T.describeChange(item)` — one vocabulary for naming a queued action, so the
  same change reads the same wherever it is seen
- `T.pendingStrip({match, label})` — **"N changes on this screen have not been
  saved yet"**, with *Try now*. A self-updating host, not a static node: it has to
  be able to appear *after* the screen rendered, because that is exactly the
  moment the operator would otherwise repeat the work.

| Screen | What it claims |
|---|---|
| Dashboard | anything at all |
| WIP board | stage moves — the card is drawn in the column it is *going* to, so a queued move cannot snap back and get dragged twice |
| Job cards / a job card | changes to job cards, or to that job card |
| To-do | task changes |
| Parts & stock | stock movements — a fitted part that still shows as in stock gets fitted twice |
| Payments & invoices | the money, most of all |
| Enquiries & Bookings | enquiry changes |

A screen must only claim changes it actually shows. Over-claiming teaches the
operator to ignore the strip, which is worse than not having one.

---

## What is **not** covered

- **The WhatsApp bot goes dark.** Meta cannot reach the webhook and replies cannot
  go out. Meta retries its side, so inbound messages tend to arrive late rather
  than vanish. A reply that fails to send is now **queued and re-sent** — see
  `docs/WHATSAPP.md` §5 — so nothing is lost, only delayed.
- **Two operators editing the same record offline.** The queue is applied in
  order and the server is the last word; there is no conflict resolution, and
  none is pretended.
- **A queued *creation* is not shown as a row.** The strip says "1 job card change
  has not been saved yet"; it does not invent a row for a job card that has no
  number yet. Inventing one would produce a record with no reference and no links.

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
