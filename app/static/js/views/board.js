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

    // Index every card once so the search box can filter without a round trip.
    const index = [];
    const columns = data.board.columns.map((col) => {
      const body = h('div.kanban-body');
      const badge = h('span.badge.text-bg-secondary', col.count);

      (col.jobs || []).forEach((job) => {
        const card = h('div.job-card', {
          class: job.is_overdue ? 'job-card overdue' : 'job-card',
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
          stage: col.stage,
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
          const existing = data.board.columns.find((c) => c.jobs.some((j) => String(j.id) === jobId));
          if (existing && existing.stage === col.stage) return;
          // Optimistic move
          const moveCard = document.querySelector(`[data-job-id="${jobId}"]`);
          if (moveCard) body.prepend(moveCard);
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
      return { shell, stage: col.stage, badge, trueCount: col.count };
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
