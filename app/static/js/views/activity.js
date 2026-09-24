/* Activity log — the workshop's audit trail. */
(function () {
  const T = window.TCA;
  const { h, api, dateTime, relTime } = T;

  const FILTERS = [
    { value: '', label: 'Everything' },
    { value: 'job', label: 'Job cards' },
    { value: 'estimate', label: 'Estimates' },
    { value: 'invoice', label: 'Invoices' },
    { value: 'part', label: 'Stock' },
    { value: 'customer', label: 'Customers' },
    { value: 'vehicle', label: 'Vehicles' },
    { value: 'booking', label: 'Bookings' },
  ];

  function dayLabel(iso) {
    const d = new Date(iso);
    const today = new Date();
    const yesterday = new Date(Date.now() - 86400000);
    const same = (a, b) => a.toDateString() === b.toDateString();
    if (same(d, today)) return 'Today';
    if (same(d, yesterday)) return 'Yesterday';
    return d.toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
  }

  T.route('/activity', async (ctx) => {
    ctx.title = 'Activity log';
    const state = { entity_type: ctx.query.type || '', limit: 80 };
    const host = h('div');
    const summary = h('div.row.g-3.mb-3');

    async function load() {
      T.mount(host, T.skeletonTable(10, 2));
      const params = new URLSearchParams({ limit: String(state.limit) });
      if (state.entity_type) params.set('entity_type', state.entity_type);
      const data = await api.get(`/api/activity?${params}`);

      const today = new Date().toDateString();
      const todayCount = data.items.filter((a) => new Date(a.created_at).toDateString() === today).length;
      const actors = new Set(data.items.map((a) => a.actor_name)).size;

      T.mount(summary, [
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Entries shown', value: data.count, icon: 'clock-history', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Today', value: todayCount, icon: 'calendar-day', colour: 'brand',
          sub: 'Changes recorded in this session' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'People active', value: actors, icon: 'people', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Retention', value: 'Permanent', icon: 'shield-lock', colour: 'secondary',
          sub: 'Audit entries are never edited' })),
      ]);

      if (!data.items.length) {
        T.mount(host, T.emptyState('No activity recorded yet',
          'Job cards, estimates, payments and stock moves all appear here.', 'clock-history'));
        return;
      }

      // Group by calendar day, newest first.
      const groups = [];
      let current = null;
      data.items.forEach((entry) => {
        const label = dayLabel(entry.created_at);
        if (!current || current.label !== label) {
          current = { label, items: [] };
          groups.push(current);
        }
        current.items.push(entry);
      });

      T.mount(host, groups.map((group) => h('div.mb-4', [
        h('div.eyebrow.mb-2.d-flex.align-items-center.gap-2', [
          h('span', group.label),
          h('span.chip.chip-plain', `${group.items.length}`),
        ]),
        h('div.timeline-v2', group.items.map((entry) => h('div.item', {
          class: `item ${entry.tone || ''}`.trim(),
        }, [
          h('div.d-flex.align-items-start.gap-2.flex-wrap', [
            h('div.flex-fill', [
              h('div', { style: 'font-size:.875rem' }, entry.summary),
              h('div.small.text-secondary.d-flex.align-items-center.gap-2.flex-wrap', [
                h('span', [T.icon('person'), ' ', entry.actor_name]),
                h('span.text-secondary', '·'),
                h('span', { title: dateTime(entry.created_at) }, relTime(entry.created_at)),
                entry.entity_ref
                  ? h('a.chip.chip-plain.text-decoration-none', {
                      href: entry.job_id ? `#/jobs/${entry.job_id}` : null,
                    }, T.icon('tag'), entry.entity_ref)
                  : null,
              ]),
            ]),
            h('span.badge.text-bg-light.text-secondary.border', { style: 'font-weight:500' }, entry.action),
          ]),
        ]))),
      ])));
    }

    const filterBar = h('div.tc-toolbar.mb-3.no-print', [
      h('div.tc-input-icon', { style: 'width:auto' },
        T.icon('funnel'),
        h('select.form-select.form-select-sm', {
          style: 'width:auto;min-width:190px',
          'aria-label': 'Filter activity',
          onchange: (e) => { state.entity_type = e.target.value; load(); },
        }, FILTERS.map((f) => h('option', { value: f.value, selected: f.value === state.entity_type }, f.label)))),
      h('div.tc-toolbar-spacer'),
      h('button.btn.btn-sm.btn-outline-secondary', {
        onclick: () => window.print(),
      }, T.icon('printer'), ' Print'),
    ]);

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2.no-print', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Activity log'),
          h('div.small.text-secondary', 'Every change made to job cards, money and stock — who, what and when.'),
        ]),
        h('button.btn.btn-sm.btn-outline-secondary', { onclick: () => ctx.refresh() },
          T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      summary,
      filterBar,
      T.section({ title: 'Audit trail', body: host }),
    ]);
  });
})();
