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
      insurance: ctx.query.insurance || '',
    };

    const tbodyHost = h('div');
    const countLabel = h('span.text-secondary.small');

    async function load() {
      T.mount(tbodyHost, T.skeletonTable(8, 6));
      const params = new URLSearchParams();
      if (state.q) params.set('q', state.q);
      if (state.status && state.status !== 'all') params.set('status', state.status);
      if (state.stage) params.set('stage', state.stage);
      if (state.insurance) params.set('insurance', state.insurance);
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
              r.is_insurance ? h('span.chip', T.icon('shield-check')) : null,
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
      select('insurance', [{ value: '', label: 'Insurance + cash' },
        { value: '1', label: 'Insurance only' }], 'shield-check'),
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
    const [panelsRes, customersRes, usersRes] = await Promise.all([
      api.get('/api/estimating/panels'), api.get('/api/customers'), api.get('/api/users'),
    ]);
    const panels = panelsRes.panels;
    const technicians = usersRes.items.filter((u) => ['technician', 'manager', 'owner'].includes(u.role));

    const selectedPanels = new Set();
    const preview = {
      lines: [], summary: null, split: null, loading: false,
    };
    const previewHost = h('div');
    const totalValue = h('span.tc-footer-total-value', '—');
    let form;

    /** A numbered section heading inside the wizard. */
    const sectionHead = (n, label) => h('div.tc-form-section', [
      h('span.idx', n), h('span', label),
    ]);

    async function refreshPreview() {
      const insurance = form.querySelector('[name=is_insurance]').checked;
      const parts = JSON.parse(form.dataset.parts || '[]');
      T.mount(previewHost, T.spinner('Pricing…'));
      try {
        const res = await api.post('/api/estimating/preview', {
          panels: [...selectedPanels], is_insurance: insurance,
          parts, excess: Number(form.querySelector('[name=excess]').value || 0),
        });
        preview.lines = res.lines; preview.summary = res.summary; preview.split = res.split;
        renderPreview();
      } catch (err) {
        T.mount(previewHost, h('div.alert.alert-danger.small', err.message));
      }
    }

    function renderPreview() {
      const s = preview.summary || {};
      const rows = preview.lines.length
        ? preview.lines.map((l) => h('tr', [
            h('td', l.description),
            h('td', h('span.chip', l.kind)),
            h('td.text-end', `${l.quantity} ${l.unit || ''}`),
            h('td.text-end', money(l.unit_price)),
            h('td.text-end', money(l.line_total)),
          ]))
        : [h('tr', h('td', { colspan: 5 },
            h('div.small.text-secondary', 'Nothing selected — pick panels above.')))];

      T.mount(previewHost, [
        h('div.table-responsive', h('table.table.table-sm.table-tc.mb-2',
          h('thead', h('tr', [h('th', 'Description'), h('th', 'Type'), h('th.text-end', 'Qty'),
            h('th.text-end', 'Rate'), h('th.text-end', 'Amount')])),
          h('tbody', rows))),
        preview.lines.length ? h('div.row.g-2.small', [
          summaryCell('Labour', s.labour_total), summaryCell('Materials', s.materials_total),
          summaryCell('Parts', s.parts_total), summaryCell('Subtotal', s.subtotal),
          summaryCell('VAT', s.vat), summaryCell('Total', s.total, true),
        ]) : null,
        preview.split ? h('div.d-flex.gap-2.mt-2.flex-wrap', [
          h('span.chip', T.icon('shield-check'), ` Insurer pays ${money(preview.split.insurer_pays)}`),
          h('span.chip', T.icon('person'), ` Customer pays ${money(preview.split.customer_pays)}`),
        ]) : null,
      ]);

      if (source.mode === 'build') {
        totalValue.textContent = preview.lines.length ? money(s.total) : '—';
        updateModeHint();
      }
    }
    function summaryCell(label, value, strong) {
      return h('div.col-6.col-md-2',
        h('div.text-secondary', label),
        h('div', { class: strong ? 'fw-bold' : 'fw-semibold' }, money(value)));
    }

    const panelChips = h('div.d-flex.flex-wrap.gap-2',
      panels.map((p) => {
        const chip = h('button.btn.btn-sm.btn-outline-secondary', { type: 'button' }, p);
        chip.addEventListener('click', () => {
          if (selectedPanels.has(p)) { selectedPanels.delete(p); chip.className = 'btn btn-sm btn-outline-secondary'; }
          else { selectedPanels.add(p); chip.className = 'btn btn-sm btn-brand'; }
          refreshPreview();
        });
        return chip;
      }));

    /* ── estimate source: attach a quotation, or build one here ───────── */
    const source = { mode: 'attach', pinned: false, estimate: null, file: null, items: [] };
    const quoteSearch = T.searchInput({
      placeholder: 'Search quotation, job card, customer or reg…', width: 300,
      oninput: T.debounce((e) => loadQuotations(e.target.value), 350),
    });
    const quoteListHost = h('div.tc-quote-list');
    const quotePreview = h('div');
    const docName = h('span.tc-hint');
    const docInput = h('input.form-control.form-control-sm', {
      type: 'file', accept: '.pdf,.png,.jpg,.jpeg,.webp', 'aria-label': 'Quotation document',
    });
    docInput.addEventListener('change', () => {
      source.file = docInput.files[0] || null;
      docName.textContent = source.file
        ? source.file.name
        : 'No file chosen — the line items above are still recorded.';
    });

    const modeButtons = [
      { id: 'attach', label: 'Attach a quotation', icon: 'file-earmark-text' },
      { id: 'build', label: 'Build it here', icon: 'rulers' },
    ].map((m) => {
      const btn = h('button', {
        type: 'button', value: m.id,
        onclick: () => { source.pinned = true; setMode(m.id); },
      }, [T.icon(m.icon), ` ${m.label}`]);
      return btn;
    });
    const modeSwitch = h('div.d-flex.align-items-center.gap-2.flex-wrap', [
      h('div.tc-segmented', { role: 'group', 'aria-label': 'How to price this job card' }, modeButtons),
      h('div.tc-hint.tc-estimate-hint'),
    ]);

    const attachPane = h('div.tc-attach-pane', [
      h('div.d-flex.align-items-center.gap-2.mb-2.flex-wrap', [
        quoteSearch,
        h('button.btn.btn-sm.btn-outline-secondary', {
          type: 'button', onclick: () => loadQuotations(''),
        }, T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      h('div.tc-hint.mb-2', 'Pick the quotation the customer already has — its lines are copied onto this job card, so nothing gets re-typed.'),
      quoteListHost,
      quotePreview,
      h('div.tc-field.mt-3', [
        h('label.tc-label', { for: 'quotationDoc' }, 'Quotation document',
          h('span.tc-hint.ms-1', '(optional)')),
        docInput,
        h('div', docName),
      ]),
    ]);
    docInput.id = 'quotationDoc';

    const buildPane = h('div.tc-build-pane', { hidden: true }, [
      h('div.tc-hint.mb-2', 'Tap the damaged panels to build the estimate — the total updates as you go.'),
      panelChips,
      h('div.card.bg-body-tertiary.border-0.mt-3', h('div.card-body', previewHost)),
    ]);

    function updateModeHint() {
      const hint = modeSwitch.querySelector('.tc-estimate-hint');
      if (source.mode === 'attach') {
        hint.textContent = source.estimate
          ? `Quotation ${source.estimate.reference} will be copied.`
          : 'Nothing is re-typed: the quotation supplies every line and amount.';
        return;
      }
      const n = selectedPanels.size;
      if (n) hint.textContent = `${n} panel${n === 1 ? '' : 's'} selected — the total updates as you go.`;
      else if (!source.items.length) hint.textContent = 'No quotation on file — build the lines manually.';
      else hint.textContent = 'Tap the damaged panels to build the estimate.';
    }

    function setMode(mode) {
      source.mode = mode;
      modeButtons.forEach((b) => b.classList.toggle('is-active', b.value === mode));
      attachPane.hidden = mode !== 'attach';
      buildPane.hidden = mode !== 'build';

      updateModeHint();
      if (mode === 'attach') renderQuotePreview();
      else refreshPreview();
    }

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
        T.mount(quoteListHost, h('div.tc-hint', 'Quotations could not be loaded — you can still build the estimate here.'));
      }
    }

    function renderQuotations() {
      if (!source.items.length) {
        T.mount(quoteListHost, T.emptyState(
          'No quotations found',
          'Nothing on file for this vehicle or customer. Build the estimate instead.',
          'file-earmark-text'));
        if (!source.pinned && !source.estimate) setMode('build');
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
            q.is_insurance ? h('span.badge.text-bg-dark', 'Insurance') : null,
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

      // The quotation decides whether this is an insurance job and what the
      // excess is — otherwise the copied total would not match the paperwork.
      const insBox = form.querySelector('[name=is_insurance]');
      const excessInput = form.querySelector('[name=excess]');
      if (insBox) insBox.checked = !!source.estimate.is_insurance;
      if (excessInput) excessInput.value = Number(source.estimate.excess || 0).toFixed(2);

      renderQuotations();
      renderQuotePreview();
      setMode('attach');
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
        if (source.mode === 'attach') {
          totalValue.textContent = '—';
          updateModeHint();
        }
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
          `${est.items.length} line${est.items.length === 1 ? '' : 's'} will be copied onto this job card`
          + (est.is_insurance
            ? ` as an insurance repair, excess ${money(est.excess, est.currency)}.` : '.')),
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
      if (source.mode === 'attach') totalValue.textContent = money(est.total, est.currency);
      updateModeHint();
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

    form = h('form.row.g-3', { onsubmit: (e) => e.preventDefault() }, [
      /* customer */
      h('div.col-12', sectionHead(1, 'Customer & vehicle')),
      h('div.col-md-6', field('Customer', h('select.form-select.form-select-sm', {
        name: 'customer_id',
        onchange: () => loadQuotations(''),
      }, [h('option', { value: '' }, '— New customer —')].concat(
        customersRes.items.map((c) => h('option', { value: c.id },
          `${c.name}${c.phone ? ' · ' + c.phone : ''}`)))),
        { hint: 'Existing quotations for this customer are offered below.' })),
      h('div.col-md-3', field('New customer name', h('input.form-control.form-control-sm', { name: 'customer_name', placeholder: 'Only if new' }))),
      h('div.col-md-3', field('Phone / WhatsApp', h('input.form-control.form-control-sm', { name: 'customer_phone', placeholder: '+263 77 000 0000' }))),
      h('div.col-md-3', field('Registration *', h('input.form-control.form-control-sm.text-uppercase', {
        name: 'reg_no', required: true, placeholder: 'ABC 1234',
        oninput: T.debounce(() => loadQuotations(''), 600),
      }))),
      h('div.col-md-3', field('Make', makeCombo.node, { hint: 'Pick a make to narrow the model list.' })),
      h('div.col-md-3', field('Model', modelCombo.node)),
      h('div.col-md-3', field('Colour', colourCombo.node)),

      /* job */
      h('div.col-12', sectionHead(2, 'Job details')),
      h('div.col-md-4', field('Service', h('select.form-select.form-select-sm', { name: 'service' },
        (meta.service_names || []).map((s) => h('option', { selected: s === 'Panel Beating & Spray Painting' }, s))))),
      h('div.col-md-2', field('Priority', h('select.form-select.form-select-sm', { name: 'priority' },
        ['LOW', 'NORMAL', 'HIGH', 'URGENT'].map((p) => h('option', { selected: p === 'NORMAL' }, p))))),
      h('div.col-md-3', field('Promised date', h('input.form-control.form-control-sm', { name: 'promised_date', type: 'date', value: T.today() }))),
      h('div.col-md-3', field('Bay', h('input.form-control.form-control-sm', { name: 'bay', placeholder: 'Bay 1' }))),
      h('div.col-md-6', field('Technician', h('select.form-select.form-select-sm', { name: 'technician_id' },
        [h('option', { value: '' }, '— Unassigned —')].concat(
          technicians.map((t) => h('option', { value: t.id }, t.full_name)))))),
      h('div.col-md-3', field('Fuel level', h('select.form-select.form-select-sm', { name: 'fuel_level' },
        ['Empty', '1/4', '1/2', '3/4', 'Full'].map((f) => h('option', { selected: f === '1/2' }, f))))),
      h('div.col-md-3', field('Odometer in', h('input.form-control.form-control-sm', { name: 'odometer_in', type: 'number' }))),
      h('div.col-12', field('Damage summary', h('input.form-control.form-control-sm', { name: 'damage_summary', placeholder: 'Front bumper, bonnet, both headlamp surrounds' }))),
      h('div.col-12', field('Notes / description', h('textarea.form-control.form-control-sm', { name: 'description', rows: 2 }))),
      h('div.col-md-6', field('Valuables in vehicle', h('input.form-control.form-control-sm', { name: 'valuables', placeholder: 'Spare wheel, jack, baby seat' }))),
      h('div.col-md-6.d-flex.align-items-end.gap-4.pb-1', [
        h('div.form-check', h('input.form-check-input', { type: 'checkbox', name: 'is_insurance', id: 'isIns', onchange: refreshPreview }),
          h('label.form-check-label.small', { for: 'isIns' }, 'Insurance claim')),
        h('div.form-check', h('input.form-check-input', { type: 'checkbox', name: 'keys_received', id: 'keysRec', checked: true }),
          h('label.form-check-label.small', { for: 'keysRec' }, 'Keys received')),
      ]),
      h('div.col-md-3', field('Excess (USD)', h('input.form-control.form-control-sm', {
        name: 'excess', type: 'number', step: '0.01', value: '0', oninput: T.debounce(refreshPreview, 500),
      }))),

      /* estimate source — attach a quotation, or build one from scratch */
      h('div.col-12', sectionHead(3, 'Estimate')),
      h('div.col-12', [
        modeSwitch,
        h('div.mt-3', attachPane),
        buildPane,
      ]),
    ]);

    refreshPreview();
    setMode('attach');
    loadQuotations('');

    return new Promise((resolve) => {
      let settled = false;
      const createBtn = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'button' },
        [T.icon('check-lg'), ' Create job card']);

      const m = T.modal({
        title: 'New job card',
        subtitle: 'Book the vehicle in and build the estimate in one pass.',
        icon: 'clipboard-plus',
        accent: 'brand',
        size: 'xl',
        body: h('div', form),
        footer: [
          h('div.tc-footer-total', [h('span.tc-footer-total-label', 'Estimate'), totalValue]),
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
        data.is_insurance = form.querySelector('[name=is_insurance]').checked;
        data.keys_received = form.querySelector('[name=keys_received]').checked;
        data.parts = JSON.parse(form.dataset.parts || '[]');
        data.excess = Number(data.excess || 0);

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

        // Either copy an attached quotation, or send the panels we picked.
        if (source.mode === 'attach') {
          if (!source.estimate) {
            T.toast('Choose a quotation to attach, or switch to "Build it here".', 'warning');
            setMode('attach');
            return;
          }
          data.source_estimate_id = source.estimate.id;
        } else {
          if (!selectedPanels.size) {
            T.toast('Tap at least one damaged panel, or attach a quotation.', 'warning');
            return;
          }
          data.panels = [...selectedPanels];
        }

        createBtn.disabled = true;
        T.mount(createBtn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Saving…']);
        try {
          const res = await api.post('/api/jobs', data);

          // The paperwork goes on after the job exists, so it always has an owner.
          if (source.file) {
            T.mount(createBtn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Attaching…']);
            try {
              const payload = new FormData();
              payload.append('file', source.file);
              payload.append('kind', 'QUOTATION');
              payload.append('caption', source.estimate
                ? `Quotation ${source.estimate.reference}` : 'Quotation attached at intake');
              await fetch(`/api/jobs/${res.job.id}/documents`, {
                method: 'POST',
                headers: { 'X-CSRFToken': window.__CSRF__ || '' },
                credentials: 'same-origin',
                body: payload,
              });
            } catch (uploadErr) {
              T.toast('The job card was created, but the document did not attach.',
                'warning');
            }
          }

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
