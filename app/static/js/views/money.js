/* Claims, invoices, reports and staff. */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  /* ── claims ───────────────────────────────────────────────────────── */
  T.route('/claims', async (ctx) => {
    ctx.title = 'Insurance claims';
    const meta = T.store.get('meta');
    const state = { status: ctx.query.status || '', insurer: '' };
    const host = h('div');
    const summary = h('div.row.g-3.mb-3');

    async function load() {
      T.mount(host, T.skeletonTable(8, 6));
      const params = new URLSearchParams();
      if (state.status) params.set('status', state.status);
      if (state.insurer) params.set('insurer', state.insurer);
      const data = await api.get(`/api/claims?${params}`);

      const byStatus = {};
      const byInsurer = {};
      data.items.forEach((c) => {
        byStatus[c.status] = (byStatus[c.status] || 0) + 1;
        byInsurer[c.insurer_name] = (byInsurer[c.insurer_name] || 0) + Number(c.approved_amount || 0);
      });
      const awaiting = data.items.filter((c) => ['SUBMITTED', 'ASSESSOR_BOOKED'].includes(c.status));
      const avgAging = awaiting.length
        ? Math.round(awaiting.reduce((s, c) => s + c.aging_days, 0) / awaiting.length) : 0;

      T.mount(summary, [
        h('div.col-6.col-lg-3', T.statCard({ label: 'Active claims', value: data.count, icon: 'shield-check', colour: 'brand' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Approved value', value: money(data.approved_value), icon: 'cash-stack', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Awaiting decision', value: awaiting.length, icon: 'hourglass-split', colour: 'warning' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Avg aging', value: `${avgAging} days`, icon: 'calendar-x', colour: avgAging > 7 ? 'danger' : 'info' })),
      ]);

      const colour = { DRAFT: 'secondary', ASSESSOR_BOOKED: 'info', SUBMITTED: 'warning',
                       APPROVED: 'success', PARTIAL: 'warning', REPUDIATED: 'danger', SETTLED: 'dark' };

      T.mount(host, T.dataTable({
        columns: [
          { label: 'Claim', render: (r) => h('div', [h('div.fw-semibold', r.claim_no || `#${r.id}`),
              h('div.small.text-secondary', r.policy_no || '')]) },
          { label: 'Insurer', render: (r) => h('span.badge.text-bg-dark', r.insurer_name) },
          { label: 'Job card', render: (r) => h('span.small', `#${r.job_id}`) },
          { label: 'Assessor', class: 'd-none d-lg-table-cell', render: (r) => h('div', [
              h('div.small', r.assessor_name || '—'),
              h('div.small.text-secondary', r.assessor_date ? dateShort(r.assessor_date) : '')]) },
          { label: 'Claimed', class: 'text-end', render: (r) => money(r.claimed_amount) },
          { label: 'Approved', class: 'text-end', render: (r) => money(r.approved_amount) },
          { label: 'Excess', class: 'text-end d-none d-md-table-cell', render: (r) => h('div', [
              h('div', money(r.excess)),
              r.excess_paid ? h('span.badge.text-bg-success', 'paid') : h('span.badge.text-bg-secondary', 'due')]) },
          { label: 'Aging', class: 'text-end', render: (r) => h('span', {
              class: r.aging_days > 10 && ['SUBMITTED', 'ASSESSOR_BOOKED'].includes(r.status) ? 'text-danger fw-semibold' : '',
            }, `${r.aging_days}d`) },
          { label: 'Status', render: (r) => h(`span.badge.text-bg-${colour[r.status] || 'secondary'}`, r.status_label) },
        ],
        rows: data.items,
        onRowClick: (r) => T.navigate(`/jobs/${r.job_id}`),
        empty: T.emptyState('No claims match', 'Link a claim from a job card.', 'shield'),
      }));
    }

    const insurerFilter = T.iconSelect({
      value: state.insurer, icon: 'building', width: 168, ariaLabel: 'Insurer',
      onChange: (v) => { state.insurer = v; load(); },
      options: [{ value: '', label: 'All insurers' }].concat(
        meta.insurers.map((i) => ({ value: i.code, label: i.name }))),
    });

    const statusFilter = T.iconSelect({
      value: state.status, icon: 'funnel', width: 158, ariaLabel: 'Claim status',
      onChange: (v) => { state.status = v; load(); },
      options: [{ value: '', label: 'All statuses' }].concat(
        meta.claim_statuses.map((s) => ({ value: s.code, label: s.label }))),
    });

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Insurance claims')),
        h('div.tc-toolbar', [statusFilter, insurerFilter]),
      ]),
      summary,
      T.section({ body: host, flush: true }),
    ]);
  });

  /* ── invoices ─────────────────────────────────────────────────────── */
  T.route('/invoices', async (ctx) => {
    ctx.title = 'Invoices';
    const meta = T.store.get('meta');
    const state = { status: '' };
    const host = h('div');
    const summary = h('div.row.g-3.mb-3');
    /* `meta.payment_methods` are API codes — show them as proper sentence case. */
    const methodOptions = (meta.payment_methods || []).map((m) => ({
      value: m,
      label: { CASH: 'Cash', ECOCASH: 'EcoCash', INNBUCKS: 'InnBucks',
               BANK_TRANSFER: 'Bank transfer', CARD: 'Card',
               INSURER_SETTLEMENT: 'Insurer settlement' }[m] || m,
    }));

    async function load() {
      T.mount(host, T.skeletonTable(8, 6));
      const data = await api.get(`/api/invoices${state.status ? `?status=${state.status}` : ''}`);

      T.mount(summary, [
        h('div.col-6.col-lg-3', T.statCard({ label: 'Invoices', value: data.count, icon: 'receipt', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Outstanding', value: money(data.outstanding), icon: 'cash-stack', colour: 'warning' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Overdue', value: data.overdue, icon: 'exclamation-circle', colour: data.overdue ? 'danger' : 'secondary' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Collected', value: money(data.items.reduce((s, i) => s + Number(i.amount_paid || 0), 0)),
          icon: 'check2-circle', colour: 'success',
        })),
      ]);

      const colour = { DRAFT: 'secondary', ISSUED: 'info', PART_PAID: 'warning',
                       PAID: 'success', OVERDUE: 'danger', CANCELLED: 'secondary' };

      T.mount(host, T.dataTable({
        columns: [
          { label: 'Invoice', render: (r) => h('div', [h('div.fw-semibold', r.invoice_no),
              h('div.small.text-secondary', r.job_no || '')]) },
          { label: 'Customer', render: (r) => r.customer_name },
          { label: 'Type', render: (r) => r.is_insurance
              ? h('span.badge.text-bg-dark', 'Insurer') : h('span.chip', 'Customer') },
          { label: 'Total', class: 'text-end', render: (r) => money(r.total, r.currency) },
          { label: 'Paid', class: 'text-end d-none d-md-table-cell', render: (r) => money(r.amount_paid) },
          { label: 'Balance', class: 'text-end', render: (r) => h('span', {
              class: r.balance > 0 ? 'fw-semibold' : 'text-success' }, money(r.balance)) },
          { label: 'Due', render: (r) => h('span', { class: r.is_overdue ? 'text-danger fw-semibold' : '' },
              dateShort(r.due_date)) },
          { label: 'Status', render: (r) => h(`span.badge.text-bg-${r.is_overdue ? 'danger' : colour[r.status] || 'secondary'}`,
              r.is_overdue && r.status !== 'PAID' ? 'OVERDUE' : r.status) },
          { label: '', class: 'text-end', render: (r) => h('div.d-flex.gap-1.justify-content-end', [
              r.status === 'DRAFT' ? h('button.btn.btn-sm.btn-outline-info', {
                onclick: async (e) => { e.stopPropagation();
                  const res = await api.post(`/api/invoices/${r.id}/issue`, {});
                  T.toast(`Invoice issued${res.notified ? ' · customer notified' : ''}.`); load(); },
              }, 'Issue') : null,
              r.balance > 0 ? h('button.btn.btn-sm.btn-outline-success', {
                onclick: (e) => { e.stopPropagation(); pay(r); },
              }, T.icon('cash')) : null,
            ]) },
        ],
        rows: data.items,
        onRowClick: (r) => invoiceDetail(r),
        empty: T.emptyState('No invoices', 'Invoices are raised when a vehicle is collected.', 'receipt'),
      }));
    }

    /* ── documents: quotation / invoice / receipt ───────────────────── */
    function openPdf(url) { window.open(url, '_blank', 'noopener'); }

    function docButtons(kind, rec) {
      return [
        h('button.btn.btn-sm.btn-outline-secondary', {
          onclick: () => openPdf(`/api/${kind === 'quote' ? 'estimates' : kind + 's'}/${rec.id}/pdf`),
          title: 'Open the PDF',
        }, T.icon('file-earmark-pdf'), ' PDF'),
        h('button.btn.btn-sm.btn-outline-secondary', {
          title: 'Copy the customer share link',
          onclick: async () => {
            try {
              const res = await api.get(`/api/documents/links/${kind}/${rec.id}`);
              await navigator.clipboard.writeText(res.view);
              T.toast('Share link copied to the clipboard.', 'info');
            } catch (err) { T.toast('Could not copy the link.', 'warning'); }
          },
        }, T.icon('link-45deg')),
      ];
    }

    function receiptRow(payment) {
      return h('tr', [
        h('td', [h('div.fw-semibold', payment.receipt_no || `#${payment.id}`),
          h('div.small.text-secondary', payment.payer_name || payment.method)]),
        h('td.text-end', money(payment.amount)),
        h('td', dateShort(payment.created_at)),
        h('td.text-end', h('div.d-flex.gap-1.justify-content-end', [
          h('button.btn.btn-sm.btn-outline-secondary', {
            onclick: () => openPdf(`/api/payments/${payment.id}/pdf`),
            title: 'Receipt PDF',
          }, T.icon('receipt')),
          h('button.btn.btn-sm.btn-success', {
            onclick: () => sendReceipt(payment),
            title: 'Send the receipt on WhatsApp',
          }, T.icon('whatsapp')),
        ])),
      ]);
    }

    async function sendReceipt(payment) {
      try {
        T.toast('Sending receipt…', 'info', { timeout: 2500 });
        const res = await api.post(`/api/payments/${payment.id}/receipt/send`, {});
        T.toast(`Receipt ${payment.receipt_no || ''} sent.`, 'success', {
          title: 'WhatsApp delivered',
          action: { label: 'Open', run: () => openPdf(res.result.link) },
        });
      } catch (err) {
        T.toast(err.message || 'The receipt could not be sent.', 'danger');
      }
    }

    async function sendInvoice(invoice) {
      const go = await T.confirmDialog({
        title: `Send invoice ${invoice.invoice_no}?`,
        message: `The PDF goes to ${invoice.customer_name} on WhatsApp.`,
        detail: `Outstanding: ${money(invoice.balance, invoice.currency)}`,
        confirmLabel: 'Send on WhatsApp',
        variant: 'success',
        icon: 'whatsapp',
        subtitle: 'Delivered instantly on WhatsApp',
      });
      if (!go) return;
      try {
        T.toast('Sending invoice…', 'info', { timeout: 2500 });
        const res = await api.post(`/api/invoices/${invoice.id}/send`, {});
        T.toast(`Invoice ${invoice.invoice_no} sent.`, 'success', {
          title: 'WhatsApp delivered',
          action: { label: 'Open', run: () => openPdf(res.result.link) },
        });
      } catch (err) {
        T.toast(err.message || 'The invoice could not be sent.', 'danger');
      }
    }

    async function pay(invoice) {
      const res = await T.formModal({
        title: `Record payment — ${invoice.invoice_no}`,
        size: 'md',
        intro: `Balance outstanding: ${money(invoice.balance, invoice.currency)}`,
        fields: [
          { name: 'amount', label: 'Amount received', type: 'money', step: '0.01', col: 6,
            value: invoice.balance, required: true, icon: 'cash-stack',
            affix: invoice.currency || 'USD' },
          { name: 'method', label: 'Payment method', type: 'select', col: 6,
            icon: 'credit-card', options: methodOptions },
          { name: 'reference', label: 'Reference', hint: 'EcoCash transaction id or bank reference.',
            col: 12, icon: 'hash', placeholder: 'e.g. MP240915.1234' },
          { name: 'payer_name', label: 'Paid by', hint: 'Leave blank to use the customer on the invoice.',
            col: 12, icon: 'person', placeholder: invoice.customer_name || '' },
          { name: 'send_receipt', label: 'Send the receipt on WhatsApp', type: 'switch',
            col: 12, value: true,
            help: 'Goes to the number on the job card. Untick to record the payment only.' },
        ],
        submitLabel: 'Record payment',
      });
      if (!res) return;
      const sendIt = !!res.send_receipt;
      delete res.send_receipt;
      try {
        const out = await api.post(`/api/invoices/${invoice.id}/payment`,
          { ...res, send_receipt: sendIt });
        const payment = out.payment || {};
        T.toast(
          `Payment recorded · receipt ${payment.receipt_no || 'issued'}.`,
          sendIt ? 'success' : 'info',
          payment.id ? {
            title: sendIt ? 'Receipt sent on WhatsApp' : 'Receipt ready',
            action: { label: 'View receipt', run: () => openPdf(`/api/payments/${payment.id}/pdf`) },
          } : undefined,
        );
        load();
      } catch (err) {
        T.toast(err.message || 'The payment could not be recorded.', 'danger');
      }
    }

    async function invoiceDetail(invoice) {
      const receiptHost = h('div.text-center.py-3', T.spinner());
      const m = T.modal({
        title: `Invoice ${invoice.invoice_no}`,
        size: 'lg',
        body: h('div', [
          h('div.d-flex.justify-content-between.mb-3', [
            h('div', [h('div.fw-bold', invoice.customer_name),
              h('div.small.text-secondary', invoice.job_no ? `Job card ${invoice.job_no}` : '')]),
            h('div.text-end', [h('div.money-lg', money(invoice.total, invoice.currency)),
              h('span.badge.text-bg-secondary', invoice.status)]),
          ]),
          h('table.table.table-sm.table-tc', h('tbody', [
            h('tr', [h('td', 'Subtotal'), h('td.text-end', money(invoice.subtotal))]),
            h('tr', [h('td', 'VAT'), h('td.text-end', money(invoice.vat))]),
            h('tr.fw-bold', [h('td', 'Total'), h('td.text-end', money(invoice.total))]),
            h('tr', [h('td', 'Paid'), h('td.text-end', money(invoice.amount_paid))]),
            h('tr.fw-bold', [h('td', 'Balance'), h('td.text-end', money(invoice.balance))]),
          ])),
          invoice.due_date ? h('div.small.text-secondary.mb-3', `Due ${dateShort(invoice.due_date)}`) : null,
          h('div.doc-sub.mt-3', 'Receipts issued'),
          receiptHost,
        ]),
        footer: [
          ...docButtons('invoice', invoice),
          h('button.btn.btn-sm.btn-success', {
            onclick: () => sendInvoice(invoice),
          }, T.icon('whatsapp'), ' Send on WhatsApp'),
          invoice.balance > 0
            ? h('button.btn.btn-sm.btn-brand', {
                onclick: () => { m.close(); pay(invoice); },
              }, T.icon('cash-coin'), ' Record payment')
            : null,
        ],
      });

      try {
        const data = await api.get(`/api/payments?invoice_id=${invoice.id}`, { silent: true });
        T.mount(receiptHost, data.items.length
          ? h('table.table.table-sm.table-tc.mb-0', h('tbody', data.items.map(receiptRow)))
          : h('div.small.text-secondary', 'No payments recorded against this invoice yet.'));
      } catch (err) {
        T.mount(receiptHost, h('div.small.text-secondary', 'Receipts could not be loaded.'));
      }
    }

    const statusFilter = T.iconSelect({
      value: state.status, icon: 'funnel', width: 158, ariaLabel: 'Invoice status',
      onChange: (v) => { state.status = v; load(); },
      options: [{ value: '', label: 'All statuses' }].concat(
        (meta.invoice_statuses || []).map((s) => ({ value: s, label: s }))),
    });

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Invoices & payments')),
        h('div.tc-toolbar', [statusFilter]),
      ]),
      summary,
      T.section({ body: host, flush: true }),
    ]);
  });

  /* ── reports ──────────────────────────────────────────────────────── */
  T.route('/reports', async (ctx) => {
    ctx.title = 'Reports';
    const data = await api.get('/api/reports/overview');
    const m = data.metrics;

    const maxTrend = Math.max(1, ...data.trend.map((t) => Math.max(t.intake, t.collected)));
    const chart = h('div.d-flex.align-items-end.gap-1', { style: 'height:170px' },
      data.trend.map((t) => h('div.flex-fill.d-flex.flex-column.justify-content-end.gap-1', {
        title: `${t.label}: ${t.intake} in, ${t.collected} out`,
      }, [
        h('div.bg-warning.rounded-top', { style: `height:${(t.intake / maxTrend) * 110}px` }),
        h('div.bg-success.rounded-bottom', { style: `height:${(t.collected / maxTrend) * 40}px` }),
      ])));
    const labels = h('div.d-flex.gap-1.mt-1', data.trend.map((t) =>
      h('div.flex-fill.text-center.text-secondary', { style: 'font-size:.6rem' }, t.label.split(' ')[0])));

    const serviceRows = Object.entries(data.by_service).sort((a, b) => b[1] - a[1]);
    const maxService = Math.max(1, ...serviceRows.map((r) => r[1]));

    const insurerRows = Object.entries(data.by_insurer);

    return h('div', [
      h('h1.h4.mb-3', 'Reports & performance'),
      h('div.row.g-3.mb-3', [
        h('div.col-6.col-lg-3', T.statCard({ label: 'Avg turnaround', value: `${m.avg_turnaround_days} days`, icon: 'stopwatch', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'WIP value', value: money(m.wip_value), icon: 'cash-stack', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Receivables', value: money(m.outstanding_receivables), icon: 'receipt', colour: 'warning' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Avg claim aging', value: `${m.avg_claim_aging_days} days`, icon: 'shield-check', colour: 'brand' })),
      ]),
      h('div.row.g-3', [
        h('div.col-lg-7', T.section({
          title: 'Intake vs collection (last 14 days)',
          body: [chart, labels,
            h('div.d-flex.gap-3.mt-2.small.text-secondary', [
              h('span', h('span.badge.text-bg-warning', ' '), ' Vehicles in'),
              h('span', h('span.badge.text-bg-success', ' '), ' Collected'),
            ])],
        })),
        h('div.col-lg-5', T.section({
          title: 'Work mix by service',
          body: h('div', serviceRows.map(([name, count]) =>
            h('div.mb-2', [
              h('div.d-flex.justify-content-between.small',
                h('span', name), h('strong', count)),
              h('div.progress', { style: 'height:6px' },
                h('div.progress-bar.bg-warning', { style: `width:${(count / maxService) * 100}%` })),
            ]))),
        })),
      ]),
      h('div.row.g-3.mt-3', [
        h('div.col-lg-6', T.section({
          title: 'Insurer performance',
          body: insurerRows.length ? T.dataTable({
            columns: [
              { label: 'Insurer', render: (r) => h('strong', r[0]) },
              { label: 'Job cards', class: 'text-end', render: (r) => r[1].jobs },
              { label: 'Claimed', class: 'text-end', render: (r) => money(r[1].claimed) },
              { label: 'Approved', class: 'text-end', render: (r) => money(r[1].approved) },
              { label: 'Avg aging', class: 'text-end', render: (r) =>
                  `${Math.round(r[1].aging / Math.max(1, r[1].jobs))}d` },
            ],
            rows: insurerRows,
          }) : T.emptyState('No insurer data yet'),
        })),
        h('div.col-lg-6', T.section({
          title: 'Technician productivity',
          body: data.technicians.length ? T.dataTable({
            columns: [
              { label: 'Technician', render: (r) => h('strong', r.name) },
              { label: 'Active', class: 'text-end', render: (r) => h('span.badge.text-bg-warning', r.active_jobs) },
              { label: 'Completed', class: 'text-end', render: (r) => r.completed_jobs },
              { label: 'Avg days', class: 'text-end', render: (r) => r.avg_days },
            ],
            rows: data.technicians,
          }) : T.emptyState('No technician data'),
        })),
      ]),
      T.section({ title: 'Stage load', body: T.dataTable({
        columns: [
          { label: 'Stage', render: (r) => r.label },
          { label: 'Job cards', class: 'text-end', render: (r) => h('span.badge.text-bg-secondary', r.count) },
          { label: '', render: (r) => h('div.progress', { style: 'height:6px;min-width:120px' },
              h('div.progress-bar.bg-warning', {
                style: `width:${(r.count / Math.max(1, m.open_jobs)) * 100}%` })) },
        ],
        rows: (T.store.get('meta').stages || []).map((s) => ({
          label: s.label, count: (m.stage_breakdown || {})[s.code] || 0,
        })),
      }) }),
    ]);
  });

  /* ── staff ────────────────────────────────────────────────────────── */
  T.route('/staff', async (ctx) => {
    ctx.title = 'Staff & settings';
    const meta = T.store.get('meta');
    const user = T.store.get('user');
    const data = await api.get('/api/users');

    const table = T.dataTable({
      columns: [
        { label: 'Name', render: (r) => h('div.d-flex.align-items-center.gap-2', [
            h('span.badge.text-bg-dark', r.initials), h('span.fw-semibold', r.full_name)]) },
        { label: 'Email', render: (r) => h('span.small', r.email) },
        { label: 'Phone', render: (r) => h('span.small', r.phone || '—') },
        { label: 'Role', render: (r) => h('span.chip', r.role_label) },
        { label: 'Active', render: (r) => r.is_active_user
            ? h('span.badge.text-bg-success', 'Active') : h('span.badge.text-bg-secondary', 'Disabled') },
        { label: '', class: 'text-end', render: (r) => user.is_manager ? h('button.btn.btn-sm.btn-outline-secondary', {
            onclick: async (e) => { e.stopPropagation();
              const res = await T.formModal({
                title: `Edit ${r.full_name}`, size: 'md',
                values: r,
                fields: [
                  { name: 'full_name', label: 'Full name', col: 12, value: r.full_name },
                  { name: 'phone', label: 'Phone', col: 12, value: r.phone },
                  { name: 'role', label: 'Role', type: 'select', col: 12, value: r.role,
                    options: meta.roles.map((x) => ({ value: x.code, label: x.label })) },
                  { name: 'password', label: 'New password', col: 12, placeholder: 'Leave blank to keep current' },
                  { name: 'is_active_user', label: 'Active', type: 'switch', col: 12, value: r.is_active_user },
                ],
                submitLabel: 'Save',
              });
              if (!res) return;
              if (!res.password) delete res.password;
              await api.patch(`/api/users/${r.id}`, res);
              T.toast('Staff member updated.');
              ctx.refresh();
            },
          }, T.icon('pencil')) : null },
      ],
      rows: data.items,
    });

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'Staff & settings'),
          h('div.small.text-secondary', `Signed in as ${user.full_name} (${user.role_label})`)]),
        user.is_manager ? h('button.btn.btn-brand.btn-sm', {
          onclick: async () => {
            const res = await T.formModal({
              title: 'Add staff member',
              fields: [
                { name: 'full_name', label: 'Full name *', col: 6, required: true },
                { name: 'email', label: 'Email *', type: 'email', col: 6, required: true },
                { name: 'phone', label: 'Phone', col: 6 },
                { name: 'role', label: 'Role', type: 'select', col: 6,
                  options: meta.roles.map((x) => ({ value: x.code, label: x.label })) },
                { name: 'password', label: 'Temporary password *', col: 12, required: true },
              ],
              submitLabel: 'Create account',
            });
            if (!res) return;
            await api.post('/api/users', res);
            T.toast('Staff account created.');
            ctx.refresh();
          },
        }, T.icon('person-plus'), ' Add staff') : null,
      ]),
      T.section({ title: 'Team accounts', body: table, flush: true }),
      T.section({
        title: 'WhatsApp bot',
        body: h('div.row.g-3', [
          h('div.col-md-6', [
            h('p.small.text-secondary.mb-2',
              'The bot answers customer questions, logs quote requests, tracks repairs and ' +
              'handles insurance claim status. Test it without a Meta account in simulator mode.'),
            h('a.btn.btn-success.btn-sm', { href: '#/inbox' }, T.icon('whatsapp'), ' Open inbox & simulator'),
          ]),
          h('div.col-md-6', h('ul.small.text-secondary.mb-0', [
            h('li', 'Webhook: POST /webhooks/whatsapp'),
            h('li', 'Verification token: from WA_VERIFY_TOKEN'),
            h('li', 'Switch to live with WA_MODE=live + access token'),
            h('li', 'Templates: job_stage_update, vehicle_ready, quotation_ready, parts_received, payment_due, document_share'),
          ])),
        ]),
      }),
    ]);
  });
})();
