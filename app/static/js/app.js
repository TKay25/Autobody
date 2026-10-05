/* App layout: sidebar, topbar, global search and route registration. */
(function () {
  const T = window.TCA;
  const { h, api, dateShort } = T;

  /* Navigation model. `badge` keys map to GET /api/badges. `key` is the letter
     you press after `g` to jump straight there — deliberately avoiding b, i and
     n, which are already single-key shortcuts, so no letter ever means two
     different things. `preview` is what the hover card shows: pairs of
     [badge key, words], and a pair with a zero count is simply left out. */
  const NAV = [
    { section: 'Workshop' },
    { route: '/dashboard', label: 'Dashboard', icon: 'grid-1x2-fill', key: 'd',
      hint: 'Overview of today',
      preview: [['jobs', 'open job cards'], ['jobs_ready', 'ready to collect']] },
    { route: '/board', label: 'WIP board', icon: 'kanban', key: 'k',
      hint: 'Drag job cards through the shop',
      preview: [['jobs', 'in the shop'], ['jobs_overdue', 'overdue']] },
    { route: '/jobs', label: 'Job cards', icon: 'clipboard-check', badge: 'jobs', key: 'j',
      hint: 'Every vehicle in the shop',
      preview: [['jobs', 'open'], ['jobs_overdue', 'overdue']] },
    { route: '/todo', label: 'To-do', icon: 'list-check', badge: 'tasks', key: 't',
      hint: 'What has to happen today and this week',
      preview: [['tasks', 'due today']] },
    { route: '/bookings', label: 'Enquiries & Bookings', icon: 'calendar-check', badge: 'bookings',
      key: 'e', hint: 'Enquiries, bookings and confirmations',
      preview: [['bookings', 'awaiting an answer']] },
    { section: 'Customers' },
    { route: '/customers', label: 'Customers', icon: 'people', key: 'c', hint: 'CRM and contact details' },
    { route: '/vehicles', label: 'Vehicles', icon: 'car-front', key: 'v', hint: 'Registration register' },
    { route: '/inbox', label: 'WhatsApp', icon: 'whatsapp', badge: 'whatsapp', key: 'w',
      hint: 'Chat with customers',
      preview: [['whatsapp', 'unread']] },
    { section: 'Money' },
    { route: '/payments', label: 'Payments', icon: 'cash-coin', key: 'p',
      hint: 'Receipts, methods and takings' },
    { route: '/invoices', label: 'Invoices', icon: 'receipt', badge: 'invoices', key: 'f',
      hint: 'Billing and payments',
      preview: [['invoices', 'past due']] },
    { route: '/reports', label: 'Reports', icon: 'graph-up-arrow', key: 'r',
      hint: 'Performance and margins' },
    { section: 'Resources' },
    { route: '/parts', label: 'Parts & stock', icon: 'box-seam', badge: 'parts', key: 's',
      hint: 'Stock levels and suppliers',
      preview: [['parts', 'at or below reorder level']] },
    { route: '/activity', label: 'Activity log', icon: 'clock-history', key: 'a',
      hint: 'Who changed what' },
    { route: '/staff', label: 'Staff & settings', icon: 'gear', key: 'g', hint: 'Accounts and bot setup' },
  ];

  /* Model order, so a rearranged rail can be put back exactly as it shipped. */
  const NAV_INDEX = {};
  NAV.forEach((item, i) => { if (item.route) NAV_INDEX[item.route] = i; });

  const NAV_BY_ROUTE = {};
  NAV.forEach((item) => { if (item.route) NAV_BY_ROUTE[item.route] = item; });

  /* Quick actions — the handful of jobs people start most often. Each renders
     as a card in the strip under the top bar. `run` fires a dialog in place;
     `href` sends you to the screen that owns the task. */
  const QUICK_ACTIONS = [
    { label: 'New job card', hint: 'Book a vehicle in and estimate it',
      icon: 'clipboard-plus', tone: 'brand', run: () => T.newJobCard() },
    { label: 'New quotation', hint: 'Price a job card that is already open',
      icon: 'calculator', run: () => T.newJobCard({ focus: 'estimate' }) },
    { label: 'Payments & invoices', hint: 'Record a receipt against an invoice',
      icon: 'cash-coin', href: '#/invoices' },
    { label: 'Add stock item', hint: 'Parts, paint and consumables',
      icon: 'box-seam', href: '#/parts?new=1' },
    { label: 'Enquiries and Bookings', hint: 'Phone, WhatsApp and walk-in requests',
      icon: 'calendar-check', href: '#/bookings' },
  ];

  /* ── Display preferences ──────────────────────────────────────────────
     Rail mode, compact rows and the folded nav sections follow the ACCOUNT, like
     the rail order: the same person works the front desk and the workshop tablet.
     The browser keeps a per-user copy purely so the first frame is right — the
     inline script in app.html reads it before paint — and the account is the
     truth. One unscoped key on a shared PC hands one operator's layout to
     whoever signs in next, which is the thing this whole layer exists to stop. */
  const DEFAULT_PREFS = { rail: false, compact_rows: false, collapsed_sections: [] };

  /* Where the browser-only version kept each one. Read once on the way past, so a
     layout somebody already set up is not silently thrown away. */
  const LEGACY_PREFS = {
    rail: ['topclass.sidebar.rail', (raw) => raw === '1'],
    compact_rows: ['topclass.rows.compact', (raw) => raw === '1'],
    collapsed_sections: ['topclass.nav.collapsed', (raw) => {
      try { return JSON.parse(raw) || []; } catch (e) { return []; }
    }],
  };

  const prefsKeyFor = (userId) => `topclass.prefs.${userId || 0}`;

  let prefs = { ...DEFAULT_PREFS };
  let prefsUserId = 0;

  function isRail() { return prefs.rail === true; }
  function isCompact() { return prefs.compact_rows === true; }
  function collapsedSections() { return new Set(prefs.collapsed_sections || []); }

  function savePrefs(patch) {
    prefs = { ...prefs, ...patch };
    try { localStorage.setItem(prefsKeyFor(prefsUserId), JSON.stringify(prefs)); }
    catch (e) { /* private mode */ }
    /* Fire and forget: the rail has already moved on screen, and failing to file
       a display preference is not worth an error anybody has to read. */
    api.patch('/api/me/preferences', patch, { silent: true }).catch(() => {});
  }

  function saveCollapsed(set) {
    savePrefs({ collapsed_sections: [...set] });
  }

  /* Server first, then this browser's copy, then the pre-server keys. Any of the
     three counts as "chosen" — which is exactly why the server sends only what
     was actually set rather than a filled-in default. */
  function loadPrefs(userId, fromServer) {
    prefsUserId = userId || 0;
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(prefsKeyFor(userId)) || 'null'); }
    catch (e) { cached = null; }

    const next = { ...DEFAULT_PREFS };
    /* Only the keys that actually came from cache or from a legacy key are pushed.
       Sending the whole set would put explicit defaults on the account for anyone
       who happened to have one old key lying around, which is noise at best and
       at worst makes the account look "already chosen" when it never was. */
    const patch = {};
    Object.keys(DEFAULT_PREFS).forEach((key) => {
      if (fromServer && fromServer[key] !== undefined) { next[key] = fromServer[key]; return; }
      if (cached && cached[key] !== undefined) {
        next[key] = cached[key];
        patch[key] = cached[key];
        return;
      }
      const [legacyKey, parse] = LEGACY_PREFS[key];
      let raw = null;
      try { raw = localStorage.getItem(legacyKey); } catch (e) { raw = null; }
      if (raw !== null) {
        next[key] = parse(raw);
        patch[key] = next[key];
      }
    });
    prefs = next;
    try { Object.values(LEGACY_PREFS).forEach(([key]) => localStorage.removeItem(key)); }
    catch (e) { /* private mode */ }
    /* Seed the account from whatever this browser had, so a layout somebody
       already set up starts following them from here. If the write fails it is
       retried on the next load, which is what makes this self-healing. */
    if (Object.keys(patch).length) savePrefs(patch);
  }

  /* Kept at module scope so it cannot be collected while it is still observing. */
  let navObserver = null;
  /* Set by `layout` so the rail toggle can re-measure the pill without reaching
     into the sidebar's closure. */
  let syncNavGlider = null;

  /* The order the operator dragged the rail into: a flat list of routes, only
     ever permuted within one section, so it can never move a screen from one
     block to another.

     The ACCOUNT holds it — it has to follow a person from the front desk to the
     workshop tablet — and the browser keeps a copy purely so the rail draws
     right on the first frame after sign-in. That copy is keyed by user id: a
     single unscoped key on a shared PC hands the previous operator's arrangement
     to the next one. */
  const orderKeyFor = (userId) => `topclass.nav.order.${userId || 0}`;
  /* Where the previous, browser-only version kept it. Read once on the way past,
     so an arrangement somebody already made is not silently thrown away. */
  const LEGACY_ORDER_KEY = 'topclass.nav.order';

  function readOrder(key) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || 'null');
      return Array.isArray(parsed) ? parsed : null;
    } catch (e) { return null; }
  }

  function writeOrder(key, routes) {
    try { localStorage.setItem(key, JSON.stringify(routes)); } catch (e) { /* ignore */ }
  }

  function dropOrder(key) {
    try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  /* Fire and forget. The rail has already moved on screen, and failing to file a
     display preference is not worth an error anybody has to read. */
  function pushOrder(routes) {
    api.patch('/api/me/nav-order', { routes }, { silent: true }).catch(() => {});
  }

  /* ── layout ──────────────────────────────────────────────────────── */
  function layout(payload) {
    const user = payload.user;

    /* Brand ---------------------------------------------------------- */
    const brand = h('div.tc-brand', [
      h('div.brand-mark', T.icon('car-front-fill')),
      h('div.tc-brand-text', [
        h('div.tc-brand-name', 'Topclass Auto Body'),
        h('div.tc-brand-sub', 'Workshop OS'),
      ]),
      h('button.tc-rail-toggle.d-none.d-lg-grid', {
        type: 'button',
        'aria-label': 'Collapse navigation',
        title: 'Collapse navigation  (Alt+B)',
        onclick: toggleRail,
      }, T.icon('chevron-double-left')),
    ]);

    /* Navigation ----------------------------------------------------- */
    const pills = {};

    /* Grouped, not a flat list: each of Workshop / Customers / Money / Resources
       is drawn as its own pane, so the eye can find a block instead of scanning
       sixteen rows. The model stays flat — the grouping is derived, so adding a
       nav item is still one line. */
    const navGroups = [];
    NAV.forEach((item) => {
      if (item.section) navGroups.push({ label: item.section, items: [] });
      else {
        // Defensive: an item before any section heading still gets rendered
        // rather than silently dropped.
        if (!navGroups.length) navGroups.push({ label: '', items: [] });
        navGroups[navGroups.length - 1].items.push(item);
      }
    });

    const orderKey = orderKeyFor(user.id);
    let hasCustomOrder = false;

    function rememberOrder(routes) {
      hasCustomOrder = true;
      writeOrder(orderKey, routes);
      pushOrder(routes);
    }

    function forgetOrder() {
      hasCustomOrder = false;
      dropOrder(orderKey);
      pushOrder([]);
    }

    /* The account's order wins. The browser's copy only covers the gap between
       signing in and the rail drawing, and the legacy key is adopted once so
       nobody loses an arrangement they made before this moved to the server. */
    let order = (user.nav_order && user.nav_order.length) ? user.nav_order : readOrder(orderKey);
    hasCustomOrder = !!(order && order.length);
    if (!order) {
      const legacy = readOrder(LEGACY_ORDER_KEY);
      if (legacy && legacy.length) {
        order = legacy;
        rememberOrder(legacy);
      }
    }
    dropOrder(LEGACY_ORDER_KEY);

    /* A saved order permutes each block on its own: the sort runs per group, so
       dragging Job cards to the top of Workshop can never smuggle it into Money.
       Routes that were never dragged keep their model order (Array.sort is
       stable), which is also why adding a new screen to NAV just works. */
    if (order) {
      const rank = new Map(order.map((route, i) => [route, i]));
      const pos = (item) => (rank.has(item.route) ? rank.get(item.route) : 1e6);
      navGroups.forEach((group) => group.items.sort((a, b) => pos(a) - pos(b)));
    }

    const navNodes = {};      // route -> the <a>, so a reset can re-append in place
    const groupBodies = {};   // section label -> the box holding that section's rows
    let droppedAt = 0;        // suppresses the click that can follow a drop

    function navItem(item) {
      const pill = item.badge ? h('span.tc-nav-pill', { hidden: true }) : null;
      if (pill) pills[item.badge] = pill;

      const row = h('a.tc-nav-item', {
        href: `#${item.route}`,
        'data-route': item.route,
        title: item.hint || item.label,
        'aria-label': item.label,
        onclick: (event) => {
          /* The grip is a handle, not a link, and a drop must never navigate. */
          if (event.target.closest('.tc-nav-grip') || Date.now() - droppedAt < 250) {
            event.preventDefault();
            return;
          }
          closeSidebar();
        },
      }, [
        h('span.tc-nav-icon', T.icon(item.icon)),
        h('span.tc-nav-label', item.label),
        pill,
        h('kbd.tc-nav-key', item.key),
        h('span.tc-nav-grip', { title: 'Drag to reorder' }, T.icon('grip-vertical')),
      ]);
      navNodes[item.route] = row;
      return row;
    }

    /* Two travelling pills, both built with the rail so neither can be missing
       when a route renders: `glider` is the crimson "you are here", `hoverPill`
       is the light "the click would land here". The glider is second in the DOM
       so that crimson wins if they ever overlap mid-slide. */
    const hoverPill = h('div.tc-nav-hover', { 'aria-hidden': 'true' });
    const glider = h('div.tc-nav-glider', { 'aria-hidden': 'true' });
    glider.classList.add('no-anim');
    hoverPill.classList.add('no-anim');
    let hoverRow = null;

    const collapsed = collapsedSections();

    function chevron() {
      const glyph = T.icon('chevron-down');
      glyph.classList.add('tc-nav-chevron');
      glyph.setAttribute('aria-hidden', 'true');
      return glyph;
    }

    function foldGroup(groupEl, heading, key, shut) {
      groupEl.classList.toggle('is-collapsed', shut);
      heading.setAttribute('aria-expanded', String(!shut));
      heading.title = `${shut ? 'Unfold' : 'Fold'} ${heading.getAttribute('aria-label')}`;
      if (shut) collapsed.add(key); else collapsed.delete(key);
      saveCollapsed(collapsed);
      syncGlider();
    }

    /* Move `el` onto `target`, or hide it when there is nothing to sit on.
       Reads only — both pills animate themselves, so nothing here runs per frame. */
    function place(el, target) {
      if (!target) { el.classList.remove('is-set'); return; }
      const host = nav.getBoundingClientRect();
      const box = target.getBoundingClientRect();
      /* Zero width means the row is inside a folded section. */
      if (!box.width) { el.classList.remove('is-set'); return; }
      el.style.width = `${box.width}px`;
      el.style.height = `${box.height}px`;
      el.style.transform =
        `translate3d(${box.left - host.left}px, ${box.top - host.top + nav.scrollTop}px, 0)`;
      el.classList.add('is-set');
    }

    /* The crimson pill tracks the route, the light one tracks the cursor — and
       the hover pill never sits on the active row, because that row is already
       spoken for. Both are placed from here so they can never disagree about
       where the rows are. */
    function syncGlider() {
      const active = nav.querySelector('.tc-nav-item.active');
      nav.querySelectorAll('.tc-nav-group').forEach((g) => {
        g.classList.toggle('has-active', !!active && g.contains(active));
      });
      if (!nav.isConnected) {
        glider.classList.remove('is-set');
        hoverPill.classList.remove('is-set');
        return;
      }
      place(glider, active);
      place(hoverPill, hoverRow && hoverRow !== active ? hoverRow : null);
    }

    const nav = h('nav.tc-nav', { 'aria-label': 'Main navigation' },
      [hoverPill, glider].concat(navGroups.map((group) => {
        const key = group.label.toLowerCase();
        const shut = collapsed.has(key);
        const heading = h('button.tc-nav-section', {
          type: 'button',
          'aria-label': group.label,
          'aria-expanded': String(!shut),
          title: `${shut ? 'Unfold' : 'Fold'} ${group.label}`,
        }, [h('span.tc-nav-section-label', group.label), chevron()]);
        const body = h('div.tc-nav-group-body', group.items.map(navItem));
        groupBodies[key] = body;
        const groupEl = h(`div.tc-nav-group${shut ? '.is-collapsed' : ''}`, [heading, body]);
        heading.onclick = () =>
          foldGroup(groupEl, heading, key, !groupEl.classList.contains('is-collapsed'));
        return groupEl;
      })));

    /* Put the rail back the way it shipped. Done in place rather than by
       reloading the page: the model order is static, so re-sorting and
       re-appending the nodes already on screen is exact. */
    function resetNavOrder() {
      forgetOrder();
      navGroups.forEach((group) => {
        const body = groupBodies[group.label.toLowerCase()];
        if (!body) return;
        group.items.sort((a, b) => NAV_INDEX[a.route] - NAV_INDEX[b.route]);
        group.items.forEach((item) => body.appendChild(navNodes[item.route]));
      });
      syncGlider();
    }

    /* ── `g` chords ──────────────────────────────────────────────────
       Fourteen rows is past the point where reaching for the mouse beats typing,
       and a chord is how you get fourteen shortcuts without eating the alphabet.
       Press `g` and every row shows its letter; idle, the chips take up no space
       at all, so nothing changes for anyone who never presses it. */
    const jumpTo = {};
    NAV.forEach((item) => { if (item.key) jumpTo[item.key] = item.route; });

    const chordHint = h('div.tc-nav-chordhint', { 'aria-hidden': 'true' }, [
      T.icon('compass'),
      h('span', 'Jump to…'),
      h('kbd', 'Esc'),
    ]);
    let chordTimer = null;

    function isChord() {
      return document.documentElement.classList.contains('nav-chord');
    }

    function setChord(on) {
      document.documentElement.classList.toggle('nav-chord', on);
      if (chordTimer) { clearTimeout(chordTimer); chordTimer = null; }
      /* The map times out on its own — an armed keyboard that waits for ever is a
         keyboard that ambushes you later. */
      if (on) chordTimer = setTimeout(() => setChord(false), 4500);
    }

    /* `g` chords run ahead of the single-key shortcuts: `g` then `k` must go to
       the board once, not fire a shortcut and then fall through to another. */
    document.addEventListener('keydown', (event) => {
      if (!isChord()) return;
      const chord = (event.key || '').toLowerCase();
      event.preventDefault();
      event.stopPropagation();
      setChord(false);
      if (chord === 'escape') return;
      if (jumpTo[chord]) { T.navigate(jumpTo[chord]); closeSidebar(); }
    }, true);

    /* The active row is scrolled into view on the first paint only — doing it on
       every navigation yanks the list around under the pointer. */
    function revealActive() {
      const active = nav.querySelector('.tc-nav-item.active');
      if (!active || !nav.isConnected) return;
      const host = nav.getBoundingClientRect();
      const row = active.getBoundingClientRect();
      if (row.top >= host.top + 8 && row.bottom <= host.bottom - 8) return;
      nav.scrollTop += row.top - host.top - (host.height - row.height) / 2;
    }

    /* ── The hover card ──────────────────────────────────────────────
       In icon-only mode it names the row you are over; a moment later it also
       says what is waiting there. The delay is the point — a card that appears
       the instant the pointer crosses a row is noise when you are only
       travelling across to the one you actually want. `.tc-sidebar` is
       `overflow: hidden`, so it has to be fixed-position and live outside the
       sidebar's subtree. */
    const navPop = h('div.tc-nav-pop', { role: 'tooltip' });
    let popRow = null;
    let popTimer = null;
    let counts = {};          // the last /api/badges payload, for the stats

    function popDelay() {
      return document.documentElement.classList.contains('sidebar-rail') ? 90 : 420;
    }

    function hidePop() {
      if (popTimer) { clearTimeout(popTimer); popTimer = null; }
      popRow = null;
      navPop.classList.remove('is-open');
    }

    function showPop(row) {
      const item = NAV_BY_ROUTE[row.dataset.route] || {};
      navPop.textContent = '';
      navPop.appendChild(h('div.tc-nav-pop-title', item.label || row.getAttribute('aria-label')));
      if (item.hint) navPop.appendChild(h('div.tc-nav-pop-hint', item.hint));
      /* Only non-zero figures: "0 overdue" is noise, and its absence already
         says the same thing. */
      const stats = (item.preview || [])
        .map(([key, word]) => ({ n: counts[key] || 0, word }))
        .filter((stat) => stat.n > 0);
      if (stats.length) {
        navPop.appendChild(h('div.tc-nav-pop-stats', stats.map((stat) =>
          h('span.tc-nav-pop-stat', [h('b', String(stat.n)), ` ${stat.word}`]))));
      }
      const box = row.getBoundingClientRect();
      navPop.style.top = `${box.top + box.height / 2}px`;
      navPop.style.left = `${box.right + 12}px`;
      navPop.classList.add('is-open');
    }

    nav.addEventListener('mouseover', (event) => {
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row) {
        /* Over the pane's own background — a gap between rows, or a section
           heading. Nothing to point at, so drop the hover pill. */
        if (hoverRow) {
          hoverRow = null;
          hoverPill.classList.remove('is-set');
        }
        hidePop();
        return;
      }
      if (row !== hoverRow) {
        hoverRow = row;
        syncGlider();
      }
      if (row === popRow) return;
      hidePop();
      popRow = row;
      popTimer = setTimeout(() => {
        popTimer = null;
        if (popRow === row && row.isConnected) showPop(row);
      }, popDelay());
    });
    nav.addEventListener('mouseleave', () => {
      hoverRow = null;
      hoverPill.classList.remove('is-set');
      hidePop();
    });

    /* ── Drag to reorder ─────────────────────────────────────────────
       Only the grip starts a drag — `draggable` is switched on in mousedown — so
       a wobbly click on a nav row can never rearrange the rail. Drops are
       confined to the block the row came from: dragging a screen into another
       section would silently re-file it under a different heading, which is a
       different feature with a different meaning. */
    let dragRow = null;
    let dropAfter = false;

    function clearDropMarks() {
      nav.querySelectorAll('.is-drop-before, .is-drop-after')
        .forEach((el) => el.classList.remove('is-drop-before', 'is-drop-after'));
    }

    function releaseGrips() {
      nav.querySelectorAll('.tc-nav-item').forEach((a) => { a.draggable = false; });
    }

    function finishDrag() {
      if (!dragRow) return;
      dragRow.classList.remove('is-dragging');
      dragRow = null;
      releaseGrips();
      clearDropMarks();
      droppedAt = Date.now();
      rememberOrder([...nav.querySelectorAll('.tc-nav-item')].map((a) => a.dataset.route));
      syncGlider();
    }

    nav.addEventListener('mousedown', (event) => {
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row) return;
      releaseGrips();
      row.draggable = !!(event.target.closest && event.target.closest('.tc-nav-grip'));
    });
    nav.addEventListener('mouseup', releaseGrips);

    nav.addEventListener('dragstart', (event) => {
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row || !row.draggable) return;
      dragRow = row;
      hidePop();
      row.classList.add('is-dragging');
      nav.classList.add('is-reordering');
      try {
        event.dataTransfer.setData('text/plain', row.dataset.route);
        event.dataTransfer.effectAllowed = 'move';
      } catch (e) { /* a synthetic DataTransfer can be read-only */ }
    });

    nav.addEventListener('dragover', (event) => {
      if (!dragRow) return;
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row || row === dragRow || row.parentElement !== dragRow.parentElement) return;
      /* preventDefault is what makes a target legal, so a row in another block
         simply refuses the drop rather than quietly moving the screen. */
      event.preventDefault();
      const box = row.getBoundingClientRect();
      dropAfter = event.clientY > box.top + box.height / 2;
      clearDropMarks();
      row.classList.add(dropAfter ? 'is-drop-after' : 'is-drop-before');
    });

    nav.addEventListener('drop', (event) => {
      if (!dragRow) return;
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (row && row !== dragRow && row.parentElement === dragRow.parentElement) {
        event.preventDefault();
        if (dropAfter) row.after(dragRow); else row.before(dragRow);
      }
      finishDrag();
    });

    nav.addEventListener('dragend', finishDrag);
    /* Keyboard users get the same treatment as the pointer: tabbing through the
       rail slides the light pill onto the focused row instead of leaving them to
       guess which one has focus. Bound on the document in the capture phase —
       it costs nothing and still works if anything downstream stops the bubble. */
    document.addEventListener('focusin', (event) => {
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row || !nav.contains(row) || row === hoverRow) return;
      hoverRow = row;
      syncGlider();
    }, true);
    document.addEventListener('focusout', (event) => {
      const row = event.target.closest && event.target.closest('.tc-nav-item');
      if (!row || !nav.contains(row) || !hoverRow) return;
      hoverRow = null;
      syncGlider();
    }, true);

    /* Foot: live clock + build stamp ---------------------------------- */
    /* Tabular figures and a 10s tick: the rail is at the edge of a workshop PC
       that nobody wants to touch just to check the time. */
    const clock = h('span.tc-side-foot-clock');
    /* The workshop's clock, not the PC's. A machine left on another timezone
       must still show the time the front desk is working to. */
    const clockFmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: T.TZ_NAME || 'Africa/Harare', hour: '2-digit', minute: '2-digit', hour12: false,
    });
    function paintClock() {
      clock.textContent = clockFmt.format(new Date());
    }
    paintClock();
    setInterval(paintClock, 10000);

    const sideFoot = h('div.tc-side-foot', [
      h('span.tc-status-dot', { title: 'Connected' }),
      h('span', 'Live'),
      h('span.tc-side-foot-sep', '·'),
      clock,
      h('span.tc-side-foot-sep', '·'),
      h('span', `v${(window.__BOOTSTRAP__ || {}).version || '1.0'}`),
    ]);

    const sidebar = h('aside.tc-sidebar', { id: 'tcSidebar' }, [brand, nav, chordHint, sideFoot]);

    /* Identity — the user menu sits at the right-hand end of the top bar. */
    let userOpen = false;

    function toggleUserMenu(force) {
      userOpen = force === undefined ? !userOpen : force;
      userMenu.hidden = !userOpen;
      /* Only offer the undo when there is something to undo. */
      resetOrderItem.hidden = !hasCustomOrder;
      userBtn.classList.toggle('is-open', userOpen);
      userBtn.setAttribute('aria-expanded', String(userOpen));
    }

    const closeMenu = () => toggleUserMenu(false);

    const userBtn = h('button.tc-user-btn', {
      type: 'button',
      'aria-haspopup': 'true',
      'aria-expanded': 'false',
      title: `${user.full_name} · ${user.role_label}`,
      onclick: (e) => { e.stopPropagation(); toggleUserMenu(); },
    }, [
      h('span.tc-avatar', user.initials),
      h('div.tc-user-meta', [
        h('div.tc-user-name', user.full_name),
        h('div.tc-user-role', user.role_label),
      ]),
      h('span.tc-user-caret', T.icon('chevron-expand')),
    ]);

    /* Rearranging the rail is easy to do by accident and impossible to
       reconstruct from memory, so the undo sits with the other display
       preferences. Hidden until there is an order to reset. */
    const resetOrderItem = h('button.tc-user-menu-item', {
      type: 'button',
      hidden: true,
      onclick: () => {
        closeMenu();
        resetNavOrder();
        T.toast('Navigation order reset.');
      },
    }, T.icon('arrow-counterclockwise'), 'Reset nav order');

    const userMenu = h('div.tc-user-menu', { role: 'menu', hidden: true }, [
      h('div.tc-user-menu-head', [
        h('span.tc-avatar.tc-avatar-lg', user.initials),
        h('div.min-w-0', [
          h('div.fw-semibold.small.text-truncate', user.full_name),
          h('div.tc-muted.text-truncate', { style: 'font-size:.72rem' },
            user.email || user.role_label),
        ]),
      ]),
      h('div.tc-user-menu-body', [
        h('a.tc-user-menu-item', { href: '#/staff', onclick: closeMenu },
          T.icon('person-gear'), 'My account'),
        user.is_manager ? h('a.tc-user-menu-item', { href: '#/staff?new=1', onclick: closeMenu },
          T.icon('person-plus'), 'Add staff member') : null,
        h('a.tc-user-menu-item', { href: '#/staff', onclick: closeMenu },
          T.icon('gear'), 'Settings'),
        h('a.tc-user-menu-item', { href: '#/activity', onclick: closeMenu },
          T.icon('clock-history'), 'Activity log'),
        h('button.tc-user-menu-item', {
          type: 'button',
          // Names the *result* of clicking, not the current state.
          onclick: () => { closeMenu(); toggleDensity(); },
        }, T.icon('list-ul'),
          isCompact() ? 'Comfortable rows' : 'Compact rows'),
        resetOrderItem,
      ]),
      h('div.tc-user-menu-foot', [
        h('button.tc-user-menu-item.is-danger', {
          type: 'button',
          onclick: async () => {
            await api.post('/auth/logout', {}, { silent: true }).catch(() => {});
            /* Drop the cached API responses. A workshop PC is shared, and that
               cache holds customers, job cards and money — the next person to
               sign in must not be served the previous one's data. */
            try {
              const reg = await navigator.serviceWorker.getRegistration();
              const worker = reg && (reg.active || reg.waiting);
              if (worker) worker.postMessage({ type: 'clear-data' });
            } catch (e) { /* no worker, or storage blocked */ }
            window.location.href = '/login';
          },
        }, T.icon('box-arrow-right'), 'Sign out'),
      ]),
    ]);

    const userWrap = h('div.tc-user', [userBtn, userMenu]);

    /* Attention panel ---------------------------------------------------
       One bell, two lists: the enquiries that still need a person, and the
       day's to-do. Both mean "something needs somebody", so they share one
       surface instead of competing as two bells in the same corner. Modelled
       on ConnectLink's floating panel — it overlays the page, opens from the
       bell, and closes the moment you click anywhere else. */
    let attnOpen = false;
    let attnTab = 'enquiries';
    const attnCount = { enquiries: 0, tasks: 0 };

    const attnBadge = h('span.tc-bell-count', { hidden: true });
    const attnBell = h('button.tc-bell', {
      type: 'button',
      'aria-haspopup': 'dialog',
      'aria-expanded': 'false',
      title: 'Enquiries and today’s to-do',
      onclick: (e) => { e.stopPropagation(); toggleAttn(); },
    }, [T.icon('bell'), attnBadge]);

    const enqBody = h('div.tc-bell-body');
    const todoBody = h('div.tc-bell-body', { hidden: true });
    const enqTabCount = h('span.tc-bell-tabcount', { hidden: true });
    const todoTabCount = h('span.tc-bell-tabcount', { hidden: true });

    const attnTabs = {
      enquiries: h('button.tc-bell-tab', {
        type: 'button', onclick: () => setAttnTab('enquiries'),
      }, [T.icon('calendar-check'), h('span', 'Enquiries'), enqTabCount]),
      todo: h('button.tc-bell-tab', {
        type: 'button', onclick: () => setAttnTab('todo'),
      }, [T.icon('list-check'), h('span', 'To-do'), todoTabCount]),
    };

    const attnPanel = h('div.tc-bell-panel', {
      role: 'dialog', 'aria-label': 'Enquiries and to-do', hidden: true,
    }, [
      h('div.tc-bell-head', [
        h('div.tc-bell-tabs', [attnTabs.enquiries, attnTabs.todo]),
        h('span.flex-fill'),
        h('button.tc-bell-act', {
          type: 'button', title: 'Open the full screen',
          onclick: () => {
            toggleAttn(false);
            T.navigate(attnTab === 'todo' ? '/todo' : '/bookings');
          },
        }, T.icon('box-arrow-up-right')),
        h('button.tc-bell-act', {
          type: 'button', title: 'Collapse',
          onclick: () => setAttnMin(true),
        }, T.icon('dash')),
        h('button.tc-bell-x', {
          type: 'button', title: 'Close',
          onclick: () => toggleAttn(false),
        }, '\u00d7'),
      ]),
      enqBody,
      todoBody,
    ]);

    /* ── offline sync indicator ─────────────────────────────────────────
       Work done while the connection was down sits in a queue in the browser.
       Queued work that nobody can see is worse than work that was refused — the
       operator walks away believing the payment was recorded. So the count is
       always in the top bar, and anything the server *refused* is spelled out
       here rather than retried in silence for ever.
       ────────────────────────────────────────────────────────────────── */
    let syncOpen = false;
    const outboxBadge = h('span.tc-bell-count', { hidden: true, id: 'outboxCount' });
    const outboxBtn = h('button.tc-bell', {
      type: 'button', id: 'outboxBtn',
      title: 'Nothing waiting to sync',
      'aria-haspopup': 'dialog', 'aria-expanded': 'false',
      onclick: (e) => { e.stopPropagation(); toggleSync(); },
    }, [T.icon('arrow-repeat'), outboxBadge]);

    const outboxBody = h('div.tc-bell-body');
    const outboxPanel = h('div.tc-bell-panel', {
      role: 'dialog', 'aria-label': 'Changes waiting to sync', hidden: true,
    }, [
      h('div.tc-bell-head', [
        h('div.tc-bell-tabs', [h('span.tc-bell-tab.is-active',
          [T.icon('arrow-repeat'), h('span', 'Waiting to sync')])]),
        h('span.flex-fill'),
        h('button.tc-bell-x', {
          type: 'button', title: 'Close', onclick: () => toggleSync(false),
        }, '\u00d7'),
      ]),
      outboxBody,
    ]);

    function toggleSync(force) {
      syncOpen = force === undefined ? !syncOpen : force;
      outboxPanel.hidden = !syncOpen;
      outboxBtn.classList.toggle('is-open', syncOpen);
      outboxBtn.setAttribute('aria-expanded', String(syncOpen));
      if (syncOpen) renderOutboxPanel();
    }

    function whenLabel(ms) {
      const secs = Math.max(0, Math.round((Date.now() - ms) / 1000));
      if (secs < 60) return 'just now';
      if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
      if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
      return `${Math.floor(secs / 86400)} d ago`;
    }

    /* Turn "POST /api/invoices/4/payment" into something a foreman would say.
       Lives in core.js now: every screen that shows a queued change names it the
       same way, or the same action reads as two different things depending on
       where you happen to be looking. */
    const describeChange = T.describeChange;

    async function renderOutboxPanel() {
      const items = await T.outboxPending();
      if (!items.length) {
        T.mount(outboxBody, h('div.tc-bell-note.is-clear', [
          h('span.tc-bell-ok', T.icon('check2-circle')),
          ' Everything is saved.',
        ]));
        return;
      }
      T.mount(outboxBody, [
        h('div.tc-bell-rows', items.map((item) => h('div.tc-bell-row'
          + (item.state === 'failed' ? '.is-request' : ''), [
          h('div.tc-bell-row-main', [
            h('span.tc-bell-name', describeChange(item)),
            h('span.tc-bell-meta', item.state === 'failed'
              ? `Could not be saved — ${item.error || 'the server refused it'}`
              : `Waiting · done ${whenLabel(item.queued_at)}`
                + (item.attempts ? ` · tried ${item.attempts}x` : '')),
          ]),
          h('div.tc-bell-row-actions', [
            item.state === 'failed'
              ? h('button.btn.btn-sm.btn-outline-secondary', {
                  type: 'button', title: 'Throw this change away',
                  onclick: async () => {
                    await T.outboxDrop(item.id);
                    renderOutboxPanel();
                  },
                }, 'Discard')
              : null,
          ]),
        ]))),
        h('div.tc-bell-foot', [
          h('button.btn.btn-sm.btn-outline-secondary', {
            type: 'button', onclick: async (e) => {
              e.target.disabled = true;
              await T.outboxRetryNow();
              e.target.disabled = false;
              renderOutboxPanel();
            },
          }, T.icon('arrow-clockwise'), ' Try now'),
          h('span.flex-fill'),
          h('span.small.text-secondary',
            `${items.length} change${items.length === 1 ? '' : 's'} on this device`),
        ]),
      ]);
    }
    // Repaint the panel whenever the queue changes, from anywhere in the app.
    T.onPending(() => { if (syncOpen) renderOutboxPanel(); });

    /* ── "showing data from 14:20" ───────────────────────────────────────
       Served from the worker's cache, so it is real but it is not live. Said out
       loud rather than left to be assumed, because the number someone reads here
       is sometimes the number they take money against. Hidden the moment a
       single response comes back from the network again.
       ────────────────────────────────────────────────────────────────── */
    const staleChip = h('button.tc-stale-chip', {
      type: 'button', hidden: true,
      title: 'This screen was served from this device. Click to try the network again.',
      onclick: async (e) => {
        e.stopPropagation();
        await T.replayOutbox();
        T.renderRoute();
      },
    });

    function paintFreshness(iso) {
      if (!iso) { staleChip.hidden = true; return; }
      const d = new Date(iso);
      const when = isNaN(d) ? null
        : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      T.mount(staleChip, [
        T.icon('cloud-slash'),
        h('span', when ? `Data as of ${when}` : 'Offline copy'),
      ]);
      staleChip.hidden = false;
    }
    T.onFreshness(paintFreshness);

    const ATT_MIN_KEY = 'topclass.bell.min';
    function setAttnMin(min) {
      attnPanel.classList.toggle('is-min', !!min);
      try { localStorage.setItem(ATT_MIN_KEY, min ? '1' : '0'); } catch (e) { /* ignore */ }
    }
    try { setAttnMin(localStorage.getItem(ATT_MIN_KEY) === '1'); } catch (e) { /* ignore */ }

    function setAttnTab(tab) {
      attnTab = tab;
      Object.entries(attnTabs).forEach(([id, el]) =>
        el.classList.toggle('is-active', id === tab));
      enqBody.hidden = tab !== 'enquiries';
      todoBody.hidden = tab !== 'todo';
      if (!attnOpen) return;
      if (tab === 'todo') loadTodo();
      else loadEnquiries();
    }

    function toggleAttn(force) {
      attnOpen = force === undefined ? !attnOpen : force;
      attnPanel.hidden = !attnOpen;
      attnBell.classList.toggle('is-open', attnOpen);
      attnBell.setAttribute('aria-expanded', String(attnOpen));
      if (attnOpen) setAttnTab(attnTab);
    }

    /* The badge is the sum of both lists: either one alone would hide the
       other's work, which is the whole reason they share a bell. */
    function setBellCount() {
      const total = (attnCount.enquiries || 0) + (attnCount.tasks || 0);
      attnBadge.textContent = total > 9 ? '9+' : String(total);
      attnBadge.hidden = !total;
      attnBell.classList.toggle('has-items', !!total);
    }

    async function loadEnquiries() {
      T.mount(enqBody, T.spinner('Checking for enquiries…'));
      try {
        /* Two different lists, because they are two different jobs. A new
           enquiry is waiting for a decision; a reschedule request is a customer
           waiting on an answer about a booking that is already confirmed. Only
           the first is `status=REQUESTED`, so the second needs its own call —
           and it is the one nobody would otherwise notice. */
        const [data, asked] = await Promise.all([
          api.get('/api/bookings?status=REQUESTED', { silent: true }),
          api.get('/api/bookings?reschedule_requests=1', { silent: true }),
        ]);
        renderEnquiries(data.items || [], asked.items || []);
      } catch (err) {
        T.mount(enqBody, h('div.tc-bell-note', 'Could not load the enquiry list.'));
      }
    }

    /* An enquiry is not a booking until somebody confirms it — hence
       "Confirm" here and "Attend" for recording who dealt with it. */
    function renderEnquiries(items, asked) {
      const requests = asked || [];
      const total = items.length + requests.length;
      attnCount.enquiries = total;
      enqTabCount.textContent = String(total);
      enqTabCount.hidden = !total;
      setBellCount();

      if (!total) {
        T.mount(enqBody, h('div.tc-bell-note.is-clear', [
          h('span.tc-bell-ok', T.icon('check2-circle')),
          ' Nothing waiting — every enquiry has been dealt with.',
        ]));
        return;
      }

      /* Requests first: somebody is waiting on an answer about a time they
         have already been given, and the clock is running against it. */
      T.mount(enqBody, h('div.tc-bell-rows', [
        ...requests.map((b) => h('div.tc-bell-row.is-request', [
          h('div.tc-bell-row-main', [
            h('span.tc-bell-ref', b.display_reference || b.reference),
            h('span.tc-bell-name', b.customer_name || '—'),
            h('span.tc-bell-meta', [
              'Wants to move to ',
              h('strong', b.requested_slot_text || 'another time'),
              ` · currently ${dateShort(b.slot_date)}${b.slot_time ? ` ${b.slot_time}` : ''}`,
            ]),
            b.requested_note
              ? h('span.tc-bell-meta.is-note', `"${b.requested_note}"`)
              : null,
          ]),
          h('div.tc-bell-row-actions', [
            h('button.btn.btn-sm.btn-brand', {
              type: 'button', title: 'Agree to the new time and tell the customer',
              onclick: () => act(() => T.bookingAcceptReschedule(b)),
            }, 'Do as asked'),
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', title: 'Keep the appointment where it is',
              onclick: () => act(() => T.bookingDeclineReschedule(b)),
            }, 'Keep as is'),
          ]),
        ])),
        ...items.map((b) => h('div.tc-bell-row', [
          h('div.tc-bell-row-main', [
            h('span.tc-bell-ref', b.reference),
            h('span.tc-bell-name', b.customer_name || '—'),
            h('span.tc-bell-meta', [
              b.service,
              ` · ${dateShort(b.slot_date)}`,
              b.slot_time ? ` ${b.slot_time}` : '',
              ` · ${b.source}`,
            ]),
          ]),
          h('div.tc-bell-row-actions', [
            h('button.btn.btn-sm.btn-brand', {
              type: 'button', onclick: () => act(() => T.bookingConfirm(b)),
            }, 'Confirm'),
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', onclick: () => act(() => T.bookingAttend(b)),
            }, 'Attend'),
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', title: 'Reschedule and notify the customer',
              onclick: () => act(() => T.bookingReschedule(b)),
            }, T.icon('calendar-week')),
          ]),
        ])),
      ]));
    }

    async function act(action) {
      await action();
      loadEnquiries();
    }

    /* ── to-do tab ──────────────────────────────────────────────────── */
    async function loadTodo() {
      T.mount(todoBody, T.spinner('Loading the list…'));
      try {
        const [day, week] = await Promise.all([
          api.get('/api/tasks?window=day&status=open', { silent: true }),
          api.get('/api/tasks?window=week&status=open', { silent: true }),
        ]);
        renderTodo(day, week);
      } catch (err) {
        T.mount(todoBody, h('div.tc-bell-note', 'Could not load the to-do list.'));
      }
    }

    function renderTodo(day, week) {
      const items = day.items || [];
      const laterThisWeek = Math.max(0, (week.count || 0) - (day.count || 0));

      attnCount.tasks = items.length;
      todoTabCount.textContent = String(items.length);
      todoTabCount.hidden = !items.length;
      setBellCount();

      // The foot is always rendered, so there is still a way to add the next
      // thing when the list happens to be empty.
      T.mount(todoBody, [
        items.length
          ? h('div.tc-bell-rows', items.map((t) => h('label.tc-bell-row.tc-bell-todo', [
              h('input.form-check-input', {
                type: 'checkbox', onchange: () => completeTask(t),
              }),
              h('div.tc-bell-row-main', [
                h('span.tc-bell-name', t.title),
                h('span.tc-bell-meta', [
                  t.custodian || 'Unassigned',
                  t.is_overdue ? ' · overdue' : '',
                  t.category ? ` · ${t.category}` : '',
                ].join('')),
              ]),
            ])))
          : h('div.tc-bell-note.is-clear', [
              h('span.tc-bell-ok', T.icon('check2-circle')),
              ' Nothing outstanding for today.',
            ]),
        laterThisWeek
          ? h('div.tc-bell-more', `${laterThisWeek} more due later this week`)
          : null,
        h('div.tc-bell-foot', [
          h('button.btn.btn-sm.btn-outline-secondary', {
            type: 'button', onclick: addTask,
          }, T.icon('plus-lg'), ' Add task'),
          h('span.flex-fill'),
          h('a.small', { href: '#/todo', onclick: () => toggleAttn(false) },
            'Open the board'),
        ]),
      ]);
    }

    async function completeTask(task) {
      await api.patch(`/api/tasks/${task.id}`, { status: 'DONE' });
      T.toast('Task done.', 'success');
      loadTodo();
    }

    async function addTask() {
      if (!T.newTask) return;
      await T.newTask();
      loadTodo();
    }

    /* Topbar --------------------------------------------------------- */
    const paletteBtn = h('button.tc-search-trigger', {
      type: 'button',
      onclick: () => T.cmdk && T.cmdk.open(''),
      'aria-label': 'Open search and commands',
    }, [
      T.icon('search'),
      h('span.tc-search-label', 'Search job cards, customers, anything…'),
      h('kbd.tc-kbd.d-none.d-md-inline', 'Ctrl K'),
    ]);

    const topbar = h('header.tc-topbar', [
      h('button.btn.btn-icon.btn-outline-secondary.d-lg-none', {
        type: 'button',
        'aria-label': 'Toggle navigation',
        onclick: () => {
          const sidebar = document.querySelector('.tc-sidebar');
          const opening = !sidebar.classList.contains('open');
          sidebar.classList.toggle('open', opening);
          scrim().classList.toggle('show', opening);
        },
      }, T.icon('list')),

      h('div.tc-crumbs', [
        h('span.tc-crumb-home', T.icon('grid-1x2')),
        h('span.tc-crumb-sep', '/'),
        h('span#pageCrumb.tc-crumb-current', 'dashboard'),
      ]),

      h('div.ms-auto.d-flex.align-items-center.gap-2', [
        paletteBtn,
        h('a.btn.btn-icon.btn-outline-secondary.d-none.d-lg-inline-grid', {
          href: '#/activity', title: 'Activity log', 'aria-label': 'Activity log',
        }, T.icon('clock-history')),
        h('a.btn.btn-icon.btn-outline-success', {
          href: '#/inbox', title: 'WhatsApp inbox', 'aria-label': 'WhatsApp inbox',
        }, T.icon('whatsapp')),
        h('span.tc-topbar-sep.d-none.d-sm-block'),
        staleChip,
        outboxBtn,
        attnBell,
        userWrap,
      ]),
    ]);

    /* Quick actions: a strip of cards under the top bar, on every screen. */
    const quickbar = h('div.tc-quickbar', { role: 'group', 'aria-label': 'Quick actions' },
      QUICK_ACTIONS.map((action) => {
        const tag = (action.href ? 'a' : 'button')
          + '.tc-quick-card' + (action.tone ? '.is-' + action.tone : '');
        const props = {
          title: action.hint,
          'aria-label': `${action.label} — ${action.hint}`,
          onclick: (e) => {
            if (action.run) { e.preventDefault(); action.run(); return; }
            /* Same hash means no hashchange event — re-render by hand. */
            if (window.location.hash === action.href) { e.preventDefault(); T.renderRoute(); }
          },
        };
        if (action.href) props.href = action.href;
        else props.type = 'button';
        return h(tag, props, [
          h('span.tc-quick-icon', T.icon(action.icon)),
          h('div.tc-quick-text', [
            h('span.tc-quick-label', action.label),
            h('span.tc-quick-hint', action.hint),
          ]),
        ]);
      }));

    const outlet = h('main.tc-content', { id: 'main-content' },
      h('div#viewOutlet.tc-view'));

    /* Behaviour ------------------------------------------------------ */
    applyRail(isRail(), sidebar);

    /* core.js fires this once the active class has moved, which is the only
       moment the pill needs to be told about. */
    syncNavGlider = syncGlider;
    document.addEventListener('topclass:nav', () => syncGlider());
    /* Alt+B animates the rail's width and every row rides along with it, so the
       pill has to keep re-measuring for the whole of that transition. Watching
       the sidebar's box does exactly that, and it covers a plain window resize
       for free — a single `transitionend` would leave the pill on the old
       geometry until the next navigation. `syncNavGlider` in `applyRail` is the
       belt to this braces: it settles the instant case where the width does not
       animate at all. */
    if (typeof ResizeObserver === 'function') {
      navObserver = new ResizeObserver(() => syncGlider());
      navObserver.observe(sidebar);
    } else {
      window.addEventListener('resize', () => syncGlider());
    }
    /* Snap on the first placement, glide on every one after it. */
    requestAnimationFrame(() => requestAnimationFrame(() => {
      glider.classList.remove('no-anim');
      hoverPill.classList.remove('no-anim');
      revealActive();
    }));

    async function pollBadges() {
      try {
        const data = await api.get('/api/badges', { silent: true });
        counts = data;
        Object.entries(pills).forEach(([key, pill]) => {
          const count = data[key] || 0;
          pill.textContent = count > 99 ? '99+' : String(count);
          pill.hidden = !count;
          pill.className = `tc-nav-pill${key === 'whatsapp' && count ? ' is-success' : ''}`
            + `${['jobs_overdue', 'invoices', 'parts'].includes(key) && count ? ' is-alert' : ''}`;
        });
        const t = document.querySelector('.tc-topbar') || topbar;
        t.classList.toggle('has-attention', (data.attention || 0) > 0);
        // Keep the badge live even while the panel is shut. Two cheap counts
        // beat polling two list endpoints on every tick.
        if (!attnOpen) {
          attnCount.enquiries = data.bookings || 0;
          attnCount.tasks = data.tasks || 0;
          setBellCount();
        }
      } catch (e) { /* silent — badges are a nicety */ }
      setTimeout(pollBadges, 30000);
    }
    setTimeout(pollBadges, 700);

    /* Offline queue. Bound once, and drained on boot: whatever a previous
       session parked goes out as soon as the app is open and the link is there. */
    T.bindOutbox();
    window.addEventListener('topclass:synced', () => {
      // The visible screen was drawn before those changes existed, so its figures
      // are stale by definition.
      T.renderRoute();
    });

    document.addEventListener('keydown', (e) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
      if (typing || e.ctrlKey || e.metaKey) return;
      if (e.altKey && e.key.toLowerCase() === 'b') { e.preventDefault(); toggleRail(); return; }
      if (e.altKey) return;
      /* Arms the key map; the second key is picked up by the capture listener
         above, which stops it from reaching any of the shortcuts below. */
      if (e.key.toLowerCase() === 'g') { e.preventDefault(); setChord(true); return; }
      if (e.key === 'n') { e.preventDefault(); T.newJobCard(); }
      if (e.key === 'b') { e.preventDefault(); T.navigate('/board'); }
      if (e.key === 'i') { e.preventDefault(); T.navigate('/inbox'); }
      if (e.key === '?') { e.preventDefault(); T.navigate('/activity'); }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeSidebar(); toggleUserMenu(false); toggleAttn(false); toggleSync(false);
      }
    });

    document.addEventListener('click', (e) => {
      if (isChord()) setChord(false);
      if (userOpen && !userWrap.contains(e.target)) toggleUserMenu(false);
      if (attnOpen && !attnPanel.contains(e.target) && !attnBell.contains(e.target)) {
        toggleAttn(false);
      }
      if (syncOpen && !outboxPanel.contains(e.target) && !outboxBtn.contains(e.target)) {
        toggleSync(false);
      }
    });

    return h('div', [sidebar, h('div.tc-main', [topbar, quickbar, outlet]),
                     attnPanel, outboxPanel, navPop]);
  }

  /* ── booking actions ──────────────────────────────────────────────────
     Shared by the enquiry bell and the bookings screen so the two cannot
     drift apart. The rules themselves live server-side; these only gather
     what the server needs and report the outcome. */
  function bookingConfirm(booking) {
    return api.patch(`/api/bookings/${booking.id}`, { status: 'CONFIRMED' })
      .then((res) => {
        T.toast(`Booking ${res.booking.display_reference} confirmed.`, 'success');
        return res;
      });
  }

  async function bookingAttend(booking) {
    let users = [];
    try { users = (await api.get('/api/users')).items || []; } catch (e) { users = []; }

    const res = await T.formModal({
      title: `Attend to ${booking.reference}`,
      subtitle: 'Record who dealt with this enquiry.',
      icon: 'person-check',
      fields: [{
        name: 'attended_by_id', label: 'Attended by', type: 'select', col: 12, required: true,
        placeholder: '— Who dealt with it? —',
        options: users.map((u) => ({ value: u.id, label: `${u.full_name} · ${u.role_label}` })),
      }],
      submitLabel: 'Mark attended',
    });
    if (!res) return null;

    const saved = await api.patch(`/api/bookings/${booking.id}`, {
      status: 'ATTENDED', attended_by_id: res.attended_by_id,
    });
    T.toast(`${booking.reference} marked attended to.`);
    return saved;
  }

  async function bookingReschedule(booking) {
    const when = booking.slot_date ? dateShort(booking.slot_date) : 'not set';
    const res = await T.formModal({
      title: `Reschedule ${booking.display_reference || booking.reference}`,
      subtitle: `Currently ${when}${booking.slot_time ? ` at ${booking.slot_time}` : ''}.`
        + ' The customer is messaged as soon as you save.',
      icon: 'calendar-week',
      fields: [
        { name: 'slot_date', label: 'New date', type: 'date', col: 7,
          required: true, value: booking.slot_date },
        { name: 'slot_time', label: 'Time', type: 'select', col: 5,
          value: booking.slot_time || '', options: ['', ...bookingSlots()] },
      ],
      submitLabel: 'Move booking',
    });
    if (!res) return null;

    const saved = await api.post(`/api/bookings/${booking.id}/reschedule`, res);
    T.toast(saved.message, saved.notified ? 'success' : 'warning');
    return saved;
  }

  function bookingSlots() {
    const meta = T.store.get('meta') || {};
    return meta.booking_slots || ['08:00', '09:00', '10:00', '11:00', '12:00',
      '13:00', '14:00', '15:00', '16:00'];
  }

  /* A customer cannot move their own appointment — they ask, and the desk
     agrees. These two are the desk answering, and they live here beside the
     other booking actions so the bell and the screen cannot offer different
     answers to the same request. */
  async function bookingAcceptReschedule(booking) {
    const res = await api.post(`/api/bookings/${booking.id}/reschedule/accept`, {});
    T.toast(res.message, res.notified ? 'success' : 'warning');
    return res;
  }

  async function bookingDeclineReschedule(booking) {
    const opts = await T.formModal({
      title: `Keep ${booking.display_reference || booking.reference} as it is`,
      subtitle: `${booking.customer_name || 'The customer'} asked for `
        + `${booking.requested_slot_text || 'another time'}. They are messaged `
        + 'either way, so say why — a refusal with a reason is still an answer.',
      icon: 'calendar-x',
      fields: [{
        name: 'reason', label: 'What should we tell them? *', type: 'textarea',
        col: 12, required: true,
        placeholder: 'e.g. That morning is fully booked — we can do the afternoon.',
        hint: 'Sent to the customer word for word.',
      }],
      submitLabel: 'Keep the appointment',
    });
    if (!opts) return null;
    const res = await api.post(`/api/bookings/${booking.id}/reschedule/decline`, opts);
    T.toast(res.message, res.notified ? 'success' : 'warning');
    return res;
  }

  T.bookingConfirm = bookingConfirm;
  T.bookingAttend = bookingAttend;
  T.bookingReschedule = bookingReschedule;
  T.bookingAcceptReschedule = bookingAcceptReschedule;
  T.bookingDeclineReschedule = bookingDeclineReschedule;

  function scrim() {
    let el = document.getElementById('sidebarScrim');
    if (!el) {
      el = document.createElement('div');
      el.className = 'sidebar-scrim';
      el.id = 'sidebarScrim';
      el.addEventListener('click', closeSidebar);
      document.body.appendChild(el);
    }
    return el;
  }

  function closeSidebar() {
    const sidebar = document.getElementById('tcSidebar');
    if (sidebar) sidebar.classList.remove('open');
    scrim().classList.remove('show');
  }

  function applyRail(rail, sidebar) {
    const bar = sidebar || document.getElementById('tcSidebar');
    document.documentElement.classList.toggle('sidebar-rail', rail);
    if (bar) {
      bar.classList.toggle('rail', rail);
      const toggle = bar.querySelector('.tc-rail-toggle');
      if (toggle) {
        toggle.title = rail ? 'Expand navigation  (Alt+B)' : 'Collapse navigation  (Alt+B)';
        toggle.innerHTML = '';
        toggle.appendChild(T.icon(rail ? 'chevron-double-right' : 'chevron-double-left'));
      }
    }
    /* The rail just changed width, so the active pill is now the wrong size and
       in the wrong place. */
    if (syncNavGlider) syncNavGlider();
  }

  function toggleRail() {
    const rail = !isRail();
    savePrefs({ rail });
    applyRail(rail);
  }

  /* ── Row density ────────────────────────────────────────────────────
     Long tables — job cards, payments, activity — run to hundreds of rows.
     Comfortable is the default; compact fits roughly a third more of the day
     on one screen, which is what a front-desk tablet wants. It is a class on
     the root that swaps the table padding tokens, so it costs one repaint and
     no reload. */
  function applyDensity(compact) {
    document.documentElement.classList.toggle('density-compact', compact);
  }

  function toggleDensity() {
    const compact = !isCompact();
    savePrefs({ compact_rows: compact });
    applyDensity(compact);
    T.toast(compact ? 'Compact rows on — more of the list per screen.' : 'Comfortable rows on.');
  }

  /* ── boot ────────────────────────────────────────────────────────── */
  async function boot() {
    let payload = window.__BOOTSTRAP__;
    if (!payload || !payload.user) {
      payload = { user: (await api.get('/api/me')).user, meta: await api.get('/api/meta') };
    }
    if (!payload.meta || !payload.meta.stages) payload.meta = await api.get('/api/meta');
    T.store.set({ user: payload.user, meta: payload.meta });
    T.stageColours = {};
    (payload.meta.stages || []).forEach((s) => { T.stageColours[s.code] = s.colour; });

    /* Before layout, because layout applies the rail from them. Row density has
       no equivalent hook — the shell script sets it before first paint, but that
       only knows what the account and the browser cache had, so a value adopted
       from a legacy key would otherwise sit unused until the next load. */
    loadPrefs((payload.user || {}).id, (payload.user || {}).preferences);
    applyDensity(isCompact());
    T.mount('#app', layout(payload));
    T.startRouter();

    /* Surface connection loss only when there is real evidence of it. */
    window.addEventListener('offline', () => {
      T.setConnectionState(false);
      T.toast('You are offline — changes will not be saved.', 'warning');
    });
    window.addEventListener('online', () => {
      T.setConnectionState(true);
      T.toast('Back online.', 'success', { title: 'Reconnected', timeout: 2500 });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();