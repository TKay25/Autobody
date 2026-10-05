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
          h('div.d-flex.justify-content-between.align-items-center.mt-2',
            h('div.d-flex.gap-1',
              job.bay ? h('span.chip', job.bay) : null),
            h('div.meta', job.is_overdue
              ? h('span.text-danger.fw-semibold', `${job.days_in_shop}d`)
              : `${job.days_in_shop}d`)),
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
          if (current && current.to === col.stage) return;
          // Optimistic move
          const moveCard = document.querySelector(`[data-job-id="${jobId}"]`);
          if (moveCard) body.prepend(moveCard);
          // Remember it, so dropping the same card twice in one visit is not
          // read as two different moves.
          if (current) current.to = col.stage;
          try {
            const res = await api.post(`/api/jobs/${jobId}/stage`, { stage: col.stage });
            T.toast(`${res.job.job_no} → ${res.job.stage_label}${res.notified ? ' · customer notified' : ''}`);
          } catch (err) {
            T.toast(err.message, 'danger');
            ctx.refresh();
          }
        },
      }, [
        h('div.kanban-head', [h('span', col.label), badge]),
        body,
      ]);
      return { shell, stage: col.stage, badge, trueCount: placed.length };
    });

    const resultLabel = h('div.tc-board-count.small.text-secondary.mb-2');

    /* Filtering is a plain show/hide over the cards already on screen, so it
       costs nothing and never re-queries the board. The column badges follow the
       filter — a stage showing "3" while only one card is visible reads as a bug. */
    function applyFilter(raw) {
      const term = (raw || '').trim().toLowerCase();
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

    const kanbanEl = h('div.kanban', columns.map((column) => column.shell));
    applyFilter('');

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Work in progress'),
          h('div.small.text-secondary', 'Drag a card to move a vehicle through the shop. Customers are notified automatically.'),
        ]),
        h('button.btn.btn-outline-secondary.btn-sm', { onclick: () => ctx.refresh() },
          T.icon('arrow-clockwise'), ' Refresh'),
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
      kanbanEl,
    ]);
  });
})();
