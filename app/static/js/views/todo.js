/* The day book.
 *
 * A task is the work that is not (yet) a job card — chase the assessor, order
 * the part, call the customer back. Each one shows the activity, the custodian
 * who owns it and where it has got to, which is what a foreman needs to run a
 * shift and hand over at the end of one.
 */
(function () {
  const T = window.TCA;
  const { h, api, dateShort } = T;

  const STATUS_FALLBACK = ['OPEN', 'DOING', 'BLOCKED', 'DONE'];

  /* Module scope, not inside the route: the attention panel in the top bar
     opens this same dialog from any screen. */
  function taskFields(task, staff, meta) {
    const t = task || {};
    const labels = (meta || {}).task_status_labels || {};
    const statusCodes = (meta || {}).task_statuses || STATUS_FALLBACK;
    const statusLabel = (code) => labels[code] || code;

    return [
      { name: 'title', label: 'What needs doing *', col: 12, required: true,
        placeholder: 'Chase the panel shop for the replacement bumper', value: t.title || '' },
      { name: 'category', label: 'Area', type: 'combo', col: 4,
        options: ['Parts', 'Front desk', 'Workshop', 'Billing', 'Customer call'],
        otherLabel: 'Other area (type it)', value: t.category || '' },
      { name: 'custodian_id', label: 'Custodian', type: 'select', col: 4,
        value: t.custodian_id || '', placeholder: '— Who owns it? —',
        options: (staff || []).map((u) => ({
          value: u.id, label: `${u.full_name} · ${u.role_label}` })) },
      { name: 'due_date', label: 'Due', type: 'date', col: 4, value: t.due_date || '' },
      { name: 'status', label: 'Status', type: 'select', col: 6,
        value: t.status || 'OPEN',
        options: statusCodes.map((code) => ({ value: code, label: statusLabel(code) })) },
      { name: 'priority', label: 'Priority', type: 'select', col: 6,
        value: t.priority || 'NORMAL',
        options: ((meta || {}).priorities || ['LOW', 'NORMAL', 'HIGH', 'URGENT'])
          .map((p) => ({ value: p, label: p.charAt(0) + p.slice(1).toLowerCase() })) },
      { name: 'detail', label: 'Notes', type: 'textarea', col: 12, value: t.detail || '' },
    ];
  }

  /** Add a task. Returns the created task, or null if cancelled. */
  async function newTask() {
    const meta = T.store.get('meta') || {};
    let staff = [];
    try { staff = (await api.get('/api/users')).items || []; } catch (err) { staff = []; }

    const res = await T.formModal({
      title: 'Add to the day book',
      subtitle: 'Anything that needs chasing, with a name against it.',
      icon: 'list-check',
      fields: taskFields(null, staff, meta),
      submitLabel: 'Add task',
    });
    if (!res) return null;
    await api.post('/api/tasks', res);
    return res;
  }

  T.newTask = newTask;

  T.route('/todo', async (ctx) => {
    ctx.title = 'To-do';
    const meta = T.store.get('meta') || {};
    const labels = meta.task_status_labels || {};
    const colours = meta.task_status_colours || {};
    const statusCodes = meta.task_statuses
      || ['OPEN', 'DOING', 'BLOCKED', 'DONE'];

    let window = ctx.query.window || 'day';
    let staff = [];
    let items = [];

    const listHost = h('div');
    const summaryHost = h('div');
    const statusLabel = (code) => labels[code] || code;

    async function loadStaff() {
      if (staff.length) return staff;
      try { staff = (await api.get('/api/users')).items || []; } catch (err) { staff = []; }
      return staff;
    }

    /* ── the board ──────────────────────────────────────────────────── */
    async function load() {
      T.mount(listHost, T.skeletonTable(5, 4));
      const data = await api.get(`/api/tasks?window=${window}`);
      items = data.items || [];
      renderSummary(data);
      renderTable();
      renderWindowTabs();
    }

    function renderSummary(data) {
      T.mount(summaryHost, h('div.row.g-3', [
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Overdue', value: String(data.overdue || 0),
          icon: 'exclamation-triangle', colour: data.overdue ? 'danger' : 'secondary' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: window === 'day' ? 'On today' : 'On this week',
          value: String(items.filter((t) => t.status !== 'DONE').length),
          icon: 'list-check', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Done today', value: String(data.done_today || 0),
          icon: 'check2-circle', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'No day set', value: String(data.undated || 0),
          icon: 'calendar-x', colour: data.undated ? 'warning' : 'secondary' })),
      ]));
    }

    const windowTabs = h('div.tc-segmented', { role: 'group', 'aria-label': 'Which days to show' },
      [['day', 'Today'], ['week', 'This week'], ['all', 'Everything']].map(([id, label]) =>
        h('button', {
          type: 'button', value: id,
          onclick: () => { window = id; load(); },
        }, label)));

    function renderWindowTabs() {
      [...windowTabs.querySelectorAll('button')]
        .forEach((b) => b.classList.toggle('is-active', b.value === window));
    }

    async function setStatus(task, status) {
      await api.patch(`/api/tasks/${task.id}`, { status });
      T.toast(status === 'DONE' ? 'Task done.' : `Task moved to ${statusLabel(status)}.`,
        status === 'DONE' ? 'success' : undefined);
      load();
    }

    function renderTable() {
      if (!items.length) {
        T.mount(listHost, T.emptyState(
          window === 'day' ? 'Nothing left for today' : 'Nothing on the list',
          'Add the next thing that needs chasing.', 'list-check'));
        return;
      }

      T.mount(listHost, h('div.table-responsive', h('table.table.table-tc.align-middle', [
        h('thead', h('tr', [
          h('th', 'Activity'),
          h('th', 'Custodian'),
          h('th', 'Due'),
          h('th', 'Status'),
          h('th', ''),
        ])),
        h('tbody', items.map((t) => h('tr', { class: t.status === 'DONE' ? 'opacity-75' : null }, [
          h('td', [
            h('div', { class: t.status === 'DONE' ? 'text-decoration-line-through' : 'fw-semibold' },
              t.title),
            t.detail ? h('div.small.text-secondary', t.detail) : null,
            h('div.d-flex.gap-1.mt-1.flex-wrap', [
              t.category ? h('span.chip', t.category) : null,
              t.job_no ? h('a.chip', { href: `#/jobs/${t.job_id}` }, t.job_no) : null,
              t.priority && t.priority !== 'NORMAL'
                ? h('span.badge.text-bg-dark', t.priority) : null,
            ]),
          ]),
          h('td', h('span.small', t.custodian || '—')),
          h('td', t.due_date
            ? h('span', { class: t.is_overdue ? 'badge.text-bg-danger' : 'small' },
                dateShort(t.due_date))
            : h('span.small.text-secondary', 'No day set')),
          h('td', h('select.form-select.form-select-sm', {
            style: 'min-width:9rem',
            onchange: (e) => setStatus(t, e.target.value),
          }, statusCodes.map((code) => h('option', {
            value: code, selected: code === t.status,
          }, statusLabel(code))))),
          h('td.text-end', h('div.d-flex.gap-1.justify-content-end', [
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', title: 'Edit', onclick: () => editTask(t),
            }, T.icon('pencil')),
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', title: 'Remove', onclick: () => removeTask(t),
            }, T.icon('trash')),
          ])),
        ]))),
      ])));
    }

    /* ── create / edit ──────────────────────────────────────────────── */
    async function addTask() {
      await loadStaff();
      if (!await T.newTask()) return;
      T.toast('Added to the day book.', 'success');
      load();
    }

    async function editTask(task) {
      await loadStaff();
      const res = await T.formModal({
        title: task.title,
        subtitle: `Added by ${task.created_by || '—'}`,
        icon: 'pencil-square',
        fields: taskFields(task, staff, meta),
        submitLabel: 'Save',
      });
      if (!res) return;
      await api.patch(`/api/tasks/${task.id}`, res);
      T.toast('Task saved.');
      load();
    }

    async function removeTask(task) {
      if (!window.confirm(`Remove “${task.title}”?`)) return;
      await api.delete(`/api/tasks/${task.id}`);
      T.toast('Task removed.');
      load();
    }

    await loadStaff();
    await load();

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [
          h('h1.h4.mb-0', 'To-do'),
          h('div.small.text-secondary',
            'What has to happen today and this week, who owns it, and where it has got to.'),
        ]),
        h('button.btn.btn-brand.btn-sm', { onclick: addTask },
          T.icon('plus-lg'), ' Add task'),
      ]),
      summaryHost,
      h('div.tc-toolbar.mt-3', [windowTabs, h('div.tc-toolbar-spacer')]),
      T.section({ body: listHost, flush: true }),
    ]);
  });
})();
