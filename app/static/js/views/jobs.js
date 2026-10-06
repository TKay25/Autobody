/* Job card list, filters and the intake wizard. */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  /* ── list ─────────────────────────────────────────────────────────── */
  T.route('/jobs', async (ctx) => {
    ctx.title = 'Job cards';
    const meta = T.store.get('meta');
    const state = {
      q: ctx.query.q || '',
      status: ctx.query.status || 'open',
      stage: ctx.query.stage || '',
    };

    const tbodyHost = h('div');
    const countLabel = h('span.text-secondary.small');

    async function load() {
      T.mount(tbodyHost, T.skeletonTable(8, 6));
      const params = new URLSearchParams();
      if (state.q) params.set('q', state.q);
      if (state.status && state.status !== 'all') params.set('status', state.status);
      if (state.stage) params.set('stage', state.stage);
      /* Put the filters in the address bar as they change. Without this the URL
         stays `#/jobs` however you have narrowed the list, so opening a job card
         and coming back loses the search you just did. */
      T.listState('jobs', params);
      const data = await api.get(`/api/jobs?${params.toString()}`);
      countLabel.textContent = `${data.count} job card${data.count === 1 ? '' : 's'}`;

      T.mount(tbodyHost, T.dataTable({
        columns: [
          { label: 'Job card', render: (r) => h('div', h('div.fw-semibold', r.job_no),
              h('div.small.text-secondary', dateShort(r.checked_in_at))) },
          { label: 'Vehicle', render: (r) => h('div', [
              h('div.fw-semibold', r.reg_no),
              h('div.small.text-secondary', r.vehicle_title)]) },
          { label: 'Customer', render: (r) => h('div', [
              h('div', r.customer_name),
              h('div.small.text-secondary', r.customer_phone || '')]) },
          { label: 'Service', class: 'd-none d-lg-table-cell', render: (r) => h('span.small', r.service) },
          { label: 'Stage', render: (r) => T.stageBadge(r.stage, r.stage_label) },
          { label: 'Progress', class: 'd-none d-md-table-cell', render: (r) => h('div.d-flex.align-items-center.gap-2',
              h('div.progress.flex-fill', { style: 'height:6px;min-width:60px' },
                h('div.progress-bar.bg-warning', { style: `width:${r.progress}%` })),
              h('span.small.text-secondary', `${r.progress}%`)) },
          { label: 'Promised', render: (r) => h('span', { class: r.is_overdue ? 'text-danger fw-semibold' : '' },
              dateShort(r.promised_date)) },
          { label: '', class: 'text-end', render: (r) => h('div.d-flex.gap-1.justify-content-end',
              T.priorityBadge(r.priority)) },
        ],
        rows: data.items,
        onRowClick: (r) => T.navigate(`/jobs/${r.id}`),
        empty: T.emptyState('No job cards match', 'Try a different filter, or open a new job card.', 'clipboard-x'),
      }));
    }

    const search = T.searchInput({
      placeholder: 'Search job card, registration or customer…', value: state.q, width: 320,
      oninput: T.debounce((e) => { state.q = e.target.value; load(); }, 350),
    });

    /** A filter select with its own icon well. */
    const select = (name, options, icon) => T.iconSelect({
      value: state[name], icon, width: 158, ariaLabel: name,
      onChange: (v) => { state[name] = v; load(); },
      options,
    });

    const filters = h('div.tc-toolbar.mb-3', [
      select('status', [
        { value: 'open', label: 'Open job cards' }, { value: 'ready', label: 'Ready for collection' },
        { value: 'overdue', label: 'Overdue' }, { value: 'closed', label: 'Collected' },
        { value: 'all', label: 'All job cards' },
      ], 'funnel'),
      select('stage', [{ value: '', label: 'All stages' }]
        .concat((meta.stages || []).map((s) => ({ value: s.code, label: s.label }))), 'diagram-3'),
      h('div.tc-toolbar-spacer'),
      search,
    ]);

    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'Job cards'), countLabel]),
        h('button.btn.btn-brand.btn-sm', { onclick: () => T.newJobCard({ onCreated: () => load() }) },
          T.icon('plus-lg'), ' New job card'),
      ]),
      /* Anything queued against a job card, including ones opened offline —
         which otherwise look like they were never created at all. */
      T.pendingStrip({ match: (url) => /\/jobs(\/|$)/.test(url), label: 'job card change' }),
      filters,
      T.section({ body: tbodyHost, flush: true }),
    ]);
  });

  /* ── intake wizard — opens in a popup modal ───────────────────────── */
  /**
   * Book a vehicle in and build its estimate without leaving the page.
   * Shared by the topbar, the command palette, the dashboard and the list.
   *
   * @param {object}   [opts]
   * @param {Function} [opts.onCreated]  called with the new job instead of
   *                                     navigating straight to its job card
   * @returns {Promise<object|null>} the created job, or null if cancelled
   */
  async function newJobCard({ onCreated } = {}) {
    const meta = T.store.get('meta');
    const [customersRes, usersRes] = await Promise.all([
      api.get('/api/customers'), api.get('/api/users'),
    ]);
    const technicians = usersRes.items.filter((u) => ['technician', 'manager', 'owner'].includes(u.role));

    /* Intake books the vehicle in; it does not price the job. Panels are
       measured on the job card afterwards, at /estimates/new/<id>, because
       that is where the estimate actually lives. */
    const totalValue = h('span.tc-footer-total-value', '—');
    let form;

    /** A numbered section heading inside the wizard. */
    const sectionHead = (n, label) => h('div.tc-form-section', [
      h('span.idx', n), h('span', label),
    ]);

    /* ── the quotation the customer already has, if any ───────────────── */
    const source = { estimate: null, items: [] };
    const quoteSearch = T.searchInput({
      placeholder: 'Search quotation, job card, customer or reg…', width: 300,
      oninput: T.debounce((e) => loadQuotations(e.target.value), 350),
    });
    const quoteListHost = h('div.tc-quote-list');
    const quotePreview = h('div');

    const attachPane = h('div.tc-attach-pane', [
      h('div.d-flex.align-items-center.gap-2.mb-2.flex-wrap', [
        quoteSearch,
        h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button', onclick: () => loadQuotations(''),
        }, T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      h('div.tc-hint.mb-2', 'The same car coming back, or the fleet sending the same job again: pick the quotation we already wrote and its lines are copied onto this card. Otherwise leave it blank and price the job on the card afterwards.'),
      quoteListHost,
      quotePreview,
    ]);

    async function loadQuotations(q) {
      const params = new URLSearchParams();
      if (q) params.set('q', q);
      const custId = form && form.querySelector('[name=customer_id]').value;
      const reg = form && form.querySelector('[name=reg_no]').value;
      if (custId) params.set('customer_id', custId);
      if (reg) params.set('reg_no', reg);

      T.mount(quoteListHost, T.spinner('Looking for quotations…'));
      try {
        const res = await api.get(`/api/quotations?${params}`, { silent: true });
        source.items = res.items;
        renderQuotations();
      } catch (err) {
        source.items = [];
        T.mount(quoteListHost, h('div.tc-hint', 'Quotations could not be loaded.'));
      }
    }

    function renderQuotations() {
      if (!source.items.length) {
        T.mount(quoteListHost, T.emptyState(
          'No quotations found',
          'Nothing on file for this vehicle or customer — you can still create the job card.',
          'file-earmark-text'));
        return;
      }
      T.mount(quoteListHost, source.items.map((q) => h('button.tc-quote', {
        type: 'button',
        class: source.estimate && source.estimate.id === q.id ? 'is-selected' : null,
        onclick: () => pickQuotation(q.id),
      }, [
        h('div.tc-quote-main', [
          h('div.d-flex.align-items-center.gap-2.flex-wrap', [
            h('span.fw-semibold', q.reference),
            T.badge(q.status, q.status === 'APPROVED' ? 'success'
              : q.status === 'DECLINED' ? 'danger' : 'secondary'),
          ]),
          h('div.tc-quote-meta',
            `${q.reg_no || '—'} · ${q.customer_name || '—'} · job card ${q.job_no || '—'} · ${q.item_count} lines`),
        ]),
        h('div.text-end', [
          h('div.fw-bold', money(q.total, q.currency)),
          h('div.tc-quote-meta', dateShort(q.created_at)),
        ]),
      ])));
    }

    async function pickQuotation(id) {
      T.mount(quotePreview, T.spinner('Loading the quotation…'));
      try {
        const res = await api.get(`/api/estimates/${id}`);
        source.estimate = res.estimate;
      } catch (err) {
        T.toast('That quotation could not be loaded.', 'danger');
        return;
      }

      renderQuotations();
      renderQuotePreview();
    }

    function clearQuotation() {
      source.estimate = null;
      renderQuotations();
      renderQuotePreview();
    }

    function renderQuotePreview() {
      const est = source.estimate;
      if (!est) {
        T.mount(quotePreview, null);
        totalValue.textContent = '—';
        return;
      }
      T.mount(quotePreview, h('div.tc-quote-picked', [
        h('div.d-flex.align-items-center.gap-2.mb-2', [
          T.icon('check2-circle'),
          h('span.fw-semibold', `Quotation ${est.reference} attached`),
          h('button.btn.btn-sm.btn-outline-secondary.ms-auto', {
            type: 'button', onclick: clearQuotation,
          }, 'Remove'),
        ]),
        h('div.tc-hint.mb-2',
          `${est.items.length} line${est.items.length === 1 ? '' : 's'} will be copied onto this job card.`),
        h('div.table-responsive', h('table.table.table-sm.table-tc.mb-2',
          h('thead', h('tr', [h('th', 'Description'), h('th', 'Type'),
            h('th.text-end', 'Qty'), h('th.text-end', 'Rate'), h('th.text-end', 'Amount')])),
          h('tbody', est.items.map((l) => h('tr', [
            h('td', l.description),
            h('td', h('span.chip', l.kind)),
            h('td.text-end', `${l.quantity} ${l.unit || ''}`),
            h('td.text-end', money(l.unit_price, est.currency)),
            h('td.text-end', money(l.line_total, est.currency)),
          ]))))),
        h('div.tc-quote-totals', [
          ['Labour', est.labour_total], ['Materials', est.materials_total],
          ['Parts', est.parts_total], ['Subtotal', est.subtotal],
          ['VAT', est.vat], ['Total', est.total],
        ].map(([label, value], i, all) => h('div', { class: i === all.length - 1 ? 'is-total' : null }, [
          h('span', label), h('strong', money(value, est.currency)),
        ]))),
      ]));
      totalValue.textContent = money(est.total, est.currency);
    }
    const veh = T.vehicleOptions();
    /* Make drives Model, so picking "Toyota" narrows the next list. */
    const makeCombo = T.comboField({
      name: 'make', options: veh.makes, placeholder: 'Type the make',
      otherLabel: 'Other make (type it)',
      onChange: (v) => modelCombo.setOptions(veh.modelsFor(v)),
    });
    const modelCombo = T.comboField({
      name: 'model', options: veh.modelsFor(''), placeholder: 'Type the model',
      otherLabel: 'Other model (type it)',
    });
    const colourCombo = T.comboField({
      name: 'colour', options: veh.colours, placeholder: 'Type the colour',
      otherLabel: 'Other colour (type it)', swatch: true,
    });

    /* Customer type is only a question worth asking for someone who is not on file
       yet. Once an existing customer is picked we show what was recorded when they
       were first assisted, instead of making the operator answer something the
       database already knows. The API ignores is_fleet for an existing customer. */
    const fleetCheck = h('input.form-check-input', {
      type: 'checkbox', name: 'is_fleet', id: 'isFleet',
    });
    const fleetChoice = h('div.form-check.pt-1', [
      fleetCheck,
      h('label.form-check-label.small', { for: 'isFleet' }, 'Fleet / corporate account'),
    ]);
    const customerTypeHost = h('div', fleetChoice);

    function syncCustomerType() {
      const select = form.querySelector('[name=customer_id]');
      const picked = select && select.value
        ? customersRes.items.find((c) => String(c.id) === String(select.value))
        : null;
      if (picked) {
        fleetCheck.checked = !!picked.is_fleet;
        /* Show the number we would ring. The dropdown already printed it beside
           the name, so leaving the box blank beside it read as though the number
           had been lost — and it was the one place the desk could not record a
           missing one either. */
        phoneField.set(picked.phone || picked.whatsapp || '');
        T.mount(customerTypeHost, h('div.form-control.form-control-sm.d-flex.align-items-center.gap-2', [
          T.icon(picked.is_fleet ? 'building' : 'person'),
          h('span.text-truncate', picked.is_fleet ? 'Fleet / corporate' : 'Retail customer'),
        ]));
      } else {
        /* Whatever was there belonged to the customer just unpicked, so it goes
           with them rather than becoming the new customer's number. */
        phoneField.set('');
        T.mount(customerTypeHost, fleetChoice);
      }
      /* "New customer name" is only a question for someone who is not on file.
         Asking for a name beside a customer that was just picked invites the
         operator to type a second, conflicting one, so the field goes away and
         any value already typed is dropped rather than submitted. The ID number
         belongs to the same question and goes with it. */
      newNameField.classList.toggle('d-none', !!picked);
      newIdField.classList.toggle('d-none', !!picked);
      if (picked) {
        if (newNameInput.value) newNameInput.value = '';
        if (newIdInput.value) newIdInput.value = '';
      }
    }

    const newNameInput = h('input.form-control.form-control-sm', {
      name: 'customer_name', placeholder: 'Only if new',
    });
    const newNameField = h('div.col-md-3', field('New customer name', newNameInput));

    /* Taken at intake when the desk can, and at collection when it cannot: the
       vehicle is only handed over against the ID the person collecting it
       presents, so it has to be on the record before then. */
    const newIdInput = h('input.form-control.form-control-sm', {
      name: 'customer_id_number', placeholder: '63-1234567 A 00',
    });
    const newIdField = h('div.col-md-3', field('ID number', newIdInput, {
      hint: 'National ID or passport. Checked when the car is collected.',
    }));

    /* The hand-built form is read with T.formData, which would collect the
       national part and the dial code as two unrelated fields. So the phone
       control carries no `name` and is read explicitly instead — one value,
       always international, which is what the API and the bot both need. */
    const phoneField = T.telField({
      id: 'f_customer_phone', placeholder: '77 000 0000',
    });

    form = h('form.row.g-3', { onsubmit: (e) => e.preventDefault() }, [
      /* customer */
      h('div.col-12', sectionHead(1, 'Customer & vehicle')),
      h('div.col-md-6', field('Customer', T.searchableSelect(h('select.form-select.form-select-sm', {
        name: 'customer_id',
        onchange: () => { loadQuotations(''); syncCustomerType(); },
      }, [h('option', { value: '' }, '— New customer —')].concat(
        customersRes.items.map((c) => h('option', { value: c.id },
          `${c.name}${c.phone ? ' · ' + c.phone : ''}`)))), {
        placeholder: 'Search name or phone…', ariaLabel: 'Search customers',
      }).node,
        { hint: 'Existing quotations for this customer are offered below.' })),
      h('div.col-md-3', field('Customer type', customerTypeHost)),
      newNameField,
      newIdField,
      h('div.col-md-3', field('Phone / WhatsApp', phoneField.node,
        { hint: 'Used for every update the bot sends.' })),
      h('div.col-md-3', field('Registration *', h('input.form-control.form-control-sm.text-uppercase', {
        name: 'reg_no', required: true, placeholder: 'ABC 1234',
        oninput: T.debounce(() => loadQuotations(''), 600),
      }))),
      h('div.col-md-3', field('Make', makeCombo.node, { hint: 'Pick a make to narrow the model list.' })),
      h('div.col-md-3', field('Model', modelCombo.node)),
      h('div.col-md-3', field('Colour', colourCombo.node)),

      /* job */
      h('div.col-12', sectionHead(2, 'Job details')),
      h('div.col-md-4', field('Service', T.searchableSelect(h('select.form-select.form-select-sm', { name: 'service' },
        (meta.service_names || []).map((s) => h('option', { selected: s === 'Panel Beating & Spray Painting' }, s))),
        { placeholder: 'Search services…', ariaLabel: 'Search services' }).node)),
      h('div.col-md-2', field('Priority', T.searchableSelect(h('select.form-select.form-select-sm', { name: 'priority' },
        ['LOW', 'NORMAL', 'HIGH', 'URGENT'].map((p) => h('option', { selected: p === 'NORMAL' }, p))),
        { placeholder: 'Search…', ariaLabel: 'Search priority' }).node)),
      h('div.col-md-3', field('Promised date', h('input.form-control.form-control-sm', { name: 'promised_date', type: 'date', value: T.today() }))),
      h('div.col-md-3', field('Bay', h('input.form-control.form-control-sm', { name: 'bay', placeholder: 'Bay 1' }))),
      h('div.col-md-6', field('Technician', T.searchableSelect(h('select.form-select.form-select-sm', { name: 'technician_id' },
        [h('option', { value: '' }, '— Unassigned —')].concat(
          technicians.map((t) => h('option', { value: t.id }, t.full_name)))),
        { placeholder: 'Search technicians…', ariaLabel: 'Search technicians' }).node)),
      h('div.col-md-3', field('Fuel level', T.searchableSelect(h('select.form-select.form-select-sm', { name: 'fuel_level' },
        ['Empty', '1/4', '1/2', '3/4', 'Full'].map((f) => h('option', { selected: f === '1/2' }, f))),
        { placeholder: 'Search…', ariaLabel: 'Search fuel level' }).node)),
      h('div.col-md-3', field('Odometer in', h('input.form-control.form-control-sm', { name: 'odometer_in', type: 'number' }))),
      h('div.col-12', field('Damage summary', h('input.form-control.form-control-sm', { name: 'damage_summary', placeholder: 'Front bumper, bonnet, both headlamp surrounds' }))),
      h('div.col-12', field('Notes / description', h('textarea.form-control.form-control-sm', { name: 'description', rows: 2 }))),
      h('div.col-md-6', field('Valuables in vehicle', h('input.form-control.form-control-sm', { name: 'valuables', placeholder: 'Spare wheel, jack, baby seat' }))),
      h('div.col-md-6.d-flex.align-items-end.gap-4.pb-1', [
        h('div.form-check', h('input.form-check-input', { type: 'checkbox', name: 'keys_received', id: 'keysRec', checked: true }),
          h('label.form-check-label.small', { for: 'keysRec' }, 'Keys received')),
      ]),

      /* the quotation the customer already has, if any */
      h('div.col-12', sectionHead(3, 'Existing quotation')),
      h('div.col-12', { id: 'jd-estimate' }, attachPane),
    ]);

    loadQuotations('');
    syncCustomerType();

    return new Promise((resolve) => {
      let settled = false;
      const createBtn = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'button' },
        [T.icon('check-lg'), ' Create job card']);

      const m = T.modal({
        title: 'New job card',
        subtitle: 'Book the vehicle in; the panels get priced on the card once it exists.',
        icon: 'clipboard-plus',
        accent: 'brand',
        size: 'xl',
        body: h('div', form),
        footer: [
          h('div.tc-footer-total', [h('span.tc-footer-total-label', 'Quotation'), totalValue]),
          h('button.btn.btn-outline-secondary.btn-sm', {
            type: 'button', 'data-bs-dismiss': 'modal',
          }, 'Cancel'),
          createBtn,
        ],
      });

      /* Clear the invalid ring the moment the operator fixes a field. */
      form.addEventListener('input', (e) => e.target.classList?.remove('is-invalid'));

      createBtn.addEventListener('click', async () => {
        const data = T.formData(form);
        data.customer_phone = phoneField.read();
        data.keys_received = form.querySelector('[name=keys_received]').checked;
        data.parts = JSON.parse(form.dataset.parts || '[]');

        const missing = [];
        if (!data.reg_no) {
          missing.push([form.querySelector('[name=reg_no]'), 'A registration number is required.']);
        }
        if (!data.customer_id && !data.customer_name) {
          missing.push([form.querySelector('[name=customer_name]'),
            'Choose an existing customer or type a new name.']);
        }
        if (missing.length) {
          missing.forEach(([el]) => el.classList.add('is-invalid'));
          missing[0][0].focus();
          T.toast(missing[0][1], 'warning');
          return;
        }

        // A quotation the customer already has is copied across. Opening a job
        // card with no estimate is fine — the panels get measured on the job
        // card afterwards.
        if (source.estimate) data.source_estimate_id = source.estimate.id;

        createBtn.disabled = true;
        T.mount(createBtn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Saving…']);
        try {
          const res = await api.post('/api/jobs', data);

          settled = true;
          m.close();
          T.toast(
            source.estimate
              ? `Job card **${res.job.job_no}** created from quotation ${source.estimate.reference}.`
              : `Job card **${res.job.job_no}** created.`,
            'success',
            {
              title: 'Vehicle booked in',
              action: { label: 'Open job card', run: () => T.navigate(`/jobs/${res.job.id}`) },
            });
          if (onCreated) onCreated(res.job);
          else T.navigate(`/jobs/${res.job.id}`);
          resolve(res.job);
        } catch (err) {
          createBtn.disabled = false;
          T.mount(createBtn, [T.icon('check-lg'), ' Create job card']);
          T.toast(err.message || 'The job card could not be created.', 'danger');
        }
      });

      m.el.addEventListener('hidden.bs.modal', () => { if (!settled) resolve(null); }, { once: true });
    });
  }
  T.newJobCard = newJobCard;

  /*
   * The intake used to be a full-page route. Deep links (#/jobs/new) still
   * work: they open the popup and land on the new job card once it is saved.
   */
  T.route('/jobs/new', async (ctx) => {
    ctx.title = 'New job card';
    setTimeout(() => newJobCard({ onCreated: (job) => T.navigate(`/jobs/${job.id}`) }), 60);
    return h('div.d-flex.justify-content-center', { style: 'padding:4rem 0' }, T.spinner('Opening intake…'));
  });

  /**
   * Label + control, wrapped in `.tc-field` so inline validation and hints
   * have somewhere to live. A trailing " *" marks the field as required.
   */
  function field(label, control, { hint } = {}) {
    const required = / \*$/.test(label);
    return [
      h('div.tc-field', [
        h('label.tc-label', required ? label.replace(/ \*$/, '') : label,
          required ? h('span.req', { title: 'Required' }, '*') : null),
        control,
        hint ? h('div.tc-hint', hint) : null,
      ]),
    ];
  }
})();
