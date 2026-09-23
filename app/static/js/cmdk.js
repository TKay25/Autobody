/* Global command palette (Ctrl/Cmd + K).
   Fuzzy jump to any job, customer, vehicle, screen or action. */
(function () {
  const T = window.TCA;
  const { h, api } = T;

  const ACTIONS = [
    { id: 'new-job', label: 'New job card', hint: 'Intake', icon: 'plus-square', run: () => T.newJobCard() },
    { id: 'new-customer', label: 'New customer', hint: 'CRM', icon: 'person-plus', run: () => T.navigate('/customers?new=1') },
    { id: 'board', label: 'WIP board', hint: 'Workshop', icon: 'kanban', run: () => T.navigate('/board') },
    { id: 'dashboard', label: 'Dashboard', hint: 'Workshop', icon: 'speedometer2', run: () => T.navigate('/dashboard') },
    { id: 'jobs', label: 'Job cards', hint: 'Workshop', icon: 'clipboard-check', run: () => T.navigate('/jobs') },
    { id: 'bookings', label: 'Bookings', hint: 'Workshop', icon: 'calendar-check', run: () => T.navigate('/bookings') },
    { id: 'customers', label: 'Customers', hint: 'CRM', icon: 'people', run: () => T.navigate('/customers') },
    { id: 'vehicles', label: 'Vehicles', hint: 'CRM', icon: 'car-front', run: () => T.navigate('/vehicles') },
    { id: 'inbox', label: 'WhatsApp inbox', hint: 'Comms', icon: 'whatsapp', run: () => T.navigate('/inbox') },
    { id: 'claims', label: 'Insurance claims', hint: 'Money', icon: 'shield-check', run: () => T.navigate('/claims') },
    { id: 'invoices', label: 'Invoices', hint: 'Money', icon: 'receipt', run: () => T.navigate('/invoices') },
    { id: 'reports', label: 'Reports', hint: 'Money', icon: 'graph-up-arrow', run: () => T.navigate('/reports') },
    { id: 'parts', label: 'Parts & stock', hint: 'Resources', icon: 'box-seam', run: () => T.navigate('/parts') },
    { id: 'activity', label: 'Activity log', hint: 'Resources', icon: 'clock-history', run: () => T.navigate('/activity') },
    { id: 'staff', label: 'Staff & settings', hint: 'Resources', icon: 'gear', run: () => T.navigate('/staff') },
    { id: 'export-jobs', label: 'Export job cards to CSV', hint: 'Download', icon: 'download', run: () => download('jobs') },
    { id: 'export-invoices', label: 'Export invoices to CSV', hint: 'Download', icon: 'download', run: () => download('invoices') },
    { id: 'export-claims', label: 'Export claims to CSV', hint: 'Download', icon: 'download', run: () => download('claims') },
    { id: 'export-parts', label: 'Export stock to CSV', hint: 'Download', icon: 'download', run: () => download('parts') },
    { id: 'simulator', label: 'Test the WhatsApp bot', hint: 'Comms', icon: 'robot', run: () => T.navigate('/inbox?sim=1') },
  ];

  function download(dataset) {
    window.location.href = `/api/export/${dataset}.csv`;
    T.toast(`Preparing ${dataset} export…`, 'info');
  }

  /* ── fuzzy scoring: subsequence match with word-boundary bonus ────── */
  function score(needle, haystack) {
    if (!needle) return 1;
    const n = needle.toLowerCase();
    const t = haystack.toLowerCase();
    if (t.includes(n)) return 100 - t.indexOf(n);
    let ti = 0;
    let hits = 0;
    for (const ch of n) {
      const found = t.indexOf(ch, ti);
      if (found === -1) return 0;
      hits += found === ti ? 2 : 1;
      ti = found + 1;
    }
    return hits;
  }

  let host = null;
  let input = null;
  let listEl = null;
  let state = { items: [], active: 0, query: '', open: false };
  let searchSeq = 0;

  function isOpen() { return state.open; }

  function buildItems(live) {
    const q = state.query.trim();
    const items = [];

    ACTIONS
      .map((a) => ({ ...a, kind: 'action', _score: score(q, a.label + ' ' + (a.hint || '')) }))
      .filter((a) => a._score > 0)
      .sort((a, b) => b._score - a._score)
      .forEach((a) => items.push(a));

    (live.jobs || []).forEach((j) => items.push({
      kind: 'job', icon: 'clipboard-check',
      label: `${j.job_no} · ${j.reg_no}`,
      hint: `${j.customer} — ${j.stage_label}`,
      _score: 90,
      run: () => T.navigate(`/jobs/${j.id}`),
    }));
    (live.customers || []).forEach((c) => items.push({
      kind: 'customer', icon: 'person',
      label: c.name,
      hint: `${c.phone || ''} · ${c.open_jobs} open`,
      _score: 85,
      run: () => T.navigate('/customers'),
    }));
    (live.vehicles || []).forEach((v) => items.push({
      kind: 'vehicle', icon: 'car-front',
      label: `${v.reg_no} · ${v.title}`,
      hint: v.customer || '',
      _score: 85,
      run: () => T.navigate(`/vehicles`),
    }));

    return items;
  }

  function render() {
    const q = state.query.trim();
    const groups = [
      { key: 'job', label: 'Job cards' },
      { key: 'customer', label: 'Customers' },
      { key: 'vehicle', label: 'Vehicles' },
      { key: 'action', label: q ? 'Actions' : 'Jump to' },
    ];

    const rows = [];
    let flatIndex = 0;
    groups.forEach((g) => {
      const group = state.items.filter((i) => i.kind === g.key);
      if (!group.length) return;
      rows.push(h('div.cmdk-group.eyebrow', g.label));
      group.slice(0, g.key === 'action' ? 8 : 6).forEach((item) => {
        const idx = flatIndex++;
        rows.push(h('button.cmdk-item', {
          type: 'button',
          class: idx === state.active ? 'cmdk-item active' : 'cmdk-item',
          onclick: () => run(item),
          onmouseenter: () => { state.active = idx; paint(); },
        }, [
          T.icon(item.icon),
          h('span', item.label),
          item.hint ? h('span.cmdk-hint', item.hint) : null,
        ]));
      });
    });

    if (!rows.length) {
      T.mount(listEl, h('div.cmdk-empty', [
        T.icon('search'), h('div', 'Nothing matched.'),
        h('div.small', 'Try a job card number, registration, customer or command.'),
      ]));
    } else {
      T.mount(listEl, rows);
    }
    const activeEl = listEl.querySelector('.cmdk-item.active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
  }

  /* The flat order must match what render() draws, so Enter always opens
     the highlighted row. */
  function flatItems() {
    const out = [];
    ['job', 'customer', 'vehicle', 'action'].forEach((k) => {
      state.items.filter((i) => i.kind === k).slice(0, k === 'action' ? 8 : 6)
        .forEach((i) => out.push(i));
    });
    return out;
  }

  function paint() {
    listEl.querySelectorAll('.cmdk-item').forEach((el) => el.classList.remove('active'));
    const items = listEl.querySelectorAll('.cmdk-item');
    if (items[state.active]) items[state.active].classList.add('active');
  }

  function run(item) {
    if (!item || !item.run) return;
    close();
    item.run();
  }

  async function refresh() {
    const items = buildItems({});
    state.items = items;
    state.active = 0;
    render();

    const q = state.query.trim();
    if (q.length < 2) return;
    const seq = ++searchSeq;
    try {
      const live = await api.get(`/api/search?q=${encodeURIComponent(q)}`, { silent: true });
      if (seq !== searchSeq) return;       // a newer keystroke won
      state.items = buildItems(live);
      state.active = 0;
      render();
    } catch (e) { /* keep the command-only list */ }
  }

  function move(delta) {
    const n = listEl.querySelectorAll('.cmdk-item').length;
    if (!n) return;
    state.active = (state.active + delta + n) % n;
    paint();
  }

  function open(seed) {
    if (state.open) return;
    state.open = true;
    state.query = seed || '';
    host = document.getElementById('cmdkHost');

    input = h('input.cmdk-input', {
      type: 'text', placeholder: 'Search job cards, customers, vehicles or run a command…',
      autocomplete: 'off', spellcheck: 'false',
      'aria-label': 'Command palette search',
      value: state.query,
    });
    listEl = h('div.cmdk-list', { role: 'listbox' });

    const dialog = h('div.cmdk', { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Command palette' }, [
      h('div.cmdk-input-wrap', [T.icon('search'), input,
        h('kbd', 'Esc')]),
      listEl,
      h('div.cmdk-foot', [
        h('span', h('kbd', '↑'), ' ', h('kbd', '↓'), ' navigate'),
        h('span', h('kbd', '↵'), ' open'),
        h('span', h('kbd', 'Ctrl'), ' ', h('kbd', 'K'), ' toggle'),
      ]),
    ]);

    const backdrop = h('div.cmdk-backdrop', { onclick: (e) => { if (e.target === backdrop) close(); } }, dialog);
    T.mount(host, backdrop);

    input.addEventListener('input', T.debounce(() => {
      state.query = input.value;
      refresh();
    }, 140));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); run(flatItems()[state.active]); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
    document.addEventListener('keydown', escHandler, true);

    refresh();
    setTimeout(() => input.focus(), 30);
  }

  function escHandler(e) {
    if (e.key === 'Escape' && state.open) { e.preventDefault(); close(); }
  }

  function close() {
    if (!state.open) return;
    state.open = false;
    document.removeEventListener('keydown', escHandler, true);
    if (host) host.innerHTML = '';
  }

  /* Enter should run the item at the active flat index. */
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      isOpen() ? close() : open('');
      return;
    }
    if (e.key === '/' && !typing && !isOpen()) { e.preventDefault(); open(''); }
  });

  T.cmdk = { open, close, isOpen };
})();
