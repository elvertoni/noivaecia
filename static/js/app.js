(function () {
  'use strict';

  const APP_TRACE = {
    name: 'NoivasCiaApp',
    version: 'frontend-a11y-rental-2026-06-16',
    features: ['enter-navigation', 'br-date-inputs'],
  };
  const DATE_INPUT_SELECTOR = [
    'input[data-date-br="true"]',
    'input[data-date-format="br"]',
  ].join(',');

  function isElementVisible(el) {
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  function isEditableElement(el) {
    return el.isContentEditable || el.tagName === 'TEXTAREA';
  }

  function focusNextControl(form, current) {
    const selector = [
      'input:not([type="hidden"])',
      'select',
      'textarea',
      'button',
      'a[href]',
    ].join(',');
    const controls = Array.from(form.querySelectorAll(selector)).filter(function (el) {
      return !el.disabled && el.tabIndex !== -1 && isElementVisible(el);
    });
    const index = controls.indexOf(current);
    if (index >= 0 && index < controls.length - 1) {
      controls[index + 1].focus();
    }
  }

  function handleEnterNavigation(event) {
    if (event.key !== 'Enter' || event.defaultPrevented || event.isComposing) return;
    const target = event.target;
    if (!(target instanceof HTMLElement) || isEditableElement(target)) return;
    if (target.closest('a[href]')) return;

    const form = target.closest('form');
    if (!form) return;

    const method = (form.getAttribute('method') || 'get').toLowerCase();
    if (method !== 'post' || form.dataset.enterSubmit === 'true') return;
    if (target.closest('[data-enter-submit]')) return;

    const type = (target.getAttribute('type') || '').toLowerCase();
    if (['button', 'submit', 'reset', 'file', 'image'].includes(type)) return;

    event.preventDefault();
    focusNextControl(form, target);
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function normalizeTwoDigitYear(value) {
    const year = Number(value);
    return year <= 69 ? 2000 + year : 1900 + year;
  }

  function isValidDateParts(parts) {
    const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
    return (
      date.getUTCFullYear() === parts.year
      && date.getUTCMonth() === parts.month - 1
      && date.getUTCDate() === parts.day
    );
  }

  function parseIsoDate(value) {
    const match = String(value || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    const parts = {
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
    };
    return isValidDateParts(parts) ? parts : null;
  }

  function parseBrDate(value) {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 6 && digits.length !== 8) return null;
    const parts = {
      day: Number(digits.slice(0, 2)),
      month: Number(digits.slice(2, 4)),
      year: digits.length === 6
        ? normalizeTwoDigitYear(digits.slice(4, 6))
        : Number(digits.slice(4, 8)),
    };
    return isValidDateParts(parts) ? parts : null;
  }

  function formatIsoDate(parts) {
    return `${parts.year}-${pad2(parts.month)}-${pad2(parts.day)}`;
  }

  function formatBrDate(parts) {
    return `${pad2(parts.day)}/${pad2(parts.month)}/${parts.year}`;
  }

  function maskDate(value) {
    const digits = String(value || '').replace(/\D/g, '').slice(0, 8);
    if (digits.length <= 2) return digits;
    if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
    return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
  }

  function cursorAfterDigits(value, digitsBefore) {
    let count = 0;
    for (let index = 0; index < value.length; index += 1) {
      if (/\d/.test(value[index])) {
        count += 1;
        if (count === digitsBefore) return index + 1;
      }
    }
    return value.length;
  }

  function dateValueToIso(value) {
    const parsed = parseBrDate(value) || parseIsoDate(value);
    return parsed ? formatIsoDate(parsed) : String(value || '').trim();
  }

  function prepareDateInput(input) {
    if (input.dataset.datePrepared === 'true') return;
    input.dataset.datePrepared = 'true';
    input.dataset.dateBr = 'true';

    const initial = parseIsoDate(input.value);
    try {
      input.type = 'text';
    } catch (_) {
      input.setAttribute('type', 'text');
    }
    input.inputMode = 'numeric';
    input.autocomplete = 'off';
    input.placeholder = input.placeholder || 'dd/mm/aaaa';
    input.maxLength = 10;
    if (initial) input.value = formatBrDate(initial);

    input.addEventListener('input', function () {
      const isoDate = parseIsoDate(this.value);
      if (isoDate) {
        this.value = formatBrDate(isoDate);
        try {
          this.setSelectionRange(this.value.length, this.value.length);
        } catch (_) {}
        return;
      }
      const digitsBefore = this.value
        .substring(0, this.selectionStart || 0)
        .replace(/\D/g, '').length;
      this.value = maskDate(this.value);
      const position = cursorAfterDigits(this.value, digitsBefore);
      try {
        this.setSelectionRange(position, position);
      } catch (_) {}
    });

    input.addEventListener('blur', function () {
      const parsed = parseBrDate(this.value) || parseIsoDate(this.value);
      if (parsed) this.value = formatBrDate(parsed);
    });
  }

  function normalizeFormDates(form) {
    form.querySelectorAll('input[data-date-br="true"]').forEach(function (input) {
      if (input.value.trim()) {
        input.value = dateValueToIso(input.value);
      }
    });
  }

  function initDateInputs(root) {
    root.querySelectorAll(DATE_INPUT_SELECTOR).forEach(prepareDateInput);
  }

  window.NoivasCiaApp = Object.assign(window.NoivasCiaApp || {}, APP_TRACE);
  window.NoivasCiaForms = Object.assign(window.NoivasCiaForms || {}, {
    getDateInputIsoValue: function (input) {
      return input ? dateValueToIso(input.value) : '';
    },
    prepareDateInputs: initDateInputs,
  });

  document.addEventListener('keydown', handleEnterNavigation);
  document.addEventListener('submit', function (event) {
    normalizeFormDates(event.target);
  }, true);
  document.addEventListener('DOMContentLoaded', function () {
    initDateInputs(document);
  });
}());
