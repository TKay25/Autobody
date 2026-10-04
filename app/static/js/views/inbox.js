/* WhatsApp inbox — live threads with human takeover. */
(function () {
  const T = window.TCA;
  const { h, api, dateTime, timeOnly } = T;

  /* ── sizing the chat column ────────────────────────────────────────────
     The message log has to scroll inside the chat panel, not grow the page.
     `calc(100vh - 150px)` was a guess at the chrome, and the guess went stale:
     the wrap stayed 1200px tall while its log grew to 6836px, so the thread
     spilled straight down the page.

     The correct height depends on the top bar and the quick-action strip —
     neither of which is a constant — so measure it instead of guessing.
     ────────────────────────────────────────────────────────────────────── */
  let chatFitBound = false;

  function fitChat() {
    const view = document.querySelector('.tc-chat-view');
    if (!view) return;
    const wrap = view.querySelector('.chat-wrap');
    if (!wrap) return;
    /* Under 860px the columns stack, so a fixed height is wrong. */
    if (window.innerWidth <= 860) { wrap.style.height = ''; return; }
    /* .tc-content's bottom padding, so the wrap never kisses the page edge. */
    const room = window.innerHeight - wrap.getBoundingClientRect().top - 40;
    wrap.style.height = `${Math.max(320, Math.round(room))}px`;
  }

  function bindChatFit() {
    if (chatFitBound) return;
    chatFitBound = true;
    window.addEventListener('resize', () => window.requestAnimationFrame(fitChat));
  }

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

  /* ── message body ──────────────────────────────────────────────────
     The bot sends its confirmations with WhatsApp's own text markup, so a
     booking read `✅ *Booking confirmed*` — asterisks and all. These helpers
     turn the markers into real emphasis and split the body on blank lines, so a
     confirmation scans as title · details · address · instruction instead of
     one grey wall.
     Built from nodes rather than innerHTML, so message text can never inject
     markup into the page.
     ─────────────────────────────────────────────────────────────── */
  const MARKUP = /(\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~|```[^`\n]+```)/g;

  function inlineMarkup(text) {
    const out = [];
    let last = 0;
    let m;
    MARKUP.lastIndex = 0;
    while ((m = MARKUP.exec(text)) !== null) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const token = m[0];
      if (token.startsWith('```')) out.push(h('code', token.slice(3, -3)));
      else if (token[0] === '*') out.push(h('strong', token.slice(1, -1)));
      else if (token[0] === '_') out.push(h('em', token.slice(1, -1)));
      else out.push(h('s', token.slice(1, -1)));
      last = m.index + token.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  /* `Reference: TC-BKG-85F746` and friends. Only worth laying out as a list when
     the block is mostly made of them, so ordinary prose with one colon in it is
     left alone. */
  const KV_LINE = /^([A-Z][A-Za-z0-9 /-]{1,22}):[ \t]+(\S.*)$/;

  function renderBody(raw) {
    const blocks = String(raw || '').split(/\n{2,}/).filter((b) => b.trim());
    return blocks.map((block) => {
      const lines = block.split('\n');
      const kvs = lines.filter((l) => KV_LINE.test(l.trim()));
      if (kvs.length >= 2 && kvs.length >= lines.length - 1) {
        return h('div.msg-kv', lines.map((line) => {
          const hit = KV_LINE.exec(line.trim());
          if (!hit) return h('div.msg-kv-row', h('div.msg-kv-v', inlineMarkup(line)));
          return h('div.msg-kv-row', [
            h('div.msg-kv-k', hit[1]),
            h('div.msg-kv-v', inlineMarkup(hit[2])),
          ]);
        }));
      }
      const parts = [];
      lines.forEach((line, i) => {
        if (i) parts.push(h('br'));
        parts.push(...inlineMarkup(line));
      });
      return h('div.msg-p', parts);
    });
  }

  const initialsOf = (name) => (name || '?')
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0].toUpperCase()).join('') || '?';

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

  /* A read receipt should not look like one that only got delivered, which is what
     a bare ✓✓ string gave us. Returns the icon, its tone and a tooltip. */
  function tick(status) {
    if (status === 'read') return { icon: 'check-all', tone: 'read', title: 'Read' };
    if (status === 'delivered') return { icon: 'check-all', title: 'Delivered' };
    if (status === 'sent') return { icon: 'check', title: 'Sent' };
    /* Stored, never sent — WhatsApp is in simulator mode. Deliberately NOT a tick:
       a delivery receipt for a message that was never handed to Meta is exactly
       how a deployment that answers nobody manages to look healthy. */
    if (status === 'simulated') {
      return { icon: 'slash-circle', tone: 'failed',
               title: 'NOT sent — WhatsApp is in simulator mode' };
    }
    if (status === 'failed') {
      return { icon: 'exclamation-triangle-fill', tone: 'failed', title: 'Failed' };
    }
    return null;
  }

  /* An attachment in the thread. Only pictures can be drawn: a customer sending an
     assessor's PDF used to render as a broken <img>, so anything that is not an
     image becomes a link the operator can actually open. */
  function mediaNode(url) {
    if (/\.(png|jpe?g|webp|gif)(\?|$)/i.test(url || '')) {
      return h('img.img-fluid.rounded.mt-2', {
        src: url, style: 'max-width:220px', loading: 'lazy', alt: 'Attachment',
      });
    }
    let name = 'Attachment';
    try { name = decodeURIComponent((url || '').split('/').pop() || name); } catch (e) { /* keep default */ }
    return h('a.btn.btn-sm.btn-outline-secondary.mt-2', {
      href: url, target: '_blank', rel: 'noopener',
    }, [T.icon('paperclip'), h('span.ms-1', name)]);
  }

  T.route('/inbox', async (ctx) => {
    ctx.title = 'WhatsApp inbox';
    const selectedId = ctx.query.id || null;
    const conversations = await api.get('/api/whatsapp/conversations');

    /* ── left rail ──────────────────────────────────────────────────── */
    const listHost = h('div.chat-list');
    let activeId = selectedId || (conversations.items[0] || {}).id || null;

    /* The header count is live: it is repainted from the same array the rail
       renders from, so the two can never disagree after a refresh. */
    const listSummary = h('div.small.text-secondary');
    function paintSummary() {
      T.mount(listSummary,
        `${conversations.items.length} conversation(s) · `
        + `${conversations.unread_total} unread message(s)`);
    }

    function renderList() {
      paintSummary();
      T.mount(listHost, conversations.items.length
        ? conversations.items.map((c) => {
            const el = h('div.chat-item', {
              class: `chat-item${c.id === activeId ? ' active' : ''}${c.unread ? ' is-unread' : ''}`,
            }, [
              h('span.tc-avatar.chat-avatar', initialsOf(c.display_name)),
              h('div.chat-item-body', [
                h('div.d-flex.justify-content-between.align-items-baseline.gap-2', [
                  h('div.name.text-truncate', c.display_name),
                  h('div.chat-time', T.relTime(c.last_message_at)),
                ]),
                h('div.snippet', snippetOf(c.last_message)),
                h('div.chat-tags', [
                  c.human_takeover ? h('span.chip.is-human', 'You') : h('span.chip', 'Bot'),
                  c.is_session_open
                    ? h('span.chip', 'Session open')
                    : h('span.chip.is-warn', 'Needs template'),
                  c.unread ? h('span.chat-dot', { title: `${c.unread} unread` }) : null,
                ]),
              ]),
            ]);
            el.addEventListener('click', () => {
              if (c.id === activeId) return;
              activeId = c.id;
              /* Swap the selection in place. Routing here re-runs this whole
                 route handler — refetching the conversation list, remounting the
                 left rail and reloading the thread — which reads as a full page
                 refresh every time a conversation is opened. The URL still moves
                 so the thread stays linkable and survives a reload. */
              Array.from(listHost.children).forEach((node, i) => {
                const item = conversations.items[i];
                if (item) node.classList.toggle('active', item.id === activeId);
              });
              window.history.replaceState(null, '', `#/inbox?id=${c.id}`);
              loadThread(c.id);
            });
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
            : h('div.msg-text', renderBody(bodyText(m))),
          /* An attachment. Only pictures can be drawn — a customer sending an
             assessor's PDF used to render as a broken <img>, so anything that is
             not an image becomes a link they can actually open. */
          m.media_url ? mediaNode(m.media_url) : null,
          /* The choices the customer was offered, recorded as they saw them.
             These used to be buttons posting to /api/whatsapp/simulate — i.e.
             they fabricated an inbound tap. Live, that writes a customer action
             into the thread that never happened, which is the worst kind of lie
             to leave in a support log. The record is the useful part; the
             pretending is not. */
          options.length
            ? h('div.bubble-buttons', options.map((b) => h('span.bubble-option', b.title)))
            : null,
          h('div.bubble-foot', [
            m.is_bot ? h('span.bubble-bot', 'BOT') : null,
            h('span.time', timeOnly(m.created_at)),
            direction === 'outbound' && stamp
              ? h('i', {
                  class: `bi bi-${stamp.icon} bubble-tick${stamp.tone ? ` is-${stamp.tone}` : ''}`,
                  title: stamp.title,
                })
              : null,
          ]),
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
        h('div.chat-head', [
          h('span.tc-avatar.chat-avatar', initialsOf(c.display_name)),
          h('div.flex-fill.min-w-0', [
            h('div.fw-bold.text-truncate', c.display_name),
            h('div.chat-head-meta', `+${c.wa_id}`),
          ]),
          c.human_takeover ? h('span.chip.is-human', 'You have it') : h('span.chip', 'Bot answering'),
          c.is_session_open ? h('span.chip', 'Session open') : h('span.chip.is-warn', 'Needs template'),
          c.human_takeover
            ? h('button.btn.btn-sm.btn-outline-secondary', {
                onclick: async () => {
                  await api.post(`/api/whatsapp/conversations/${c.id}/takeover`, { human_takeover: false });
                  T.toast('Bot resumed for this conversation.');
                  loadThread(c.id); refreshList();
                },
              }, T.icon('robot'), ' Hand back')
            : h('button.btn.btn-sm.btn-brand', {
                onclick: async () => {
                  await api.post(`/api/whatsapp/conversations/${c.id}/takeover`, { human_takeover: true });
                  T.toast('You now own this conversation — the bot is paused.', 'warning');
                  loadThread(c.id); refreshList();
                },
              }, T.icon('person'), ' Take over'),
          h('a.btn.btn-sm.btn-outline-success', {
            href: `https://wa.me/${c.wa_id}`, target: '_blank',
            title: 'Open in WhatsApp',
          }, T.icon('box-arrow-up-right')),
        ]),
        log,
        h('div.tc-composer', [replyBox, sendBtn]),
      ]);

      /* Scroll after mounting — before that the log has no height to scroll. */
      log.scrollTop = log.scrollHeight;
      replyBox.focus();
    }

    async function refreshList() {
      const fresh = await api.get('/api/whatsapp/conversations');
      conversations.items = fresh.items;
      conversations.unread_total = fresh.unread_total;
      renderList();
    }

    /* The day book is not repeated here — it lives in the attention panel in
       the top bar, so it is available from every screen rather than only this
       one. See T.newTask / the bell in app.js. */
    renderList();
    await loadThread(activeId);

    bindChatFit();
    /* Is this deployment actually connected? If it is not, say so at the top
       rather than letting the operator believe every customer has been answered.
       Fails soft: a status hiccup must not take the inbox down with it. */
    const waWarn = h('div');
    api.get('/api/whatsapp/status').then((s) => {
      if (s.live) return;
      T.mount(waWarn, h('div.alert.alert-warning.d-flex.gap-2.align-items-start.mb-3', [
        T.icon('exclamation-triangle-fill'),
        h('div', [
          h('div.fw-semibold', 'WhatsApp is in simulator mode — nothing is being sent.'),
          h('div.small', 'Messages arrive and the bot answers, but every reply stops '
            + `here. Missing: ${(s.missing || []).join(', ') || 'unknown'}.`),
        ]),
      ]));
    }).catch(() => {});

    const root = h('div.tc-chat-view', [
      h('div.d-flex.align-items-center.mb-3.flex-wrap.gap-2', [
        h('div.flex-fill', [h('h1.h4.mb-0', 'WhatsApp inbox'), listSummary]),
        h('button.btn.btn-outline-secondary.btn-sm', { onclick: refreshList }, T.icon('arrow-clockwise'), ' Refresh'),
      ]),
      waWarn,
      h('div.chat-wrap', [
        T.section({ body: listHost, flush: true }),
        panelHost,
      ]),
    ]);
    /* Once now, and once after the first paint so the measured top is real. */
    fitChat();
    window.requestAnimationFrame(fitChat);
    return root;
  });
})();
