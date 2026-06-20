(function () {
  'use strict';

  const APP_TRACE = {
    name: 'NoivasCiaApp',
    version: 'frontend-a11y-rental-2026-06-16',
    features: ['enter-navigation', 'br-date-inputs', 'br-decimal-inputs'],
  };
  const DATE_INPUT_SELECTOR = [
    'input[data-date-br="true"]',
    'input[data-date-format="br"]',
  ].join(',');
  const DECIMAL_INPUT_SELECTOR = 'input[data-decimal-br="true"]';

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

  // ── Brazilian Decimal Inputs ───────────────────────────────────────────────

  /**
   * Parse a Brazilian-formatted decimal string into a plain numeric string.
   * Accepts: "1.234,56", "1234,56", "1234.56", "1234", "0,5"
   * Returns: "1234.56" (dot-decimal, no thousands separator)
   */
  function parseBRDecimal(value) {
    var s = String(value || '').trim();
    if (!s) return '';
    // If the string contains both dot and comma, the last one is the decimal sep.
    var hasComma = s.indexOf(',') !== -1;
    var hasDot = s.indexOf('.') !== -1;
    if (hasComma && hasDot) {
      // Brazilian format: dots are thousands, comma is decimal
      // e.g. "1.234,56" → "1234.56"
      if (s.lastIndexOf(',') > s.lastIndexOf('.')) {
        s = s.replace(/\./g, '').replace(',', '.');
      } else {
        // US format: commas are thousands, dot is decimal
        s = s.replace(/,/g, '');
      }
    } else if (hasComma) {
      // Only comma: treat as decimal separator
      s = s.replace(',', '.');
    }
    // else only dot or no separator: already ok
    // Remove any non-numeric chars except dot and minus
    s = s.replace(/[^\d.\-]/g, '');
    return s;
  }

  /**
   * Format a numeric value into Brazilian decimal display: 1.234,56
   */
  function formatBRDecimal(value) {
    var s = parseBRDecimal(value);
    if (!s) return '';
    var num = parseFloat(s);
    if (isNaN(num)) return String(value || '');
    // Format with 2 decimal places
    var fixed = num.toFixed(2);
    // Split into integer and decimal parts
    var parts = fixed.split('.');
    var intPart = parts[0];
    var decPart = parts[1];
    var negative = false;
    if (intPart.charAt(0) === '-') {
      negative = true;
      intPart = intPart.substring(1);
    }
    // Add thousand separators (dots)
    var result = '';
    for (var i = intPart.length - 1, count = 0; i >= 0; i--, count++) {
      if (count > 0 && count % 3 === 0) {
        result = '.' + result;
      }
      result = intPart.charAt(i) + result;
    }
    return (negative ? '-' : '') + result + ',' + decPart;
  }

  /**
   * Live mask for decimal input: keeps only digits and one comma,
   * auto-inserts thousand-dot separators.
   */
  function maskDecimal(raw) {
    // Strip everything except digits and comma
    var s = raw.replace(/[^\d,]/g, '');
    // Allow only one comma
    var commaIndex = s.indexOf(',');
    if (commaIndex !== -1) {
      // Keep only the first comma and limit decimal digits to 2
      var before = s.substring(0, commaIndex).replace(/,/g, '');
      var after = s.substring(commaIndex + 1).replace(/,/g, '').substring(0, 2);
      // Remove leading zeros from integer part (but keep at least one digit)
      before = before.replace(/^0+(?=\d)/, '') || '0';
      // Add thousand separators to the integer part
      var formatted = '';
      for (var i = before.length - 1, count = 0; i >= 0; i--, count++) {
        if (count > 0 && count % 3 === 0) formatted = '.' + formatted;
        formatted = before.charAt(i) + formatted;
      }
      return formatted + ',' + after;
    }
    // No comma yet: just format integer part with thousand separators
    s = s.replace(/^0+(?=\d)/, '') || '';
    if (!s) return s;
    var result = '';
    for (var j = s.length - 1, cnt = 0; j >= 0; j--, cnt++) {
      if (cnt > 0 && cnt % 3 === 0) result = '.' + result;
      result = s.charAt(j) + result;
    }
    return result;
  }

  function prepareDecimalInput(input) {
    if (input.dataset.decimalPrepared === 'true') return;
    input.dataset.decimalPrepared = 'true';

    // Convert type="number" to type="text" if needed
    try {
      if (input.type === 'number') input.type = 'text';
    } catch (_) {
      input.setAttribute('type', 'text');
    }
    input.inputMode = 'decimal';
    input.autocomplete = 'off';
    input.dataset.decimalBr = 'true';

    // Format existing value from dot-decimal to BR format
    var initial = input.value;
    if (initial && initial.trim()) {
      input.value = formatBRDecimal(initial);
    }

    input.addEventListener('input', function () {
      var cursorPos = this.selectionStart || 0;
      var oldLength = this.value.length;
      // Count digits before cursor in old value
      var digitsBefore = this.value.substring(0, cursorPos).replace(/[^\d]/g, '').length;
      this.value = maskDecimal(this.value);
      // Reposition cursor: find position after the same number of digits
      var newPos = 0;
      var counted = 0;
      for (var i = 0; i < this.value.length && counted < digitsBefore; i++) {
        if (/\d/.test(this.value.charAt(i))) counted++;
        newPos = i + 1;
      }
      // If we didn't find enough digits, put cursor at end
      if (counted < digitsBefore) newPos = this.value.length;
      try {
        this.setSelectionRange(newPos, newPos);
      } catch (_) {}
    });

    input.addEventListener('blur', function () {
      if (this.value.trim()) {
        this.value = formatBRDecimal(this.value);
      }
    });
  }

  function normalizeFormDecimals(form) {
    form.querySelectorAll(DECIMAL_INPUT_SELECTOR).forEach(function (input) {
      if (input.value.trim()) {
        input.value = parseBRDecimal(input.value);
      }
    });
  }

  function initDecimalInputs(root) {
    root.querySelectorAll(DECIMAL_INPUT_SELECTOR).forEach(prepareDecimalInput);
  }

  window.NoivasCiaApp = Object.assign(window.NoivasCiaApp || {}, APP_TRACE);
  window.NoivasCiaForms = Object.assign(window.NoivasCiaForms || {}, {
    getDateInputIsoValue: function (input) {
      return input ? dateValueToIso(input.value) : '';
    },
    prepareDateInputs: initDateInputs,
    prepareDecimalInputs: initDecimalInputs,
    parseBRDecimal: parseBRDecimal,
    formatBRDecimal: formatBRDecimal,
  });

  document.addEventListener('keydown', handleEnterNavigation);
  document.addEventListener('submit', function (event) {
    normalizeFormDates(event.target);
    normalizeFormDecimals(event.target);
  }, true);
  document.addEventListener('DOMContentLoaded', function () {
    initDateInputs(document);
    initDecimalInputs(document);
  });
}());
