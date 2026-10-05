/* Quotations: raising one on an enquiry, and everything already quoted.
 *
 * A quotation is priced against the *enquiry* and only joins a job card once the
 * customer accepts it. That is why this screen is separate from intake: the desk
 * can price a phone call in two minutes without inventing a job card for a car
 * that may never turn up, and accepting the quotation is what opens one.
 *
 * Nothing here asks for a registration, an ID number or a paint code. The bot
 * takes most enquiries from a photo and a message with none of that on it, and
 * demanding it before a price can be written is what stopped the desk quoting at
 * all — the car is captured when it is booked in.
 */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  const STATUS_TONE = {
    DRAFT: 'secondary', SENT: 'info', APPROVED: 'success', DECLINED: 'danger',
  };
  const statusBadge = (status) => T.badge(
    { DRAFT: 'Draft', SENT: 'Sent', APPROVED: 'Approved', DECLINED: 'Declined' }[status]
      || status,
    STATUS_TONE[status] || 'secondary');

  const serviceNames = () => (T.store.get('meta')?.service_names || []);

  /* ── raising one ────────────────────────────────────────────────────── */
  /**
   * Build a quotation on a new enquiry, without touching the job cards.
   *
   * @param {object}   [opts]
   * @param {Function} [opts.onCreated] called with the saved estimate
   * @returns {Promise<object|null>} the estimate, or null if cancelled
   */
  async function newQuotation({ onCreated } = {}) {
    const [customersRes, panelsRes] = await Promise.all([
      api.get('/api/customers'), api.get('/api/estimating/panels'),
    ]);
    const customers = customersRes.items;
    const panels = panelsRes.panels;

    const selected = new Set();
    /* Which customer this is for. `id` is set only when the desk picked one off
       the register; a typed name stays null and the API creates them. */
    const chosen = { id: null };
    let lines = [];

    const totalValue = h('span.tc-footer-total-value', '—');
    const previewHost = h('div');
    const notesInput = h('textarea.form-control.form-control-sm', { rows: 2 });
    const nameInput = h('input.form-control.form-control-sm', { placeholder: 'Full name' });
    const phoneField = T.telField({ id: 'q_phone', placeholder: '77 000 0000' });

    const paintBox = h('input.form-check-input', { type: 'checkbox', checked: true, id: 'qPaint' });
    const consumablesBox = h('input.form-check-input', {
      type: 'checkbox', checked: true, id: 'qConsumables',
    });

    const serviceSelect = h('select.form-select.form-select-sm', { name: 'service' },
      (serviceNames().length ? serviceNames() : ['Panel Beating & Spray Painting'])
        .map((s) => h('option', { selected: s === 'Panel Beating & Spray Painting' }, s)));

    /* Name and number are only questions for somebody who is not on file. Asking
       beside a customer that was just picked invites a second, conflicting record
       — and the number shown for an existing customer is the one every WhatsApp
       update goes to, so it stays editable and is saved back as a correction. */
    const nameField = h('div.col-md-4', field('Customer name *', nameInput));
    const phoneFieldHost = h('div.col-md-4', field('Phone / WhatsApp', phoneField.node, {
      hint: 'Every update and the quotation itself go here.',
    }));

    const customerSelect = T.searchableSelect(h('select.form-select.form-select-sm', {
      onchange: (e) => {
        const picked = customers.find((c) => String(c.id) === String(e.target.value));
        chosen.id = picked ? picked.id : null;
        nameField.classList.toggle('d-none', !!picked);
        if (picked) {
          nameInput.value = '';
          phoneField.set(picked.phone || picked.whatsapp || '');
        }
      },
    }, [h('option', { value: '' }, '— New customer —')].concat(
      customers.map((c) => h('option', { value: c.id },
        `${c.name}${c.phone ? ' · ' + c.phone : ''}`)))), {
      placeholder: 'Search name or phone…', ariaLabel: 'Search customers',
    });

    const check = (el, label) => h('div.form-check', [
      el, h('label.form-check-label', { for: el.id }, label),
    ]);

    async function refreshPreview() {
      if (!selected.size) {
        lines = [];
        T.mount(previewHost, h('div.tc-hint', 'Nothing picked yet.'));
        totalValue.textContent = '—';
        return;
      }
      T.mount(previewHost, T.spinner('Pricing…'));
      try {
        const res = await api.post('/api/estimating/preview', {
          panels: [...selected],
          include_paint: paintBox.checked,
          include_consumables: consumablesBox.checked,
        });
        lines = res.lines;
        renderPreview(res.summary || {});
      } catch (err) {
        lines = [];
        T.mount(previewHost, h('div.alert.alert-danger.small', err.message));
        totalValue.textContent = '—';
      }
    }

    function renderPreview(summary) {
      T.mount(previewHost, [
        h('div.table-responsive', h('table.table.table-sm.table-tc.mb-2', [
          h('thead', h('tr', [
            h('th', 'Description'), h('th', 'Type'), h('th.text-end', 'Qty'),
            h('th.text-end', 'Rate'), h('th.text-end', 'Amount'),
          ])),
          h('tbody', lines.map((l) => h('tr', [
            h('td', l.description),
            h('td', h('span.chip', l.kind)),
            h('td.text-end', `${l.quantity} ${l.unit || ''}`),
            h('td.text-end', money(l.unit_price)),
            h('td.text-end', money(l.line_total)),
          ]))),
        ])),
        h('div.row.g-2.small', [
          ['Labour', summary.labour_total], ['Materials', summary.materials_total],
          ['Parts', summary.parts_total], ['Subtotal', summary.subtotal],
          ['VAT', summary.vat], ['Total', summary.total],
        ].map(([label, value], i, all) => h('div.col-6.col-md-2', [
          h('div.text-secondary', label),
          h('div', { class: i === all.length - 1 ? 'fw-bold' : 'fw-semibold' }, money(value)),
        ]))),
      ]);
      totalValue.textContent = money(summary.total);
    }

    const panelChips = h('div.d-flex.flex-wrap.gap-2', panels.map((p) => {
      const chip = h('button.btn.btn-sm.btn-outline-secondary', { type: 'button' }, p);
      chip.addEventListener('click', () => {
        if (selected.has(p)) {
          selected.delete(p);
          chip.className = 'btn btn-sm btn-outline-secondary';
        } else {
          selected.add(p);
          chip.className = 'btn btn-sm btn-brand';
        }
        refreshPreview();
      });
      return chip;
    }));

    /* Both feet on the same enquiry, so "save" and "save and send" cannot drift
       into two quotations for the same job. */
    async function saveQuotation() {
      if (!selected.size) {
        T.toast('Pick at least one damaged panel to quote.', 'warning');
        return null;
      }
      const phone = phoneField.read();
      if (!chosen.id && !nameInput.value.trim()) {
        nameInput.classList.add('is-invalid');
        nameInput.focus();
        T.toast('Choose a customer or type the new customer’s name.', 'warning');
        return null;
      }

      const bookingBody = chosen.id
        ? { customer_id: chosen.id, phone, service: serviceSelect.value, source: 'phone' }
        : {
            name: nameInput.value.trim(), phone, service: serviceSelect.value,
            source: 'phone',
          };
      if (!chosen.id && !phone) {
        T.toast('A WhatsApp number is needed to send the quotation.', 'warning');
        return null;
      }

      /* The enquiry is what the quotation hangs off. It is the same record the
         bot creates from a WhatsApp enquiry, so a quote taken at the desk and one
         taken by the Flow end up looking the same to everybody downstream. */
      const bookingRes = await api.post('/api/bookings', bookingBody);
      const estRes = await api.post(`/api/bookings/${bookingRes.booking.id}/estimate`, {
        panels: [...selected],
        include_paint: paintBox.checked,
        include_consumables: consumablesBox.checked,
        notes: notesInput.value,
      });
      return { estimate: estRes.estimate, booking: bookingRes.booking };
    }

    let busy = false;
    async function run(btn, label, send) {
      if (busy) return;
      busy = true;
      T.mount(btn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Working…']);
      try {
        const saved = await saveQuotation();
        if (!saved) {
          busy = false;
          T.mount(btn, label);
          return;
        }
        if (send) {
          T.mount(btn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Sending…']);
          const sent = await api.post(`/api/estimates/${saved.estimate.id}/send`, {});
          T.toast(`Quotation **${saved.estimate.reference}** sent on WhatsApp · `
            + money(saved.estimate.total), 'success');
          if (sent && sent.result && sent.result.link) {
            console.debug('Quotation link', sent.result.link);
          }
        } else {
          T.toast(`Quotation **${saved.estimate.reference}** saved · `
            + money(saved.estimate.total), 'success');
        }
        settled = true;
        m.close();
        if (onCreated) onCreated(saved.estimate);
      } catch (err) {
        busy = false;
        T.mount(btn, label);
        T.toast(err.message || 'The quotation could not be saved.', 'danger');
      }
    }

    const saveBtn = h('button.btn.btn-outline-secondary.btn-sm.fw-semibold', { type: 'button' },
      [T.icon('clipboard-check'), ' Save quotation']);
    const sendBtn = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'button' },
      [T.icon('whatsapp'), ' Send quotation via WhatsApp']);
    let settled = false;

    const m = T.modal({
      title: 'New quotation',
      subtitle: 'Price the job now. The customer accepts, and the car is booked in after that.',
      icon: 'calculator',
      accent: 'brand',
      size: 'lg',
      body: h('div.row.g-3', [
        h('div.col-12', h('div.tc-form-section', [h('span.idx', 1), h('span', 'Who it is for')])),
        h('div.col-md-4', field('Customer', customerSelect.node)),
        nameField,
        phoneFieldHost,
        h('div.col-md-4', field('Service', serviceSelect)),

        h('div.col-12', h('div.tc-form-section', [h('span.idx', 2), h('span', 'Damaged panels')])),
        h('div.col-12', h('div', [
          h('div.tc-hint.mb-2', 'Tap every damaged panel — the total updates as you go.'),
          panelChips,
        ])),
        h('div.col-md-6', check(paintBox, 'Include refinishing')),
        h('div.col-md-6', check(consumablesBox, 'Include paint and consumables')),

        h('div.col-12', h('div.tc-form-section', [h('span.idx', 3), h('span', 'Totals')])),
        h('div.col-12', h('div.card.bg-body-tertiary.border-0',
          h('div.card-body', previewHost))),

        h('div.col-12', h('div.tc-form-section', [h('span.idx', 4), h('span', 'Notes')])),
        h('div.col-12', field('Anything the customer should see', notesInput)),
      ]),
      footer: [
        h('div.tc-footer-total', [h('span.tc-footer-total-label', 'Quotation'), totalValue]),
        h('button.btn.btn-outline-secondary.btn-sm', {
          type: 'button', 'data-bs-dismiss': 'modal',
        }, 'Cancel'),
        saveBtn,
        sendBtn,
      ],
    });

    saveBtn.addEventListener('click', () => run(saveBtn,
      [T.icon('clipboard-check'), ' Save quotation'], false));
    sendBtn.addEventListener('click', () => run(sendBtn,
      [T.icon('whatsapp'), ' Send quotation via WhatsApp'], true));
    [paintBox, consumablesBox].forEach((el) => el.addEventListener('change', refreshPreview));
    m.el.addEventListener('hidden.bs.modal', () => { if (!settled && !onCreated) onCreated?.(null); });

    setTimeout(() => nameInput.focus(), 300);
    return null;
  }
  T.newQuotation = newQuotation;

  /* ── the tab ────────────────────────────────────────────────────────── */
  T.route('/quotations', async (ctx) => {
    ctx.title = 'Quotations';
    const meta = T.store.get('meta');
    const state = {
      q: ctx.query.q || '',
      status: ctx.query.status || '',
      open: ctx.query.open === '1',
    };

    const host = h('div');
    const countLabel = h('span.text-secondary.small');

    const params = () => {
      const p = new URLSearchParams();
      if (state.q) p.set('q', state.q);
      if (state.status) p.set('status', state.status);
      if (state.open) p.set('open_only', '1');
      return p;
    };

    async function load() {
      T.mount(host, T.skeletonTable(8, 6));
      const p = params();
      T.listState('quotations', p);
      const data = await api.get(`/api/quotations?${p.toString()}`);
      const openCount = data.items.filter((q) => ['DRAFT', 'SENT'].includes(q.status)).length;
      countLabel.textContent = `${data.count} quotation${data.count === 1 ? '' : 's'}`
        + (openCount ? ` · ${openCount} awaiting an answer` : '');

      T.mount(host, T.dataTable({
        columns: [
          { label: 'Quotation', render: (r) => h('div', [
              h('div.fw-semibold', r.reference),
              h('div.small.text-secondary', dateShort(r.created_at)),
            ]) },
          { label: 'Customer', render: (r) => h('div', [
              h('div', r.customer_name || '—'),
              h('div.small.text-secondary', r.booking_reference || '')]) },
          { label: 'Vehicle', render: (r) => h('div', [
              h('div', r.reg_no || 'Not captured yet'),
              h('div.small.text-secondary', r.vehicle_title || '')]) },
          { label: 'Service', class: 'd-none d-lg-table-cell',
            render: (r) => h('span.small', r.service || '—') },
          { label: 'Total', class: 'text-end',
            render: (r) => h('span.fw-semibold', money(r.total, r.currency)) },
          { label: 'Status', render: (r) => h('div.d-flex.align-items-center.gap-2', [
              statusBadge(r.status),
              r.is_expired ? T.badge('Expired', 'warning') : null,
            ]) },
          { label: 'Job card', render: (r) => (r.job_no
              ? h('a.small', { href: `#/jobs/${r.job_id}` }, r.job_no)
              : h('span.small.text-secondary', 'not booked in')) },
        ],
        rows: data.items,
        onRowClick: (r) => openQuotation(r.id, load),
        empty: T.emptyState('No quotations here',
          state.q || state.status || state.open
            ? 'Try a different filter.'
            : 'Price a job and it will appear here, before any job card exists.',
          'calculator'),
      }));
    }

    const filters = h('div.tc-toolbar.mb-3', [
      T.iconSelect({
        value: state.status, icon: 'funnel', width: 175, ariaLabel: 'Status',
        onChange: (v) => { state.status = v; load(); },
        options: [
          { value: '', label: 'All quotations' },
          { value: 'APPROVED', label: 'Approved' },
          { value: 'SENT', label: 'Sent' },
          { value: 'DRAFT', label: 'Draft' },
          { value: 'DECLINED', label: 'Declined' },
        ],
      }),
      h('button.btn.btn-sm.btn-outline-secondary', {
        type: 'button',
        class: state.open ? 'is-active' : null,
        onclick: (e) => {
          state.open = !state.open;
          e.currentTarget.classList.toggle('is-active', state.open);
          load();
        },
      }, T.icon('hourglass-split'), ' Awaiting an answer'),
      h('div.tc-toolbar-spacer'),
      T.searchInput({
        placeholder: 'Search reference, customer, registration or enquiry…',
        value: state.q, width: 330,
        oninput: T.debounce((e) => { state.q = e.target.value; load(); }, 350),
      }),
      h('button.btn.btn-brand.btn-sm', {
        onclick: () => newQuotation({ onCreated: () => load() }),
      }, T.icon('plus-lg'), ' New quotation'),
    ]);

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'Quotations'), countLabel]),
        h('span.tc-hint.d-none.d-md-inline',
          'Priced on the enquiry · a job card opens when the customer accepts'),
      ]),
      filters,
      T.section({ body: host, flush: true, tools: (meta && null) }),
    ]);
  });

  /* ── one quotation ──────────────────────────────────────────────────── */
  async function openQuotation(id, onChanged) {
    const res = await api.get(`/api/estimates/${id}`);
    const est = res.estimate;

    const actionHost = h('div.d-flex.gap-2.flex-wrap');
    const m = T.modal({
      title: `Quotation ${est.reference}`,
      subtitle: [est.customer_name, est.reg_no || 'vehicle not captured yet',
                 est.service].filter(Boolean).join(' · '),
      icon: 'file-earmark-text',
      accent: est.status === 'APPROVED' ? 'success'
        : est.status === 'DECLINED' ? 'danger' : 'brand',
      size: 'lg',
      body: h('div', [
        h('div.d-flex.align-items-center.gap-2.mb-3.flex-wrap', [
          statusBadge(est.status),
          est.is_expired ? T.badge('Expired', 'warning') : null,
          h('span.tc-hint', `Raised ${dateShort(est.created_at)} · valid until `
            + dateShort(est.expires_on)),
        ]),
        h('div.table-responsive', h('table.table.table-sm.table-tc.mb-3', [
          h('thead', h('tr', [
            h('th', 'Description'), h('th', 'Type'), h('th.text-end', 'Qty'),
            h('th.text-end', 'Rate'), h('th.text-end', 'Amount'),
          ])),
          h('tbody', est.items.map((l) => h('tr', [
            h('td', l.description),
            h('td', h('span.chip', l.kind)),
            h('td.text-end', `${l.quantity} ${l.unit || ''}`),
            h('td.text-end', money(l.unit_price, est.currency)),
            h('td.text-end', money(l.line_total, est.currency)),
          ]))),
        ])),
        h('div.tc-quote-totals', [
          ['Labour', est.labour_total], ['Materials', est.materials_total],
          ['Parts', est.parts_total], ['Subtotal', est.subtotal],
          ['VAT', est.vat], ['Total', est.total],
        ].map(([label, value], i, all) => h('div', { class: i === all.length - 1 ? 'is-total' : null }, [
          h('span', label), h('strong', money(value, est.currency)),
        ]))),
        est.notes ? h('div.tc-hint.mt-3', est.notes) : null,
        h('div.mt-3', actionHost),
      ]),
      footer: [h('button.btn.btn-outline-secondary.btn-sm', {
        type: 'button', 'data-bs-dismiss': 'modal',
      }, 'Close')],
    });

    function redraw() {
      T.mount(actionHost, [
        h('a.btn.btn-sm.btn-outline-secondary', {
          href: `/api/estimates/${est.id}/pdf`, target: '_blank', rel: 'noopener',
        }, T.icon('file-earmark-pdf'), ' Download PDF'),

        est.job_id
          ? h('a.btn.btn-sm.btn-outline-secondary', { href: `#/jobs/${est.job_id}` },
              T.icon('clipboard-check'), ` Job card ${est.job_no}`)
          : null,

        est.status === 'DECLINED' ? null : h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button', onclick: (e) => send(e.currentTarget, est.status === 'SENT'),
        }, T.icon('whatsapp'), est.status === 'SENT' ? ' Send again' : ' Send on WhatsApp'),

        est.status === 'APPROVED' ? null : h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button', onclick: (e) => decide(e.currentTarget, 'approve'),
        }, T.icon('hand-thumbs-up'), ' Customer approved'),

        est.status === 'DECLINED' ? null : h('button.btn.btn-sm.btn-outline-danger', {
          type: 'button', onclick: (e) => decide(e.currentTarget, 'decline'),
        }, T.icon('hand-thumbs-down'), ' Customer declined'),

        est.status === 'APPROVED' && !est.job_id
          ? h('button.btn.btn-sm.btn-brand', {
              type: 'button', onclick: (e) => bookIn(e.currentTarget, est),
            }, T.icon('clipboard-plus'), ' Book the car in')
          : null,
      ]);
    }

    async function send(btn, resend) {
      if (!resend && !(await T.confirmDialog({
        title: `Send quotation ${est.reference}?`,
        message: 'The customer gets the PDF on WhatsApp with Approve / Decline buttons.',
        confirmLabel: 'Send it', variant: 'brand', icon: 'whatsapp',
        subtitle: null,
      }))) return;
      btn.disabled = true;
      try {
        await api.post(`/api/estimates/${est.id}/send`, {});
        T.toast(`Quotation ${est.reference} sent on WhatsApp.`, 'success');
        m.close();
        onChanged && onChanged();
      } catch (err) {
        btn.disabled = false;
        T.toast(err.message || 'The quotation could not be sent.', 'danger');
      }
    }

    async function decide(btn, which) {
      const approve = which === 'approve';
      if (!(await T.confirmDialog({
        title: approve ? 'Mark this quotation as accepted?'
          : 'Mark this quotation as declined?',
        message: approve
          ? 'The customer has agreed to the price. Their car is booked in as a job card.'
          : 'The customer has turned the price down. No job card is opened.',
        confirmLabel: approve ? 'Approve' : 'Decline',
        variant: approve ? 'success' : 'danger',
        icon: approve ? 'hand-thumbs-up' : 'hand-thumbs-down',
        subtitle: null,
      }))) return;
      btn.disabled = true;
      try {
        const res = await api.post(`/api/estimates/${est.id}/${approve ? 'approve' : 'decline'}`,
          approve ? {} : { reason: 'Declined at the desk' });
        if (approve && res.job) {
          T.toast(`Quotation accepted — job card **${res.job.job_no}** opened.`, 'success', {
            title: 'Booked in',
            action: { label: 'Open job card', run: () => T.navigate(`/jobs/${res.job.id}`) },
          });
        } else if (approve) {
          T.toast('Quotation accepted. Capture the registration to open the job card.',
            'success');
        } else {
          T.toast(`Quotation ${est.reference} marked as declined.`, 'info');
        }
        m.close();
        onChanged && onChanged();
      } catch (err) {
        btn.disabled = false;
        T.toast(err.message || 'That could not be recorded.', 'danger');
      }
    }

    redraw();
    return m;
  }

  /* ── booking the car in ─────────────────────────────────────────────── */
  /**
   * The registration is the one thing a quotation cannot be raised with — the
   * bot takes most enquiries from a photo and a message — so it is asked for
   * here, once the customer has committed and a card is about to exist.
   */
  async function bookIn(trigger, est) {
    const [usersRes, panelsRes] = await Promise.all([
      api.get('/api/users'), api.get('/api/estimate-meta').catch(() => null),
    ]);
    void panelsRes;
    const technicians = usersRes.items.filter((u) => (
      ['technician', 'manager', 'owner'].includes(u.role)));

    const input = (name, extra) => h('input.form-control.form-control-sm',
      Object.assign({ name }, extra || {}));
    const fields = {
      reg_no: input('reg_no', { placeholder: 'ABC 1234', class: 'form-control form-control-sm text-uppercase' }),
      make: input('make', { placeholder: 'Toyota' }),
      model: input('model', { placeholder: 'Hilux' }),
      colour: input('colour', { placeholder: 'White' }),
      odometer_in: input('odometer_in', { type: 'number' }),
      damage_summary: input('damage_summary', { placeholder: 'What the customer described' }),
      bay: input('bay', { placeholder: 'Bay 1' }),
      promised_date: input('promised_date', { type: 'date' }),
      priority: h('select.form-select.form-select-sm',
        { name: 'priority' }, ['LOW', 'NORMAL', 'HIGH', 'URGENT']
          .map((p) => h('option', { selected: p === 'NORMAL' }, p))),
      fuel_level: h('select.form-select.form-select-sm',
        { name: 'fuel_level' }, ['Empty', '1/4', '1/2', '3/4', 'Full']
          .map((f) => h('option', { selected: f === '1/2' }, f))),
      technician_id: h('select.form-select.form-select-sm', { name: 'technician_id' },
        [h('option', { value: '' }, '— Unassigned —')].concat(
          technicians.map((t) => h('option', { value: t.id }, t.full_name)))),
    };

    const saveBtn = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'button' },
      [T.icon('clipboard-plus'), ' Open the job card']);
    const m = T.modal({
      title: 'Book the car in',
      subtitle: `Quotation ${est.reference} · ${est.customer_name || ''} — `
        + 'the quotation moves onto the job card, it is not re-priced.',
      icon: 'clipboard-plus',
      accent: 'brand',
      size: 'lg',
      body: h('div.row.g-3', [
        h('div.col-md-4', field('Registration *', fields.reg_no)),
        h('div.col-md-4', field('Make', fields.make)),
        h('div.col-md-4', field('Model', fields.model)),
        h('div.col-md-4', field('Colour', fields.colour)),
        h('div.col-md-4', field('Odometer in', fields.odometer_in)),
        h('div.col-md-4', field('Fuel level', fields.fuel_level)),
        h('div.col-md-4', field('Priority', fields.priority)),
        h('div.col-md-4', field('Promised date', fields.promised_date)),
        h('div.col-md-4', field('Bay', fields.bay)),
        h('div.col-md-6', field('Technician', fields.technician_id)),
        h('div.col-12', field('Damage summary', fields.damage_summary, {
          hint: 'What the customer described. Left blank, the enquiry notes are used.',
        })),
      ]),
      footer: [
        h('button.btn.btn-outline-secondary.btn-sm', {
          type: 'button', 'data-bs-dismiss': 'modal',
        }, 'Cancel'),
        saveBtn,
      ],
    });

    saveBtn.addEventListener('click', async () => {
      const body = {};
      Object.entries(fields).forEach(([key, el]) => { body[key] = el.value; });
      if (!body.reg_no) {
        fields.reg_no.classList.add('is-invalid');
        fields.reg_no.focus();
        T.toast('A registration number is required to open a job card.', 'warning');
        return;
      }
      saveBtn.disabled = true;
      T.mount(saveBtn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Booking in…']);
      try {
        const res = await api.post(`/api/estimates/${est.id}/book-in`, body);
        m.close();
        T.toast(`Job card **${res.job.job_no}** opened for ${res.job.reg_no || body.reg_no}.`,
          'success', {
            title: 'Vehicle booked in',
            action: { label: 'Open job card', run: () => T.navigate(`/jobs/${res.job.id}`) },
          });
        if (trigger) trigger.disabled = false;
      } catch (err) {
        saveBtn.disabled = false;
        T.mount(saveBtn, [T.icon('clipboard-plus'), ' Open the job card']);
        T.toast(err.message || 'The car could not be booked in.', 'danger');
      }
    });
    return m;
  }
  T.bookInQuotation = bookIn;

  /** Label + control, wrapped so inline validation and hints have somewhere to live. */
  function field(label, control, { hint } = {}) {
    const required = / \*$/.test(label);
    return h('div.tc-field', [
      h('label.tc-label', required ? label.replace(/ \*$/, '') : label,
        required ? h('span.req', { title: 'Required' }, '*') : null),
      control,
      hint ? h('div.tc-hint', hint) : null,
    ]);
  }
})();
