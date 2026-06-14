// Minimal vanilla helpers. Most interactivity is handled by HTMX attributes in templates.

// Custom tooltip for [data-tip] elements: works on hover AND tap (native title
// tooltips don't fire on touch devices and are slow on desktop).
(function () {
  let pop = null;
  function el() {
    if (!pop) {
      pop = document.createElement('div');
      pop.className = 'tip-pop';
      pop.style.display = 'none';
      document.body.appendChild(pop);
    }
    return pop;
  }
  function show(target) {
    const text = target.getAttribute('data-tip');
    if (!text) return;
    const t = el();
    t.textContent = text;
    t.style.display = 'block';
    const r = target.getBoundingClientRect();
    let left = window.scrollX + r.left;
    left = Math.min(left, window.scrollX + document.documentElement.clientWidth - t.offsetWidth - 8);
    t.style.left = Math.max(window.scrollX + 4, left) + 'px';
    t.style.top = (window.scrollY + r.bottom + 4) + 'px';
  }
  function hide() { if (pop) pop.style.display = 'none'; }
  document.addEventListener('pointerover', function (e) {
    const t = e.target.closest('[data-tip]');
    if (t && t.getAttribute('data-tip')) show(t);
  });
  document.addEventListener('pointerout', function (e) {
    if (e.target.closest('[data-tip]')) hide();
  });
  document.addEventListener('click', function (e) {
    const t = e.target.closest('[data-tip]');
    if (t && t.getAttribute('data-tip')) {
      if (pop && pop.style.display === 'block') hide(); else show(t);
    } else {
      hide();
    }
  });
  document.addEventListener('scroll', hide, true);
})();

// Dark mode: apply the saved preference on load, toggle + persist on click.
(function () {
  if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark');
})();
document.addEventListener('click', function (e) {
  if (e.target.closest('[data-toggle-theme]')) {
    const dark = document.body.classList.toggle('dark');
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }
});

// Close a detail panel (event delegation — panels are added dynamically by HTMX).
document.addEventListener('click', function (e) {
  const closer = e.target.closest('[data-close-panel]');
  if (closer) {
    const panel = closer.closest('.panel');
    if (panel) panel.remove();
    return;
  }
  // Close the modal when clicking the backdrop itself or an explicit close control,
  // but never when interacting with controls inside the modal (e.g. <select>).
  if (e.target.classList.contains('modal-backdrop') || e.target.closest('[data-close-modal]')) {
    const m = document.getElementById('modal-root');
    if (m) m.innerHTML = '';
  }
});

// Management group visibility is now a server-persisted toggle (#151): the
// Management button hx-posts /toggle/management and re-renders #dashboard.

// Highest-only toggle: after it persists, recolor any open detail panel.
document.body.addEventListener('htmx:afterRequest', function (e) {
  if (e.detail.elt && e.detail.elt.id === 'highest-toggle') {
    const panel = document.querySelector('#panels .panel');
    if (panel && window.htmx) {
      const email = panel.getAttribute('data-engineer');
      const region = panel.getAttribute('data-region') || '';
      htmx.ajax('GET',
        '/chip/' + encodeURIComponent(email) + '/detail?regions=' + encodeURIComponent(region),
        { target: '#panels', swap: 'innerHTML' });
    }
  }
});

// One detail panel at a time: clicking a chip replaces #panels (hx-swap=innerHTML).
// Clicking the already-open person's chip again closes the panel (toggle off).
document.body.addEventListener('htmx:beforeRequest', function (e) {
  const el = e.detail.elt;
  const email = el && el.getAttribute('data-engineer');
  if (!email) return;
  const open = document.querySelector('#panels .panel[data-engineer="' + CSS.escape(email) + '"]');
  if (open) {
    document.getElementById('panels').innerHTML = '';
    e.preventDefault();
  }
});
