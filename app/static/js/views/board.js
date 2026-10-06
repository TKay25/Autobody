/* WIP board — drag a job card between stages. */
(function () {
  const T = window.TCA;
  const { h, api } = T;

  T.route('/board', async (ctx) => {
    ctx.title = 'WIP board';
    // The stage labels and counts all come from the board payload — this screen
    // used to fetch /api/meta as well, purely to feed a lookup that no longer
    // had a reader.
    const data = await api.get('/api/dashboard');

    /* The shop's service lines, and the stages each one walks. The walks ride
       with the board payload because the columns are shared: the walk is what
       makes this the *card's* board, and the move dialog needs it to offer a
       card its own stages without a second round trip. */
    const meta = T.store.get('meta') || {};
    const services = meta.services || [];
    const walks = data.board.walks || {};
    const serviceByCode = {};
    services.forEach((s) => { serviceByCode[s.code] = s; });

    /* A card's own walk, falling back to every column. The server does the same
       for a service line it no longer offers, so the board can never end up
       unable to move a card. */
    function walkFor(job) {
      return walks[job.service_code] || columns.map((c) => c.stage);
    }

    /* What the car is in for, and how far along that service's own walk it is.
       A card that is *off* its walk says so: a valet parked in the spray booth
       is a mistake worth seeing, not one to be hidden behind a step count that
       assumes it belongs where it is. */
    function serviceChip(job) {
      const svc = serviceByCode[job.service_code];
      const name = svc ? svc.short : (job.service || '');
      if (!name) return null;
      const step = job.stage_step
        ? `step ${job.stage_step}/${job.stage_steps || '?'}`
        : 'off its walk';
      return h('span', {
        class: `chip${job.stage_step ? '' : ' is-warn'}`,
        title: `${job.service || 'Service'} — ${step}`,
      }, `${name} · ${step}`);
    }

    /* A queued stage move must not look undone.
       The server has not been told yet, so a plain re-render drops the card back
       into its old column — and a foreman who sees their move undone drags it
       again, which queues the same car twice. So the pending move decides where
       the card is drawn, and the card says it is not saved. */
    const pendingMoves = new Map();
    T.pendingFor((url) => /\/jobs\/\d+\/stage$/.test(url)).forEach((item) => {
      const id = T.pendingJobId(item);
      const target = (item.body || {}).stage;
      if (id && target) pendingMoves.set(id, target);
    });

    // The live search term, so the counts can be recomputed against it after a
    // card moves rather than snapping back to showing every card.
    let currentTerm = '';

    // Every job, with the column it should *appear* in.
    const placements = [];
    data.board.columns.forEach((col) => {
      (col.jobs || []).forEach((job) => {
        placements.push({ job, from: col.stage, to: pendingMoves.get(job.id) || col.stage });
      });
    });

    // Index every card once so the search box can filter without a round trip.
    const index = [];
    const columns = data.board.columns.map((col) => {
      const body = h('div.kanban-body');
      // Placed, not reported: the badge has to match what is under it, or a
      // column reading "3" while showing two reads as a bug.
      const placed = placements.filter((p) => p.to === col.stage);
      const badge = h('span.badge.text-bg-secondary', placed.length);

      placed.forEach(({ job, from, to }) => {
        const unsaved = to !== from;
        const card = h('div.job-card', {
          class: 'job-card' + (job.is_overdue ? ' overdue' : '')
            + (unsaved ? ' is-unsaved' : ''),
          draggable: true,
          onclick: () => T.navigate(`/jobs/${job.id}`),
          dataset: { jobId: job.id },
        }, [
          h('div.d-flex.justify-content-between.align-items-start.gap-1',
            h('div', [
              h('div.reg', job.reg_no || '—'),
              h('div.meta', job.vehicle_title || ''),
            ]),
            job.priority !== 'NORMAL' ? T.priorityBadge(job.priority) : null),
          h('div.title.text-truncate', job.customer_name || ''),
          /* Says why the card is in this column when the shop floor has not been
             told yet. Without it the move looks like it simply has not happened. */
          T.pendingFor((url) => url === `/api/jobs/${job.id}/stage`).length
            ? h('div.job-card-unsaved', [T.icon('cloud-arrow-up'), ' Not saved yet'])
            : null,
          h('div.d-flex.justify-content-between.align-items-center.mt-2.gap-1', [
            h('div.d-flex.gap-1.flex-wrap', [
              serviceChip(job),
              job.bay ? h('span.chip', job.bay) : null,
            ]),
            h('div.d-flex.align-items-center.gap-2', [
              h('div.meta', job.is_overdue
                ? h('span.text-danger.fw-semibold', `${job.days_in_shop}d`)
                : `${job.days_in_shop}d`),
              /* Drag-and-drop never fires on a touchscreen, so the card also
                 carries a Move button that opens the same stage picker. */
              h('button.job-card-move', {
                type: 'button',
                title: 'Move to another stage',
                'aria-label': `Move ${job.job_no || job.reg_no || 'this job card'} to another stage`,
                onclick: (event) => { event.stopPropagation(); openMove(job, to); },
              }, T.icon('arrows-move')),
            ]),
          ]),
        ]);

        card.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', String(job.id));
          e.dataTransfer.effectAllowed = 'move';
          card.classList.add('dragging');
        });
        card.addEventListener('dragend', () => card.classList.remove('dragging'));
        body.appendChild(card);

        index.push({
          el: card,
          jobId: job.id,
          stage: to,
          // Everything an operator might type: plate, name, job number, vehicle.
          haystack: [job.job_no, job.reg_no, job.vehicle_title, job.customer_name,
                     job.service, job.bay, job.stage_label]
            .filter(Boolean).join(' ').toLowerCase(),
        });
      });

      const shell = h('div.kanban-col', {
        dataset: { stage: col.stage },
        ondragover: (e) => { e.preventDefault(); shell.classList.add('drop-target'); },
        ondragleave: () => shell.classList.remove('drop-target'),
        ondrop: async (e) => {
          e.preventDefault();
          shell.classList.remove('drop-target');
          const jobId = e.dataTransfer.getData('text/plain');
          if (!jobId) return;
          const current = placements.find((p) => String(p.job.id) === jobId);
          /* Dropping a card on the column it is already in is not a move, so it
             asks nothing. Anything else opens the dialog: the note and the
             customer's notification are decided there, not by the drop. */
          if (!current || current.to === col.stage) return;
          openMove(current.job, current.to, { stage: col.stage });
        },
      }, [
        h('div.kanban-head', [h('span', col.label), badge]),
        body,
      ]);
      return { shell, stage: col.stage, label: col.label, badge, body, trueCount: placed.length };
    });

    /* Move a card, and everything derived from where it sits: the placement the
       counts are built from, the search index, the element itself, and then the
       badges. Moving only the element left a card sitting in Strip Down while its
       old column still counted it under Intake — and the count is what the shop
       reads to decide whether the board is telling the truth. */
    function placeCard(jobId, stage) {
      const placement = placements.find((p) => String(p.job.id) === jobId);
      if (placement) placement.to = stage;
      const entry = index.find((e) => String(e.jobId) === jobId);
      if (entry) entry.stage = stage;
      const target = columns.find((c) => c.stage === stage);
      const card = document.querySelector(`[data-job-id="${jobId}"]`);
      if (target && card) target.body.prepend(card);
      recount();
    }

    /* Recomputed from where the cards actually are, never nudged by one: a card
       moved twice, or a move the server rejects, would otherwise drift the totals
       away from the columns they describe. */
    function recount() {
      const perStage = {};
      placements.forEach((p) => { perStage[p.to] = (perStage[p.to] || 0) + 1; });
      columns.forEach((column) => { column.trueCount = perStage[column.stage] || 0; });
      applyFilter(currentTerm);
    }

    /* One move, two affordances: the desktop drag and the phone's Move button
       post the identical request — including the note and the answer to "tell the
       customer?". The card is placed optimistically first — through placeCard, so
       the badges come with it — then the server is told, and a rejection rolls the
       whole board back. */
    async function moveTo(jobId, stage, currentStage, { note = '', notify = true, force = false } = {}) {
      if (!stage || stage === currentStage) return;
      placeCard(jobId, stage);
      try {
        const res = await api.post(`/api/jobs/${jobId}/stage`, { stage, note, notify, force });
        /* The toast has to say what actually happened. "Notified" is the server's
           answer, not the tick in the box: a customer with no number on file, or
           one whose window has closed, is told nothing whatever was asked for. */
        const told = !notify
          ? ' · customer not told'
          : (res.notified ? ' · customer notified' : ' · nobody to notify');
        // A jump the card's own walk does not include is worth saying out loud on
        // the card's own screen, not only in the audit trail.
        const jump = force ? ' · overridden' : '';
        T.toast(`${res.job.job_no} → ${res.job.stage_label}${told}${jump}`);
      } catch (err) {
        T.toast(err.message, 'danger');
        ctx.refresh();
      }
    }

    /* The stage-move dialog: the one place a move is explained.

       Every move asks for a note and for whether the customer hears about it,
       because that is the moment both facts exist — what the shop has just done,
       and whether it is ready to be said out loud. The note is kept on the card
       whatever the answer: a note written only when a customer is being messaged
       would be an audit trail shaped by marketing. */
    async function openMove(job, currentStage, { stage: preset } = {}) {
      /* The card's own walk comes first, in order, under its own heading. The
         columns it never visits are still there — the shop knows things the
         service list does not — but they are a separate, deliberate group, and
         the server asks for the override switch to accept one. */
      const walk = walkFor(job);
      const labelOf = (stage) => (columns.find((c) => c.stage === stage) || {}).label || stage;
      const onWalk = walk.map((stage) => ({ value: stage, label: labelOf(stage) }));
      const offWalk = columns.filter((c) => !walk.includes(c.stage))
        .map((c) => ({ value: c.stage, label: c.label }));

      const res = await T.formModal({
        title: `Move ${job.job_no || job.reg_no || 'this job card'}`,
        intro: 'Say what was done, and whether the customer hears about it.',
        icon: 'arrows-move',
        fields: [
          { name: 'stage', label: 'Stage', type: 'select', col: 12, required: true,
            value: preset || currentStage,
            optgroups: [
              { label: `${job.service || 'This card'}'s stages`, options: onWalk },
              ...(offWalk.length
                ? [{ label: `Not on this card's walk`, options: offWalk }]
                : []),
            ],
            help: job.stage_step
              ? `Step ${job.stage_step} of ${job.stage_steps} on this card's walk.`
              : 'This card is not on its own walk — move it back onto it, or override.' },
          { name: 'force', label: 'Override the walk', type: 'switch', col: 12,
            value: false,
            help: 'Only for a stage this service does not go through. The move is stamped "overridden".' },
          { name: 'note', label: 'What was done', type: 'textarea', col: 12, rows: 3,
            placeholder: 'e.g. Both doors primed and blocked back',
            help: 'Kept on the job card, and printed on the end-of-day sheet.' },
          { name: 'notify', label: 'WhatsApp the customer', type: 'switch', col: 12,
            value: true,
            help: job.customer_whatsapp
              ? `Sends the standard stage update to ${job.customer_whatsapp}.`
              : 'This customer has no WhatsApp number on file — nothing will be sent.' },
        ],
        submitLabel: 'Move card',
      });
      if (!res) return;
      await moveTo(job.id, res.stage, currentStage,
        { note: res.note, notify: res.notify, force: res.force });
    }

    /* ── Adding a card, and closing the day out ───────────────────────────
       Both belong here rather than on a screen three taps away: the board is the
       one page the shop floor keeps open, so it is where a car arriving becomes a
       job card, and where the day is finished. */
    const PRESETS = [
      { value: 'today', label: 'Today' },
      { value: 'yesterday', label: 'Yesterday' },
      { value: 'month', label: 'This month' },
      { value: 'year', label: 'This year' },
      { value: 'custom', label: 'Custom' },
    ];

    /* A card added from the board.

       The five things the shop has in front of it: what the car is in for, which
       car, who owns it, where to reach them, and what is wrong with it. Bay,
       technician and promised date are chosen on the card itself, where there is
       room to think about them.

       The service is asked for here rather than defaulted to panel work, because
       it decides the stages the card walks: a car booked in for a valet that
       silently became a panel job was sent through the spray booth. The default
       offered is the same one the server would have assumed (the panel line). */
    async function quickJob() {
      const serviceField = services.length
        ? [{
            name: 'service', label: 'What is the car in for', type: 'select',
            col: 12, required: true,
            value: (services[1] || services[0]).name,
            options: services.map((s) => ({ value: s.name, label: s.name })),
            help: 'This sets the stages the card walks — its own walk, not the whole board.',
          }]
        : [];

      const res = await T.formModal({
        title: 'Add a job card',
        intro: 'Book the vehicle in. Everything else is filled in on the card.',
        icon: 'clipboard-plus',
        fields: [
          ...serviceField,
          { name: 'reg_no', label: 'Registration', col: 6, required: true,
            placeholder: 'ABC 1234', help: 'Spaces are ignored.' },
          { name: 'customer_name', label: 'Client name', col: 6, required: true,
            placeholder: 'Who is the car for?' },
          { name: 'customer_whatsapp', label: 'Client WhatsApp', type: 'tel', col: 12,
            help: 'Every stage update on this card goes here.' },
          { name: 'description', label: 'Job description', type: 'textarea', col: 12,
            rows: 3, placeholder: 'e.g. Right rear quarter panel and door' },
        ],
        submitLabel: 'Open the card',
      });
      if (!res) return;
      try {
        const body = await api.post('/api/jobs', {
          service: res.service,
          reg_no: res.reg_no,
          customer_name: res.customer_name,
          customer_whatsapp: res.customer_whatsapp,
          description: res.description,
        });
        /* Offline this was queued rather than created, so there is no card number
           to open yet — claiming one was opened would send the desk looking for a
           job card the server has never heard of. */
        if (body && body.queued) { T.toast(body.message, 'warning'); return; }
        T.toast(`${body.job.job_no} opened for ${body.job.reg_no}`
          + (body.job.stage_steps ? ` · ${body.job.stage_steps}-stage ${body.job.service} walk.` : '.'));
        T.navigate(`/jobs/${body.job.id}`);
      } catch (err) {
        T.toast(err.message, 'danger');
      }
    }

    const sheetRow = (label, value) => h('div.d-flex.justify-content-between.small.py-1', [
      h('span.text-secondary', label), h('strong', value),
    ]);

    /* The sheet itself — one renderer for the screen and for the paper, so the
       printed copy can never be a summary of something other than what is on
       screen. */
    function sheetNode(s) {
      const table = (columns, rows, empty) => (rows.length
        ? T.dataTable({ columns, rows })
        : h('div.small.text-secondary.py-3', empty));

      return h('div', [
        h('div.d-flex.align-items-end.justify-content-between.mb-2', [
          h('div', [
            h('div.fw-bold', 'Closing sheet'),
            h('div.small.text-secondary', s.range.label),
          ]),
          h('div.small.text-secondary.text-end', `Generated ${s.generated_at_local}`),
        ]),
        h('div.row.g-3.mb-3', [
          h('div.col-6.col-lg-4', T.statCard({ label: 'Open job cards',
            value: String(s.jobs.open), icon: 'clipboard-check', colour: 'primary' })),
          h('div.col-6.col-lg-4', T.statCard({ label: 'Booked in',
            value: String(s.jobs.opened), icon: 'box-arrow-in-right', colour: 'brand' })),
          h('div.col-6.col-lg-4', T.statCard({ label: 'Completed',
            value: String(s.jobs.completed), icon: 'check2-circle', colour: 'success' })),
          h('div.col-6.col-lg-4', T.statCard({ label: 'Collected',
            value: String(s.jobs.collected), icon: 'truck', colour: 'success' })),
          h('div.col-6.col-lg-4', T.statCard({ label: 'Stage notes',
            value: `${s.notes.with_note} of ${s.notes.moves}`,
            icon: 'chat-left-text', colour: 'warning' })),
          h('div.col-6.col-lg-4', T.statCard({ label: 'Taken in',
            value: T.money(s.money.collected), icon: 'cash-coin', colour: 'success' })),
        ]),
        T.section({
          title: 'What happened, stage by stage',
          body: table([
            { label: 'When', class: 'text-nowrap',
              render: (r) => h('span.small', r.at_local || '—') },
            { label: 'Job card', render: (r) => h('div', [
              h('div.fw-semibold', r.job_no || '—'),
              h('div.small.text-secondary', r.reg_no || '')]) },
            { label: 'Moved to', render: (r) => T.badge(r.stage_label) },
            { label: 'Note', render: (r) => h('div.tc-note-cell', r.note || '—') },
            { label: 'By', render: (r) => h('span.small', r.user || '—') },
          ], s.notes.list, 'No stage moves in this range.'),
        }),
        h('div.row.g-3.mt-3', [
          h('div.col-lg-6', T.section({
            title: 'Job cards by stage',
            body: table([
              { label: 'Stage', render: (r) => r.label },
              { label: 'Job cards', class: 'text-end',
                render: (r) => T.badge(String(r.count), r.count ? 'secondary' : 'light') },
            ], s.jobs.by_stage, 'Nothing open.'),
          })),
          h('div.col-lg-6', T.section({
            title: 'The money',
            body: h('div', [
              sheetRow('Taken in', T.money(s.money.collected)),
              sheetRow('Invoiced', T.money(s.money.invoiced)),
              sheetRow('Outstanding', T.money(s.money.outstanding)),
              sheetRow('Unpaid invoices', String(s.money.unpaid_count)),
              sheetRow('Overdue', `${s.money.overdue_count} · ${T.money(s.money.overdue_total)}`),
            ]),
          })),
        ]),
        h('div.row.g-3.mt-3', [
          h('div.col-lg-6', T.section({
            title: 'The shop floor',
            body: h('div', [
              sheetRow('Enquiries raised', String(s.bookings.raised)),
              sheetRow('Appointments in range', String(s.bookings.scheduled)),
              sheetRow('Awaiting confirmation', String(s.bookings.awaiting_confirmation)),
              sheetRow('Walk-outs', String(s.bookings.walked_out)),
              sheetRow('To-do open / overdue', `${s.tasks.open} / ${s.tasks.overdue}`),
              sheetRow('To-do closed in range', String(s.tasks.done_today)),
            ]),
          })),
          h('div.col-lg-6', T.section({
            title: 'Who did what',
            body: table([
              { label: 'Name', render: (r) => h('strong', r.name) },
              { label: 'Done in range', class: 'text-end', render: (r) => String(r.done_tasks) },
              { label: 'Carrying', class: 'text-end', render: (r) => String(r.open_tasks) },
              { label: 'Job cards', class: 'text-end', render: (r) => String(r.open_jobs) },
            ], s.staff, 'Nobody recorded anything in this range.'),
          })),
        ]),
      ]);
    }

    /* The closing sheet over any range, chosen here and answered by the API. */
    async function openReport() {
      let preset = 'today';
      let from = T.today();
      let to = T.today();
      let sheet = null;

      const host = h('div');
      const label = h('div.small.text-secondary.mb-2');
      const printBtn = h('button.btn.btn-outline-secondary.btn-sm', {
        type: 'button', onclick: () => printSheet(),
      }, T.icon('printer'), ' Print');
      const pdfBtn = h('button.btn.btn-outline-secondary.btn-sm', {
        type: 'button', onclick: () => downloadPdf(),
      }, T.icon('file-earmark-pdf'), ' PDF');

      const fromInput = h('input.form-control.form-control-sm', {
        type: 'date', value: from, max: T.today(),
        onchange: (e) => { from = e.target.value; load(); },
      });
      const toInput = h('input.form-control.form-control-sm', {
        type: 'date', value: to, max: T.today(),
        onchange: (e) => { to = e.target.value; load(); },
      });
      const dates = h('div.tc-report-dates', [
        fromInput, h('span.small.text-secondary', 'to'), toInput,
      ]);
      const buttons = PRESETS.map((p) => h('button.btn.btn-sm', {
        type: 'button', onclick: () => { preset = p.value; load(); },
      }, p.label));

      function paint() {
        buttons.forEach((b, i) => {
          const on = PRESETS[i].value === preset;
          b.className = `btn btn-sm ${on ? 'btn-brand' : 'btn-outline-secondary'}`;
        });
        dates.hidden = preset !== 'custom';
        /* A PDF is one day at a time — that is the sheet that gets printed and
           signed off — so the button only appears when it means something. */
        pdfBtn.hidden = !sheet || sheet.range.days !== 1;
        printBtn.disabled = !sheet;
      }

      async function load() {
        T.mount(host, T.spinner('Building the closing sheet…'));
        const query = preset === 'custom' ? `&from=${from}&to=${to}` : '';
        try {
          sheet = await api.get(`/api/reports/range?preset=${preset}${query}`);
          label.textContent = sheet.range.label;
          T.mount(host, sheetNode(sheet));
        } catch (err) {
          sheet = null;
          label.textContent = '';
          T.mount(host, h('div.alert.alert-danger.py-2.mb-0', err.message));
        }
        paint();
      }

      function downloadPdf() {
        if (sheet && sheet.range.days === 1) {
          window.open(`/api/reports/end-of-day/pdf?date=${sheet.range.to}`, '_blank');
        }
      }

      function printSheet() {
        if (!sheet) return;
        const node = sheetNode(sheet);
        T.closeModal();
        T.printDoc({ title: 'Closing sheet', node });
      }

      T.modal({
        title: 'End of day report',
        subtitle: 'The closing sheet for a day, a month, a year, or any range in between.',
        icon: 'clipboard-data',
        size: 'xl',
        body: h('div', [
          h('div.tc-report-bar', [h('div.tc-report-presets', buttons), dates]),
          label,
          host,
        ]),
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm',
            { type: 'button', 'data-bs-dismiss': 'modal' }, 'Close'),
          pdfBtn,
          printBtn,
        ],
      });
      await load();
    }

    const resultLabel = h('div.tc-board-count.small.text-secondary.mb-2');

    /* Filtering is a plain show/hide over the cards already on screen, so it
       costs nothing and never re-queries the board. The column badges follow the
       filter — a stage showing "3" while only one card is visible reads as a bug. */
    function applyFilter(raw) {
      const term = (raw || '').trim().toLowerCase();
      currentTerm = raw || '';
      const perStage = {};
      let total = 0;

      index.forEach((entry) => {
        const match = !term || entry.haystack.includes(term);
        entry.el.hidden = !match;
        if (match) {
          total += 1;
          perStage[entry.stage] = (perStage[entry.stage] || 0) + 1;
        }
      });

      columns.forEach((column) => {
        const count = term ? (perStage[column.stage] || 0) : column.trueCount;
        // Only a column that actually matched gets the "live" badge; a column with
        // nothing to show while a filter is on is faded, so the eye skips it instead
        // of reading thirteen darkened counts as thirteen results.
        const hit = Boolean(term) && count > 0;
        column.badge.textContent = String(count);
        column.badge.classList.toggle('text-bg-dark', hit);
        column.badge.classList.toggle('text-bg-secondary', !hit);
        column.shell.classList.toggle('tc-board-col-dim', Boolean(term) && count === 0);
        // The rail's counts are the same numbers, so they are set here too: a
        // chip reading "3" while the column under it shows one card is the same
        // lie as the badge would be.
        if (column.railChip) {
          column.railChip.querySelector('.tc-stage-rail-count').textContent = String(count);
          column.railChip.classList.toggle('is-dim', Boolean(term) && count === 0);
          column.railChip.classList.toggle('is-hit', hit);
        }
      });

      resultLabel.textContent = term
        ? (total
            ? `${total} of ${index.length} job cards match “${raw.trim()}”`
            : `No job card matches “${raw.trim()}”`)
        : `${index.length} job card${index.length === 1 ? '' : 's'} on the board`;
      resultLabel.classList.toggle('text-danger', Boolean(term) && total === 0);

      // The board is thirteen columns wide, so a match can easily land off-screen
      // and read as "the search found nothing". Bring the first one into view —
      // but only when nothing matching is already on screen, so we never fight
      // someone who is scrolling deliberately.
      if (term && total && kanbanEl) {
        const first = index.find((entry) => !entry.el.hidden);
        const pane = kanbanEl.getBoundingClientRect();
        if (first) {
          const box = first.el.getBoundingClientRect();
          if (box.right < pane.left + 8 || box.left > pane.right - 8) {
            first.el.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
          }
        }
      }
    }

    /* ── The phone's way across the board ─────────────────────────────────
       Eleven columns is a wall you can see across on a desktop and a kilometre
       of swiping on a phone, so a phone gets a rail: one chip per stage with its
       count, which jumps the board to that column.

       Why not drag on touch: HTML5 drag events do not fire on a touchscreen at
       all, and a finger-drag on a card fights the page for the scroll — the
       gesture the shop uses twenty times a minute. So on touch the move is a tap
       (the card's Move button, then a stage) and the rail is how you travel.
       Drag stays for the mouse, where it is genuinely faster. */
    const rail = h('div.tc-stage-rail');
    columns.forEach((column) => {
      const chip = h('button.tc-stage-rail-chip', {
        type: 'button',
        onclick: () => {
          rail.querySelectorAll('.is-active').forEach((b) => b.classList.remove('is-active'));
          chip.classList.add('is-active');
          column.shell.scrollIntoView({ block: 'nearest', inline: 'start', behavior: 'smooth' });
        },
      }, [h('span', column.label), h('span.tc-stage-rail-count', String(column.trueCount))]);
      column.railChip = chip;
      rail.appendChild(chip);
    });

    const kanbanEl = h('div.kanban', columns.map((column) => column.shell));

    applyFilter('');

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Work in progress'),
          /* The old caption promised the customer was told on every move. That is
             no longer true, and a promise the shop cannot keep is worse than no
             caption at all — the dialog is where the answer is given now.
             It also has to stop promising that drag is the way in: on a phone the
             card's Move button is, and the caption below says so. */
          h('div.small.text-secondary', 'Every card walks its own service’s stages — a valet never sees the spray booth. Drag a card on a computer; on a phone tap Move. Every move takes a note, and you decide whether the customer hears about it.'),
        ]),
        h('div.tc-board-actions', [
          h('button.btn.btn-sm.btn-brand', { onclick: () => quickJob() },
            T.icon('plus-lg'), ' Add job card'),
          h('button.btn.btn-sm.btn-outline-secondary', { onclick: () => openReport() },
            T.icon('clipboard-data'), ' End of day'),
          h('button.btn.btn-sm.btn-outline-secondary', { onclick: () => ctx.refresh() },
            T.icon('arrow-clockwise'), ' Refresh'),
        ]),
      ]),
      /* Only the moves — a part fitted or a QC tick is not a board change. */
      T.pendingStrip({
        match: (url) => /\/jobs\/\d+\/(stage|advance)/.test(url),
        label: 'move',
      }),
      h('div.tc-toolbar.mb-2', [
        T.searchInput({
          placeholder: 'Search registration, customer or job card…',
          width: 320,
          oninput: T.debounce((e) => applyFilter(e.target.value), 160),
        }),
      ]),
      resultLabel,
      rail,
      kanbanEl,
    ]);
  });
})();
