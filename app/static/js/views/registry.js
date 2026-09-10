/* Customers, vehicles and bookings. */
(function () {
  const T = window.TCA;
  const { h, api, money, dateShort } = T;

  /* ── customers ────────────────────────────────────────────────────── */
  T.route('/customers', async (ctx) => {
    ctx.title = 'Customers';
    const host = h('div');

    async function load(q) {
      T.mount(host, T.skeletonTable(8, 5));
      const data = await api.get(`/api/customers?q=${encodeURIComponent(q || '')}`);
      T.mount(host, T.dataTable({
        columns: [
          { label: 'Name', render: (r) => h('div', [
              h('div.fw-semibold', r.name),
              r.company ? h('div.small.text-secondary', r.company) : null]) },
          { label: 'Phone', render: (r) => h('div', h('div', r.phone || '—'),
              r.whatsapp ? h('div.small.text-secondary', `WA ${r.whatsapp}`) : null) },
          { label: 'Email', class: 'd-none d-lg-table-cell', render: (r) => h('span.small', r.email || '—') },
          { label: 'Type', render: (r) => r.is_fleet ? h('span.badge.text-bg-dark', 'Fleet') : h('span.chip', 'Retail') },
          { label: 'Open job cards', render: (r) => h('span.badge.text-bg-warning', r.open_jobs) },
          { label: 'Portal', render: (r) => h('div.d-flex.gap-1', [
              h('button.btn.btn-sm.btn-outline-secondary', {
                onclick: (e) => { e.stopPropagation(); window.open(`/portal/${r.portal_token}`, '_blank'); },
              }, T.icon('box-arrow-up-right')),
              r.wa_number ? h('a.btn.btn-sm.btn-outline-success', {
                href: `https://wa.me/${r.wa_number}`, target: '_blank',
                onclick: (e) => e.stopPropagation(),
              }, T.icon('whatsapp')) : null,
            ]) },
        ],
        rows: data.items,
        onRowClick: (r) => customerDetail(r),
        empty: T.emptyState('No customers yet', 'Customer records are created automatically when you open a job card.', 'people'),
      }));
    }

    async function customerDetail(customer) {
      const full = await api.get(`/api/customers/${customer.id}`);
      const c = full.customer;
      T.modal({
        title: c.name,
        body: h('div', [
          h('div.row.g-2.small.mb-3', [
            kv('Phone', c.phone || '—'), kv('WhatsApp', c.whatsapp || '—'),
            kv('Email', c.email || '—'), kv('Address', c.address || '—'),
            kv('Type', c.is_fleet ? 'Fleet / corporate' : 'Retail'),
            kv('WhatsApp updates', c.whatsapp_opt_in ? 'Opted in' : 'Opted out'),
          ]),
          h('h3.h6.text-uppercase.text-secondary', 'Vehicles'),
          c.vehicles.length ? h('ul.list-group.list-group-flush.mb-3', c.vehicles.map((v) =>
            h('li.list-group-item.d-flex.justify-content-between',
              h('span', `${v.reg_no} — ${v.title}`),
              h('span.small.text-secondary', v.colour || '')))) : h('div.small.text-secondary', 'None'),
          h('h3.h6.text-uppercase.text-secondary', 'Job card history'),
          c.jobs.length ? h('ul.list-group.list-group-flush', c.jobs.map((j) =>
            h('li.list-group-item.d-flex.justify-content-between.cursor-pointer', {
              onclick: () => { T.navigate(`/jobs/${j.id}`); },
            }, h('span', `${j.job_no} · ${j.reg_no}`), T.stageBadge(j.stage, j.stage_label))))
            : h('div.small.text-secondary', 'No job cards yet'),
        ]),
        footer: [
          h('a.btn.btn-sm.btn-outline-secondary', { href: `/portal/${c.portal_token}`, target: '_blank' },
            'Open customer portal'),
          h('button.btn.btn-sm.btn-brand', {
            onclick: async () => {
              const res = await api.patch(`/api/customers/${c.id}`, { whatsapp_opt_in: !c.whatsapp_opt_in });
              T.toast(`WhatsApp updates ${res.customer.whatsapp_opt_in ? 'enabled' : 'disabled'}.`);
              load('');
            },
          }, c.whatsapp_opt_in ? 'Opt out of WhatsApp' : 'Opt in to WhatsApp'),
        ],
      });
    }

    function kv(label, value) {
      return h('div.col-6.col-md-4', h('div.text-secondary.text-uppercase', { style: 'font-size:.66rem' }, label),
        h('div.fw-semibold', value));
    }

    const search = T.searchInput({
      placeholder: 'Search name, phone, email…', width: 320,
      oninput: T.debounce((e) => load(e.target.value), 350),
    });

    await load('');

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Customers')),
        search,
        h('button.btn.btn-brand.btn-sm', {
          onclick: async () => {
            const res = await T.formModal({
              title: 'New customer',
              icon: 'person-plus',
              fields: [
                { name: 'name', label: 'Full name or company', col: 6, required: true,
                  icon: 'person', placeholder: 'Chipo Zvenyika' },
                { name: 'company', label: 'Company', col: 6, icon: 'building',
                  hint: 'Fleet or corporate account name.' },
                { name: 'phone', label: 'Phone', col: 4, icon: 'telephone',
                  placeholder: '+263 77 000 0000' },
                { name: 'whatsapp', label: 'WhatsApp', col: 4, icon: 'whatsapp',
                  hint: 'Leave blank to use the phone number.' },
                { name: 'email', label: 'Email', type: 'email', col: 4, icon: 'envelope' },
                { name: 'address', label: 'Address', col: 12, icon: 'geo-alt' },
                { name: 'is_fleet', label: 'Fleet / corporate account', type: 'switch', col: 12,
                  value: false, help: 'Fleet customers get consolidated invoicing and priority slots.' },
                { name: 'notes', label: 'Notes', type: 'textarea', col: 12,
                  placeholder: 'Preferred contact times, special instructions…' },
              ],
              submitLabel: 'Create customer',
            });
            if (!res) return;
            await api.post('/api/customers', res);
            T.toast('Customer created.');
            load('');
          },
        }, T.icon('person-plus'), ' New customer'),
      ]),
      T.section({ body: host, flush: true }),
    ]);
  });

  /* ── vehicles ─────────────────────────────────────────────────────── */
  T.route('/vehicles', async (ctx) => {
    ctx.title = 'Vehicles';
    const veh = T.vehicleOptions();
    const host = h('div');

    async function load(q) {
      T.mount(host, T.skeletonTable(8, 5));
      const data = await api.get(`/api/vehicles?q=${encodeURIComponent(q || '')}`);
      T.mount(host, T.dataTable({
        columns: [
          { label: 'Registration', render: (r) => h('span.fw-bold', r.reg_no) },
          { label: 'Vehicle', render: (r) => r.title },
          { label: 'Colour', render: (r) => r.colour || '—' },
          { label: 'Owner', render: (r) => r.customer_name || '—' },
          { label: 'Odometer', class: 'd-none d-lg-table-cell', render: (r) => r.mileage ? `${r.mileage.toLocaleString()} km` : '—' },
          { label: 'VIN', class: 'd-none d-lg-table-cell', render: (r) => h('span.small.text-secondary', r.vin || '—') },
        ],
        rows: data.items,
        empty: T.emptyState('No vehicles', 'Vehicles are added with each job card.', 'car-front'),
      }));
    }

    const search = T.searchInput({
      placeholder: 'Search registration, make or VIN…', width: 320,
      oninput: T.debounce((e) => load(e.target.value), 350),
    });

    await load('');
    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Vehicles')),
        search,
        h('button.btn.btn-brand.btn-sm', {
          onclick: async () => {
            const customers = await api.get('/api/customers');
            const res = await T.formModal({
              title: 'New vehicle',
              fields: [
                { name: 'customer_id', label: 'Owner', type: 'select', col: 12, required: true,
                  icon: 'person', placeholder: '— Choose customer —',
                  options: customers.items.map((c) => ({ value: c.id, label: `${c.name} · ${c.phone || ''}` })) },
                { name: 'reg_no', label: 'Registration', col: 4, required: true, icon: '123',
                  placeholder: 'ABC 1234' },
                { name: 'make', label: 'Make', type: 'combo', col: 4,
                  options: veh.makes, otherLabel: 'Other make (type it)', placeholder: 'Type the make',
                  onComboChange: (v, _n, api) => api.setOptions('model', veh.modelsFor(v)) },
                { name: 'model', label: 'Model', type: 'combo', col: 4,
                  options: veh.modelsFor(''), otherLabel: 'Other model (type it)', placeholder: 'Type the model' },
                { name: 'colour', label: 'Colour', type: 'combo', col: 3, swatch: true,
                  options: veh.colours, otherLabel: 'Other colour (type it)', placeholder: 'Type the colour' },
                { name: 'year', label: 'Year', type: 'number', col: 3, min: 1950, max: 2100,
                  step: '1', icon: 'calendar3' },
                { name: 'mileage', label: 'Odometer', type: 'number', col: 3,
                  icon: 'speedometer2', affix: 'km' },
                { name: 'vin', label: 'VIN / chassis', col: 3, icon: 'upc-scan' },
              ],
              submitLabel: 'Create vehicle',
            });
            if (!res) return;
            await api.post('/api/vehicles', res);
            T.toast('Vehicle created.');
            load('');
          },
        }, T.icon('plus-lg'), ' New vehicle'),
      ]),
      T.section({ body: host, flush: true }),
    ]);
  });

  /* ── bookings ─────────────────────────────────────────────────────── */
  T.route('/bookings', async (ctx) => {
    ctx.title = 'Bookings';
    const host = h('div');
    const meta = T.store.get('meta');

    async function load() {
      T.mount(host, T.skeletonTable(6, 5));
      const data = await api.get('/api/bookings');
      const colour = { REQUESTED: 'warning', CONFIRMED: 'info', ARRIVED: 'primary',
                       COMPLETED: 'success', NO_SHOW: 'secondary', CANCELLED: 'danger' };
      T.mount(host, T.dataTable({
        columns: [
          { label: 'Reference', render: (r) => h('div', [h('div.fw-semibold', r.reference),
              h('div.small.text-secondary', r.source)]) },
          { label: 'Customer', render: (r) => h('div', [h('div', r.customer_name),
              h('div.small.text-secondary', r.customer_phone || '')]) },
          { label: 'Service', render: (r) => h('span.small', r.service) },
          { label: 'Date', render: (r) => h('div', [h('div', dateShort(r.slot_date)),
              h('div.small.text-secondary', r.slot_time || 'Any time')]) },
          { label: 'From', render: (r) => money(r.quoted_from) },
          { label: 'Status', render: (r) => h(`span.badge.text-bg-${colour[r.status] || 'secondary'}`, r.status) },
          { label: '', class: 'text-end', render: (r) => h('div.d-flex.gap-1.justify-content-end', [
              r.status === 'REQUESTED' ? h('button.btn.btn-sm.btn-brand', {
                onclick: async (e) => { e.stopPropagation();
                  const res = await api.patch(`/api/bookings/${r.id}`, { status: 'CONFIRMED' });
                  T.toast(`Booking ${res.booking.reference} confirmed${res.delivered === false ? '' : ' · customer notified'}`);
                  load(); },
              }, 'Confirm') : null,
              h('button.btn.btn-sm.btn-outline-secondary', {
                onclick: (e) => { e.stopPropagation(); editBooking(r); },
              }, T.icon('pencil')),
            ]) },
        ],
        rows: data.items,
        onRowClick: (r) => editBooking(r),
        empty: T.emptyState('No bookings', 'Website and WhatsApp booking requests land here.', 'calendar-check'),
      }));
    }

    async function editBooking(booking) {
      const res = await T.formModal({
        title: `Booking ${booking.reference}`,
        fields: [
          { name: 'status', label: 'Status', type: 'select', col: 6, value: booking.status,
            options: meta.booking_statuses || ['REQUESTED', 'CONFIRMED', 'ARRIVED', 'COMPLETED', 'NO_SHOW', 'CANCELLED'] },
          { name: 'slot_date', label: 'Date', type: 'date', col: 3, value: booking.slot_date },
          { name: 'slot_time', label: 'Time', col: 3, value: booking.slot_time || '' },
          { name: 'notes', label: 'Notes', type: 'textarea', col: 12, value: booking.notes || '' },
        ],
        submitLabel: 'Save',
      });
      if (!res) return;
      await api.patch(`/api/bookings/${booking.id}`, res);
      T.toast('Booking updated.');
      load();
    }

    await load();
    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Bookings')),
        h('button.btn.btn-outline-secondary.btn-sm', {
          onclick: () => window.open('/quote', '_blank'),
        }, T.icon('box-arrow-up-right'), ' Public quote page'),
        h('button.btn.btn-brand.btn-sm', {
          onclick: async () => {
            const customers = await api.get('/api/customers');
            const res = await T.formModal({
              title: 'New booking',
              fields: [
                { name: 'customer_id', label: 'Customer *', type: 'select', col: 12, required: true,
                  placeholder: '— Choose customer —',
                  options: customers.items.map((c) => ({ value: c.id, label: c.name })) },
                { name: 'service', label: 'Service *', type: 'select', col: 6, required: true,
                  options: meta.service_names },
                { name: 'slot_date', label: 'Date', type: 'date', col: 3, value: T.today() },
                { name: 'slot_time', label: 'Time', type: 'select', col: 3,
                  options: ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00'] },
                { name: 'notes', label: 'Notes', type: 'textarea', col: 12 },
              ],
              submitLabel: 'Book in',
            });
            if (!res) return;
            const customer = customers.items.find((c) => String(c.id) === String(res.customer_id));
            await api.post('/api/bookings', {
              ...res, name: customer.name, phone: customer.phone, source: 'phone',
            });
            T.toast('Booking created.');
            load();
          },
        }, T.icon('plus-lg'), ' New booking'),
      ]),
      T.section({ body: host, flush: true }),
    ]);
  });
})();
