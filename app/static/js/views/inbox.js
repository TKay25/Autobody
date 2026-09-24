/* WhatsApp inbox — live threads plus a built-in bot simulator. */
(function () {
  const T = window.TCA;
  const { h, api, dateTime, timeOnly } = T;

  /* ── chat rendering helpers ───────────────────────────────────────── */
  const DAY_MS = 24 * 60 * 60 * 1000;

  const dayKey = (iso) => {
    const d = new Date(iso);
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  };

  function dayLabel(iso) {
    const d = new Date(iso);
    const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((midnight(new Date()) - midnight(d)) / DAY_MS);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
    return d.toLocaleDateString(undefined,
      { day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* Historic taps were logged as the raw payload ("[button:a_approve:1]"), which
     told the operator nothing, so they are shown as the action taken. */
  function bodyText(message) {
    const raw = message.body || '';
    const tap = /^\[button:(.+)\]$/.exec(raw.trim());
    if (!tap) return raw;
    return tap[1].split(':')[0].replace(/^[a-z]+_/, '').replace(/_/g, ' ')
      .replace(/^\w/, (ch) => ch.toUpperCase());
  }

  const isTap = (message) => /^\[button:/.test((message.body || '').trim());

  /* A raw payload id makes a useless one-line preview in the rail. */
  function snippetOf(lastMessage) {
    const raw = (lastMessage || '').trim();
    if (!raw) return 'No messages yet';
    const tap = /^\[button:(.+)\]$/.exec(raw);
    if (tap) {
      const name = tap[1].split(':')[0].replace(/^[a-z]+_/, '').replace(/_/g, ' ');
      return `Tapped: ${name}`;
    }
    return raw.length > 90 ? `${raw.slice(0, 90)}…` : raw;
  }

  function tick(status) {
    if (status === 'read') return '✓✓';
    if (status === 'delivered') return '✓✓';
    if (status === 'sent') return '✓';
    if (status === 'failed') return '⚠';
    return '';
  }

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
                  h('div.snippet', snippetOf(c.last_message)),
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
        : T.emptyState('No conversations yet', 'Customer messages appear here.', 'whatsapp'));
    }

    /* ── right pane ─────────────────────────────────────────────────── */
    const panelHost = h('div.flex-fill.d-flex.flex-column');

    async function loadThread(id) {
      if (!id) {
        T.mount(panelHost, T.emptyState('Pick a conversation',
          'Choose a customer from the list.', 'chat-dots'));
        return;
      }
      T.mount(panelHost, T.spinner('Loading conversation…'));
      const data = await api.get(`/api/whatsapp/conversations/${id}`);
      const c = data.conversation;
      c.unread = 0;

      const log = h('div.chat-log');
      let lastDay = null;
      let lastDirection = null;

      c.messages.forEach((m) => {
        const direction = m.direction === 'inbound' ? 'inbound' : 'outbound';
        const key = dayKey(m.created_at);
        if (key !== lastDay) {
          log.appendChild(h('div.chat-day', dayLabel(m.created_at)));
          lastDay = key;
          lastDirection = null;
        }
        const grouped = direction === lastDirection;
        lastDirection = direction;

        const buttons = (m.payload && m.payload.buttons) || [];
        const rows = ((m.payload && m.payload.sections) || [])
          .reduce((acc, s) => acc.concat(s.rows || []), []);
        const options = buttons.length ? buttons : rows;
        const stamp = tick(m.status);

        log.appendChild(h(`div.bubble.${direction}`, {
          class: `bubble ${direction}${m.is_bot ? ' bot' : ''}${grouped ? ' is-grouped' : ''}`,
        }, [
          isTap(m)
            ? h('div.bubble-tap', [T.icon('hand-index-thumb'), h('span', bodyText(m))])
            : h('div.msg-text', bodyText(m)),
          m.media_url
            ? h('img.img-fluid.rounded.mt-2', { src: m.media_url, style: 'max-width:220px' })
            : null,
          options.length
            ? h('div.bubble-buttons', options.map((b) =>
                h('button.btn.btn-outline-secondary.btn-sm', {
                  title: 'Send this option again',
                  onclick: async () => {
                    await api.post('/api/whatsapp/simulate', { wa_id: c.wa_id, interactive_id: b.id });
                    loadThread(c.id);
                    refreshList();
                  },
                }, b.title)))
            : null,
          h('span.time', [timeOnly(m.created_at),
            direction === 'outbound' && stamp ? ` · ${stamp}` : '']),
        ]));
      });

      if (!c.messages.length) {
        log.appendChild(h('div.chat-day', 'No messages yet'));
      }

      const replyBox = h('textarea.form-control', {
        rows: 1, placeholder: 'Type a reply… (Enter to send)', style: 'resize:none',
      });
      const sendBtn = h('button.btn.btn-brand', [T.icon('send')]);
      const send = async () => {
        const body = replyBox.value.trim();
        if (!body || sendBtn.disabled) return;
        sendBtn.disabled = true;
        replyBox.value = '';
        try {
          await api.post(`/api/whatsapp/conversations/${c.id}/reply`, { body });
          await loadThread(c.id);
          refreshList();
        } finally {
          sendBtn.disabled = false;
        }
      };
      sendBtn.addEventListener('click', send);
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
        h('div.p-3.border-top.bg-white', h('div.d-flex.gap-2', [replyBox, sendBtn])),
      ]);

      /* Scroll after mounting — before that the log has no height to scroll. */
      log.scrollTop = log.scrollHeight;
      replyBox.focus();
    }

    async function refreshList() {
      const fresh = await api.get('/api/whatsapp/conversations');
      conversations.items = fresh.items;
      renderList();
    }

    /* ── simulator ──────────────────────────────────────────────────── */
    const simNumber = h('input.form-control.form-control-sm', { value: '+263775550555', placeholder: 'Customer number' });
    const simBody = h('input.form-control.form-control-sm', { placeholder: 'Type what the customer says…' });
    const simOut = h('div.small.bg-body-tertiary.rounded.p-2.mt-2',
      { style: 'white-space:pre-wrap', hidden: true });

    async function simulate(interactiveId) {
      const number = simNumber.value.trim();
      const body = interactiveId ? '' : simBody.value.trim();
      if (!body && !interactiveId) return;
      simBody.value = '';
      try {
        const res = await api.post('/api/whatsapp/simulate', { wa_id: number, body, interactive_id: interactiveId });
        simOut.hidden = false;
        T.mount(simOut, [
          h('div.text-secondary', `state: ${res.state} · ${res.replies_sent} repl${res.replies_sent === 1 ? 'y' : 'ies'}`),
          h('hr.my-2'),
          h('div', res.conversation.messages
            .slice(-Math.max(res.replies_sent || 0, 1))
            .map((m) => h('div.mb-1', m.body))),
        ]);
        await refreshList();
        if (activeId) loadThread(activeId);
      } catch (err) { T.toast(err.message, 'danger'); }
    }

    simBody.addEventListener('keydown', (e) => { if (e.key === 'Enter') simulate(); });

    const simulator = T.section({
      title: 'Bot simulator',
      body: h('div', [
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
    ]);
  });
})();
