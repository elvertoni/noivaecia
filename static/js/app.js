(function () {
  'use strict';

  const APP_TRACE = {
    name: 'NoivasCiaApp',
    version: 'frontend-ui-refactor-2026-08-12',
    features: [
      'enter-navigation',
      'br-date-inputs',
      'br-decimal-inputs',
      'strict-masks',
      'inline-validation',
      'action-menus',
      'confirm-dialog',
    ],
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
      return true;
    }
    return false;
  }

  function handleEnterNavigation(event) {
    if (event.key !== 'Enter' || event.defaultPrevented || event.isComposing) return;
    const target = event.target;
    if (!(target instanceof HTMLElement) || isEditableElement(target)) return;
    if (target.closest('a[href]')) return;
    if (target.matches('select, [role="combobox"], [role="listbox"]')) return;

    const form = target.closest('form');
    if (!form) return;

    const method = (form.getAttribute('method') || 'get').toLowerCase();
    if (method !== 'post' || form.dataset.enterSubmit === 'true') return;
    if (target.closest('[data-enter-submit]')) return;

    const type = (target.getAttribute('type') || '').toLowerCase();
    if (['button', 'submit', 'reset', 'file', 'image', 'checkbox', 'radio'].includes(type)) return;

    if (focusNextControl(form, target)) event.preventDefault();
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
    } else if (hasDot) {
      var dotParts = s.split('.');
      var looksLikeThousands = dotParts.length > 1 && dotParts.slice(1).every(function (part) {
        return part.length === 3;
      });
      if (looksLikeThousands) {
        s = s.replace(/\./g, '');
      }
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
    var normalized = String(raw || '');
    // A dot only ever reaches here as a thousands-grouping separator the
    // mask itself inserted (always exactly 3 digits per group) — UNLESS the
    // value was just pasted/autofilled from outside, where a dot is more
    // likely a decimal point (e.g. "12.34"). Treating that as thousands
    // would silently turn it into 1234 (100x the intended amount), so
    // reclassify a non-3-digit-grouped dot as the decimal separator before
    // stripping, mirroring the same heuristic parseBRDecimal already uses.
    if (normalized.indexOf(',') === -1 && normalized.indexOf('.') !== -1) {
      var dotParts = normalized.split('.');
      var looksLikeThousands = dotParts.length > 1 && dotParts.slice(1).every(function (part) {
        return /^\d{3}$/.test(part);
      });
      if (!looksLikeThousands) {
        var lastDot = normalized.lastIndexOf('.');
        normalized = normalized.slice(0, lastDot) + ',' + normalized.slice(lastDot + 1);
      }
    }
    // Strip everything except digits and comma
    var s = normalized.replace(/[^\d,]/g, '');
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

    // Select all on the click that brings focus in, so overwriting the value
    // works the same by mouse as it already does via Tab. A second click
    // while already focused still repositions the caret normally.
    var justFocused = false;
    input.addEventListener('mousedown', function () {
      justFocused = document.activeElement !== input;
    });
    input.addEventListener('focus', function () {
      if (!justFocused) this.select();
    });
    input.addEventListener('mouseup', function (event) {
      if (justFocused) {
        event.preventDefault();
        this.select();
        justFocused = false;
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

  // ── Strict Character Masks (CPF / CNPJ / Phone / RG) ──────────────────────
  //
  // Lives here instead of per-template so every screen that renders a document
  // or phone widget behaves the same way. Forms opt in through widget attrs
  // (`data-mask="cpf|cnpj|phone"`, `data-rg-format="true"`) declared in Python.

  function onlyDigits(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function maskCPF(value) {
    const d = onlyDigits(value).slice(0, 11);
    if (d.length <= 3) return d;
    if (d.length <= 6) return `${d.slice(0, 3)}.${d.slice(3)}`;
    if (d.length <= 9) return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6)}`;
    return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
  }

  function maskCNPJ(value) {
    const d = onlyDigits(value).slice(0, 14);
    if (d.length <= 2) return d;
    if (d.length <= 5) return `${d.slice(0, 2)}.${d.slice(2)}`;
    if (d.length <= 8) return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5)}`;
    if (d.length <= 12) return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8)}`;
    return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
  }

  function maskPhone(value) {
    let d = onlyDigits(value);
    // Numbers pasted from WhatsApp arrive with the +55 country code.
    if (d.startsWith('55') && (d.length === 12 || d.length === 13)) d = d.slice(2);
    d = d.slice(0, 11);
    if (d.length <= 2) return d.length ? `(${d}` : '';
    if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
    if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
    return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
  }

  function formatNumericRG(value) {
    const raw = String(value || '').trim();
    if (!/^\d+$/.test(raw)) return raw;
    if (raw.length === 8) {
      return `${raw.slice(0, 1)}.${raw.slice(1, 4)}.${raw.slice(4, 7)}-${raw.slice(7)}`;
    }
    if (raw.length === 9) {
      return `${raw.slice(0, 2)}.${raw.slice(2, 5)}.${raw.slice(5, 8)}-${raw.slice(8)}`;
    }
    return raw;
  }

  const MASKS = { cpf: maskCPF, cnpj: maskCNPJ, phone: maskPhone };

  // Characters the operator is allowed to type. Separators are supplied by the
  // mask itself, so typing them is blocked rather than tolerated.
  const MASK_ALLOWED = {
    cpf: /\d/,
    cnpj: /\d/,
    phone: /\d/,
    // RG is not purely numeric in Brazil: issuing states prepend letters and
    // the check digit can be an "X". Digits and letters pass; punctuation does
    // not, because `formatNumericRG` inserts it on blur.
    rg: /[0-9A-Za-z]/,
  };

  function maskCursorPosition(maskedValue, digitsBefore) {
    let count = 0;
    for (let index = 0; index < maskedValue.length; index += 1) {
      if (/[0-9A-Za-z]/.test(maskedValue[index])) {
        count += 1;
        if (count === digitsBefore) return index + 1;
      }
    }
    return maskedValue.length;
  }

  function blocksTypedCharacter(allowed, text) {
    return String(text || '').split('').some(function (character) {
      return !allowed.test(character);
    });
  }

  function guardTypedCharacters(input, allowed) {
    // `keydown` covers the physical keyboard, `beforeinput` covers autofill,
    // IME commits and virtual keyboards. Pasting is deliberately allowed
    // through so the mask can sanitise "(43) 99999-1234" instead of rejecting
    // an otherwise valid number.
    input.addEventListener('keydown', function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key.length !== 1) return;
      if (allowed.test(event.key)) return;
      event.preventDefault();
    });

    input.addEventListener('beforeinput', function (event) {
      if (event.inputType !== 'insertText' && event.inputType !== 'insertCompositionText') return;
      if (!blocksTypedCharacter(allowed, event.data)) return;
      event.preventDefault();
    });
  }

  function prepareMaskedInput(input) {
    if (input.dataset.maskPrepared === 'true') return;
    input.dataset.maskPrepared = 'true';

    const maskName = input.dataset.mask;
    const isRG = input.dataset.rgFormat === 'true';
    const mask = MASKS[maskName];
    const allowed = MASK_ALLOWED[maskName] || (isRG ? MASK_ALLOWED.rg : null);

    if (allowed) guardTypedCharacters(input, allowed);

    if (mask) {
      input.addEventListener('input', function () {
        const digitsBefore = this.value
          .substring(0, this.selectionStart || 0)
          .replace(/[^0-9A-Za-z]/g, '').length;
        this.value = mask(this.value);
        const position = maskCursorPosition(this.value, digitsBefore);
        try {
          this.setSelectionRange(position, position);
        } catch (_) {}
      });
    }

    if (isRG) {
      input.addEventListener('blur', function () {
        this.value = formatNumericRG(this.value);
      });
    }
  }

  function initMaskedInputs(root) {
    root.querySelectorAll('[data-mask], [data-rg-format="true"]').forEach(prepareMaskedInput);
  }

  // ── Reveal registry ────────────────────────────────────────────────────────
  //
  // Progressive-disclosure containers (stepper steps, <details>, collapsed
  // rows) register a callback so validation can bring a hidden invalid field
  // back on screen instead of focusing something the operator cannot see.

  const revealers = [];

  function registerRevealer(callback) {
    if (typeof callback === 'function') revealers.push(callback);
  }

  function revealElement(element) {
    revealers.forEach(function (callback) {
      try {
        callback(element);
      } catch (_) {}
    });
    let node = element.parentElement;
    while (node && node !== document.body) {
      if (node.tagName === 'DETAILS') node.open = true;
      node = node.parentElement;
    }
  }

  // ── Inline Form Validation ─────────────────────────────────────────────────
  //
  // Replaces the native constraint bubbles, which render in the browser locale,
  // vanish on the next click and cannot be reached by a screen reader once
  // dismissed. Errors are written into the layout next to the field instead.

  const VALIDATION_MESSAGES = {
    valueMissing: function (el) {
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (el.tagName === 'SELECT') return 'Selecione uma opção.';
      if (type === 'checkbox' || type === 'radio') return 'Marque esta opção para continuar.';
      if (type === 'file') return 'Selecione um arquivo.';
      return 'Preencha este campo.';
    },
    typeMismatch: function (el) {
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (type === 'email') return 'Informe um e-mail válido.';
      if (type === 'url') return 'Informe um endereço válido.';
      return 'Informe um valor válido.';
    },
    tooShort: function (el) {
      return `Use pelo menos ${el.minLength} caracteres.`;
    },
    tooLong: function (el) {
      return `Use no máximo ${el.maxLength} caracteres.`;
    },
    rangeUnderflow: function (el) {
      return `Informe um valor maior ou igual a ${el.getAttribute('min')}.`;
    },
    rangeOverflow: function (el) {
      return `Informe um valor menor ou igual a ${el.getAttribute('max')}.`;
    },
    stepMismatch: function () {
      return 'Informe um valor válido para este campo.';
    },
    patternMismatch: function (el) {
      return el.dataset.patternMessage || 'Informe o valor no formato esperado.';
    },
    badInput: function () {
      return 'Informe um valor válido.';
    },
  };

  const VALIDATION_STATES = [
    'valueMissing',
    'typeMismatch',
    'patternMismatch',
    'tooShort',
    'tooLong',
    'rangeUnderflow',
    'rangeOverflow',
    'stepMismatch',
    'badInput',
  ];

  function validationMessageFor(el) {
    const validity = el.validity;
    for (let index = 0; index < VALIDATION_STATES.length; index += 1) {
      const state = VALIDATION_STATES[index];
      if (validity[state]) return VALIDATION_MESSAGES[state](el);
    }
    return el.validationMessage || 'Informe um valor válido.';
  }

  function validatableControls(form) {
    return Array.from(form.elements).filter(function (el) {
      // `willValidate` already excludes buttons, fieldsets, outputs, disabled
      // and readonly controls and `type="hidden"` — no hand-rolled list needed.
      if (!el.willValidate) return false;
      // A control kept off screen on purpose (the `select.hidden` behind each
      // autocomplete) cannot receive focus, so a bubble or an inline error
      // beside it would point at nothing. The server still validates it and
      // renders the message next to the visible search box.
      return isElementVisible(el);
    });
  }

  // The error paragraph goes after the whole widget, not after the raw input,
  // so the R$/% affix wrappers keep their inline layout intact.
  function errorAnchorFor(el) {
    const wrapper = el.closest('.currency-field, .percent-field');
    return wrapper || el;
  }

  function clientErrorId(el) {
    return el.id ? `${el.id}-client-error` : '';
  }

  function ensureClientErrorId(el) {
    if (!el.id) {
      el.id = `field-${Math.random().toString(36).slice(2, 9)}`;
    }
    return `${el.id}-client-error`;
  }

  function describedByWithout(el, id) {
    return (el.getAttribute('aria-describedby') || '')
      .split(/\s+/)
      .filter(function (token) {
        return token && token !== id;
      });
  }

  function clearFieldError(el) {
    const id = clientErrorId(el);
    // No id means this module never wrote an error for the control.
    if (!id) return;
    const existing = document.getElementById(id);
    if (existing) existing.remove();
    el.removeAttribute('aria-invalid');
    const remaining = describedByWithout(el, id);
    if (remaining.length) {
      el.setAttribute('aria-describedby', remaining.join(' '));
    } else {
      el.removeAttribute('aria-describedby');
    }
  }

  function showFieldError(el, message) {
    const id = ensureClientErrorId(el);
    let error = document.getElementById(id);
    if (!error) {
      error = document.createElement('p');
      error.id = id;
      error.className = 'field-error';
      error.setAttribute('role', 'alert');
      error.dataset.clientError = 'true';
      const anchor = errorAnchorFor(el);
      anchor.insertAdjacentElement('afterend', error);
    }
    error.textContent = message;
    el.setAttribute('aria-invalid', 'true');
    const described = describedByWithout(el, id);
    described.push(id);
    el.setAttribute('aria-describedby', described.join(' '));
  }

  function validateControl(el) {
    if (el.checkValidity()) {
      clearFieldError(el);
      return true;
    }
    showFieldError(el, validationMessageFor(el));
    return false;
  }

  function scrollToInvalid(el) {
    revealElement(el);
    const reduced = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // A rAF gives the reveal callbacks a frame to unhide the container before
    // its position is measured; otherwise a stepper step scrolls to y=0.
    window.requestAnimationFrame(function () {
      el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
      try {
        el.focus({ preventScroll: true });
      } catch (_) {
        el.focus();
      }
    });
  }

  function validateForm(form) {
    // Only forms this module prepared: a GET filter form keeps native
    // validation, and the browser blocks it before the submit event fires.
    if (form.dataset.validationPrepared !== 'true') return true;
    // Progressive-disclosure containers (the rental stepper) unhide every
    // panel for the duration of the sweep, so a required field two steps ahead
    // is still checked instead of silently skipped by the visibility filter.
    // A collapsed <details> hides its fields the same way, so it is opened here
    // too — `revealElement` reopens whichever one holds the first error.
    const collapsed = Array.from(form.querySelectorAll('details:not([open])'));
    let firstInvalid = null;
    try {
      form.dispatchEvent(new CustomEvent('app:validate-scope-open'));
      collapsed.forEach(function (details) { details.open = true; });
      validatableControls(form).forEach(function (el) {
        if (!validateControl(el) && !firstInvalid) firstInvalid = el;
      });
    } finally {
      // `finally`: an exception mid-sweep must not strand the form with every
      // step expanded and every <details> forced open.
      collapsed.forEach(function (details) {
        if (!details.contains(firstInvalid)) details.open = false;
      });
      form.dispatchEvent(new CustomEvent('app:validate-scope-close'));
    }
    if (firstInvalid) scrollToInvalid(firstInvalid);
    return !firstInvalid;
  }

  function initFormValidation(root) {
    root.querySelectorAll('form').forEach(function (form) {
      if (form.dataset.validationPrepared === 'true') return;
      if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
      if (form.dataset.nativeValidation === 'true') return;
      form.dataset.validationPrepared = 'true';
      // Only suppress the native bubbles once this script is confirmed to run,
      // so a no-JS session keeps browser-level constraint enforcement.
      form.noValidate = true;
    });
  }

  function handleValidationFeedback(event) {
    const el = event.target;
    if (!(el instanceof HTMLElement) || !el.form) return;
    if (el.form.dataset.validationPrepared !== 'true') return;
    // A radio group is one control to the operator but N elements in the DOM;
    // clearing only the one that changed leaves the siblings marked invalid.
    const group = el.type === 'radio' && el.name
      ? Array.from(el.form.elements).filter(function (other) {
        return other.type === 'radio' && other.name === el.name;
      })
      : [el];
    group.forEach(function (member) {
      if (member.getAttribute('aria-invalid') !== 'true') return;
      // Only clear what this module wrote; server-rendered errors stay until
      // the next round-trip so the operator keeps the record of what failed.
      const id = clientErrorId(member);
      if (!id || !document.getElementById(id)) return;
      validateControl(member);
    });
  }

  // ── Action Menus (kebab) ───────────────────────────────────────────────────

  function actionMenuPanel(menu) {
    return menu.querySelector('.action-menu-panel');
  }

  function actionMenuItems(panel) {
    return Array.from(panel.querySelectorAll('.action-menu-item')).filter(function (el) {
      return !el.disabled;
    });
  }

  function closeActionMenu(menu, restoreFocus) {
    const panel = actionMenuPanel(menu);
    const trigger = menu.querySelector('.action-menu-trigger');
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    panel.removeAttribute('style');
    if (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    }
  }

  function closeAllActionMenus(except) {
    document.querySelectorAll('[data-action-menu]').forEach(function (menu) {
      if (menu !== except) closeActionMenu(menu, false);
    });
  }

  // Row menus live inside `.table-shell`, which scrolls horizontally and would
  // clip an absolutely positioned panel. Fixed positioning escapes the clip.
  function positionActionMenu(trigger, panel) {
    const rect = trigger.getBoundingClientRect();
    panel.style.position = 'fixed';
    panel.style.visibility = 'hidden';
    panel.style.top = '0';
    panel.style.left = '0';
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const margin = 8;
    // `clientWidth`/`clientHeight`, not `innerWidth`/`innerHeight`: the latter
    // include the scrollbar, so on a desktop-style scrollbar the panel was
    // being clamped to a right edge that sits under it.
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;

    let left = rect.right - width;
    left = Math.min(Math.max(margin, left), viewportWidth - width - margin);

    let top = rect.bottom + 4;
    if (top + height > viewportHeight - margin) {
      top = Math.max(margin, rect.top - height - 4);
    }

    panel.style.top = `${top}px`;
    panel.style.left = `${left}px`;
    panel.style.visibility = '';
  }

  function openActionMenu(menu) {
    const panel = actionMenuPanel(menu);
    const trigger = menu.querySelector('.action-menu-trigger');
    if (!panel || !trigger) return;
    closeAllActionMenus(menu);
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    positionActionMenu(trigger, panel);
    const items = actionMenuItems(panel);
    // `preventScroll` matters: the panel is fixed and already in view, and any
    // scroll would hit the scroll listener that closes every open menu.
    if (items.length) items[0].focus({ preventScroll: true });
  }

  function handleActionMenuClick(event) {
    const trigger = event.target.closest('.action-menu-trigger');
    if (trigger) {
      const menu = trigger.closest('[data-action-menu]');
      const panel = menu && actionMenuPanel(menu);
      if (!menu || !panel) return;
      event.preventDefault();
      if (panel.hidden) openActionMenu(menu);
      else closeActionMenu(menu, true);
      return;
    }
    const insideMenu = event.target.closest('[data-action-menu]');
    if (!insideMenu) {
      closeAllActionMenus(null);
      return;
    }
    const item = event.target.closest('.action-menu-item');
    // A submit item keeps the panel open on purpose: the page is navigating
    // away and the button still has to show its "Reativando…" busy state.
    if (item && item.type !== 'submit') closeActionMenu(insideMenu, false);
  }

  function handleActionMenuKeydown(event) {
    const menu = event.target.closest('[data-action-menu]');
    if (!menu) return;
    const panel = actionMenuPanel(menu);
    if (!panel) return;

    if (event.key === 'Escape' && !panel.hidden) {
      event.preventDefault();
      closeActionMenu(menu, true);
      return;
    }

    // Enter/Space already fire a native click on the trigger button, which the
    // click handler toggles — handling them here too would immediately reclose.
    const isTrigger = !!event.target.closest('.action-menu-trigger');
    if (isTrigger && event.key === 'ArrowDown') {
      event.preventDefault();
      openActionMenu(menu);
      return;
    }

    if (panel.hidden || isTrigger) return;
    // Menu items are `tabindex="-1"` per the ARIA menu pattern, so Tab leaves
    // the menu instead of walking it. Close it so it does not linger open
    // behind wherever focus went.
    if (event.key === 'Tab') {
      closeActionMenu(menu, false);
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const items = actionMenuItems(panel);
    if (!items.length) return;
    event.preventDefault();
    const current = items.indexOf(document.activeElement);
    const offset = event.key === 'ArrowDown' ? 1 : -1;
    const next = (current + offset + items.length) % items.length;
    items[next].focus();
  }

  // ── Confirmation Dialog ────────────────────────────────────────────────────
  //
  // One dialog instance shared by every destructive trigger, replacing
  // `window.confirm` — which is unstyled, untranslatable and blocks the thread.

  const confirmState = { trigger: null, lastFocus: null };

  function confirmDialogEl() {
    return document.getElementById('confirm-dialog');
  }

  function closeConfirmDialog() {
    const dialog = confirmDialogEl();
    if (!dialog || dialog.hidden) return;
    dialog.hidden = true;
    document.body.classList.remove('is-dialog-open');
    const lastFocus = confirmState.lastFocus;
    confirmState.trigger = null;
    confirmState.lastFocus = null;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // `form.submit()` does not fire the submit event, so it would skip inline
  // validation and the date/decimal normalisation. Clicking a real submitter
  // runs the whole pipeline on browsers without `requestSubmit`.
  function submitFormThrough(form, submitter) {
    if (form.requestSubmit) {
      form.requestSubmit(submitter);
      return;
    }
    if (submitter) {
      submitter.click();
      return;
    }
    const fallback = form.querySelector('button[type="submit"], input[type="submit"]');
    if (fallback) fallback.click();
  }

  function runConfirmedAction(trigger) {
    // `data-confirmed` lets the trigger through the interceptor on the replay.
    trigger.dataset.confirmed = 'true';
    try {
      if (trigger.tagName === 'A') {
        // A confirmed link keeps its target: `location.href` would drag a
        // `target="_blank"` action into the current tab.
        if (trigger.target && trigger.target !== '_self') {
          window.open(trigger.href, trigger.target);
        } else {
          window.location.href = trigger.href;
        }
        return;
      }
      if (trigger.tagName === 'FORM') {
        submitFormThrough(trigger, null);
        return;
      }
      const form = trigger.form;
      if (form) {
        submitFormThrough(form, trigger.name ? trigger : null);
        return;
      }
      trigger.click();
    } finally {
      // The replay may be rejected by validation and leave the operator on the
      // page. Without clearing this, the next click would run the destructive
      // action with no confirmation at all.
      delete trigger.dataset.confirmed;
    }
  }

  function openConfirmDialog(trigger) {
    const dialog = confirmDialogEl();
    if (!dialog) return false;

    const title = dialog.querySelector('[data-confirm-title]');
    const message = dialog.querySelector('[data-confirm-message]');
    const accept = dialog.querySelector('[data-confirm-accept]');
    if (!accept) return false;

    if (title) title.textContent = trigger.dataset.confirmTitle || 'Confirmar ação';
    if (message) message.textContent = trigger.dataset.confirm || 'Deseja continuar?';
    accept.textContent = trigger.dataset.confirmAction || 'Confirmar';
    accept.className = trigger.dataset.confirmTone === 'default'
      ? 'btn btn-primary'
      : 'btn btn-danger';

    confirmState.trigger = trigger;
    const active = document.activeElement;
    confirmState.lastFocus = active && active !== document.body ? active : null;
    dialog.hidden = false;
    document.body.classList.add('is-dialog-open');
    accept.focus();
    return true;
  }

  function handleConfirmTriggers(event) {
    const trigger = event.target.closest('[data-confirm]');
    if (!trigger || trigger.dataset.confirmed === 'true') return;
    if (trigger.tagName === 'FORM') return;
    if (!confirmDialogEl()) return;
    event.preventDefault();
    openConfirmDialog(trigger);
  }

  function handleConfirmFormSubmit(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return false;
    if (!form.hasAttribute('data-confirm') || form.dataset.confirmed === 'true') return false;
    if (!confirmDialogEl()) return false;
    event.preventDefault();
    event.stopPropagation();
    openConfirmDialog(form);
    return true;
  }

  function initConfirmDialog() {
    const dialog = confirmDialogEl();
    if (!dialog || dialog.dataset.prepared === 'true') return;
    dialog.dataset.prepared = 'true';

    dialog.addEventListener('click', function (event) {
      if (event.target.closest('[data-confirm-accept]')) {
        const trigger = confirmState.trigger;
        closeConfirmDialog();
        if (trigger) runConfirmedAction(trigger);
        return;
      }
      if (event.target.closest('[data-confirm-dismiss]')) {
        closeConfirmDialog();
      }
    });

    dialog.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeConfirmDialog();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter(isElementVisible);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  function updateScrollableTable(shell) {
    const isScrollable = shell.scrollWidth > shell.clientWidth + 1;
    if (isScrollable) {
      shell.tabIndex = 0;
      shell.setAttribute('role', 'region');
      if (!shell.hasAttribute('aria-label') && !shell.hasAttribute('aria-labelledby')) {
        const caption = shell.querySelector('caption');
        shell.setAttribute('aria-label', caption && caption.textContent.trim()
          ? caption.textContent.trim()
          : 'Tabela com rolagem horizontal');
      }
    } else {
      shell.removeAttribute('tabindex');
      shell.removeAttribute('role');
    }
  }

  function initScrollableTables(root) {
    root.querySelectorAll('.table-shell').forEach(updateScrollableTable);
  }

  window.NoivasCiaApp = Object.assign(window.NoivasCiaApp || {}, APP_TRACE);
  window.NoivasCiaForms = Object.assign(window.NoivasCiaForms || {}, {
    getDateInputIsoValue: function (input) {
      return input ? dateValueToIso(input.value) : '';
    },
    prepareDateInputs: initDateInputs,
    prepareDecimalInputs: initDecimalInputs,
    prepareMaskedInputs: initMaskedInputs,
    prepareValidation: initFormValidation,
    parseBRDecimal: parseBRDecimal,
    formatBRDecimal: formatBRDecimal,
    validateField: validateControl,
    validateForm: validateForm,
    showFieldError: showFieldError,
    clearFieldError: clearFieldError,
  });
  window.NoivasCiaUI = Object.assign(window.NoivasCiaUI || {}, {
    registerRevealer: registerRevealer,
    revealElement: revealElement,
    closeActionMenus: closeAllActionMenus,
  });

  document.addEventListener('keydown', handleEnterNavigation);
  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    // Order matters: confirm first (nothing is validated for an action the
    // operator may still cancel), then validate, and only normalise dates and
    // decimals once the submit is actually going through — a rejected form
    // must keep showing "12/03/2026", not "2026-03-12".
    if (handleConfirmFormSubmit(event)) return;
    if (!validateForm(form)) {
      event.preventDefault();
      // Stops the page-level submit handlers that disable the save button and
      // relabel it "Salvando…", which would strand the operator on an
      // unsubmitted form with a dead button.
      event.stopPropagation();
      return;
    }
    normalizeFormDates(form);
    normalizeFormDecimals(form);
  }, true);
  document.addEventListener('click', handleConfirmTriggers, true);
  document.addEventListener('click', handleActionMenuClick);
  document.addEventListener('keydown', handleActionMenuKeydown);
  document.addEventListener('input', handleValidationFeedback);
  document.addEventListener('change', handleValidationFeedback);
  document.addEventListener('scroll', function () { closeAllActionMenus(null); }, true);
  document.addEventListener('DOMContentLoaded', function () {
    initDateInputs(document);
    initDecimalInputs(document);
    initMaskedInputs(document);
    initFormValidation(document);
    initScrollableTables(document);
    initConfirmDialog();
  });
  window.addEventListener('resize', function () {
    initScrollableTables(document);
    closeAllActionMenus(null);
  });
}());
