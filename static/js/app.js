/* ── iPXE Manager app.js ── */

window.IpxeConfirm = (function () {
  var modal, titleEl, bodyEl, btnConfirm, confirmTextEl, iconEl, defaults = {};
  var pendingForm = null;
  var pendingResolve = null;
  var confirmed = false;

  var VARIANT_BTN = {
    danger: 'btn-danger',
    info: 'btn-info',
    warning: 'btn-warning',
    primary: 'btn-primary',
  };

  function readDefaults() {
    var el = document.getElementById('ipxe-confirm-defaults');
    if (!el) return;
    defaults = {
      title: el.dataset.title || 'Confirmation',
      cancel: el.dataset.cancel || 'Cancel',
      confirm: el.dataset.confirm || 'Confirm',
    };
    var cancelBtn = document.getElementById('ipxeConfirmModalCancel');
    if (cancelBtn && defaults.cancel) cancelBtn.textContent = defaults.cancel;
  }

  function setConfirmVariant(variant) {
    if (!btnConfirm) return;
    btnConfirm.className = 'btn btn-sm ' + (VARIANT_BTN[variant] || VARIANT_BTN.primary);
  }

  function show(opts) {
    if (!modal) return Promise.resolve(false);
    confirmed = false;
    titleEl.textContent = opts.title || defaults.title || 'Confirmation';
    bodyEl.textContent = opts.body || '';
    confirmTextEl.textContent = opts.confirmText || defaults.confirm || 'Confirm';
    setConfirmVariant(opts.variant || 'primary');
    if (iconEl) {
      iconEl.className = 'bi me-2 text-warning ' + (opts.icon || 'bi-exclamation-triangle');
    }
    return bootstrap.Modal.getOrCreateInstance(modal).show();
  }

  function ask(opts) {
    return new Promise(function (resolve) {
      pendingResolve = resolve;
      show(opts || {});
    });
  }

  function onConfirmClick() {
    confirmed = true;
    var inst = bootstrap.Modal.getInstance(modal);
    if (pendingForm) {
      var form = pendingForm;
      pendingForm = null;
      form.dataset.confirmSkip = '1';
      if (inst) inst.hide();
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.submit();
      }
      return;
    }
    if (inst) inst.hide();
  }

  function onModalHidden() {
    if (pendingResolve) {
      var resolve = pendingResolve;
      pendingResolve = null;
      resolve(confirmed);
    }
    pendingForm = null;
    confirmed = false;
  }

  function bindForms() {
    document.addEventListener(
      'submit',
      function (e) {
        var form = e.target;
        if (!form || form.tagName !== 'FORM') return;
        if (form.dataset.confirmSkip === '1') {
          delete form.dataset.confirmSkip;
          return;
        }
        var msg = form.getAttribute('data-confirm');
        if (!msg) return;
        e.preventDefault();
        e.stopPropagation();
        pendingForm = form;
        show({
          title: form.getAttribute('data-confirm-title') || undefined,
          body: msg,
          confirmText: form.getAttribute('data-confirm-btn') || undefined,
          variant: form.getAttribute('data-confirm-variant') || 'primary',
        });
      },
      true
    );
  }

  function init() {
    modal = document.getElementById('ipxeConfirmModal');
    if (!modal) return;
    titleEl = document.getElementById('ipxeConfirmModalTitleText');
    bodyEl = document.getElementById('ipxeConfirmModalBody');
    btnConfirm = document.getElementById('ipxeConfirmModalConfirm');
    confirmTextEl = document.getElementById('ipxeConfirmModalConfirmText');
    iconEl = document.getElementById('ipxeConfirmModalIcon');
    readDefaults();
    bindForms();
    btnConfirm.addEventListener('click', onConfirmClick);
    modal.addEventListener('hidden.bs.modal', onModalHidden);
  }

  return { init: init, show: show, ask: ask };
})();

document.addEventListener('DOMContentLoaded', () => {
  IpxeConfirm.init();

  document.querySelectorAll('.alert:not(.alert-info):not(.alert-warning)').forEach(el => {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      bsAlert.close();
    }, 5000);
  });

  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
    new bootstrap.Tooltip(el);
  });

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
    cm.on('change', () => { editor.value = cm.getValue(); });
    window._cm = cm;
  }
});

function detectMode(content) {
  if (content.trim().startsWith('<?xml') || content.includes('<unattend')) return 'xml';
  if (content.trim().startsWith('#cloud-config') || content.trim().startsWith('hostname:')) return 'yaml';
  return 'shell';
}

function insertTemplate(type) {
  const el = document.getElementById('config-editor');
  if (!el) return;
  if (window._cm) {
    window._cm.setValue(TEMPLATES[type] || '');
  } else {
    el.value = TEMPLATES[type] || '';
  }
}

async function confirmDelete(msg) {
  return IpxeConfirm.ask({
    body: msg || 'Confirmer la suppression ?',
    variant: 'danger',
  });
}
