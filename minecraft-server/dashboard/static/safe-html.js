/* Sanitize legacy dashboard templates; use delegated, parsed actions instead of executable HTML. */
(() => {
  const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  Object.defineProperty(Element.prototype, 'innerHTML', {
    get: descriptor.get,
    set(value) {
      const markup = String(value).replace(/\bonclick\s*=/gi, 'data-command=');
      descriptor.set.call(this, DOMPurify.sanitize(markup, {USE_PROFILES:{html:true}}));
    }
  });
  const allowed = new Set(['addOperator','approveCurrentAlert','approveUser','deleteBackupFile',
    'deleteCustomWaypoint','denyCurrentAlert','denyUser','dismissRequest','downloadBackupFile',
    'fillCmd','focusMapLocation','kickPlayer','openInventoryModal','removeOperator','removeUser',
    'removeWhitelist','restoreBackupFile','toggleAdminSession']);
  document.addEventListener('click', event => {
    const element = event.target.closest('[data-command]');
    if (!element) return;
    const match = /^([A-Za-z]+)\((.*)\)$/.exec(element.dataset.command.trim());
    if (!match || !allowed.has(match[1])) return;
    const args = []; let input = match[2].trim();
    while (input) {
      // Deliberately exclude quotes, escapes and syntax inside string arguments.
      const arg = /^(?:'([^'"\\<>]*)'|(-?\d+(?:\.\d+)?))(?:\s*,\s*|$)/.exec(input);
      if (!arg) return;
      args.push(arg[1] === undefined ? Number(arg[2]) : arg[1]);
      input = input.slice(arg[0].length);
    }
    if (typeof window[match[1]] === 'function') window[match[1]](...args);
  });
})();
