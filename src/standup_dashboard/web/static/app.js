// Minimal vanilla helpers. Most interactivity is handled by HTMX attributes in templates.

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

// Avoid opening duplicate panels for the same engineer: if one exists, scroll to it.
document.body.addEventListener('htmx:beforeRequest', function (e) {
  const trigger = e.detail.elt;
  const email = trigger && trigger.getAttribute('data-engineer');
  if (email) {
    const existing = document.querySelector('.panel[data-engineer="' + CSS.escape(email) + '"]');
    if (existing) {
      existing.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      e.preventDefault();
    }
  }
});
