/* Dashboard — today's workshop at a glance. */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  T.route('/dashboard', async (ctx) => {
    ctx.title = 'Dashboard';
    const data = await api.get('/api/dashboard');
    const m = data.metrics;

    const stats = h('div.row.g-3.mb-3', [
      h('div.col-6.col-lg-3', T.statCard({
        label: 'Open job cards', value: m.open_jobs, icon: 'clipboard-check', colour: 'primary',
        sub: `${m.ready_for_collection} ready for collection`,
        onClick: () => T.navigate('/jobs?status=open'),
      })),
      h('div.col-6.col-lg-3', T.statCard({
        label: 'Overdue', value: m.overdue_jobs, icon: 'exclamation-triangle',
        colour: m.overdue_jobs ? 'danger' : 'success',
        sub: m.overdue_jobs ? 'Past promised date' : 'All on schedule',
        onClick: () => T.navigate('/jobs?status=overdue'),
      })),
      h('div.col-6.col-lg-3', T.statCard({
        label: 'WIP value', value: money(m.wip_value), icon: 'cash-stack', colour: 'success',
        sub: `Avg turnaround ${m.avg_turnaround_days} days`,
      })),
      h('div.col-6.col-lg-3', T.statCard({
        label: 'Receivables', value: money(m.outstanding_receivables), icon: 'receipt',
        colour: m.overdue_invoices ? 'warning' : 'secondary',
        sub: `${m.outstanding_count} unpaid · ${m.overdue_invoices} overdue`,
        onClick: () => T.navigate('/invoices'),
      })),
    ]);

    const flow = h('div.row.g-3.mb-3', [
      h('div.col-6.col-lg-3', T.statCard({ label: 'In spray booth', value: m.in_paint, icon: 'brush', colour: 'info' })),
      h('div.col-6.col-lg-3', T.statCard({ label: 'Waiting on parts', value: m.awaiting_parts, icon: 'box-seam', colour: 'warning' })),
      h('div.col-6.col-lg-3', T.statCard({
        label: 'Active claims', value: m.active_claims, icon: 'shield-check', colour: 'brand',
        sub: `Avg ${m.avg_claim_aging_days} days with insurer`,
        onClick: () => T.navigate('/claims'),
      })),
      h('div.col-6.col-lg-3', T.statCard({
        label: 'Collected this month', value: m.collected_this_month, icon: 'check2-circle', colour: 'success',
      })),
    ]);

    const stageStrip = h('div.d-flex.gap-2.flex-wrap',
      (data.board.columns || []).filter((c) => c.count > 0).map((c) =>
        h('a.chip.text-decoration-none', {
          href: `#/jobs?stage=${c.stage}`, class: 'chip',
        }, h('strong', c.count), c.label)));

    const readyTable = T.dataTable({
      columns: [
        { label: 'Job card', render: (r) => h('div', h('div.fw-semibold', r.reg_no), h('div.small.text-secondary', r.job_no)) },
        { label: 'Customer', render: (r) => r.customer_name },
        { label: 'Service', render: (r) => h('span.small', r.service) },
        { label: 'Promised', render: (r) => h('span', { class: r.is_overdue ? 'text-danger fw-semibold' : '' }, dateShort(r.promised_date)) },
        { label: '', class: 'text-end', render: (r) => h('span.chip', `${r.days_in_shop}d in shop`) },
      ],
      rows: data.ready_jobs,
      empty: T.emptyState('Nothing waiting for collection', 'Vehicles in QC will appear here.', 'car-front'),
    });

    const recentTable = T.dataTable({
      columns: [
        { label: 'Job card', render: (r) => h('div', h('div.fw-semibold', r.job_no), h('div.small.text-secondary', r.reg_no)) },
        { label: 'Customer', render: (r) => r.customer_name },
        { label: 'Stage', render: (r) => T.stageBadge(r.stage, r.stage_label) },
        { label: 'Progress', render: (r) => h('div.progress', { style: 'height:6px;min-width:70px' },
            h('div.progress-bar.bg-warning', { style: `width:${r.progress}%` })) },
        { label: 'Priority', render: (r) => T.priorityBadge(r.priority) },
      ],
      rows: data.recent_jobs,
      onRowClick: (r) => T.navigate(`/jobs/${r.id}`),
      rowLabel: (r) => `Open job card ${r.job_no} for ${r.customer_name}`,
    });

    const activityHost = h('div.text-secondary.small', 'Loading…');
    api.get('/api/activity?limit=8').then((res) => {
      T.mount(activityHost, res.items.length
        ? h('div.timeline-v2', res.items.map((a) => h('div.item', {
            class: `item ${a.tone || ''}`.trim(),
          }, [
            h('div', { style: 'font-size:.82rem' }, a.summary),
            h('div.small.text-secondary', `${a.actor_name} · ${T.relTime(a.created_at)}`),
          ])))
        : T.emptyState('No activity yet', null, 'clock-history'));
    }).catch(() => T.mount(activityHost, ''));

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', `Good day, ${(T.store.get('user').full_name || '').split(' ')[0]} 👋`),
          h('div.small.text-secondary', `Workshop snapshot · updated ${T.timeOnly(data.generated_at)}`),
        ]),
        data.unread_whatsapp
          ? h('button.btn.btn-success.btn-sm', { onclick: () => T.navigate('/inbox') },
              T.icon('whatsapp'), ` ${data.unread_whatsapp} unread WhatsApp`)
          : null,
        h('button.btn.btn-brand.btn-sm', { onclick: () => T.newJobCard({ onCreated: () => ctx.refresh() }) },
          T.icon('plus-lg'), ' New job card'),
      ]),
      stats,
      flow,
      stageStrip,
      h('div.row.g-3', [
        h('div.col-lg-5', T.section({ title: 'Ready for collection', body: readyTable, flush: true })),
        h('div.col-lg-7', T.section({ title: 'Latest job cards', body: recentTable, flush: true,
          actions: [h('button.btn.btn-sm.btn-outline-secondary', { onclick: () => T.navigate('/board') }, 'WIP board')] })),
      ]),
      h('div.row.g-3.mt-1', [
        h('div.col-lg-5', T.section({
          title: 'Recent activity',
          actions: [h('a.btn.btn-sm.btn-outline-secondary', { href: '#/activity' }, 'View all')],
          body: activityHost,
        })),
      ]),
    ]);
  });
})();
