/* App layout: sidebar, topbar, global search and route registration. */
(function () {
  const T = window.TCA;
  const { h, api, dateShort } = T;

  /* Navigation model. `badge` keys map to GET /api/badges. */
  const NAV = [
    { section: 'Workshop' },
    { route: '/dashboard', label: 'Dashboard', icon: 'speedometer2', hint: 'Overview of today' },
    { route: '/board', label: 'WIP board', icon: 'kanban', hint: 'Drag job cards through the shop' },
    { route: '/jobs', label: 'Job cards', icon: 'clipboard-check', badge: 'jobs', hint: 'Every vehicle in the shop' },
    { route: '/todo', label: 'To-do', icon: 'list-check', badge: 'tasks',
      hint: 'What has to happen today and this week' },
    { route: '/bookings', label: 'Enquiries & Bookings', icon: 'calendar-check', badge: 'bookings',
      hint: 'Enquiries, bookings and confirmations' },
    { section: 'Customers' },
    { route: '/customers', label: 'Customers', icon: 'people', hint: 'CRM and contact details' },
    { route: '/vehicles', label: 'Vehicles', icon: 'car-front', hint: 'Registration register' },
    { route: '/inbox', label: 'WhatsApp', icon: 'whatsapp', badge: 'whatsapp', hint: 'Chat with customers' },
    { section: 'Money' },
    { route: '/payments', label: 'Payments', icon: 'cash-coin',
      hint: 'Receipts, methods and takings' },
    { route: '/invoices', label: 'Invoices', icon: 'receipt', badge: 'invoices', hint: 'Billing and payments' },
    { route: '/reports', label: 'Reports', icon: 'graph-up-arrow', hint: 'Performance and margins' },
    { section: 'Resources' },
    { route: '/parts', label: 'Parts & stock', icon: 'box-seam', badge: 'parts', hint: 'Stock levels and suppliers' },
    { route: '/activity', label: 'Activity log', icon: 'clock-history', hint: 'Who changed what' },
    { route: '/staff', label: 'Staff & settings', icon: 'gear', hint: 'Accounts and bot setup' },
  ];

  /* Quick actions — the handful of jobs people start most often. Each renders
     as a card in the strip under the top bar. `run` fires a dialog in place;
     `href` sends you to the screen that owns the task. */
  const QUICK_ACTIONS = [
    { label: 'New job card', hint: 'Book a vehicle in and estimate it',
      icon: 'clipboard-plus', tone: 'brand', run: () => T.newJobCard() },
    { label: 'New quotation', hint: 'Price a job card that is already open',
      icon: 'calculator', run: () => T.newJobCard({ focus: 'estimate' }) },
    { label: 'Add stock item', hint: 'Parts, paint and consumables',
      icon: 'box-seam', href: '#/parts?new=1' },
    { label: 'Payments & invoices', hint: 'Record a receipt against an invoice',
      icon: 'cash-coin', href: '#/invoices' },
    { label: 'Enquiries and Bookings', hint: 'Phone, WhatsApp and walk-in requests',
      icon: 'calendar-check', href: '#/bookings' },
  ];

  const RAIL_KEY = 'topclass.sidebar.rail';

  function isRail() {
    try { return localStorage.getItem(RAIL_KEY) === '1'; } catch (e) { return false; }
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
    const nav = h('nav.tc-nav', { 'aria-label': 'Main navigation' },
      NAV.map((item) => {
        if (item.section) {
          return h('div.tc-nav-section', [
            h('span.tc-nav-section-label', item.section),
          ]);
        }
        const pill = item.badge ? h('span.tc-nav-pill', { hidden: true }) : null;
        if (pill) pills[item.badge] = pill;

        return h('a.tc-nav-item', {
          href: `#${item.route}`,
          'data-route': item.route,
          title: item.hint || item.label,
          'aria-label': item.label,
          onclick: () => closeSidebar(),
        }, [
          h('span.tc-nav-icon', T.icon(item.icon)),
          h('span.tc-nav-label', item.label),
          pill,
        ]);
      }));

    /* Foot: build stamp ---------------------------------------------- */
    const sideFoot = h('div.tc-side-foot', [
      h('span.tc-status-dot', { title: 'Connected' }),
      h('span', 'Live'),
      h('span.tc-side-foot-sep', '·'),
      h('span', `v${(window.__BOOTSTRAP__ || {}).version || '1.0'}`),
    ]);

    const sidebar = h('aside.tc-sidebar', { id: 'tcSidebar' }, [brand, nav, sideFoot]);

    /* Identity — the user menu sits at the right-hand end of the top bar. */
    let userOpen = false;

    function toggleUserMenu(force) {
      userOpen = force === undefined ? !userOpen : force;
      userMenu.hidden = !userOpen;
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

    /* Turn "POST /api/invoices/4/payment" into something a foreman would say. */
    function describeChange(item) {
      const path = String(item.url || '');
      const verb = item.method === 'DELETE' ? 'Remove'
        : item.method === 'PATCH' ? 'Update' : 'Add';
      const known = [
        [/\/invoices\/\d+\/payment/, 'Record a payment'],
        [/\/jobs\/\d+\/stage/, 'Move a job card stage'],
        [/\/jobs\/\d+\/advance/, 'Advance a job card'],
        [/\/jobs\/\d+\/qc/, 'Record a quality check'],
        [/\/jobs\/\d+\/estimate/, 'Save an estimate'],
        [/\/jobs\/\d+\/parts/, 'Fit a part'],
        [/\/jobs\/\d+\/photos/, 'Add a job photo'],
        [/\/jobs\/\d+\/documents/, 'Add a job document'],
        [/\/invoices\/\d+\/issue/, 'Issue an invoice'],
        [/\/jobs$/, 'Open a job card'],
        [/\/invoices$/, 'Raise an invoice'],
        [/\/tasks\/\d+$/, `${verb} a to-do`],
        [/\/tasks$/, 'Add a to-do'],
        [/\/parts\/\d+\/movement/, 'Record stock movement'],
        [/\/parts$/, 'Add a stock item'],
        [/\/customers/, 'Update a customer'],
        [/\/vehicles/, 'Update a vehicle'],
        [/\/bookings/, 'Update an enquiry'],
      ];
      for (const [re, label] of known) if (re.test(path)) return label;
      return `${verb} — ${path.replace('/api/', '').replace(/\/\d+/g, '')}`;
    }

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
    T.emitOutbox = () => { if (syncOpen) renderOutboxPanel(); };

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

    async function pollBadges() {
      try {
        const data = await api.get('/api/badges', { silent: true });
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
      if (userOpen && !userWrap.contains(e.target)) toggleUserMenu(false);
      if (attnOpen && !attnPanel.contains(e.target) && !attnBell.contains(e.target)) {
        toggleAttn(false);
      }
      if (syncOpen && !outboxPanel.contains(e.target) && !outboxBtn.contains(e.target)) {
        toggleSync(false);
      }
    });

    return h('div', [sidebar, h('div.tc-main', [topbar, quickbar, outlet]),
                     attnPanel, outboxPanel]);
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
  }

  function toggleRail() {
    const rail = !isRail();
    try { localStorage.setItem(RAIL_KEY, rail ? '1' : '0'); } catch (e) { /* ignore */ }
    applyRail(rail);
  }

  /* ── Row density ────────────────────────────────────────────────────
     Long tables — job cards, payments, activity — run to hundreds of rows.
     Comfortable is the default; compact fits roughly a third more of the day
     on one screen, which is what a front-desk tablet wants. It is a class on
     the root that swaps the table padding tokens, so it costs one repaint and
     no reload. */
  const DENSITY_KEY = 'topclass.rows.compact';

  function isCompact() {
    try { return localStorage.getItem(DENSITY_KEY) === '1'; } catch (e) { return false; }
  }

  function applyDensity(compact) {
    document.documentElement.classList.toggle('density-compact', compact);
  }

  function toggleDensity() {
    const compact = !isCompact();
    try { localStorage.setItem(DENSITY_KEY, compact ? '1' : '0'); } catch (e) { /* ignore */ }
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