/* Payments, invoices, reports and staff. */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  /* ── payments ─────────────────────────────────────────────────────── */
  T.route('/payments', async (ctx) => {
    ctx.title = 'Payments';
    const meta = T.store.get('meta');
    const methodLabels = meta.payment_method_labels || {};
    const methodLabel = (code) => methodLabels[code] || (code || '').replace(/_/g, ' ');

    const state = { method: ctx.query.method || '', since: '', until: '' };
    const host = h('div');
    const summary = h('div.row.g-3.mb-3');
    const breakdown = h('div');

    function params() {
      const p = new URLSearchParams();
      if (state.method) p.set('method', state.method);
      if (state.since) p.set('since', state.since);
      if (state.until) p.set('until', state.until);
      return p;
    }

    async function load() {
      T.mount(host, T.skeletonTable(7, 6));
      const data = await api.get(`/api/payments?${params()}`);

      T.mount(summary, [
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Received today', value: money(data.received_today),
          icon: 'cash-coin', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'This month', value: money(data.received_month),
          icon: 'calendar-check', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'In this view', value: money(data.total),
          icon: 'funnel', colour: 'brand' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Receipts', value: String(data.count),
          icon: 'receipt', colour: 'secondary' })),
      ]);

      // The banking summary: what came in on each method, so the EcoCash float
      // and the bank account can be reconciled separately.
      T.mount(breakdown, data.by_method.length
        ? h('div.d-flex.flex-wrap.gap-2.mb-3', data.by_method.map((row) =>
            h('span.chip', [
              h('strong', methodLabel(row.method)),
              ` ${money(row.total)}`,
              h('span.text-secondary',
                ` · ${row.count} receipt${row.count === 1 ? '' : 's'}`),
            ])))
        : null);

      T.mount(host, T.dataTable({
        columns: [
          { label: 'Receipt', render: (r) => h('div', [
              h('div.fw-semibold', r.receipt_no || `#${r.id}`),
              h('div.small.text-secondary', dateShort(r.created_at))]) },
          { label: 'Customer', render: (r) => h('div', [
              h('div', r.payer_name || '—'),
              h('div.small.text-secondary', r.reference || '')]) },
          { label: 'Invoice', render: (r) => h('span.small', r.invoice_no || '—') },
          { label: 'Method', render: (r) => h('span.badge.text-bg-dark', methodLabel(r.method)) },
          { label: 'Received by', class: 'd-none d-lg-table-cell',
            render: (r) => h('span.small', r.received_by || '—') },
          { label: 'Amount', class: 'text-end', render: (r) => h('strong', money(r.amount)) },
          { label: '', class: 'text-end',
            render: (r) => h('div.d-flex.gap-1.justify-content-end', [
              h('a.btn.btn-sm.btn-outline-secondary', {
                href: `/api/payments/${r.id}/pdf`, target: '_blank',
                title: 'Download the receipt', onclick: (e) => e.stopPropagation(),
              }, T.icon('file-earmark-pdf')),
              h('button.btn.btn-sm.btn-outline-secondary', {
                type: 'button', title: 'Send the receipt on WhatsApp',
                onclick: async (e) => {
                  e.stopPropagation();
                  try {
                    const out = await api.post(`/api/payments/${r.id}/receipt/send`, {});
                    T.toast(out.sent ? 'Receipt sent.' : 'The receipt could not be sent.',
                      out.sent ? 'success' : 'warning');
                  } catch (err) { T.toast(err.message, 'danger'); }
                },
              }, T.icon('whatsapp')),
            ]) },
        ],
        rows: data.items,
        empty: T.emptyState(
          'No payments to show',
          (state.method || state.since || state.until)
            ? 'Nothing came in on those filters.'
            : 'Receipts appear here as money is recorded against invoices.',
          'cash-coin'),
      }));
    }

    /* Built from the published list, so adding a method needs no UI change. */
    const methodFilter = T.iconSelect({
      value: state.method, icon: 'credit-card', width: 176, ariaLabel: 'Payment method',
      onChange: (v) => { state.method = v; load(); },
      options: [{ value: '', label: 'Every method' }].concat(
        (meta.payment_methods || []).map((m) => ({ value: m, label: methodLabel(m) }))),
    });

    const sinceInput = h('input.form-control.form-control-sm', {
      type: 'date', style: 'max-width:10rem', 'aria-label': 'From',
      onchange: (e) => { state.since = e.target.value; load(); },
    });
    const untilInput = h('input.form-control.form-control-sm', {
      type: 'date', style: 'max-width:10rem', 'aria-label': 'To',
      onchange: (e) => { state.until = e.target.value; load(); },
    });

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Payments'),
          h('div.small.text-secondary', 'Every receipt, how it came in, and who took it.'),
        ]),
        h('a.btn.btn-outline-secondary.btn-sm', { href: '#/invoices' },
          T.icon('receipt'), ' Invoices'),
        h('a.btn.btn-brand.btn-sm', { href: '#/invoices?record=1' },
          T.icon('cash-coin'), ' Record payment'),
      ]),
      summary,
      h('div.d-flex.align-items-end.gap-3.flex-wrap.mb-3', [
        h('div', [h('div.tc-label', 'Method'), methodFilter]),
        h('div', [h('div.tc-label', 'From'), sinceInput]),
        h('div', [h('div.tc-label', 'To'), untilInput]),
        (state.method || state.since || state.until)
          ? h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button',
              onclick: () => {
                state.method = ''; state.since = ''; state.until = '';
                sinceInput.value = ''; untilInput.value = '';
                load();
              },
            }, 'Clear')
          : null,
      ]),
      breakdown,
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
      value: m, label: methodLabels[m] || m,
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
        empty: T.emptyState('No invoices yet',
          'Use “New invoice” to bill straight from the desk, or open a job card and bill from there.',
          'receipt'),
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

    /* ── raising documents from the desk ───────────────────────────── */

    /* Shared last field: what to do with the PDF once the record is saved. */
    function afterSaveField() {
      return {
        name: 'after_save', label: 'Then', type: 'segmented', col: 12,
        value: 'download',
        options: [
          { value: 'download', label: 'Download PDF', icon: 'download' },
          { value: 'whatsapp', label: 'Send on WhatsApp', icon: 'whatsapp' },
          { value: 'none', label: 'Just save' },
        ],
        help: 'WhatsApp delivers the PDF to the customer on the number on file.',
      };
    }

    /* Honour that choice on the record we just created. */
    async function deliverDoc(rec, choice, sendPath) {
      const pdfUrl = sendPath.startsWith('/api/invoices')
        ? `/api/invoices/${rec.id}/pdf`
        : `/api/payments/${rec.id}/pdf`;

      if (choice === 'download') { openPdf(pdfUrl); return; }
      if (choice !== 'whatsapp') return;

      try {
        T.toast('Sending…', 'info', { timeout: 2500 });
        await api.post(sendPath, {});
        T.toast('Delivered on WhatsApp.', 'success', {
          title: 'Sent',
          action: { label: 'Open PDF', run: () => openPdf(pdfUrl) },
        });
      } catch (err) {
        // Never lose the document just because the message failed.
        T.toast(err.message || 'Could not send on WhatsApp — downloading instead.', 'warning');
        openPdf(pdfUrl);
      }
    }

    async function customerOptions() {
      const data = await api.get('/api/customers', { silent: true });
      return (data.items || []).map((c) => ({
        value: String(c.id),
        label: c.company ? `${c.name} — ${c.company}` : (c.phone ? `${c.name} · ${c.phone}` : c.name),
      }));
    }

    async function newInvoice() {
      let options = [];
      try {
        options = await customerOptions();
      } catch (err) {
        T.toast('Could not load the customer list.', 'danger');
        return;
      }
      if (!options.length) {
        T.toast('Add a customer before raising an invoice.', 'warning');
        return;
      }

      const res = await T.formModal({
        title: 'Raise an invoice',
        size: 'lg',
        intro: 'For work billed straight from the desk — no job card needed. VAT is worked out for you.',
        submitLabel: 'Save invoice',
        fields: [
          { name: 'customer_id', label: 'Customer *', type: 'select', col: 12,
            icon: 'person', options, required: true },
          { name: 'description', label: 'What is being invoiced *', type: 'textarea', col: 12,
            rows: 2, required: true, icon: 'card-text',
            hint: 'This line is printed on the invoice, so write it as the customer should read it.',
            placeholder: 'e.g. Full respray — Toyota Hilux, 2 panels plus paint materials' },
          { name: 'subtotal', label: 'Amount before VAT *', type: 'money', col: 6,
            step: '0.01', required: true, icon: 'cash-stack', affix: 'USD' },
          { name: 'due_date', label: 'Payment due', type: 'date', col: 6, icon: 'calendar-event' },
          { name: 'issue', label: 'Issue it now', type: 'switch', col: 6, value: true,
            help: 'Untick to keep it as a draft you can still change.' },
          afterSaveField(),
        ],
      });
      if (!res) return;

      const after = res.after_save;
      delete res.after_save;
      try {
        const out = await api.post('/api/invoices', res);
        const invoice = out.invoice || {};
        T.toast(`Invoice ${invoice.invoice_no} saved — ${money(invoice.total, invoice.currency)}.`,
          'info');
        load();
        await deliverDoc(invoice, after, `/api/invoices/${invoice.id}/send`);
      } catch (err) {
        T.toast(err.message || 'The invoice could not be saved.', 'danger');
      }
    }

    async function newReceipt() {
      let data;
      try {
        data = await api.get('/api/invoices', { silent: true });
      } catch (err) {
        T.toast('Could not load the invoice list.', 'danger');
        return;
      }
      const open_ = (data.items || []).filter(
        (i) => Number(i.balance) > 0 && i.status !== 'CANCELLED');
      if (!open_.length) {
        T.toast('Every invoice is settled — there is nothing to receipt.', 'info');
        return;
      }

      const res = await T.formModal({
        title: 'Record a payment',
        size: 'md',
        intro: 'Records the money against an invoice and issues a numbered receipt.',
        submitLabel: 'Save receipt',
        fields: [
          { name: 'invoice_id', label: 'Against invoice *', type: 'select', col: 12,
            icon: 'receipt', required: true,
            options: open_.map((i) => ({
              value: String(i.id),
              label: `${i.invoice_no} · ${i.customer_name} · ${money(i.balance, i.currency)} due`,
            })) },
          { name: 'amount', label: 'Amount received *', type: 'money', col: 6,
            step: '0.01', required: true, icon: 'cash-stack', affix: 'USD' },
          { name: 'method', label: 'Method', type: 'select', col: 6,
            icon: 'credit-card', options: methodOptions },
          { name: 'reference', label: 'Reference', col: 12, icon: 'hash',
            hint: 'EcoCash transaction id or bank reference.',
            placeholder: 'e.g. MP240915.1234' },
          afterSaveField(),
        ],
        validate: (values) => {
          const chosen = open_.find((i) => String(i.id) === String(values.invoice_id));
          if (chosen && Number(values.amount) > Number(chosen.balance) + 0.001) {
            return { amount: `That is more than the ${money(chosen.balance, chosen.currency)} outstanding.` };
          }
          return {};
        },
      });
      if (!res) return;

      const after = res.after_save;
      delete res.after_save;
      const invoiceId = res.invoice_id;
      try {
        const out = await api.post(`/api/invoices/${invoiceId}/payment`,
          { ...res, send_receipt: false });
        const payment = out.payment || {};
        T.toast(`Receipt ${payment.receipt_no || 'issued'} saved.`, 'info');
        load();
        await deliverDoc(payment, after, `/api/payments/${payment.id}/receipt/send`);
      } catch (err) {
        T.toast(err.message || 'The payment could not be recorded.', 'danger');
      }
    }

    const statusFilter = T.iconSelect({
      value: state.status, icon: 'funnel', width: 158, ariaLabel: 'Invoice status',
      onChange: (v) => { state.status = v; load(); },
      options: [{ value: '', label: 'All statuses' }].concat(
        (meta.invoice_statuses || []).map((s) => ({ value: s, label: s }))),
    });

    await load();
    /* Deep link from the Payments tab: a receipt has to hang off an invoice, so
       recording one always lands here. */
    if (ctx.query.record) setTimeout(newReceipt, 250);

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Invoices & payments')),
        h('div.tc-toolbar', [statusFilter]),
        h('button.btn.btn-sm.btn-outline-secondary', {
          onclick: () => newReceipt(),
          title: 'Record a payment and issue a receipt',
        }, T.icon('receipt'), ' Record payment'),
        h('button.btn.btn-sm.btn-brand', {
          onclick: () => newInvoice(),
          title: 'Raise an invoice without a job card',
        }, T.icon('plus-lg'), ' New invoice'),
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

    /* ── end of day ──────────────────────────────────────────────────
       The closing sheet: job card statuses, enquiries and bookings, and the
       money, for one chosen day. Rendered by the same service that builds the
       PDF, so the screen and the paper can never disagree. */
    const eodHost = h('div');
    const eodDate = h('input.form-control.form-control-sm', {
      type: 'date', value: T.today(), style: 'max-width:11rem',
      onchange: (e) => loadSheet(e.target.value),
    });

    async function loadSheet(day) {
      T.mount(eodHost, T.spinner('Building the closing sheet…'));
      try {
        const sheet = await api.get(
          `/api/reports/end-of-day?date=${day || eodDate.value}`);
        T.mount(eodHost, renderSheet(sheet));
      } catch (err) {
        T.mount(eodHost, T.emptyState('Could not build the report',
          'Try another date.', 'exclamation-triangle'));
      }
    }

    const sheetRow = (label, value) => h('div.d-flex.justify-content-between.small.py-1', [
      h('span.text-secondary', label), h('strong', value),
    ]);

    function renderSheet(s) {
      const sheetTable = (columns, rows, empty) => rows.length
        ? T.dataTable({ columns, rows })
        : h('div.small.text-secondary.py-3', empty);

      return h('div', [
        h('div.row.g-3.mb-3', [
          h('div.col-6.col-lg', T.statCard({
            label: 'Open job cards', value: String(s.jobs.open),
            icon: 'clipboard-check', colour: 'primary' })),
          h('div.col-6.col-lg', T.statCard({
            label: 'Awaiting confirmation', value: String(s.bookings.awaiting_confirmation),
            icon: 'calendar-check', colour: 'brand' })),
          h('div.col-6.col-lg', T.statCard({
            label: 'Open to-do', value: String(s.tasks.open),
            icon: 'list-check',
            colour: s.tasks.overdue ? 'danger' : 'warning' })),
          h('div.col-6.col-lg', T.statCard({
            label: 'Received today', value: money(s.money.collected),
            icon: 'cash-coin', colour: 'success' })),
          h('div.col-6.col-lg', T.statCard({
            label: 'Outstanding', value: money(s.money.outstanding),
            icon: 'receipt', colour: 'warning' })),
        ]),
        h('div.row.g-3', [
          h('div.col-lg-6', T.section({
            title: 'Job cards by status',
            body: sheetTable([
              { label: 'Stage', render: (r) => r.label },
              { label: 'Job cards', class: 'text-end',
                render: (r) => h('span.badge.text-bg-secondary', r.count) },
            ], s.jobs.by_stage, 'Nothing open.'),
          })),
          h('div.col-lg-6', T.section({
            title: 'Enquiries & bookings by status',
            body: sheetTable([
              { label: 'Status', render: (r) => r.label },
              { label: 'Count', class: 'text-end',
                render: (r) => h('span.badge.text-bg-secondary', r.count) },
            ], s.bookings.by_status, 'Nothing booked for this day.'),
          })),
        ]),
        h('div.row.g-3.mt-3', [
          h('div.col-lg-6', T.section({
            title: 'Enquiries waiting for confirmation',
            body: sheetTable([
              { label: 'Reference', render: (r) => h('div', [
                  h('div.fw-semibold', r.reference),
                  h('div.small.text-secondary', r.source || '')]) },
              { label: 'Customer', render: (r) => r.customer_name || '—' },
              { label: 'Service', render: (r) => h('span.small', r.service) },
              { label: 'Slot', class: 'text-end', render: (r) => dateShort(r.slot_date) },
            ], s.bookings.pending, 'Nothing waiting — every enquiry has been handled.'),
          })),
          h('div.col-lg-6', T.section({
            title: "Today's appointments",
            body: sheetTable([
              { label: 'Reference', render: (r) => r.reference },
              { label: 'Customer', render: (r) => r.customer_name || '—' },
              { label: 'Status', render: (r) => h('span.badge.text-bg-secondary', r.status_label) },
              { label: 'Handled by', render: (r) =>
                  h('span.small', r.attended_by || r.confirmed_by || '—') },
            ], s.bookings.scheduled_list, 'Nothing booked for this day.'),
          })),
        ]),
        h('div.row.g-3.mt-3', [
          h('div.col-lg-6', T.section({
            title: 'The day book — to-do by status',
            body: sheetTable([
              { label: 'Status', render: (r) => r.label },
              { label: 'Tasks', class: 'text-end',
                render: (r) => h('span.badge.text-bg-secondary', r.count) },
            ], s.tasks.by_status, 'Nothing on the list.'),
          })),
          h('div.col-lg-6', T.section({
            title: 'Who is carrying what',
            body: sheetTable([
              { label: 'Custodian', render: (r) => h('strong', r.name) },
              { label: 'Open', class: 'text-end', render: (r) => r.open },
              { label: 'Done today', class: 'text-end', render: (r) => r.done },
              { label: 'Past due', class: 'text-end', render: (r) => r.overdue
                  ? h('span.badge.text-bg-danger', r.overdue) : r.overdue },
            ], s.tasks.by_custodian, 'Nobody has anything on the list.'),
          })),
        ]),
        h('div.mt-3', T.section({
          title: 'To-do list',
          body: sheetTable([
            { label: 'Activity', render: (r) => h('div', [
                h('div.fw-semibold', r.title),
                r.job_no ? h('div.small.text-secondary', `Job card ${r.job_no}`) : null]) },
            { label: 'Area', render: (r) => r.category ? h('span.chip', r.category) : '—' },
            { label: 'Custodian', render: (r) => h('span.small', r.custodian || 'Unassigned') },
            { label: 'Status', render: (r) => h('span.badge.text-bg-secondary', r.status_label) },
            { label: 'Due', class: 'text-end', render: (r) => r.is_overdue
                ? h('span.badge.text-bg-danger', dateShort(r.due_date))
                : h('span.small', r.due_date ? dateShort(r.due_date) : 'No date') },
          ], s.tasks.list, 'The list is clear — nothing outstanding.'),
        })),
        s.tasks.done.length ? h('div.mt-3', T.section({
          title: 'Completed today',
          body: sheetTable([
            { label: 'Activity', render: (r) => r.title },
            { label: 'Custodian', render: (r) => h('span.small', r.custodian || 'Unassigned') },
            { label: 'Area', render: (r) => h('span.small', r.category || '—') },
          ], s.tasks.done, ''),
        })) : null,
        h('div.row.g-3.mt-3', [
          h('div.col-lg-6', T.section({
            title: 'Money',
            body: h('div', [
              sheetRow('Invoiced today', money(s.money.invoiced)),
              sheetRow('Received today', money(s.money.collected)),
              sheetRow('Invoices settled today', String(s.money.paid_today_count)),
              sheetRow('Unpaid invoices', String(s.money.unpaid_count)),
              sheetRow('Outstanding', money(s.money.outstanding)),
              sheetRow('Overdue',
                `${s.money.overdue_count} · ${money(s.money.overdue_total)}`),
              s.money.by_method.length ? h('div.mt-2.border-top.pt-2',
                s.money.by_method.map((row) =>
                  h('div.d-flex.justify-content-between.small.text-secondary', [
                    h('span', row.method.replace(/_/g, ' ')),
                    h('span', `${row.count} · ${money(row.total)}`),
                  ]))) : null,
            ]),
          })),
          h('div.col-lg-6', T.section({
            title: 'Unpaid & part-paid invoices',
            body: sheetTable([
              { label: 'Invoice', render: (r) => h('div', [
                  h('div.fw-semibold', r.invoice_no),
                  h('div.small.text-secondary', r.customer_name || '')]) },
              { label: 'Total', class: 'text-end', render: (r) => money(r.total) },
              { label: 'Paid', class: 'text-end', render: (r) => money(r.amount_paid) },
              { label: 'Balance', class: 'text-end',
                render: (r) => h('strong', money(r.balance)) },
              { label: 'Due', class: 'text-end', render: (r) => r.is_overdue
                  ? h('span.badge.text-bg-danger', dateShort(r.due_date))
                  : h('span.small', r.due_date ? dateShort(r.due_date) : '—') },
            ], s.money.unsettled, 'Every invoice is settled.'),
          })),
        ]),
        s.staff.length ? h('div.mt-3', T.section({
          title: 'Staff activity',
          body: sheetTable([
            { label: 'Name', render: (r) => h('strong', r.name) },
            { label: 'Role', render: (r) => h('span.small', r.role) },
            { label: 'Confirmed', class: 'text-end', render: (r) => r.confirmed },
            { label: 'Attended', class: 'text-end', render: (r) => r.attended },
            { label: 'Open jobs', class: 'text-end', render: (r) => r.open_jobs },
            { label: 'To-do', class: 'text-end', render: (r) => r.open_tasks },
            { label: 'Done today', class: 'text-end', render: (r) => r.done_tasks },
          ], s.staff, ''),
        })) : null,
      ]);
    }

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


    await loadSheet(eodDate.value);
    return h('div', [
      h('h1.h4.mb-3', 'Reports & performance'),

      T.section({
        title: 'End of day',
        body: h('div', [
          h('div.d-flex.align-items-center.gap-2.mb-3.flex-wrap', [
            h('span.small.text-secondary.me-auto',
              'Job card statuses, enquiries and bookings, the day book, and where ' +
              'the money stands.'),
            eodDate,
            h('button.btn.btn-outline-secondary.btn-sm', {
              onclick: () => window.open(
                `/api/reports/end-of-day/pdf?date=${eodDate.value}`, '_blank'),
            }, T.icon('file-earmark-pdf'), ' Download PDF'),
          ]),
          eodHost,
        ]),
      }),

      h('div.row.g-3.mb-3', [
        h('div.col-6.col-lg-3', T.statCard({ label: 'Avg turnaround', value: `${m.avg_turnaround_days} days`, icon: 'stopwatch', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'WIP value', value: money(m.wip_value), icon: 'cash-stack', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Receivables', value: money(m.outstanding_receivables), icon: 'receipt', colour: 'warning' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Overdue invoices', value: m.overdue_invoices, icon: 'exclamation-triangle',
          colour: m.overdue_invoices ? 'danger' : 'secondary' })),
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
      h('div.mt-3', T.section({
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

    /* A one-line explanation of what each role is for, so a manager picking
       from the list knows what they are granting. */
    const ROLE_BLURB = {
      owner: 'Full control, including staff accounts and settings.',
      manager: 'Runs the shop: job cards, money, parts and staff.',
      estimator: 'Prices the work and sends quotations to customers.',
      storeman: 'Stock, parts ordering and supplier receipts.',
      technician: 'Sees the board and updates the job cards assigned to them.',
      frontdesk: 'Books vehicles in, takes payments and handles customers.',
    };
    const roleOptions = meta.roles.map((x) => ({
      value: x.code, label: x.label, description: ROLE_BLURB[x.code] || '',
    }));

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
                    options: roleOptions },
                  { name: 'password', label: 'New password', type: 'password', col: 12,
                    placeholder: 'Leave blank to keep the current one',
                    hint: 'Fill this in to issue someone a fresh password.' },
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
      empty: T.emptyState('No staff yet', 'Add the people who work in the shop.', 'people'),
    });

    async function addStaff() {
      const res = await T.formModal({
        title: 'Add staff member',
        icon: 'person-plus',
        size: 'lg',
        intro: 'They can sign in as soon as you save. Pass the password on — you can '
          + 'change it any time by editing their row.',
        fields: [
          { name: 'full_name', label: 'Full name', col: 6, required: true, icon: 'person',
            placeholder: 'Tapiwa Sibanda' },
          { name: 'email', label: 'Work email', type: 'email', col: 6, required: true,
            icon: 'envelope', placeholder: 'tapiwa@topclass.co.zw',
            hint: 'This becomes their sign-in name, and must be unique.' },
          { name: 'phone', label: 'Phone', type: 'tel', col: 6, icon: 'telephone',
            placeholder: '+263 77 000 0000' },
          { name: 'role', label: 'Role', type: 'select', col: 6, icon: 'person-badge',
            value: 'technician', options: roleOptions,
            hint: 'Decides what they can see and change.' },
          { name: 'password', label: 'Temporary password', type: 'password', col: 6,
            required: true, icon: 'key', hint: 'At least 8 characters.' },
          { name: 'confirm', label: 'Repeat password', type: 'password', col: 6,
            required: true, icon: 'key' },
        ],
        /* Checked before the dialog closes, so a typo doesn't cost the manager
           everything they just typed. */
        validate: (v) => {
          const errors = {};
          if (String(v.password || '').length < 8) {
            errors.password = 'Use at least 8 characters.';
          }
          if (v.confirm !== v.password) {
            errors.confirm = 'This must match the password above.';
          }
          return errors;
        },
        submitLabel: 'Create account',
      });
      if (!res) return;
      try {
        await api.post('/api/users', {
          full_name: res.full_name, email: res.email, phone: res.phone,
          role: res.role, password: res.password,
        });
        T.toast(`${res.full_name} signs in with ${res.email}.`, 'success', {
          title: 'Staff account created',
        });
        ctx.refresh();
      } catch (err) {
        T.toast(err.message || 'The account could not be created.', 'danger');
      }
    }

    /* /staff?new=1 opens the form straight away. */
    if (user.is_manager && ctx.query.new === '1') setTimeout(addStaff, 150);

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'Staff & settings'),
          h('div.small.text-secondary', `Signed in as ${user.full_name} (${user.role_label})`)]),
        user.is_manager ? h('button.btn.btn-brand.btn-sm', { onclick: addStaff },
          T.icon('person-plus'), ' Add staff member') : null,
      ]),
      T.section({ title: 'Team accounts', body: table, flush: true }),
      T.section({
        title: 'WhatsApp bot',
        body: h('div.row.g-3', [
          h('div.col-md-6', [
            h('p.small.text-secondary.mb-2',
              'The bot answers customer questions, logs quote requests, tracks repairs and ' +
              'takes bookings. Test it without a Meta account in simulator mode.'),
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
