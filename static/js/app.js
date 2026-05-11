/* ── iPXE Manager app.js ── */

// Auto-dismiss alerts after 5 s
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.alert:not(.alert-info):not(.alert-warning)').forEach(el => {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      bsAlert.close();
    }, 5000);
  });

  // Init all tooltips
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
    new bootstrap.Tooltip(el);
  });

  // CodeMirror on #config-editor if present
  const editor = document.getElementById('config-editor');
  if (editor) {
    const cm = CodeMirror.fromTextArea(editor, {
      theme: 'dracula',
      lineNumbers: true,
      lineWrapping: true,
      mode: detectMode(editor.value),
      indentUnit: 2,
      tabSize: 2,
    });
    // Keep textarea in sync
    cm.on('change', () => { editor.value = cm.getValue(); });
    // Allow global access for template injection
    window._cm = cm;
  }
});

function detectMode(content) {
  if (content.trim().startsWith('<?xml') || content.includes('<unattend')) return 'xml';
  if (content.trim().startsWith('#cloud-config') || content.trim().startsWith('hostname:')) return 'yaml';
  return 'shell';
}

// Called by template buttons in config editor
function insertTemplate(type) {
  const el = document.getElementById('config-editor');
  if (!el) return;
  if (window._cm) {
    window._cm.setValue(TEMPLATES[type] || '');
  } else {
    el.value = TEMPLATES[type] || '';
  }
}

// Confirm-delete helper
function confirmDelete(msg) {
  return confirm(msg || 'Confirmer la suppression ?');
}
