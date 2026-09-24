/* WIP board — drag a job card between stages. */
(function () {
  const T = window.TCA;
  const { h, api } = T;

  T.route('/board', async (ctx) => {
    ctx.title = 'WIP board';
    const [data, meta] = await Promise.all([api.get('/api/dashboard'), api.get('/api/meta')]);
    const stages = meta.stages;

    const columns = data.board.columns.map((col) => {
      const stageMeta = stages.find((s) => s.code === col.stage) || { colour: 'secondary' };
      const body = h('div.kanban-body');

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
        h('div.kanban-head', [
          h('span', col.label),
          h('span.badge.text-bg-secondary', col.count),
        ]),
        body,
      ]);
      return shell;
    });

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'Work in progress'),
          h('div.small.text-secondary', 'Drag a card to move a vehicle through the shop. Customers are notified automatically.'),
        ]),
        h('button.btn.btn-outline-secondary.btn-sm', { onclick: () => ctx.refresh() },
          T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      h('div.kanban', columns),
    ]);
  });
})();
