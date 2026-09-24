/* ==========================================================================
   Topclass Auto Body — front-end micro-framework
   A tiny hyperscript + store + hash-router + API client, so the app behaves
   like a React SPA without a build step or extra dependencies.
   ========================================================================== */
(function () {
  'use strict';
  const TCA = (window.TCA = window.TCA || {});

  /* ── hyperscript ─────────────────────────────────────────────────── */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  /**
   * Parse a tag shorthand into `{ tag, classes, id }`.
   *
   * Accepts the dotted form (`div.card.p-3#main`), space-separated classes
   * (`div.card is-active`), or a mix. Empty class fragments — the kind produced
   * by a template literal like `` `div.item.${maybeEmpty}` `` — are dropped
   * rather than becoming a literal garbage class name.
   */
  function parseTag(tag) {
    const out = { tag: 'div', classes: [], id: null };
    const parts = String(tag).trim().split(/\s+/).filter(Boolean);

    parts.forEach((part, partIndex) => {
      part.split(/(?=[.#])/).filter(Boolean).forEach((token, tokenIndex) => {
        if (token.startsWith('#')) {
          out.id = token.slice(1);
        } else if (token.startsWith('.')) {
          const cls = token.slice(1);
          if (cls) out.classes.push(cls);
        } else if (partIndex === 0 && tokenIndex === 0 && /^[a-zA-Z][a-zA-Z0-9-]*$/.test(token)) {
          out.tag = token;
        } else if (token) {
          out.classes.push(token);
        }
      });
    });

    return out;
  }

  function appendChild(parent, child) {
    if (child === null || child === undefined || child === false || child === true) return;
    if (Array.isArray(child)) { child.forEach((c) => appendChild(parent, c)); return; }
    if (child instanceof Node) { parent.appendChild(child); return; }
    if (typeof child === 'function') { appendChild(parent, child()); return; }
    if (typeof child === 'object' && child.__html !== undefined) {
      parent.insertAdjacentHTML('beforeend', child.__html);
      return;
    }
    parent.appendChild(document.createTextNode(String(child)));
  }

  function h(tag, props, ...children) {
    const { tag: name, classes, id } = parseTag(tag);
    const el = document.createElement(name);

    // Anything that isn't a plain props object is really the first child.
    const isProps = props !== null && typeof props === 'object'
      && !Array.isArray(props) && !(props instanceof Node);
    if (!isProps) {
      children.unshift(props);
      props = {};
    }

    if (classes.length) el.className = classes.join(' ');
    if (id) el.id = id;

    Object.entries(props).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === 'class' || key === 'className') {
        el.className = ((el.className ? el.className + ' ' : '') + value).trim();
      } else if (key === 'style') {
        if (typeof value === 'string') el.style.cssText = value;
        else Object.assign(el.style, value);
      } else if (key === 'dataset') {
        Object.entries(value).forEach(([k, v]) => { el.dataset[k] = v; });
      } else if (key === 'html' || key === '__html') {
        el.innerHTML = value;
      } else if (key.startsWith('on') && typeof value === 'function') {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === 'value' && (name === 'input' || name === 'textarea' || name === 'select')) {
        el.value = value;
      } else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'readonly') {
        el[key] = !!value;
      } else if (key.startsWith('data-') || key === 'role' || key === 'aria-label'
                 || key === 'type' || key === 'href' || key === 'target'
                 || key === 'colspan' || key === 'rowspan' || key === 'placeholder'
                 || key === 'title' || key === 'name' || key === 'for') {
        el.setAttribute(key, value);
      } else {
        try { el[key] = value; } catch (e) { el.setAttribute(key, value); }
      }
    });

    children.forEach((c) => appendChild(el, c));
    return el;
  }

  function frag(...children) { const f = document.createDocumentFragment(); children.forEach((c) => appendChild(f, c)); return f; }
  function mount(target, node) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return null;
    el.innerHTML = '';
    appendChild(el, node);
    return el;
  }
  function html(markup) { return { __html: markup }; }
  function icon(name, cls) { return h('i', { class: `bi bi-${name} ${cls || ''}` }); }

  /* ── store ───────────────────────────────────────────────────────── */
  function createStore(initial) {
    let state = initial || {};
    const subs = new Set();
    return {
      get: (key) => (key === undefined ? state : state[key]),
      set(patch) {
        state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
        subs.forEach((fn) => fn(state));
        return state;
      },
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    };
  }

  /* ── api client ──────────────────────────────────────────────────── */
  /**
   * Connection state is driven by real evidence — failed fetches and the
   * browser's offline event — not by an optimistic navigator.onLine read, which
   * is unreliable inside embedded browsers.
   */
  let connectionDown = false;
  function setConnectionState(online) {
    if (online === !connectionDown) return;
    connectionDown = !online;
    const banner = document.getElementById('connBanner');
    if (banner) banner.classList.toggle('show', !online);
  }

  const store = createStore({
    user: (window.__BOOTSTRAP__ || {}).user,
    meta: (window.__BOOTSTRAP__ || {}).meta || {},
    offline: false,
  });
  TCA.store = store;

  class ApiError extends Error {
    constructor(message, status, body) { super(message); this.status = status; this.body = body; }
  }

  async function request(method, path, body, opts = {}) {
    const init = {
      method,
      headers: {
        Accept: 'application/json',
        'X-Requested-With': 'fetch',
        'X-CSRFToken': window.__CSRF__ || '',
      },
      credentials: 'same-origin',
    };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (err) {
      setConnectionState(false);
      if (!opts.silent) TCA.toast('Network problem — check your connection.', 'danger');
      throw new ApiError('Network error', 0, null);
    }
    setConnectionState(true);
    if (res.status === 401 && !opts.silent) {
      window.location.href = '/login';
      throw new ApiError('Signed out', 401, null);
    }
    const text = await res.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch (e) { json = { raw: text }; }
    if (!res.ok) {
      const msg = (json && (json.message || json.error)) || `Request failed (${res.status})`;
      throw new ApiError(msg, res.status, json);
    }
    return json;
  }

  const api = {
    get: (p, o) => request('GET', p, undefined, o),
    post: (p, b, o) => request('POST', p, b === undefined ? {} : b, o),
    patch: (p, b, o) => request('PATCH', p, b === undefined ? {} : b, o),
    del: (p, o) => request('DELETE', p, undefined, o),
  };
  TCA.api = api;

  /* ── formatting helpers ──────────────────────────────────────────── */
  function money(value, currency) {
    const n = Number(value || 0);
    const s = n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return currency ? `${currency} ${s}` : s;
  }
  function dateShort(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  function dateTime(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function timeOnly(value) {
    if (!value) return '';
    const d = new Date(value);
    return isNaN(d) ? '' : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  }
  function relTime(value) {
    if (!value) return '';
    const diff = (Date.now() - new Date(value).getTime()) / 1000;
    if (isNaN(diff)) return '';
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return dateShort(value);
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function src(obj, path) { return path.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), obj); }
  function debounce(fn, ms) {
    let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms || 300); };
  }

  /* ── Toasts ──────────────────────────────────────────────────────── */
  const TOAST_STYLE = {
    success: { icon: 'check-lg', label: 'Done' },
    danger: { icon: 'exclamation-octagon', label: 'Problem' },
    warning: { icon: 'exclamation-triangle', label: 'Heads up' },
    info: { icon: 'info-lg', label: 'For your information' },
    brand: { icon: 'bell', label: 'Update' },
  };

  /**
   * Show a toast.
   *
   * @param {string} message  body copy; `**bold**` is rendered
   * @param {string} variant  success | danger | warning | info | brand
   * @param {object|number} opts  { title, timeout, action: { label, run } } or a delay in ms
   */
  function toast(message, variant = 'success', opts = {}) {
    const host = document.getElementById('toastHost');
    if (!host) return null;

    const options = typeof opts === 'number' ? { timeout: opts } : (opts || {});
    const style = TOAST_STYLE[variant] || TOAST_STYLE.info;
    const timeout = options.timeout === undefined ? 4200 : options.timeout;

    const el = h(`div.toast-tc.toast-tc-${variant}`, { role: 'status' }, []);
    el.appendChild(h('span.toast-tc-icon', icon(style.icon)));
    el.appendChild(h('div.toast-tc-body', [
      h('div.toast-tc-title', options.title || style.label),
      h('div.toast-tc-msg', { html: String(message).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') }),
      options.action
        ? h('button.toast-tc-action', {
            type: 'button',
            onclick: () => { options.action.run(); dismiss(); },
          }, options.action.label)
        : null,
    ]));

    const closeBtn = h('button.toast-tc-close', {
      type: 'button', 'aria-label': 'Dismiss notification', onclick: () => dismiss(),
    }, icon('x-lg'));
    el.appendChild(closeBtn);

    let bar = null;
    if (timeout) {
      bar = h('div.toast-tc-bar', { style: `animation-duration:${timeout}ms` });
      el.appendChild(bar);
    }

    host.prepend(el);
    requestAnimationFrame(() => el.classList.add('show'));

    let timer = null;
    let dismissed = false;

    function startTimer() {
      if (!timeout || dismissed) return;
      timer = setTimeout(dismiss, timeout);
      if (bar) bar.style.animationPlayState = 'running';
    }
    function pauseTimer() {
      if (timer) clearTimeout(timer);
      if (bar) bar.style.animationPlayState = 'paused';
    }
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      if (timer) clearTimeout(timer);
      el.classList.remove('show');
      el.classList.add('hide');
      setTimeout(() => el.remove(), 260);
    }

    el.addEventListener('mouseenter', pauseTimer);
    el.addEventListener('mouseleave', startTimer);
    el.addEventListener('focusin', pauseTimer);
    el.addEventListener('focusout', startTimer);

    startTimer();
    // Keep at most 4 on screen so a burst of updates cannot cover the UI.
    Array.from(host.children).slice(4).forEach((old) => {
      old.classList.remove('show');
      setTimeout(() => old.remove(), 260);
    });
    return { dismiss, el };
  }
  TCA.toast = toast;

  /* ── Modals ──────────────────────────────────────────────────────── */
  const SIZES = { sm: 'modal-sm', md: 'modal-md', lg: 'modal-lg', xl: 'modal-xl', full: 'modal-full' };
  const ACCENTS = {
    brand: 'text-bg-brand', danger: 'text-bg-danger', warning: 'text-bg-warning',
    success: 'text-bg-success', info: 'text-bg-info', primary: 'text-bg-primary',
  };

  function modalNode() {
    const el = document.getElementById('tcaModal');
    return { el, instance: bootstrap.Modal.getOrCreateInstance(el, { backdrop: true, keyboard: true }) };
  }

  /**
   * Open the global modal.
   *
   * Every dialog reuses one `#tcaModal` element, so when one closes and another
   * opens straight away (invoice → record payment) the closing animation would
   * otherwise hide the newly opened dialog. A token makes the newest request
   * win and defers the reveal until the outgoing dialog has finished hiding.
   *
   * @param {object} options
   * @param {string} options.title
   * @param {Node}   options.body
   * @param {Node[]} options.footer
   * @param {string} options.size    sm | md | lg | xl | full
   * @param {string} options.icon    bootstrap icon name for the header well
   * @param {string} options.accent  brand | danger | warning | success | info | primary
   * @param {string} options.subtitle
   */
  let modalToken = 0;

  function modal({ title, body, footer, size, icon: iconName, accent, subtitle }) {
    const { el, instance } = modalNode();

    el.querySelector('.modal-dialog').className =
      `modal-dialog modal-dialog-centered modal-dialog-scrollable ${SIZES[size] || SIZES.lg}`;
    el.querySelector('.modal-title').textContent = title || '';

    const subtitleEl = el.querySelector('#tcaModalSubtitle');
    subtitleEl.textContent = subtitle || '';
    subtitleEl.hidden = !subtitle;

    const iconEl = el.querySelector('#tcaModalIcon');
    if (iconName) {
      iconEl.className = `tc-modal-icon ${ACCENTS[accent] || 'text-bg-brand'}`;
      iconEl.innerHTML = '';
      appendChild(iconEl, icon(iconName));
      iconEl.hidden = false;
    } else {
      iconEl.hidden = true;
    }

    const bodyEl = el.querySelector('.modal-body');
    bodyEl.scrollTop = 0;
    mount(bodyEl, body);

    const footerEl = el.querySelector('.modal-footer');
    footerEl.innerHTML = '';
    if (footer && footer.length) {
      appendChild(footerEl, footer);
    } else {
      footerEl.appendChild(h('button.btn.btn-outline-secondary.btn-sm', {
        type: 'button', 'data-bs-dismiss': 'modal',
      }, 'Close'));
    }

    const token = ++modalToken;
    const reveal = () => { if (token === modalToken) instance.show(); };

    // Mid-hide: wait for the outgoing dialog so it cannot hide us on the way out.
    const midHide = instance._isShown === false && instance._isTransitioning === true;
    if (midHide) {
      el.addEventListener('hidden.bs.modal', reveal, { once: true });
    } else {
      reveal();
    }

    return { el, instance, close: () => instance.hide() };
  }

  function closeModal() {
    const el = document.getElementById('tcaModal');
    if (el) bootstrap.Modal.getOrCreateInstance(el).hide();
  }

  /**
   * Confirmation dialog with an icon well and colour-coded action button.
   * Resolves true when confirmed, false otherwise.
   *
   * opts.subtitle  override the caption under the title. When omitted, a
   *                warning is shown only for destructive variants so that
   *                benign actions ("send", "notify") do not look dangerous.
   */
  function confirmDialog({ title, message, confirmLabel = 'Confirm', variant = 'danger',
                           detail, icon: iconName, subtitle }) {
    const warning = subtitle !== undefined
      ? subtitle
      : (['danger', 'warning'].includes(variant) ? 'This action cannot be undone.' : null);
    return new Promise((resolve) => {
      const btn = h(`button.btn.btn-${variant}.btn-sm`, { type: 'button' }, confirmLabel);
      const m = modal({
        title: title || 'Are you sure?',
        subtitle: warning,
        size: 'sm',
        icon: iconName || (variant === 'danger' ? 'exclamation-triangle' : 'patch-question'),
        accent: variant,
        body: h('div', [
          h('p.mb-0', message),
          detail ? h('div.small.text-secondary.mt-2', detail) : null,
        ]),
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm', {
            type: 'button', 'data-bs-dismiss': 'modal',
          }, 'Cancel'),
          btn,
        ],
      });
      let answered = false;
      btn.addEventListener('click', () => { answered = true; m.close(); resolve(true); });
      btn.focus();
      m.el.addEventListener('hidden.bs.modal', () => { if (!answered) resolve(false); }, { once: true });
    });
  }

  /**
   * A select with an "Other…" escape hatch.
   *
   * Picking "Other" swaps in a free-text input, so an unlisted make or colour
   * is never a dead end. A hidden input carries the value, which keeps
   * `FormData` seeing exactly one field with the expected name.
   *
   * @returns {{node: Node, read: Function, select: Node, setOptions: Function}}
   */
  function comboField({ name, options = [], value = '', placeholder, otherLabel,
                        small = true, disabled = false, swatch = null, onChange,
                        chooseLabel = '— Choose —' } = {}) {
    const hidden = h('input', { type: 'hidden', name, value: value || '' });
    const free = h(`input.form-control${small ? '.form-control-sm' : ''}`, {
      hidden: true, 'aria-label': `${name} (other)`,
      placeholder: placeholder || 'Type it in',
    });
    const dot = swatch ? h('span.tc-swatch') : null;

    const paint = () => {
      if (!dot) return;
      const key = hidden.value;
      const found = (options || []).find((o) => String(o.value ?? o) === String(key));
      dot.style.background = (found && found.swatch) || '#e5ebf5';
      dot.style.visibility = found ? 'visible' : 'hidden';
    };

    const select = h(`select.form-select${small ? '.form-select-sm' : ''}`, {
      disabled,
      'aria-label': name,
      onchange: (e) => {
        if (e.target.value === '__other__') {
          free.hidden = false;
          free.value = '';
          hidden.value = '';
          free.focus();
        } else {
          free.hidden = true;
          free.value = '';
          hidden.value = e.target.value;
        }
        paint();
        if (onChange) onChange(hidden.value);
      },
    }, buildOptions(options, value, otherLabel, chooseLabel));

    free.addEventListener('input', () => {
      hidden.value = free.value;
      if (onChange) onChange(hidden.value);
    });

    const picker = searchableSelect(select, {
      placeholder: swatch ? 'Search colour…' : 'Search…',
      ariaLabel: `Search ${name}`,
      lead: dot,
    });
    const node = h('div.tc-combo', [picker.node, free, hidden]);
    paint();

    return {
      node,
      select,
      read: () => hidden.value,
      /** Swap the option list (used for a Make → Model cascade). */
      setOptions(list, { keep = false } = {}) {
        const current = keep ? hidden.value : '';
        select.innerHTML = '';
        appendChild(select, buildOptions(list, current, otherLabel, chooseLabel));
        /* The select now holds the full new list, so re-read it before filtering. */
        if (picker.search.value) picker.search.value = '';
        picker.resync();
        if (current && !select.value) { free.hidden = false; free.value = current; hidden.value = current; }
        paint();
      },
    };
  }

  function buildOptions(options, value, otherLabel, chooseLabel) {
    const nodes = [h('option', { value: '' }, chooseLabel)];
    (options || []).forEach((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      nodes.push(h('option', {
        value: opt.value, selected: String(opt.value) === String(value),
      }, opt.label ?? opt.value));
    });
    nodes.push(h('option', { value: '__other__' }, otherLabel || 'Other (type it in)'));
    return nodes;
  }

  /* ── searchable dropdowns ────────────────────────────────────────────
     A real combobox: the visible control is a text box, and typing filters the
     option list *in the list itself*. An earlier version put a filter row above
     a native <select>, which silently did nothing on a phone — a native select
     opens the OS picker, where an inline filter cannot reach. Here the options
     are ordinary buttons, so search works with a finger, a mouse or a keyboard.

     The native <select> is kept in the form but hidden: it stays the single
     source of value, so `name`, `FormData`, `formValue()` and the required-field
     validation all keep working untouched. Picking dispatches a real `change`
     event on it, which is what the intake's customer handler, the Make → Model
     cascade and the form's error-clearing all listen for.
     ─────────────────────────────────────────────────────────────────── */

  /** Read a <select> into plain specs, keeping any <optgroup> structure. */
  function readSelectSpecs(select) {
    const groups = [];
    let current = null;
    Array.from(select.children).forEach((child) => {
      if (child.tagName === 'OPTGROUP') {
        current = { label: child.label, options: [] };
        groups.push(current);
      } else if (child.tagName === 'OPTION') {
        if (!current) { current = { label: null, options: [] }; groups.push(current); }
        current.options.push({
          value: child.value, label: child.textContent, disabled: !!child.disabled,
        });
      }
    });
    return groups;
  }

  /**
   * Turn a <select> into a searchable combobox.
   *
   * @returns {{node: Node, search: Node, inputId: string, resync: Function}}
   */
  function searchableSelect(select, { placeholder = 'Search…', ariaLabel, lead } = {}) {
    let groups = readSelectSpecs(select);

    /* Value carrier only. `display:none` still submits, and still reads through
       formValue(), so nothing downstream has to know this is a combobox. */
    select.classList.add('d-none');
    select.setAttribute('tabindex', '-1');

    const flat = () => groups.flatMap((g) => g.options);
    const labelOf = (value) => {
      const found = flat().find((o) => String(o.value) === String(value));
      return found ? found.label : '';
    };

    const input = h('input.form-control.form-control-sm.tc-cbo-input', {
      type: 'text', autocomplete: 'off', spellcheck: 'false',
      role: 'combobox', 'aria-expanded': 'false', 'aria-autocomplete': 'list',
      'aria-label': ariaLabel || 'Choose an option',
      placeholder,
    });
    if (select.id) input.id = `${select.id}__combo`;

    const list = h('div.tc-cbo-list', { hidden: true, role: 'listbox' });
    const node = h(`div.tc-cbo${lead ? '.has-lead' : ''}`, [
      h('div.tc-cbo-control', [lead || null, input,
        h('i.bi.bi-chevron-expand.tc-cbo-caret')]),
      list,
      select,
    ]);

    let items = [];
    let active = -1;

    const showLabel = () => { input.value = labelOf(select.value); };

    function highlight(index) {
      items.forEach((it) => it.el.classList.remove('is-active'));
      if (index < 0 || index >= items.length) return;
      items[index].el.classList.add('is-active');
      items[index].el.scrollIntoView({ block: 'nearest' });
    }

    function render(term) {
      const needle = (term || '').trim().toLowerCase();
      const matches = (o) => !needle
        || String(o.label).toLowerCase().includes(needle)
        || String(o.value).toLowerCase().includes(needle);
      list.innerHTML = '';
      items = [];

      groups.forEach((group) => {
        const opts = group.options.filter(matches);
        if (!opts.length) return;
        if (group.label) list.appendChild(h('div.tc-cbo-group', group.label));
        opts.forEach((o) => {
          const chosen = String(o.value) === String(select.value);
          const el = h('button.tc-cbo-opt', {
            type: 'button', role: 'option', disabled: o.disabled,
            'aria-selected': chosen ? 'true' : 'false',
            class: chosen ? 'is-selected' : null,
          }, o.label);
          /* Keep focus on the input so the blur-close does not fire mid-pick. */
          el.addEventListener('mousedown', (e) => e.preventDefault());
          el.addEventListener('click', () => pick(o.value));
          list.appendChild(el);
          items.push({ value: o.value, disabled: o.disabled, el });
        });
      });

      if (!items.length) list.appendChild(h('div.tc-cbo-empty', 'No match'));
      active = items.findIndex((it) => String(it.value) === String(select.value));
      highlight(active);
    }

    function open() {
      if (!list.hidden) return;
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      render('');
      input.select();
    }

    function close() {
      if (list.hidden) return;
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      showLabel();
    }

    function pick(value) {
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      close();
    }

    input.addEventListener('focus', open);
    input.addEventListener('click', open);
    input.addEventListener('input', () => { if (list.hidden) open(); render(input.value); });
    /* A blur can land after a click on an option, so let the click win first. */
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { close(); input.blur(); return; }
      if (e.key === 'Enter') {
        /* Enter belongs to the dropdown, not to the surrounding form. */
        e.preventDefault();
        if (!list.hidden && items[active]) pick(items[active].value);
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) { open(); return; }
        const step = e.key === 'ArrowDown' ? 1 : -1;
        let next = active + step;
        while (next >= 0 && next < items.length && items[next].disabled) next += step;
        if (next >= 0 && next < items.length) { active = next; highlight(next); }
      }
    });

    /* A value set from elsewhere (a cascade rebuilding the list) must not leave
       the panel open over stale options. */
    select.addEventListener('change', () => { if (!list.hidden) close(); });

    showLabel();
    return {
      node,
      search: input,
      inputId: input.id,
      /** Re-read the option list after the caller has rebuilt it. */
      resync() {
        groups = readSelectSpecs(select);
        if (!list.hidden) render('');
        showLabel();
      },
    };
  }

  /** Standard helpers for the vehicle reference lists served by /api/meta. */
  function vehicleOptions() {
    const meta = TCA.store.get('meta') || {};
    return {
      makes: (meta.vehicle_makes || []).map((m) => ({ value: m, label: m })),
      modelsFor: (make) => {
        const table = meta.vehicle_models || {};
        const list = table[make];
        const source = list && list.length ? list : (meta.vehicle_models_common || []);
        return source.map((m) => ({ value: m, label: m }));
      },
      colours: (meta.vehicle_colours || []).map((c) => ({
        value: c.value, label: c.value, swatch: c.swatch,
      })),
    };
  }

  /**
   * Build a form inside a modal.
   *
   * fields: [{
   *   name, label, type, options, required, value, placeholder, col, help,
   *   icon, affix, min, max, step, rows, disabled, hint
   * }]
   *
   * type — text · email · tel · url · number · money · password · date · time
   *        search · textarea · select · combo · checkbox · switch · radio
   *        segmented · options · static
   *
   * `optgroups` — for a grouped (native) select.
   * `combo`     — a select with an "Other…" escape hatch, plus an optional
   *               `swatch` colour dot. `onComboChange(value, name)` fires on
   *               every change, which is how Make → Model cascades.
   */
  function formModal({ title, fields, values = {}, submitLabel = 'Save', size, intro,
                       icon: headerIcon, accent, validate }) {
    return new Promise((resolve) => {
      const refs = {};
      const gates = {};
      const combos = {};
      /* Handed to `onComboChange` so one combo can repopulate another. */
      const comboApi = {
        setOptions: (name, list) => { if (combos[name]) combos[name].setOptions(list); },
        get: (name) => (combos[name] ? combos[name].read() : ''),
      };
      const initial = (f) => (values[f.name] !== undefined ? values[f.name]
        : (f.value !== undefined ? f.value : ''));
      const optionList = (f) => (f.options || []).map(
        (o) => (typeof o === 'string' ? { value: o, label: o } : o));
      /* Labels may arrive as "Name *" from older call sites; the required
         marker is rendered as its own element so it can be styled. */
      const labelText = (f) => String(f.label || f.name || '').replace(/\s*\*\s*$/, '');

      /** Wrap a control with its label, hint and inline error text. */
      function shell(f, control, { labelFor, bare } = {}) {
        const required = !!f.required;
        const hint = f.help || f.hint;
        const field = h('div.tc-field', [
          f.label && !bare
            ? h('label.tc-label', { for: labelFor || null }, labelText(f),
                required ? h('span.req', { title: 'Required' }, '*') : null)
            : null,
          control,
          hint && !bare ? h('div.tc-hint', hint) : null,
          h('div.tc-error', [h('i.bi.bi-exclamation-circle'), h('span.err-text')]),
        ]);
        const slot = h(`div.col-${f.col || 6}`, field);
        gates[f.name] = {
          field,
          slot,
          fail(message) {
            field.classList.add('has-error');
            field.querySelector('.err-text').textContent =
              message || `${labelText(f) || 'This field'} is required.`;
          },
          clear() { field.classList.remove('has-error'); },
        };
        return slot;
      }

      const rows = fields.map((f) => {
        const start = initial(f);
        const common = {
          id: `f_${f.name}`, name: f.name, disabled: !!f.disabled,
          placeholder: f.placeholder || '',
          'aria-required': f.required ? 'true' : undefined,
        };
        /* A leading icon and/or a trailing affix ("USD", "hrs") shares a wrapper. */
        const iconWrap = (input) => {
          if (!f.icon && !f.affix) return input;
          return h('div.tc-input-icon', {
            class: [f.icon ? 'has-icon' : null, f.affix ? 'has-affix' : null,
                    f.affixAlign === 'left' ? 'affix-left' : null].filter(Boolean).join(' '),
          }, [
            f.icon ? h(`i.bi.bi-${f.icon}`) : null,
            f.affix ? h('span.tc-affix', f.affix) : null,
            input,
          ]);
        };

        /* — readonly presentation — */
        if (f.type === 'static') {
          refs[f.name] = { value: start };
          return shell(f, h('div.form-control-plaintext.py-0.fw-semibold',
            { style: 'min-height:auto' }, start === '' || start == null ? '—' : String(start)));
        }

        /* — switch: a full-width settings row — */
        if (f.type === 'switch') {
          const input = h('input.form-check-input', { ...common, type: 'checkbox', checked: !!start });
          refs[f.name] = input;
          return shell(f, h('label.tc-switch-row.form-switch', { for: common.id }, [
            h('div.tc-switch-text', [
              h('div.tc-switch-title', labelText(f)),
              f.help ? h('div.tc-switch-sub', f.help) : null,
            ]),
            input,
          ]), { bare: true });
        }

        /* — checkbox — */
        if (f.type === 'checkbox') {
          const input = h('input.form-check-input', { ...common, type: 'checkbox', checked: !!start });
          refs[f.name] = input;
          return shell(f, h('div.form-check.pt-1', [
            input,
            h('label.form-check-label.small.fw-semibold', { for: common.id }, labelText(f)),
          ]), { bare: true });
        }

        /* — option cards with real radios underneath — */
        if (f.type === 'options' || f.type === 'radio') {
          const opts = optionList(f);
          const cards = opts.map((o) => {
            const radio = h('input.form-check-input', {
              id: `f_${f.name}_${o.value}`, name: f.name, type: 'radio',
              value: o.value, checked: String(o.value) === String(start), disabled: !!f.disabled,
            });
            refs[f.name] = refs[f.name] || radio;
            const card = h('label.tc-option', {
              class: String(o.value) === String(start) ? 'is-checked' : null,
              for: `f_${f.name}_${o.value}`,
            }, [
              f.type === 'radio' ? null : radio,
              h('div.tc-option-body', [
                h('div.tc-option-title', o.label),
                o.description ? h('div.tc-option-desc', o.description) : null,
              ]),
              f.type === 'options' ? radio : null,
              o.badge ? h(`span.badge.text-bg-${o.badge.tone || 'secondary'}`, o.badge.text) : null,
            ]);
            radio.addEventListener('change', () => {
              cards.forEach((c) => c.classList.remove('is-checked'));
              card.classList.add('is-checked');
            });
            return card;
          });
          return shell(f, h('div.tc-option-list', cards), { bare: true });
        }

        /* — segmented control (still posts a real value) — */
        if (f.type === 'segmented') {
          const hidden = h('input', { type: 'hidden', name: f.name, value: start });
          refs[f.name] = hidden;
          const buttons = optionList(f).map((o) => {
            const btn = h('button', {
              type: 'button', value: o.value,
              class: String(o.value) === String(start) ? 'is-active' : null,
              onclick: () => {
                buttons.forEach((b) => b.classList.remove('is-active'));
                btn.classList.add('is-active');
                hidden.value = o.value;
              },
            }, o.icon ? [h(`i.bi.bi-${o.icon}`), ` ${o.label}`] : o.label);
            return btn;
          });
          return shell(f, h('div.tc-segmented', { role: 'group' }, buttons));
        }

        /* — textarea — */
        if (f.type === 'textarea') {
          const input = h('textarea.form-control.form-control-sm', { ...common, rows: f.rows || 3 }, start);
          refs[f.name] = input;
          return shell(f, input, { labelFor: common.id });
        }

        /* — select with an "Other…" escape hatch — */
        if (f.type === 'combo') {
          const combo = comboField({
            name: f.name,
            options: optionList(f),
            value: start,
            placeholder: f.otherPlaceholder || f.placeholder,
            otherLabel: f.otherLabel,
            disabled: f.disabled,
            swatch: !!f.swatch,
            chooseLabel: f.chooseLabel,
            onChange: (v) => f.onComboChange && f.onComboChange(v, f.name, comboApi),
          });
          refs[f.name] = { value: start, combo, focus: () => combo.select.focus() };
          combos[f.name] = combo;
          return shell(f, combo.node, { labelFor: null });
        }

        /* — select (flat or grouped) — */
        if (f.type === 'select') {
          const groups = f.optgroups
            ? f.optgroups.map((g) => h('optgroup', { label: g.label },
                optionList(g).map((o) => h('option', {
                  value: o.value, selected: String(o.value) === String(start) }, o.label))))
            : null;
          const input = h('select.form-select.form-select-sm', common,
            f.placeholder ? h('option', { value: '' }, f.placeholder) : null,
            groups || optionList(f).map((o) => h('option', {
              value: o.value, selected: String(o.value) === String(start) }, o.label)));
          refs[f.name] = input;
          const picker = searchableSelect(input, {
            placeholder: f.searchPlaceholder || 'Search…',
            ariaLabel: `Search ${labelText(f)}`,
          });
          return shell(f, iconWrap(picker.node), { labelFor: picker.inputId || common.id });
        }

        /* — everything else is an <input> — */
        const isNumber = f.type === 'number' || f.type === 'money';
        const input = h('input.form-control.form-control-sm', {
          ...common,
          type: isNumber ? 'number' : (f.type || 'text'),
          value: start,
          min: f.min, max: f.max, step: f.step || (isNumber ? '0.01' : undefined),
          inputmode: isNumber ? 'decimal' : undefined,
          class: isNumber ? 'form-control form-control-sm.no-spin' : null,
        });
        refs[f.name] = input;
        return shell(f, iconWrap(input), { labelFor: common.id });
      });

      const formId = `tca-form-${Math.random().toString(36).slice(2, 9)}`;
      const form = h('form.row.g-3', { id: formId, novalidate: true }, rows);
      const submit = h('button.btn.btn-brand.btn-sm.fw-semibold', { type: 'submit' },
        [h('i.bi.bi-check2.me-1'), submitLabel]);
      /* The footer sits outside the <form>, so a submit button there is orphaned
         and clicking it does nothing at all — which made every formModal in the
         app quietly unsubmittable. `form` is a read-only property on
         HTMLButtonElement, so it has to be set as an attribute rather than
         through h()'s property assignment. */
      submit.setAttribute('form', formId);

      const m = modal({
        title,
        subtitle: intro,
        icon: headerIcon || 'pencil-square',
        accent: accent || 'brand',
        size: size || 'lg',
        body: form,
        footer: [
          h('button.btn.btn-outline-secondary.btn-sm', { type: 'button', 'data-bs-dismiss': 'modal' }, 'Cancel'),
          submit,
        ],
      });

      /* Clear a field's error the moment the operator fixes it. */
      Object.entries(refs).forEach(([name, el]) => {
        const gate = gates[name];
        if (!gate) return;
        if (el && el.combo) {
          el.combo.node.addEventListener('change', () => gate.clear());
          el.combo.node.addEventListener('input', () => gate.clear());
          return;
        }
        if (!el || !el.addEventListener) return;
        const evt = el.type === 'checkbox' || el.type === 'radio' || el.tagName === 'SELECT'
          ? 'change' : 'input';
        el.addEventListener(evt, () => gate.clear());
      });

      let answered = false;
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const out = {};
        let firstBad = null;
        let ok = true;

        fields.forEach((f) => {
          const el = refs[f.name];
          let v;
          if (f.type === 'static') v = el.value;
          else if (f.type === 'combo') v = el.combo.read().trim();
          else if (f.type === 'switch' || f.type === 'checkbox') v = el.checked;
          else if (f.type === 'options' || f.type === 'radio') {
            v = form.querySelector(`[name="${f.name}"]:checked`)?.value ?? '';
          } else if (f.type === 'number' || f.type === 'money') {
            v = el.value === '' ? null : Number(el.value);
          } else v = el.value.trim();

          const gate = gates[f.name];
          if (f.required && (v === '' || v === null || v === undefined)) {
            gate.fail(f.requiredMessage);
            if (!firstBad) firstBad = el;
            ok = false;
          } else {
            gate.clear();
          }
          out[f.name] = v;
        });

        /* Cross-field rules (matching passwords, a date after another…).
           Running here rather than after the dialog closes keeps the
           operator's typing on screen when something is wrong. */
        if (typeof validate === 'function') {
          const errors = validate(out) || {};
          Object.entries(errors).forEach(([name, message]) => {
            if (!message) return;
            if (gates[name]) gates[name].fail(message);
            if (!firstBad) firstBad = refs[name];
            ok = false;
          });
        }

        if (!ok) {
          toast('Please complete the highlighted fields.', 'warning');
          const target = firstBad && firstBad.type !== 'hidden'
            && !firstBad.classList.contains('d-none')
            ? firstBad
            : firstBad?.closest('.tc-field')
                ?.querySelector('input:not([type=hidden]),select:not(.d-none),textarea,button');
          target?.focus();
          return;
        }
        answered = true;
        m.close();
        resolve(out);
      });

      m.el.addEventListener('hidden.bs.modal', () => { if (!answered) resolve(null); }, { once: true });
      setTimeout(() => {
        const first = form.querySelector('input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled)');
        if (first) first.focus();
      }, 380);
    });
  }

  function badge(text, colour) { return h(`span.badge.text-bg-${colour || 'secondary'}`, text); }
  function stageBadge(stage, label, colour) {
    const c = colour || (TCA.stageColours || {})[stage] || 'secondary';
    return h(`span.badge.text-bg-${c}`, label || stage);
  }
  function priorityBadge(priority) {
    const c = { LOW: 'secondary', NORMAL: 'info', HIGH: 'warning', URGENT: 'danger' }[priority] || 'secondary';
    return h(`span.badge.text-bg-${c}`, priority);
  }
  function emptyState(title, subtitle, iconName) {
    return h('div.empty-state', [h('div', icon(iconName || 'inbox')),
      h('div.fw-semibold.text-secondary', title),
      subtitle ? h('div.small', subtitle) : null]);
  }
  function skeletonTable(rows = 6, cols = 5) {
    return h('div.p-3', Array.from({ length: rows }, () =>
      h('div.d-flex.gap-3.mb-3', Array.from({ length: cols }, () =>
        h('div.skeleton.flex-fill', { style: 'height:16px' })))));
  }
  function spinner(text) {
    return h('div.d-flex.align-items-center.gap-2.text-secondary.p-4',
      h('div.spinner-border.spinner-border-sm.text-warning', { role: 'status' }), text || 'Loading…');
  }

  /** The standard list-screen filter: a search box with a leading icon. */
  function searchInput({ placeholder, oninput, value = '', width = 300 } = {}) {
    return h('div.tc-input-icon.has-icon',
      { style: `flex:1 1 ${width}px;max-width:${width + 60}px` },
      h('i.bi.bi-search'),
      h('input.form-control.form-control-sm', {
        type: 'search', placeholder, value, oninput, 'aria-label': placeholder || 'Search',
      }));
  }

  /** A filter select with its own icon well, for toolbars. */
  function iconSelect({ options = [], value = '', onChange, icon = 'funnel',
                        width = 160, ariaLabel } = {}) {
    const select = h('select.form-select.form-select-sm', {
      'aria-label': ariaLabel || undefined,
      onchange: (e) => onChange && onChange(e.target.value),
    }, options.map((o) => {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      return h('option', { value: opt.value, selected: String(opt.value) === String(value) }, opt.label);
    }));
    const picker = searchableSelect(select, {
      placeholder: ariaLabel || 'Any',
      ariaLabel,
      lead: h(`i.bi.bi-${icon}.tc-cbo-lead`),
    });
    picker.search.style.minWidth = `${width}px`;
    return picker.node;
  }
  function dataTable({ columns, rows, onRowClick, empty, rowLabel }) {
    if (!rows || !rows.length) return empty || emptyState('Nothing here yet');
    return h('div.table-responsive',
      h('table.table.table-tc.table-hover.align-middle.mb-0',
        h('thead', h('tr', columns.map((c) => h('th', { class: c.class || '', scope: 'col' }, c.label)))),
        h('tbody', rows.map((row) => {
          const clickable = !!onRowClick;
          return h('tr', {
            class: clickable ? null : 'row-static',
            role: clickable ? 'button' : null,
            tabindex: clickable ? '0' : null,
            'aria-label': clickable && rowLabel ? rowLabel(row) : null,
            style: clickable ? 'cursor:pointer' : null,
            onclick: clickable ? () => onRowClick(row) : null,
            onkeydown: clickable ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(row); }
            } : null,
          }, columns.map((c) => h('td', { class: c.class || '' }, c.render(row))));
        }))));
  }

  /**
   * Open a document in a modal that is laid out for paper and print it.
   * Used for job cards and invoices so the shop can hand a customer a real
   * printout instead of a screenshot.
   */
  function printDoc({ title, node, size = 'xl' }) {
    return modal({
      title,
      size,
      body: h('div.print-doc', node),
      footer: [
        h('button.btn.btn-outline-secondary.btn-sm.no-print', {
          type: 'button', 'data-bs-dismiss': 'modal' },
          'Close'),
        h('button.btn.btn-brand.btn-sm.no-print', {
          type: 'button',
          onclick: () => setTimeout(() => window.print(), 150),
        }, icon('printer'), ' Print'),
      ],
    });
  }

  /** Standard letterhead for any printed document. */
  function printHeader(docTitle, metaLines) {
    const company = (window.__BOOTSTRAP__ && window.__BOOTSTRAP__.company) || {};
    return h('div.print-head', [
      h('div', [
        h('h1', company.name || 'Topclass Auto Body'),
        h('div.small', company.address || '23 George Avenue, Msasa, Harare'),
        h('div.small', `${company.tel || ''} · ${company.email || ''}`),
      ]),
      h('div.text-end', [
        h('div.fw-bold', { style: 'font-size:1rem' }, docTitle),
        ...(metaLines || []).map((line) => h('div.small', line)),
      ]),
    ]);
  }

  /* Every tile tone resolves here, so a caller cannot emit a Bootstrap colour
     that is not in the palette — `info` was rendering cyan and `secondary` a
     warm grey, which made a screen of tiles look like several products.
     `tone` is the documented name; `colour` is kept for older call sites. */
  const TILE_TONES = {
    brand: 'bg-brand-subtle text-brand',
    navy: 'bg-primary-subtle text-primary',
    primary: 'bg-primary-subtle text-primary',
    steel: 'bg-info-subtle text-info',
    info: 'bg-info-subtle text-info',
    slate: 'bg-secondary-subtle text-secondary',
    secondary: 'bg-secondary-subtle text-secondary',
    green: 'bg-success-subtle text-success',
    success: 'bg-success-subtle text-success',
    amber: 'bg-warning-subtle text-warning',
    warning: 'bg-warning-subtle text-warning',
    red: 'bg-danger-subtle text-danger',
    danger: 'bg-danger-subtle text-danger',
  };

  function statCard({ label, value, icon: ic, colour = 'brand', tone, sub, onClick }) {
    const iconClass = TILE_TONES[tone || colour] || TILE_TONES.brand;
    const el = h('div.card.stat-card.h-100', { onclick: onClick, style: onClick ? 'cursor:pointer' : null },
      h('div.card-body.d-flex.align-items-center.gap-3',
        h(`div.stat-icon.${iconClass}`, icon(ic || 'graph-up')),
        h('div.flex-fill',
          h('div.text-secondary.small.text-uppercase.fw-semibold', { style: 'font-size:.68rem;letter-spacing:.05em' }, label),
          h('div.stat-value', value),
          sub ? h('div.small.text-secondary', sub) : null)));
    return el;
  }

  function section({ title, actions, body, flush, tools, grow }) {
    // `grow` fills the column so two cards side by side end on the same line
    // instead of leaving a gap under whichever one is shorter.
    return h(`div.card.soft-card.mb-3${grow ? '.h-100' : ''}`,
      title ? h('div.card-header.d-flex.align-items-center.gap-2',
        h('span.flex-fill', title),
        tools || null,
        ...(actions || [])) : null,
      h(`div.card-body${flush ? '.p-0' : ''}`, body));
  }

  function formValue(form, name) {
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'radio') {
      const picked = form.querySelector(`[name="${name}"]:checked`);
      return picked ? picked.value : '';
    }
    return el.value.trim();
  }
  function formData(form) {
    const out = {};
    new FormData(form).forEach((v, k) => { out[k] = typeof v === 'string' ? v.trim() : v; });
    form.querySelectorAll('input[type=checkbox]').forEach((c) => { out[c.name] = c.checked; });
    return out;
  }

  /* ── router ──────────────────────────────────────────────────────── */
  const routes = [];
  function route(pattern, handler) {
    const keys = [];
    const regex = new RegExp('^' + pattern.replace(/:([\w]+)/g, (_, k) => {
      keys.push(k); return '([^/]+)';
    }).replace(/\*/g, '.*') + '$');
    routes.push({ regex, keys, handler, pattern });
  }
  function currentPath() {
    const hash = window.location.hash.replace(/^#/, '');
    return hash || '/dashboard';
  }
  function navigate(path, replace) {
    const target = '#' + path;
    if (replace) window.location.replace(target);
    else window.location.hash = path;
  }
  function resolve() {
    const path = currentPath();
    const query = {};
    const [pure, qs] = path.split('?');
    new URLSearchParams(qs || '').forEach((v, k) => { query[k] = v; });
    for (const r of routes) {
      const m = pure.match(r.regex);
      if (m) {
        const params = {};
        r.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
        return { handler: r.handler, params, query, path: pure };
      }
    }
    return { handler: () => h('div.empty-state', [icon('question-circle'), 'Page not found']), params: {}, query, path: pure };
  }
  let currentCleanup = null;
  async function renderRoute() {
    const { handler, params, query, path } = resolve();
    const outlet = document.getElementById('viewOutlet');
    if (!outlet) return;
    if (typeof currentCleanup === 'function') { try { currentCleanup(); } catch (e) { /* noop */ } currentCleanup = null; }
    outlet.innerHTML = '';
    outlet.appendChild(spinner('Loading…'));
    document.querySelectorAll('.tc-nav-item[data-route]').forEach((a) => {
      const base = a.dataset.route;
      a.classList.toggle('active', path === base || (base !== '/dashboard' && path.startsWith(base)));
    });
    const context = {
      params, query, path,
      onCleanup: (fn) => { currentCleanup = fn; },
      navigate,
      refresh: () => renderRoute(),
    };
    try {
      const node = await handler(context);
      if (outlet) mount(outlet, node);
      const crumb = document.getElementById('pageCrumb');
      if (crumb) crumb.textContent = (context.title || path.replace(/^\//, '').replace(/\//g, ' › '));
    } catch (err) {
      if (err && err.status === 401) return;
      console.error(err);
      mount(outlet, h('div.alert.alert-danger', [h('strong', 'Could not load this view. '), err.message]));
    }
  }
  function startRouter() {
    window.addEventListener('hashchange', renderRoute);
    if (!window.location.hash) window.location.hash = '/dashboard';
    else renderRoute();
  }

  TCA.h = h;
  TCA.frag = frag;
  TCA.mount = mount;
  TCA.html = html;
  TCA.icon = icon;
  TCA.createStore = createStore;
  TCA.modal = modal;
  TCA.closeModal = closeModal;
  TCA.confirmDialog = confirmDialog;
  TCA.formModal = formModal;
  TCA.comboField = comboField;
  TCA.vehicleOptions = vehicleOptions;
  TCA.badge = badge;
  TCA.stageBadge = stageBadge;
  TCA.priorityBadge = priorityBadge;
  TCA.emptyState = emptyState;
  TCA.searchInput = searchInput;
  TCA.iconSelect = iconSelect;
  TCA.searchableSelect = searchableSelect;
  TCA.skeletonTable = skeletonTable;
  TCA.spinner = spinner;
  TCA.dataTable = dataTable;
  TCA.printDoc = printDoc;
  TCA.printHeader = printHeader;
  TCA.statCard = statCard;
  TCA.section = section;
  TCA.formData = formData;
  TCA.formValue = formValue;
  TCA.money = money;
  TCA.dateShort = dateShort;
  TCA.dateTime = dateTime;
  TCA.timeOnly = timeOnly;
  TCA.relTime = relTime;
  TCA.today = today;
  TCA.src = src;
  TCA.debounce = debounce;
  TCA.setConnectionState = setConnectionState;
  TCA.route = route;
  TCA.navigate = navigate;
  TCA.startRouter = startRouter;
  TCA.renderRoute = renderRoute;
})();
