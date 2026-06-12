// Minimal vanilla helpers. Most interactivity is handled by HTMX attributes in templates.

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

// One detail panel at a time: clicking a chip replaces #panels (hx-swap=innerHTML).
