/* Job card detail — progress, estimate, parts, QC, history. */(function () {
  const T = window.TCA;
  const { h, api, money, dateShort, dateTime } = T;

  T.route('/jobs/:id', async (ctx) => {
    const jobId = ctx.params.id;
    const data = await api.get(`/api/jobs/${jobId}`);
    const job = data.job;
    ctx.title = `Job card ${job.job_no}`;

    const host = h('div');

    async function reload() {
      const fresh = await api.get(`/api/jobs/${jobId}`);
      T.mount(host, render(fresh));
    }

    function render(payload) {
      const j = payload.job;
      const meta = T.store.get('meta');
      const estimate = j.estimate;
      const invoice = j.invoice;

      /* ── header ─────────────────────────────────────────────────── */
      const header = h('div.card.soft-card.mb-3', h('div.card-body', [
        h('div.d-flex.flex-wrap.gap-3.align-items-start', [
          h('div.flex-fill', [
            h('div.d-flex.align-items-center.gap-2.flex-wrap', [
              h('h1.h4.mb-0', j.reg_no),
              h('span.text-secondary', '·'),
              h('span.text-secondary', j.vehicle_title || ''),
              T.stageBadge(j.stage, j.stage_label),
              j.priority !== 'NORMAL' ? T.priorityBadge(j.priority) : null,
            ]),
            h('div.small.text-secondary.mt-1',
              `Job card ${j.job_no} · ${j.service} · in shop ${j.days_in_shop} day(s) · promised ${dateShort(j.promised_date)}`),
            h('div.small.mt-1', [
              T.icon('person'), ' ', h('a.text-decoration-none', { href: `#/customers` }, j.customer_name),
              j.customer_phone ? h('span.text-secondary', ` · ${j.customer_phone}`) : null,
            ]),
          ]),
          h('div.text-end', [
            h('div.text-secondary.small', 'Estimate total'),
            h('div.money-lg', estimate ? money(estimate.total, estimate.currency) : '—'),
            estimate
              ? h('div.small', { class: estimate.status === 'APPROVED' ? 'text-success' : 'text-warning' },
                  estimate.status === 'APPROVED' ? '✓ Approved' : `Status: ${estimate.status}`)
              : h('div.small.text-secondary', 'Not estimated yet'),
          ]),
        ]),
        h('div.mt-3', [
          h('div.d-flex.justify-content-between.small.text-secondary.mb-1',
            h('span', `${j.progress}% complete`),
            h('span', j.is_overdue ? h('span.text-danger.fw-semibold', 'Overdue') : 'On schedule')),
          h('div.progress', { style: 'height:10px' },
            h('div.progress-bar.bg-warning', { style: `width:${j.progress}%` })),
        ]),
      ]));

      /* ── actions ────────────────────────────────────────────────── */
      const nextLabel = payload.next_stage
        ? (meta.stages.find((s) => s.code === payload.next_stage) || {}).label
        : null;
      const actions = h('div.d-flex.gap-2.flex-wrap.mb-3', [
        h('button.btn.btn-brand.btn-sm.fw-semibold', {
          disabled: !j.is_open,
          onclick: async () => {
            try {
              const res = await api.post(`/api/jobs/${j.id}/advance`, {});
              T.toast(`${res.job.job_no} moved to ${res.job.stage_label}${res.notified ? ' · customer notified' : ''}`);
              reload();
            } catch (err) {
              if (err.status === 409) {
                const forced = await T.confirmDialog({
                  title: 'Cannot advance yet', message: err.message,
                  detail: 'Override the check and move the job forward anyway?',
                  confirmLabel: 'Override & advance', variant: 'danger',
                });
                if (forced) {
                  await api.post(`/api/jobs/${j.id}/advance`, { force: true });
                  T.toast('Job advanced (overridden).', 'warning');
                  reload();
                }
              } else T.toast(err.message, 'danger');
            }
          },
        }, T.icon('arrow-right-circle'), nextLabel ? ` Advance to ${nextLabel}` : ' Advance'),

        h('button.btn.btn-outline-primary.btn-sm', {
          onclick: () => T.navigate(`/estimates/new/${j.id}`),
        }, T.icon('calculator'), estimate ? ' New estimate version' : ' Build estimate'),

        h('button.btn.btn-outline-secondary.btn-sm', {
          onclick: () => addPartDialog(j),
        }, T.icon('box-seam'), ' Add part'),

        h('button.btn.btn-outline-success.btn-sm', {
          onclick: async () => {
            try {
              const res = await api.post(`/api/jobs/${j.id}/invoice`, {});
              T.toast(`Invoice ${res.invoice.invoice_no} created.`);
              reload();
            } catch (err) { T.toast(err.message, 'danger'); }
          },
        }, T.icon('receipt'), invoice ? ' View invoice' : ' Raise invoice'),

        h('button.btn.btn-outline-secondary.btn-sm', {
          onclick: () => notifyDialog(j),
        }, T.icon('whatsapp'), ' Notify customer'),

        h('button.btn.btn-outline-secondary.btn-sm', {
          onclick: () => printJobCard(j),
        }, T.icon('printer'), ' Print job card'),
      ]);

      /* ── overview tab ───────────────────────────────────────────── */
      const overview = h('div.row.g-3', [
        h('div.col-lg-7', [
          T.section({
            title: 'Damage & notes',
            body: [
              h('div.mb-3', [h('div.small.text-uppercase.text-secondary.fw-semibold', 'Damage summary'),
                h('div', j.damage_summary || '—')]),
              h('div.mb-3', [h('div.small.text-uppercase.text-secondary.fw-semibold', 'Description'),
                h('div', j.description || '—')]),
              h('div.row.g-2.small', [
                info('Bay', j.bay || '—'), info('Technician', j.technician || 'Unassigned'),
                info('Estimator', j.estimator || '—'),
                info('Fuel in', j.fuel_level || '—'), info('Odometer', j.odometer_in || '—'),
                info('Keys received', j.keys_received ? 'Yes' : 'No'),
                info('Valuables', j.valuables || 'None'),
                info('Checked in', dateTime(j.checked_in_at)),
              ]),
            ],
          }),
          T.section({ title: 'WhatsApp updates sent', body: notificationsHost(j) }),
        ]),
        h('div.col-lg-5', [
          T.section({ title: 'Parts', body: partsTable(j) }),
          T.section({ title: 'Photos & documents', body: photosHost(j) }),
        ]),
      ]);

      /* ── estimate tab ───────────────────────────────────────────── */
      const estimateTab = estimate ? h('div.row.g-3', [
        h('div.col-lg-8', T.section({
          title: `Estimate ${estimate.reference} · v${estimate.version}`,
          actions: [
            h('span.badge.text-bg-secondary', estimate.status),
            estimate.status !== 'APPROVED'
              ? h('button.btn.btn-sm.btn-success', {
                  onclick: async () => {
                    await api.post(`/api/estimates/${estimate.id}/approve`, {});
                    T.toast('Estimate approved.');
                    reload();
                  },
                }, T.icon('check2'), ' Approve')
              : null,
            estimate.status !== 'DECLINED'
              ? h('button.btn.btn-sm.btn-outline-danger', {
                  onclick: async () => {
                    const reason = await T.formModal({
                      title: 'Decline estimate', size: 'md',
                      fields: [{ name: 'reason', label: 'Reason', type: 'textarea', col: 12 }],
                      submitLabel: 'Decline',
                    });
                    if (reason) {
                      await api.post(`/api/estimates/${estimate.id}/decline`, reason);
                      T.toast('Estimate declined.', 'warning');
                      reload();
                    }
                  },
                }, 'Decline')
              : null,
            h('button.btn.btn-sm.btn-outline-secondary', {
              onclick: () => window.open(`/api/estimates/${estimate.id}/pdf`, '_blank'),
              title: 'Open the quotation as a PDF',
            }, T.icon('file-earmark-pdf'), ' PDF'),
            h('button.btn.btn-sm.btn-success', {
              onclick: () => sendQuotation(estimate),
              title: 'Send the quotation PDF on WhatsApp',
            }, T.icon('whatsapp'), ' Send'),
          ],
          body: h('div.table-responsive', h('table.table.table-sm.table-tc',
            h('thead', h('tr', [h('th', 'Description'), h('th', 'Type'), h('th.text-end', 'Qty'),
              h('th.text-end', 'Rate'), h('th.text-end', 'Amount')])),
            h('tbody', estimate.items.map((it) => h('tr', [
              h('td', it.description), h('td', h('span.chip', it.kind)),
              h('td.text-end', `${it.quantity} ${it.unit || ''}`),
              h('td.text-end', money(it.unit_price)), h('td.text-end', money(it.line_total)),
            ]))))),
          flush: true,
        })),
        h('div.col-lg-4', T.section({
          title: 'Summary',
          body: h('div', [
            row('Labour', money(estimate.labour_total)),
            row('Materials', money(estimate.materials_total)),
            row('Parts', money(estimate.parts_total)),
            h('hr.my-2'),
            row('Subtotal', money(estimate.subtotal)),
            row('VAT', money(estimate.vat)),
            h('hr.my-2'),
            row('Total', money(estimate.total, estimate.currency), true),
            estimate.approved_by ? h('div.small.text-success.mt-3',
              T.icon('check2-circle'), ` Approved by ${estimate.approved_by} on ${dateShort(estimate.approved_at)}`) : null,
          ]),
        })),
      ]) : T.emptyState('No estimate yet', 'Build an estimate from the damaged panels.', 'calculator');

      /* ── QC tab ─────────────────────────────────────────────────── */
      const qcTab = h('div.row.g-3', [
        h('div.col-lg-7', T.section({
          title: 'Quality control checklist',
          actions: j.stage === 'QC' || j.qc.total
            ? [h('button.btn.btn-sm.btn-brand', { onclick: () => qcDialog(j) }, 'Run checklist')] : [],
          body: j.qc_results.length ? h('div', j.qc_results.map((r) =>
            h('div.form-check.py-1', [
              h('input.form-check-input', { type: 'checkbox', checked: r.passed, disabled: true, id: `qc${r.id}` }),
              h('label.form-check-label', { for: `qc${r.id}` },
                r.item,
                r.comment ? h('div.small.text-secondary', r.comment) : null,
                r.checked_by ? h('div.small.text-secondary', `Checked by ${r.checked_by}`) : null),
            ]))) : T.emptyState('Checklist not started', 'It is generated automatically when the job reaches QC.', 'clipboard-check'),
        })),
        h('div.col-lg-5', T.section({
          title: 'Status',
          body: h('div', [
            h('div.d-flex.justify-content-between.mb-2', h('span', 'Checks passed'),
              h('strong', `${j.qc.passed} / ${j.qc.total}`)),
            j.qc.total ? h('div.progress', { style: 'height:8px' },
              h('div.progress-bar.bg-success', { style: `width:${Math.round((j.qc.passed / j.qc.total) * 100)}%` })) : null,
            j.qc.failed
              ? h('div.alert.alert-danger.small.mt-3.mb-0',
                  `${j.qc.failed} check(s) failed — rework required before the vehicle can be released.`)
              : j.qc.total ? h('div.alert.alert-success.small.mt-3.mb-0', 'All checks passed. Vehicle can be released.') : null,
          ]),
        })),
      ]);

      /* ── history tab ────────────────────────────────────────────── */
      const historyTab = T.section({
        title: 'Stage history',
        body: h('ul.timeline.mb-0', j.stage_history.slice().reverse().map((e) =>
          h('li', [h('span.dot'),
            h('div.fw-semibold', e.stage_label),
            h('div.small.text-secondary', `${dateTime(e.at)} · ${e.user}${e.note ? ' — ' + e.note : ''}`)]))),
      });

      const tabs = [
        { id: 'overview', label: 'Overview', body: overview },
        { id: 'estimate', label: 'Estimate', body: estimateTab },
        { id: 'qc', label: `QC${j.qc.total ? ` (${j.qc.passed}/${j.qc.total})` : ''}`, body: qcTab },
        { id: 'history', label: 'History', body: historyTab },
      ];
      const active = ctx.query.tab || 'overview';
      const paneHost = h('div');
      const tabBar = h('ul.nav.nav-tabs.mb-3', tabs.map((t) =>
        h('li.nav-item', h('a.nav-link', {
          class: t.id === active ? 'nav-link active' : 'nav-link',
          href: `#/jobs/${j.id}?tab=${t.id}`,
          onclick: (e) => {
            e.preventDefault();
            T.navigate(`/jobs/${j.id}?tab=${t.id}`);
          },
        }, t.label))));
      T.mount(paneHost, (tabs.find((t) => t.id === active) || tabs[0]).body);

      return h('div', [
        h('div.d-flex.align-items-center.mb-2.gap-2', [
          h('a.btn.btn-sm.btn-outline-secondary', { href: '#/jobs' }, T.icon('arrow-left'), ' All job cards'),
          h('span.small.text-secondary', `Job card ${j.job_no}`),
          h('div.ms-auto.d-flex.gap-2',
            j.is_open ? h('span.chip', T.icon('hourglass-split'), `${j.days_in_shop} days in shop`) : h('span.chip.text-bg-success', 'Collected')),
        ]),
        header,
        actions,
        tabBar,
        paneHost,
      ]);
    }

    /* ── sub-components ─────────────────────────────────────────── */
    function info(label, value) {
      return h('div.col-6.col-md-3',
        h('div.text-secondary.text-uppercase', { style: 'font-size:.66rem' }, label),
        h('div.fw-semibold', value));
    }
    function row(label, value, strong) {
      return h('div.d-flex.justify-content-between.mb-1',
        h('span.text-secondary', label), h('span', { class: strong ? 'fw-bold' : 'fw-semibold' }, value));
    }

    function partsTable(j) {
      if (!j.parts.length) return T.emptyState('No parts recorded', 'Add parts as they are identified.', 'box-seam');
      const colour = { REQUIRED: 'danger', ORDERED: 'warning', IN_TRANSIT: 'info',
                       RECEIVED: 'success', FITTED: 'dark', CANCELLED: 'secondary' };
      return h('div.table-responsive', h('table.table.table-sm.table-tc.mb-0',
        h('thead', h('tr', [h('th', 'Part'), h('th', 'Status'), h('th.text-end', 'Value')])),
        h('tbody', j.parts.map((p) => h('tr', {
          onclick: async () => {
            const next = { REQUIRED: 'ORDERED', ORDERED: 'IN_TRANSIT', IN_TRANSIT: 'RECEIVED',
                           RECEIVED: 'FITTED' }[p.status];
            if (!next) return;
            await api.patch(`/api/job-parts/${p.id}`, { status: next });
            T.toast(`${p.description} → ${next}`);
            reload();
          },
          style: 'cursor:pointer',
        }, [
          h('td', [h('div.small', p.description),
            h('div.small.text-secondary', p.supplier || '',
              p.eta ? ` · ETA ${dateShort(p.eta)}` : '')]),
          h('td', h(`span.badge.text-bg-${colour[p.status] || 'secondary'}`, p.status)),
          h('td.text-end', money(p.line_total)),
        ])))));
    }

    /** Anything that is not an image renders as a document row, not an <img>. */
    const isPdf = (name) => /\.pdf($|\?)/i.test(name || '');

    function photosHost(j) {
      const host = h('div');
      /* A real picker. This used to POST a hard-coded placehold.co URL and call it
         "Add photo", so there was no way to attach an actual picture or PDF and
         the button opened nothing at all. */
      const picker = h('input', {
        type: 'file', multiple: true, hidden: true,
        accept: 'image/png,image/jpeg,image/webp,image/gif,application/pdf',
      });

      async function upload(files) {
        let done = 0;
        const failed = [];
        for (const file of files) {
          const payload = new FormData();
          payload.append('file', file);
          payload.append('kind', isPdf(file.name) ? 'DOCUMENT' : 'PHOTO');
          payload.append('caption', file.name);
          try {
            const res = await fetch(`/api/jobs/${j.id}/documents`, {
              method: 'POST',
              headers: { 'X-CSRFToken': window.__CSRF__ || '' },
              credentials: 'same-origin',
              body: payload,
            });
            if (res.ok) done += 1;
            else {
              const body = await res.json().catch(() => ({}));
              failed.push(`${file.name}: ${body.error || `HTTP ${res.status}`}`);
            }
          } catch (err) {
            failed.push(`${file.name}: upload failed`);
          }
        }
        if (done) T.toast(`${done} file${done === 1 ? '' : 's'} attached to ${j.job_no}.`, 'success');
        failed.forEach((msg) => T.toast(msg, 'danger'));
        reload();
      }

      picker.addEventListener('change', () => {
        const files = Array.from(picker.files || []);
        picker.value = '';            // so the same file can be re-picked later
        if (files.length) upload(files);
      });

      host.appendChild(h('div.d-flex.align-items-center.gap-2.flex-wrap.mb-2', [
        h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button', onclick: () => picker.click(),
        }, T.icon('cloud-arrow-up'), ' Attach photo or PDF'),
        h('span.tc-hint', 'JPG, PNG, WebP, GIF or PDF, up to 24 MB each.'),
        picker,
      ]));

      host.appendChild(j.photos.length
        ? h('div.row.g-2', j.photos.map((p) => h('div.col-6', h('div',
            isPdf(p.url)
              ? h('a.d-flex.align-items-center.gap-2.text-truncate',
                  { href: p.url, target: '_blank', rel: 'noopener' },
                  T.icon('file-earmark-pdf'), h('span.text-truncate', p.caption || 'Document'))
              : h('img.img-fluid.rounded', { src: p.url, alt: p.caption || 'Job photo', loading: 'lazy' }),
            h('div.small.text-secondary.text-truncate', p.caption || p.kind)))))
        : T.emptyState('Nothing attached yet', 'Photos and PDFs you attach appear here.', 'camera'));
      return host;
    }

    /* ── document delivery ──────────────────────────────────────── */
    async function sendQuotation(estimate) {
      const go = await T.confirmDialog({
        title: `Send quotation ${estimate.reference}?`,
        message: `The PDF goes to ${job.customer_name} on WhatsApp, with Approve and Decline buttons attached.`,
        detail: `${estimate.currency} ${T.money(estimate.total)} · valid until ${T.dateShort(estimate.expires_on)}`,
        confirmLabel: 'Send on WhatsApp',
        variant: 'success',
        icon: 'whatsapp',
        subtitle: 'Delivered instantly on WhatsApp',
      });
      if (!go) return;
      try {
        T.toast('Sending quotation…', 'info', { timeout: 2500 });
        const res = await api.post(`/api/estimates/${estimate.id}/send`, {});
        T.toast(`Quotation sent to ${job.customer_name}.`, 'success', {
          title: 'WhatsApp delivered',
          action: { label: 'Open PDF', run: () => window.open(res.result.pdf, '_blank') },
        });
        reload();
      } catch (err) {
        T.toast(err.message || 'The quotation could not be sent.', 'danger');
      }
    }

    function notificationsHost(j) {
      const host = h('div');
      api.get('/api/notifications').then((res) => {
        const items = res.items.filter((n) => n.job_id === j.id).slice(0, 8);
        T.mount(host, items.length
          ? h('ul.list-unstyled.mb-0', items.map((n) => h('li.border-bottom.py-2', [
              h('div.d-flex.justify-content-between',
                h('span.small.fw-semibold', n.template),
                h('span.small.text-secondary', dateTime(n.created_at))),
              h('div.small.text-secondary', { style: 'white-space:pre-wrap' }, n.body),
              n.status === 'sent' ? null : h('span.badge.text-bg-warning.mt-1', n.status),
            ])))
          : T.emptyState('No WhatsApp updates sent yet', null, 'whatsapp'));
      });
      return host;
    }

    /* ── dialogs ────────────────────────────────────────────────── */
    async function addPartDialog(j) {
      const parts = await api.get('/api/parts');
      const res = await T.formModal({
        title: `Add part to ${j.job_no}`,
        intro: 'Parts marked as blocking will prevent the job card advancing past "Awaiting parts".',
        fields: [
          { name: 'part_id', label: 'Stock item', type: 'select', col: 6,
            options: [{ value: '', label: '— Custom / not in stock —' }].concat(
              parts.items.map((p) => ({ value: p.id, label: `${p.sku} · ${p.name}` }))) },
          { name: 'description', label: 'Description *', col: 6, required: true,
            placeholder: 'Front bumper — replacement' },
          { name: 'quantity', label: 'Quantity', type: 'number', col: 3, value: 1, step: '1' },
          { name: 'unit_price', label: 'Unit price (USD)', type: 'number', col: 3, step: '0.01', value: 0 },
          { name: 'status', label: 'Status', type: 'select', col: 3,
            options: ['REQUIRED', 'ORDERED', 'IN_TRANSIT', 'RECEIVED', 'FITTED'] },
          { name: 'eta', label: 'ETA', type: 'date', col: 3 },
          { name: 'supplier', label: 'Supplier', col: 12,
            options: undefined, placeholder: 'Croco Motor Spares' },
        ],
        submitLabel: 'Add part',
      });
      if (!res) return;
      if (!res.part_id) delete res.part_id;
      await api.post(`/api/jobs/${j.id}/parts`, res);
      T.toast('Part added.');
      reload();
    }

    async function qcDialog(j) {
      const list = await api.get(`/api/jobs/${j.id}`);
      const existing = {};
      (list.job.qc_results || []).forEach((r) => { existing[r.item] = r; });
      const checks = {};
      const items = (T.store.get('meta').qc_checklist || []).length
        ? T.store.get('meta').qc_checklist
        : (list.job.qc_results || []).map((r) => r.item);

      const body = h('form', items.map((item) => {
        const rec = existing[item] || {};
        const box = h('input.form-check-input', { type: 'checkbox', checked: !!rec.passed });
        checks[item] = { box, comment: null };
        const comment = h('input.form-control.form-control-sm.mt-1', {
          placeholder: 'Comment (if failed)', value: rec.comment || '',
        });
        checks[item].comment = comment;
        return h('div.border-bottom.py-2', [
          h('div.form-check', [box, h('label.form-check-label.fw-semibold', item)]),
          comment,
        ]);
      }));

      const m = T.modal({
        title: `QC checklist — ${j.job_no}`, size: 'lg', body,
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm', { type: 'button', 'data-bs-dismiss': 'modal' }, 'Cancel'),
          h('button.btn.btn-brand.btn-sm', {
            type: 'button',
            onclick: async () => {
              const results = items.map((item) => ({
                item, passed: checks[item].box.checked,
                comment: checks[item].comment.value.trim() || null,
              }));
              const res = await api.post(`/api/jobs/${j.id}/qc`, { results });
              m.close();
              T.toast(res.can_release ? 'All checks passed — vehicle can be released.'
                                      : `${res.qc.failed} check(s) failed.`, res.can_release ? 'success' : 'warning');
              reload();
            },
          }, 'Save checklist'),
        ],
      });
    }

    async function notifyDialog(j) {
      const res = await T.formModal({
        title: `Message ${j.customer_name}`,
        intro: `Sends to ${j.customer_phone || 'the number on file'} on WhatsApp.`,
        size: 'md',
        fields: [{ name: 'body', label: 'Message', type: 'textarea', col: 12, rows: 5, required: true,
          value: `Hello ${(j.customer_name || '').split(' ')[0]}, an update on job card ${j.job_no}: ` }],
        submitLabel: 'Send on WhatsApp',
      });
      if (!res) return;
      const conversations = await api.get('/api/whatsapp/conversations');
      const match = conversations.items.find((c) => c.display_name === j.customer_name)
        || conversations.items[0];
      if (!match) { T.toast('No WhatsApp conversation found for this customer.', 'warning'); return; }
      await api.post(`/api/whatsapp/conversations/${match.id}/reply`, { body: res.body });
      T.toast('Message sent.');
    }

    await reload();
    return host;
  });
})();
