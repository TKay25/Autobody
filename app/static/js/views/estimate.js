/* Build an estimate on a job card that already exists.
 *
 * A quotation can only ever live inside a job card — Estimate.job_id is NOT
 * NULL — so pricing belongs here, on a real job, rather than inside the intake
 * dialog. Intake books the vehicle in and attaches any quotation the customer
 * already has; this screen does the measuring and the money.
 *
 * Reached from the job card's "Build estimate" button and the command palette.
 */
(function () {
  const T = window.TCA;
  const { h, api, money } = T;

  T.route('/estimates/new/:id', async (ctx) => {
    const jobId = ctx.params.id;

    const [jobRes, panelsRes] = await Promise.all([
      api.get(`/api/jobs/${jobId}`),
      api.get('/api/estimating/panels'),
    ]);
    const job = jobRes.job;
    ctx.title = `Estimate for ${job.job_no}`;
    const panels = panelsRes.panels;

    const selected = new Set();
    const preview = { lines: [], summary: null };
    const previewHost = h('div');
    const totalValue = h('span.tc-footer-total-value', '—');

    const paintBox = h('input.form-check-input', {
      type: 'checkbox', checked: true, id: 'estPaint',
    });
    const consumablesBox = h('input.form-check-input', {
      type: 'checkbox', checked: true, id: 'estConsumables',
    });
    const sendBox = h('input.form-check-input', {
      type: 'checkbox', checked: true, id: 'estSend',
    });
    const notesInput = h('textarea.form-control.form-control-sm', { rows: 2 });

    async function refreshPreview() {
      T.mount(previewHost, T.spinner('Pricing…'));
      try {
        const res = await api.post('/api/estimating/preview', {
          panels: [...selected],
          include_paint: paintBox.checked,
          include_consumables: consumablesBox.checked,
        });
        preview.lines = res.lines;
        preview.summary = res.summary;
        renderPreview();
      } catch (err) {
        T.mount(previewHost, h('div.alert.alert-danger.small', err.message));
      }
    }

    const summaryCell = (label, value, strong) => h('div.col-6.col-md-2', [
      h('div.text-secondary', label),
      h('div', { class: strong ? 'fw-bold' : 'fw-semibold' }, money(value)),
    ]);

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
            h('div.small.text-secondary', 'No panels selected yet.')))];

      T.mount(previewHost, [
        h('div.table-responsive', h('table.table.table-sm.table-tc.mb-2', [
          h('thead', h('tr', [
            h('th', 'Description'), h('th', 'Type'), h('th.text-end', 'Qty'),
            h('th.text-end', 'Rate'), h('th.text-end', 'Amount'),
          ])),
          h('tbody', rows),
        ])),
        preview.lines.length ? h('div.row.g-2.small', [
          summaryCell('Labour', s.labour_total),
          summaryCell('Materials', s.materials_total),
          summaryCell('Parts', s.parts_total),
          summaryCell('Subtotal', s.subtotal),
          summaryCell('VAT', s.vat),
          summaryCell('Total', s.total, true),
        ]) : null,
      ]);

      totalValue.textContent = preview.lines.length ? money(s.total) : '—';
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

    const saveBtn = h('button.btn.btn-brand', { type: 'button' });

    async function save() {
      if (!selected.size) {
        T.toast('Pick at least one panel to estimate.', 'warning');
        return;
      }
      saveBtn.disabled = true;
      T.mount(saveBtn, [h('span.spinner-border.spinner-border-sm.me-1'), ' Saving…']);
      try {
        const res = await api.post(`/api/jobs/${jobId}/estimate`, {
          panels: [...selected],
          include_paint: paintBox.checked,
          include_consumables: consumablesBox.checked,
          notes: notesInput.value,
          send: sendBox.checked,
        });
        T.toast(
          `Estimate ${res.estimate.reference} saved · ${money(res.estimate.total)}`
          + (sendBox.checked ? ' · quotation sent' : ''),
          'success');
        T.navigate(`/jobs/${jobId}`);
      } catch (err) {
        T.toast(err.message, 'danger');
        saveBtn.disabled = false;
        T.mount(saveBtn, [T.icon('calculator'), ' Save estimate']);
      }
    }
    saveBtn.addEventListener('click', save);
    T.mount(saveBtn, [T.icon('calculator'), ' Save estimate']);

    /* Any change to the money knobs re-prices the job. */
    [paintBox, consumablesBox].forEach((el) => {
      el.addEventListener('change', refreshPreview);
    });

    const check = (el, label, hint) => h('div.form-check', [
      el,
      h('label.form-check-label', { for: el.id }, label),
      hint ? h('div.tc-hint', hint) : null,
    ]);

    await refreshPreview();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Build estimate'),
          h('div.small.text-secondary',
            `${job.job_no} · ${job.reg_no || '—'} · ${job.customer_name || '—'}`
            + (job.estimate ? ` · currently ${job.estimate.reference} (${job.estimate.status})` : '')),
        ]),
        h('a.btn.btn-outline-secondary.btn-sm', { href: `#/jobs/${jobId}` },
          T.icon('arrow-left'), ' Back to job card'),
      ]),
      T.section({
        title: 'Damaged panels',
        body: h('div', [
          h('div.tc-hint.mb-2',
            'Tap every damaged panel — the total updates as you go.'),
          panelChips,
        ]),
      }),
      h('div.mt-3', T.section({
        title: 'What to include',
        body: h('div.row.g-3', [
          h('div.col-md-6', [
            check(paintBox, 'Include refinishing'),
            check(consumablesBox, 'Include paint and consumables'),
          ]),
        ]),
      })),
      h('div.mt-3', T.section({
        title: 'Estimate',
        body: h('div.card.bg-body-tertiary.border-0',
          h('div.card-body', previewHost)),
      })),
      h('div.mt-3', T.section({
        title: 'Notes and sending',
        body: h('div', [
          h('div.tc-field', [
            h('label.tc-label', { for: 'estNotes' }, 'Notes on this estimate'),
            notesInput,
          ]),
          h('div.mt-2', check(sendBox, 'Send the quotation to the customer',
            'The customer gets the PDF on WhatsApp straight away.')),
        ]),
      })),
      h('div.tc-toolbar.mt-3', [
        h('div.tc-toolbar-spacer'),
        h('div.tc-footer-total', [
          h('span.tc-footer-total-label', 'Estimate total'),
          totalValue,
        ]),
        saveBtn,
      ]),
    ]);
  });
})();
