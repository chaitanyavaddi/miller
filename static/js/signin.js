/* Sign-in code: one input laid over six boxes. Each digit shows in its box, the next box to fill is marked, and the form
 * goes once all six are in (typed, pasted or filled in by the phone). */
(function () {
  'use strict';

  const form = document.querySelector('[data-code-form]');
  if (!form) return;
  const input = form.querySelector('[data-code-input]');
  const boxes = [...form.querySelectorAll('[data-code-box]')];

  function show() {
    const digits = input.value.replace(/\D/g, '').slice(0, boxes.length);
    if (digits !== input.value) input.value = digits;
    const focused = document.activeElement === input;
    boxes.forEach((box, i) => {
      box.textContent = digits[i] || '';
      box.classList.toggle('is-next', focused && i === Math.min(digits.length, boxes.length - 1));
    });
    if (digits.length === boxes.length && !form._sent) {
      form._sent = true;
      form.requestSubmit();
    }
  }

  input.addEventListener('input', () => { form._sent = false; show(); });
  input.addEventListener('focus', show);
  input.addEventListener('blur', show);
  show();
})();
