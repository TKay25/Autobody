/* WhatsApp inbox — live threads plus a built-in bot simulator. */
(function () {
  const T = window.TCA;
  const { h, api, dateTime, timeOnly } = T;

  T.route('/inbox', async (ctx) => {
    ctx.title = 'WhatsApp inbox';
    const selectedId = ctx.query.id || null;
    const conversations = await api.get('/api/whatsapp/conversations');

    /* ── left rail ──────────────────────────────────────────────────── */
    const listHost = h('div.chat-list');
    let activeId = selectedId || (conversations.items[0] || {}).id || null;

    function renderList() {
      T.mount(listHost, conversations.items.length
        ? conversations.items.map((c) => {
            const el = h('div.chat-item', { class: c.id === activeId ? 'chat-item active' : 'chat-item' }, [
              h('div.d-flex.justify-content-between.align-items-start.gap-2', [
                h('div.flex-fill', [
                  h('div.name', c.display_name),
                  h('div.snippet', c.last_message || '—'),
                ]),
                h('div.text-end', [
                  h('div.small.text-secondary', { style: 'font-size:.68rem' }, T.relTime(c.last_message_at)),
                  c.unread ? h('span.badge.text-bg-success', c.unread) : null,
                ]),
              ]),
              h('div.d-flex.gap-1.mt-1', [
                c.human_takeover ? h('span.badge.text-bg-dark', 'Human') : h('span.chip', c.state),
                c.is_session_open ? h('span.chip', 'session open') : h('span.chip', 'needs template'),
              ]),
            ]);
            el.addEventListener('click', () => { activeId = c.id; T.navigate(`/inbox?id=${c.id}`); });
            return el;
          })
        : T.emptyState('No conversations', 'Send a test message with the simulator.', 'whatsapp'));
    }

    /* ── right pane ─────────────────────────────────────────────────── */
    const panelHost = h('div.flex-fill.d-flex.flex-column');

    async function loadThread(id) {
      if (!id) {
        T.mount(panelHost, T.emptyState('Pick a conversation', 'Or use the simulator to start one.', 'chat-dots'));
        return;
      }
      T.mount(panelHost, T.spinner('Loading conversation…'));
      const data = await api.get(`/api/whatsapp/conversations/${id}`);
      const c = data.conversation;
      c.unread = 0;

      const log = h('div.chat-log');
      c.messages.forEach((m) => {
        const buttons = (m.payload && m.payload.buttons) || [];
        const rows = (m.payload && m.payload.sections || [])
          .reduce((acc, s) => acc.concat(s.rows || []), []);
        log.appendChild(h(`div.bubble.${m.direction === 'inbound' ? 'inbound' : 'outbound'}`,
          { class: (m.is_bot ? 'bubble outbound bot' : null) }, [
            h('div', m.body || ''),
            m.media_url ? h('img.img-fluid.rounded.mt-2', { src: m.media_url, style: 'max-width:220px' }) : null,
            (buttons.length || rows.length)
              ? h('div.bubble-buttons', (buttons.length ? buttons : rows).map((b) =>
                  h('button.btn.btn-outline-secondary.btn-sm', {
                    onclick: async () => {
                      await api.post('/api/whatsapp/simulate', { wa_id: c.wa_id, interactive_id: b.id });
                      loadThread(c.id);
                      refreshList();
                    },
                  }, b.title)))
              : null,
            h('span.time', `${timeOnly(m.created_at)}${m.status && m.status !== 'delivered' ? ' · ' + m.status : ''}`),
          ]));
      });
      log.scrollTop = log.scrollHeight;

      const replyBox = h('textarea.form-control', {
        rows: 1, placeholder: 'Type a reply… (Enter to send)', style: 'resize:none',
      });
      const send = async () => {
        const body = replyBox.value.trim();
        if (!body) return;
        replyBox.value = '';
        await api.post(`/api/whatsapp/conversations/${c.id}/reply`, { body });
        loadThread(c.id);
        refreshList();
      };
      replyBox.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
      });

      T.mount(panelHost, [
        h('div.d-flex.align-items-center.gap-2.p-3.border-bottom.bg-white', [
          h('div.flex-fill', [
            h('div.fw-bold', c.display_name),
            h('div.small.text-secondary', `+${c.wa_id} · state ${c.state}${c.assigned_to ? ' · ' + c.assigned_to : ''}`),
          ]),
          c.human_takeover
            ? h('button.btn.btn-sm.btn-outline-secondary', {
                onclick: async () => {
                  await api.post(`/api/whatsapp/conversations/${c.id}/takeover`, { human_takeover: false });
                  T.toast('Bot resumed for this conversation.');
                  loadThread(c.id); refreshList();
                },
              }, T.icon('robot'), ' Hand back to bot')
            : h('button.btn.btn-sm.btn-brand', {
                onclick: async () => {
                  await api.post(`/api/whatsapp/conversations/${c.id}/takeover`, { human_takeover: true });
                  T.toast('You now own this conversation — the bot is paused.', 'warning');
                  loadThread(c.id); refreshList();
                },
              }, T.icon('person'), ' Take over'),
          h('a.btn.btn-sm.btn-outline-success', { href: `https://wa.me/${c.wa_id}`, target: '_blank' },
            T.icon('box-arrow-up-right')),
        ]),
        log,
        h('div.p-3.border-top.bg-white', h('div.d-flex.gap-2', [replyBox,
          h('button.btn.btn-brand', { onclick: send }, T.icon('send'))])),
      ]);
    }

    async function refreshList() {
      const fresh = await api.get('/api/whatsapp/conversations');
      conversations.items = fresh.items;
      renderList();
    }

    /* ── simulator ──────────────────────────────────────────────────── */
    const simNumber = h('input.form-control.form-control-sm', { value: '+263775550555', placeholder: 'Customer number' });
    const simBody = h('input.form-control.form-control-sm', { placeholder: 'Type what the customer says…' });
    const simOut = h('div.small.bg-body-tertiary.rounded.p-2.mt-2', { style: 'white-space:pre-wrap' },
      'Responses from the bot appear here.');

    async function simulate(interactiveId) {
      const number = simNumber.value.trim();
      const body = interactiveId ? '' : simBody.value.trim();
      if (!body && !interactiveId) return;
      simBody.value = '';
      try {
        const res = await api.post('/api/whatsapp/simulate', { wa_id: number, body, interactive_id: interactiveId });
        const last = res.conversation.messages.slice(-1)[0];
        T.mount(simOut, [
          h('div.text-secondary', `state: ${res.state} · ${res.replies_sent} repl${res.replies_sent === 1 ? 'y' : 'ies'}`),
          h('hr.my-2'),
          h('div', res.conversation.messages.slice(-res.replies_sent || 1).map((m) => h('div.mb-1', m.body))),
        ]);
        await refreshList();
        if (activeId) loadThread(activeId);
      } catch (err) { T.toast(err.message, 'danger'); }
    }

    simBody.addEventListener('keydown', (e) => { if (e.key === 'Enter') simulate(); });

    const simulator = T.section({
      title: 'Bot simulator',
      body: h('div', [
        h('div.small.text-secondary.mb-2',
          'Test the WhatsApp bot without a Meta account — try "hi", "quote", "track TC-2026-0001", "claim" or "book".'),
        h('div.row.g-2', [
          h('div.col-md-4', simNumber), h('div.col-md-6', simBody),
          h('div.col-md-2', h('button.btn.btn-brand.btn-sm.w-100', { onclick: () => simulate() }, 'Send')),
        ]),
        h('div.d-flex.gap-1.flex-wrap.mt-2',
          [['Main menu', 'm_menu'], ['Get a quote', 'm_quote'], ['Track repair', 'm_track'],
           ['My claim', 'm_claim'], ['Talk to a person', 'm_human']]
            .map(([label, id]) => h('button.btn.btn-sm.btn-outline-secondary', {
              onclick: () => simulate(id),
            }, label))),
        simOut,
      ]),
    });

    renderList();
    await loadThread(activeId);

    return h('div', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'WhatsApp inbox'),
          h('div.small.text-secondary',
            `${conversations.items.length} conversation(s) · ${conversations.unread_total} unread message(s)`)]),
        h('button.btn.btn-outline-secondary.btn-sm', { onclick: refreshList }, T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      h('div.chat-wrap', [
        T.section({ body: listHost, flush: true }),
        panelHost,
      ]),
      h('div.mt-3', simulator),
      h('div.mt-3', T.section({
        title: 'How the bot is wired',
        body: h('div.row.g-3.small', [
          h('div.col-md-6', [
            h('div.fw-semibold.mb-1', 'Inbound flow'),
            h('ol.text-secondary.ps-3.mb-0', [
              h('li', 'Meta POSTs to /webhooks/whatsapp'),
              h('li', 'Message is logged against the conversation'),
              h('li', 'IntentRouter resolves the next state and replies'),
              h('li', 'Quotes become Booking + Customer + Vehicle records'),
              h('li', 'Stage changes push proactive notifications back out'),
            ]),
          ]),
          h('div.col-md-6', [
            h('div.fw-semibold.mb-1', 'Supported intents'),
            h('div.d-flex.flex-wrap.gap-1', [
              'get a quote', 'track my repair', 'my claim', 'book a service', 'hours', 'location',
              'services', 'warranty', 'talk to a person', 'stop / start', 'language (EN/SN/ND)',
            ].map((x) => h('span.chip', x))),
          ]),
        ]),
      })),
    ]);
  });
})();
