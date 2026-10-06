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
      const m = T.modal({
        title: c.name,
        body: h('div', [
          h('div.row.g-2.small.mb-3', [
            kv('ID number', c.id_number || 'Not recorded'),
            kv('Phone', c.phone || '—'), kv('WhatsApp', c.whatsapp || '—'),
            kv('Email', c.email || '—'), kv('Address', c.address || '—'),
            kv('Type', c.is_fleet ? 'Fleet / corporate' : 'Retail'),
            kv('WhatsApp updates', c.whatsapp_opt_in ? 'Opted in' : 'Opted out'),
          ]),
          !c.id_number
            ? h('div.alert.alert-warning.small.py-2',
                'No ID on file. It is checked against the document when the '
                + 'vehicle is collected, so record it before then.')
            : null,
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
          h('button.btn.btn-sm.btn-outline-secondary', {
            onclick: () => { m.close(); editCustomer(c); },
          }, T.icon('pencil-square'), ' Edit details'),
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

    /* Identity fields, shared by "New customer" and "Edit customer" so the two
       can never come to ask for different things. `c` is empty for a new one. */
    function identityFields(c = {}) {
      return [
        { name: 'name', label: 'Full name or company', col: 6, required: true,
          icon: 'person', placeholder: 'Chipo Zvenyika', value: c.name },
        { name: 'id_number', label: 'ID number', col: 6, icon: 'person-vcard',
          value: c.id_number, placeholder: '63-1234567 A 00',
          hint: 'National ID or passport. This is what the desk checks the '
              + 'document against when the vehicle is collected.' },
        { name: 'company', label: 'Company', col: 6, icon: 'building',
          value: c.company, hint: 'Fleet or corporate account name.' },
        { name: 'phone', label: 'Phone', type: 'tel', col: 6, value: c.phone,
          placeholder: '77 000 0000',
          hint: 'The code is picked for you — just type the number as it is written.' },
        { name: 'whatsapp', label: 'WhatsApp', type: 'tel', col: 6, value: c.whatsapp,
          placeholder: '77 000 0000',
          hint: 'Leave blank to use the phone number. This is the number the bot messages.' },
        { name: 'email', label: 'Email', type: 'email', col: 6, icon: 'envelope',
          value: c.email },
        { name: 'address', label: 'Address', col: 12, icon: 'geo-alt', value: c.address },
        { name: 'is_fleet', label: 'Fleet / corporate account', type: 'switch', col: 12,
          value: !!c.is_fleet,
          help: 'Fleet customers get consolidated invoicing and priority slots.' },
        { name: 'notes', label: 'Notes', type: 'textarea', col: 12, value: c.notes,
          placeholder: 'Preferred contact times, special instructions…' },
      ];
    }

    async function newCustomer() {
      const res = await T.formModal({
        title: 'New customer',
        icon: 'person-plus',
        fields: identityFields(),
        submitLabel: 'Create customer',
      });
      if (!res) return;
      await api.post('/api/customers', res);
      T.toast('Customer created.');
      load('');
    }

    /** Edit an identity record — including the ID, which is often only produced
        at the counter when somebody turns up to collect a car. */
    async function editCustomer(customer) {
      const res = await T.formModal({
        title: `Edit ${customer.name}`,
        icon: 'pencil-square',
        fields: identityFields(customer),
        submitLabel: 'Save changes',
      });
      if (!res) return;
      await api.patch(`/api/customers/${customer.id}`, res);
      T.toast('Customer updated.');
      load('');
    }

    await load('');
    /* /customers?new=1 deep-links straight into the form. */
    if (ctx.query.new === '1') setTimeout(newCustomer, 150);

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Customers')),
        search,
        h('button.btn.btn-brand.btn-sm', { onclick: newCustomer },
          T.icon('person-plus'), ' New customer'),
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
    ctx.title = 'Enquiries and Bookings';
    const host = h('div');
    const meta = T.store.get('meta');
    const labels = (meta || {}).booking_status_labels || {};
    const statusLabel = (code) => labels[code] || code;
    const STATUS_CODES = ['REQUESTED', 'CONFIRMED', 'ATTENDED', 'ARRIVED',
                          'COMPLETED', 'NO_SHOW', 'CANCELLED'];

    /* Where an enquiry came from. The column holds a code — written by the bot,
       by the Flow and by the desk — and this screen used to print it raw, so the
       desk read "whatsapp" beside "phone" and "web". An enquiry the bot took on
       its own is the one worth spotting, so it gets the name and the mark.
       The wording comes from the server so every screen agrees. */
    const sourceLabels = (meta || {}).booking_source_labels || {};
    const sourceLabel = (code) => sourceLabels[code] || code || '';
    const SOURCE_ICONS = {
      whatsapp: 'whatsapp', phone: 'telephone', walkin: 'person-walking',
      web: 'globe', email: 'envelope',
    };
    const SOURCE_CODES = (meta || {}).booking_sources
      || ['whatsapp', 'phone', 'walkin', 'web', 'email'];

    const filters = {
      source: ctx.query.source || '',
      unquoted: ctx.query.unquoted === '1',
    };
    let staffCache = null;

    /* Staff list for the "who attended" picker — fetched once and allowed to
       fail, because front desk may not be permitted to read the user table and
       that must not take the whole screen down with it. */
    async function staffOptions() {
      if (staffCache) return staffCache;
      try { staffCache = (await api.get('/api/users')).items || []; }
      catch (err) { staffCache = []; }
      return staffCache;
    }

    /* The work itself lives in app.js as T.bookingConfirm / T.bookingAttend /
       T.bookingReschedule, so the enquiry bell and this screen can never drift
       apart. These wrappers exist only to refresh the table afterwards. */
    async function confirmBooking(booking) {
      await T.bookingConfirm(booking);
      load();
    }

    async function markAttended(booking) {
      const saved = await T.bookingAttend(booking);
      if (saved) load();
    }

    async function rescheduleBooking(booking) {
      const saved = await T.bookingReschedule(booking);
      if (saved) load();
    }

    /* The desk answering a customer's ask. Lives in app.js with the rest of the
       booking actions, so the bell and this screen offer the same two answers. */
    async function acceptRequest(booking) {
      await T.bookingAcceptReschedule(booking);
      load();
    }

    async function declineRequest(booking) {
      const saved = await T.bookingDeclineReschedule(booking);
      if (saved) load();
    }

    /* The requests, above the table. Nobody opens this screen looking for them,
       and a customer waiting on a time is the most time-sensitive thing on it —
       so they get a banner rather than a hidden row action. */
    function requestBanner(rows) {
      const waiting = rows.filter((r) => r.has_reschedule_request);
      if (!waiting.length) return null;
      return h('div.tc-request-banner', [
        h('div.tc-request-banner-head', [
          T.icon('calendar-week'),
          h('span', `${waiting.length} appointment`
            + `${waiting.length === 1 ? '' : 's'} to move`),
          h('span.tc-request-banner-note',
            'A customer asked — the appointment stays where it is until you agree.'),
        ]),
        h('div.tc-request-banner-rows', waiting.map((b) => h('div.tc-request-banner-row', [
          h('div.flex-fill', [
            h('div.fw-semibold', `${b.customer_name || '—'} · `
              + `${b.display_reference || b.reference}`),
            h('div.small', [
              'Currently ', h('strong', `${dateShort(b.slot_date)}`
                + (b.slot_time ? ` ${b.slot_time}` : '')),
              ' · wants ', h('strong', b.requested_slot_text || 'another time'),
            ]),
            b.requested_note ? h('div.small.fst-italic', `"${b.requested_note}"`) : null,
          ]),
          h('div.d-flex.gap-1.align-items-start', [
            h('button.btn.btn-sm.btn-brand', {
              type: 'button', onclick: () => acceptRequest(b),
            }, 'Do as asked'),
            h('button.btn.btn-sm.btn-outline-secondary', {
              type: 'button', onclick: () => declineRequest(b),
            }, 'Keep as is'),
          ]),
        ]))),
      ]);
    }

    /* Enquiry photos. A customer sends them before any job card exists, so they
       hang off the enquiry — and this pill is the only place the desk sees them
       without opening the chat thread in the inbox. */
    function enquiryPhotos(booking) {
      return T.photoViewer(booking.photos || [], {
        title: `Photos · ${booking.display_reference || booking.reference}`,
        subtitle: booking.customer_name || '',
      });
    }

    async function load() {
      T.mount(host, T.skeletonTable(7, 5));
      const params = new URLSearchParams();
      if (filters.source) params.set('source', filters.source);
      if (filters.unquoted) params.set('unquoted', '1');
      /* Keep the filters in the address bar, or opening an enquiry and coming
         back loses the channel you were working through. */
      T.listState('bookings', params);
      const data = await api.get(`/api/bookings?${params.toString()}`);
      const colour = { REQUESTED: 'warning', CONFIRMED: 'info', ATTENDED: 'success',
                       ARRIVED: 'primary', COMPLETED: 'success', NO_SHOW: 'secondary',
                       CANCELLED: 'danger' };
      T.mount(host, [
        requestBanner(data.items),
        T.dataTable({
        columns: [
          /* A new enquiry only has an enquiry reference. It earns the booking
             reference when it is confirmed, and both stay visible so the
             customer's original number can still be traced. */
          { label: 'Reference', render: (r) => h('div', [
              h('div.fw-semibold', r.display_reference || r.reference),
              h('div.small.text-secondary', r.booking_reference
                ? `Enquiry ${r.reference}` : ''),
              h('div.small.tc-hint', [
                T.icon(SOURCE_ICONS[r.source] || 'question-circle'),
                ` ${sourceLabel(r.source)}`,
              ]),
              T.photoPill(r, { cls: 'mt-1',
                title: 'Damage photos sent with the enquiry',
                onClick: enquiryPhotos }),
            ]) },
          { label: 'Customer', render: (r) => h('div', [h('div', r.customer_name),
              h('div.small.text-secondary', r.customer_phone || '')]) },
          { label: 'Service', render: (r) => h('span.small', r.service) },
          { label: 'Date', render: (r) => h('div', [h('div', dateShort(r.slot_date)),
              h('div.small.text-secondary', r.slot_time || 'Any time'),
              /* Flagged on the row too, so a request is findable by scrolling
                 and not only through the banner. */
              r.has_reschedule_request ? h('div.small.tc-request-tag', [
                T.icon('calendar-week'),
                ` asked for ${r.requested_slot_text || 'another time'}`,
              ]) : null,
            ]) },
          { label: 'Quotation', render: (r) => (r.quotation
              ? h('div', [
                  h('div.fw-semibold', money(r.quotation.total, r.quotation.currency)),
                  h('div.small.text-secondary', r.quotation.reference
                    + (r.quotation.count > 1 ? ` · ${r.quotation.count} versions` : '')),
                  h('div.mt-1', T.badge(r.quotation.status,
                    r.quotation.status === 'APPROVED' ? 'success'
                      : r.quotation.status === 'DECLINED' ? 'danger' : 'secondary')),
                ])
              : h('div', [
                  h('div.small.text-secondary', r.quoted_from > 0
                    ? `From ${money(r.quoted_from)}` : 'Not priced yet'),
                  h('div.small.tc-hint', 'No quotation raised'),
                ])) },
          { label: 'Status', render: (r) => h('div', [
              h(`span.badge.text-bg-${colour[r.status] || 'secondary'}`, statusLabel(r.status)),
              r.outcome_label ? h('div.small.text-secondary.mt-1', r.outcome_label) : null,
            ]) },
          { label: 'Handled by', class: 'd-none d-lg-table-cell', render: (r) =>
              h('span.small', r.attended_by || r.confirmed_by || '—') },
          { label: '', class: 'text-end', render: (r) => h('div.d-flex.gap-1.justify-content-end', [
              r.quotation && r.quotation.status === 'APPROVED' ? null
                : h('button.btn.btn-sm.btn-outline-secondary', {
                    title: r.quotation ? 'Raise another version' : 'Price this enquiry',
                    onclick: (e) => {
                      e.stopPropagation();
                      T.newQuotation({ booking: r, onCreated: () => load() });
                    },
                  }, T.icon('calculator'), r.quotation ? null : ' Quote'),
              r.status === 'REQUESTED' ? h('button.btn.btn-sm.btn-brand', {
                onclick: (e) => { e.stopPropagation(); confirmBooking(r); },
              }, 'Confirm') : null,
              r.status !== 'ATTENDED' && r.status !== 'COMPLETED' && r.status !== 'CANCELLED'
                ? h('button.btn.btn-sm.btn-outline-secondary', {
                    onclick: (e) => { e.stopPropagation(); markAttended(r); },
                  }, 'Attend') : null,
              h('button.btn.btn-sm.btn-outline-secondary', {
                title: 'Reschedule and notify the customer',
                onclick: (e) => { e.stopPropagation(); rescheduleBooking(r); },
              }, T.icon('calendar-week')),
              h('button.btn.btn-sm.btn-outline-secondary', {
                onclick: (e) => { e.stopPropagation(); editBooking(r); },
              }, T.icon('pencil')),
            ]) },
        ],
        rows: data.items,
        onRowClick: (r) => editBooking(r),
        empty: T.emptyState('No enquiries or bookings',
          'Phone, WhatsApp and walk-in requests land here.', 'calendar-check'),
        }),
      ]);
    }

    async function editBooking(booking) {
      const staff = await staffOptions();
      const res = await T.formModal({
        title: `${booking.display_reference || booking.reference}`,
        subtitle: booking.booking_reference
          ? `Confirmed booking · enquiry ${booking.reference}`
          : 'Still an enquiry — confirm it to turn it into a booking.',
        fields: [
          { name: 'status', label: 'Status', type: 'select', col: 6, value: booking.status,
            options: (meta.booking_statuses || STATUS_CODES)
              .map((code) => ({ value: code, label: statusLabel(code) })) },
          { name: 'attended_by_id', label: 'Attended / confirmed by', type: 'select', col: 6,
            value: booking.attended_by_id || booking.confirmed_by_id,
            placeholder: '— Not recorded —',
            options: staff.map((u) => ({
              value: u.id, label: `${u.full_name} · ${u.role_label}` })) },
          { name: 'outcome', label: 'Outcome', type: 'select', col: 6,
            value: booking.outcome || '', placeholder: '— Not recorded —',
            hint: 'Only once the customer has come through.',
            options: (meta.booking_outcomes || [])
              .map((o) => ({ value: o.code, label: o.label })) },
          { name: 'rescheduled_count', label: 'Times moved', type: 'static', col: 6,
            value: String(booking.rescheduled_count || 0) },
          { name: 'slot_date', label: 'Date', type: 'date', col: 3, value: booking.slot_date },
          { name: 'slot_time', label: 'Time', col: 3,
            type: 'select', value: booking.slot_time || '',
            options: ['', ...((meta.booking_slots) || ['08:00', '09:00', '10:00', '11:00',
              '12:00', '13:00', '14:00', '15:00', '16:00'])] },
          { name: 'notes', label: 'Notes', type: 'textarea', col: 12, value: booking.notes || '' },
        ],
        submitLabel: 'Save',
      });
      if (!res) return;
      await api.patch(`/api/bookings/${booking.id}`, res);
      T.toast('Saved.');
      load();
    }

    await load();
    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', h('h1.h4.mb-0', 'Enquiries and Bookings')),
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
                  options: meta.booking_slots || ['08:00', '09:00', '10:00', '11:00',
                    '12:00', '13:00', '14:00', '15:00', '16:00'] },
                { name: 'confirm', label: 'Confirm it now', type: 'switch', col: 12,
                  hint: 'Otherwise it stays an enquiry until somebody confirms it.' },
                { name: 'notes', label: 'Notes', type: 'textarea', col: 12 },
              ],
              submitLabel: 'Book in',
            });
            if (!res) return;
            const customer = customers.items.find((c) => String(c.id) === String(res.customer_id));
            const confirmed = res.confirm === true || res.confirm === 'true' || res.confirm === 'on';
            const saved = await api.post('/api/bookings', {
              ...res, name: customer.name, phone: customer.phone, source: 'phone',
              confirm: confirmed,
            });
            T.toast(confirmed
              ? `Booking ${saved.booking.display_reference} created and confirmed.`
              : `Enquiry ${saved.booking.reference} recorded.`, 'success');
            load();
          },
        }, T.icon('plus-lg'), ' New booking'),
      ]),
      /* Which channel an enquiry arrived on, and whether anybody has priced it.
         Both are the questions the desk actually starts the day with: what came
         in on WhatsApp overnight, and what is still sitting unquoted. */
      h('div.tc-toolbar.mb-3', [
        T.iconSelect({
          value: filters.source, icon: 'broadcast', width: 172, ariaLabel: 'Where it came from',
          onChange: (v) => { filters.source = v; load(); },
          options: [{ value: '', label: 'Every channel' }].concat(
            SOURCE_CODES.map((code) => ({ value: code, label: sourceLabel(code) }))),
        }),
        (() => {
          /* The class has to be right on the first paint too, not only after the
             first click: arriving from a link that already filters would show a
             filter that is on while the button says it is off. */
          const btn = h('button', { type: 'button' },
            T.icon('calculator'), ' Not priced yet');
          const paint = () => {
            btn.className = filters.unquoted
              ? 'btn btn-sm btn-brand' : 'btn btn-sm btn-outline-secondary';
          };
          paint();
          btn.addEventListener('click', () => {
            filters.unquoted = !filters.unquoted;
            paint();
            load();
          });
          return btn;
        })(),
        h('div.tc-toolbar-spacer'),
      ]),
      T.pendingStrip({ match: (url) => /\/bookings/.test(url), label: 'enquiry change' }),
      T.section({ body: host, flush: true }),
    ]);
  });
})();
