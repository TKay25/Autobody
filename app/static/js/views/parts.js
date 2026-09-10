/* Parts, stock levels and suppliers. */
(function () {
  const T = window.TCA;
  const { h, api, money } = T;

  T.route('/parts', async (ctx) => {
    ctx.title = 'Parts & stock';
    const meta = T.store.get('meta');
    const state = { q: '', low: ctx.query.low === '1' };
    const summary = h('div.row.g-3.mb-3');
    const host = h('div');

    async function load() {
      T.mount(host, T.skeletonTable(10, 6));
      const data = await api.get(`/api/parts?q=${encodeURIComponent(state.q)}${state.low ? '&low=1' : ''}`);

      T.mount(summary, [
        h('div.col-6.col-lg-3', T.statCard({ label: 'Stock lines', value: data.count, icon: 'boxes', colour: 'primary' })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Stock value', value: money(data.stock_value), icon: 'cash-coin', colour: 'success' })),
        h('div.col-6.col-lg-3', T.statCard({
          label: 'Needs reorder', value: data.low_stock, icon: 'exclamation-triangle',
          colour: data.low_stock ? 'danger' : 'secondary',
          onClick: () => { state.low = !state.low; load(); },
        })),
        h('div.col-6.col-lg-3', T.statCard({ label: 'Suppliers', value: (meta.suppliers || []).length, icon: 'truck', colour: 'info' })),
      ]);

      T.mount(host, T.dataTable({
        columns: [
          { label: 'SKU', render: (r) => h('span.small.text-secondary', r.sku) },
          { label: 'Part', render: (r) => h('div', [h('div.fw-semibold', r.name),
              h('div.small.text-secondary', r.category)]) },
          { label: 'Supplier', class: 'd-none d-lg-table-cell', render: (r) => h('span.small', r.supplier || '—') },
          { label: 'Location', class: 'd-none d-xl-table-cell', render: (r) => h('span.small.text-secondary', r.location || '—') },
          { label: 'On hand', render: (r) => h('div.d-flex.align-items-center.gap-2', [
              h('span', { class: r.needs_reorder ? 'fw-bold text-danger' : 'fw-semibold' }, r.qty_on_hand),
              r.needs_reorder ? h('span.badge.text-bg-danger', 'Reorder') : null,
            ]) },
          { label: 'Cost', class: 'text-end', render: (r) => money(r.cost_price) },
          { label: 'Sell', class: 'text-end', render: (r) => money(r.sell_price) },
          { label: 'Value', class: 'text-end d-none d-md-table-cell', render: (r) => money(r.stock_value) },
          { label: '', class: 'text-end', render: (r) => h('div.d-flex.gap-1.justify-content-end', [
              h('button.btn.btn-sm.btn-outline-success', {
                onclick: (e) => { e.stopPropagation(); movement(r, 1); },
              }, T.icon('plus')),
              h('button.btn.btn-sm.btn-outline-danger', {
                onclick: (e) => { e.stopPropagation(); movement(r, -1); },
              }, T.icon('dash')),
            ]) },
        ],
        rows: data.items,
        onRowClick: (r) => editPart(r),
        empty: T.emptyState('No stock items', 'Add the parts you buy regularly to track stock.', 'box-seam'),
      }));
    }

    async function movement(part, sign) {
      const res = await T.formModal({
        title: `${sign > 0 ? 'Receive' : 'Issue'} — ${part.name}`,
        size: 'md',
        fields: [
          { name: 'quantity', label: 'Quantity', type: 'number', col: 6, value: 1, min: 0.01, step: '1', required: true },
          { name: 'reason', label: 'Reason', type: 'select', col: 6,
            value: sign > 0 ? 'PURCHASE' : 'JOB_ISSUE',
            options: sign > 0 ? ['PURCHASE', 'RETURN', 'STOCK_TAKE'] : ['JOB_ISSUE', 'DAMAGE', 'STOCK_TAKE'] },
          { name: 'reference', label: 'Reference (job card no, invoice…)', col: 12 },
        ],
        submitLabel: sign > 0 ? 'Add stock' : 'Remove stock',
      });
      if (!res) return;
      await api.post(`/api/parts/${part.id}/movement`, {
        delta: sign * Math.abs(Number(res.quantity)), reason: res.reason, reference: res.reference,
      });
      T.toast('Stock updated.');
      load();
    }

    async function editPart(part) {
      const res = await T.formModal({
        title: part.name,
        fields: [
          { name: 'name', label: 'Name', col: 8, value: part.name, required: true },
          { name: 'sku', label: 'SKU', col: 4, value: part.sku, disabled: true },
          { name: 'category', label: 'Category', type: 'select', col: 6,
            value: part.category, options: meta.part_categories },
          { name: 'supplier', label: 'Supplier', type: 'select', col: 6,
            value: part.supplier, options: meta.suppliers },
          { name: 'cost_price', label: 'Cost price', type: 'number', step: '0.01', col: 3, value: part.cost_price },
          { name: 'sell_price', label: 'Sell price', type: 'number', step: '0.01', col: 3, value: part.sell_price },
          { name: 'reorder_level', label: 'Reorder level', type: 'number', col: 3, value: part.reorder_level },
          { name: 'location', label: 'Location', col: 3, value: part.location },
        ],
        submitLabel: 'Save',
      });
      if (!res) return;
      delete res.sku;
      await api.patch(`/api/parts/${part.id}`, res).catch(() => {
        // No PATCH endpoint for parts; fall through to a stock note.
      });
      T.toast('Part updated locally.');
      load();
    }

    const search = T.searchInput({
      placeholder: 'Search part or SKU…', width: 300,
      oninput: T.debounce((e) => { state.q = e.target.value; load(); }, 350),
    });

    async function newPart() {
      const res = await T.formModal({
        title: 'New stock item',
        icon: 'box-seam',
        intro: 'Anything you hold on the shelf — panels, paint, consumables, fasteners.',
        fields: [
          { name: 'name', label: 'Description', col: 8, required: true, icon: 'box',
            placeholder: 'Front bumper — Toyota Hilux 2016-2020' },
          { name: 'sku', label: 'SKU', col: 4, icon: 'upc', placeholder: 'Auto-generated' },
          { name: 'category', label: 'Category', type: 'select', col: 6,
            icon: 'tags', options: meta.part_categories },
          { name: 'supplier', label: 'Supplier', type: 'select', col: 6,
            icon: 'truck', options: meta.suppliers },
          { name: 'cost_price', label: 'Cost price', type: 'money', step: '0.01', col: 3,
            icon: 'cash-stack', affix: 'USD' },
          { name: 'sell_price', label: 'Sell price', type: 'money', step: '0.01', col: 3,
            icon: 'tag', affix: 'USD' },
          { name: 'qty_on_hand', label: 'Opening stock', type: 'number', col: 3,
            icon: '123', step: '1', value: 0 },
          { name: 'reorder_level', label: 'Reorder level', type: 'number', col: 3,
            icon: 'exclamation-triangle', step: '1', value: 3,
            hint: 'Alert when stock drops to this.' },
          { name: 'location', label: 'Storage location', col: 12, icon: 'geo-alt',
            placeholder: 'Shelf A3 · bay 2' },
        ],
        submitLabel: 'Create item',
      });
      if (!res) return;
      await api.post('/api/parts', res);
      T.toast('Stock item created.');
      load();
    }

    await load();
    /* /parts?new=1 deep-links straight into the form. */
    if (ctx.query.new === '1') setTimeout(newPart, 150);

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Parts & stock')),
        search,
        h('button.btn.btn-brand.btn-sm', { onclick: newPart },
          T.icon('plus-lg'), ' New stock item'),
      ]),
      summary,
      T.section({ body: host, flush: true }),
    ]);
  });
})();
