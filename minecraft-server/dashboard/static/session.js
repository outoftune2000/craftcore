/* Same-origin fetches carry Tornado's anti-CSRF token. Sessions stay HttpOnly. */
(() => {
  const original = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin === location.origin) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      const match = document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
      if (match) headers.set('X-XSRFToken', decodeURIComponent(match[1]));
      options = {...options, headers};
    }
    const response = await original(input, options);
    if (response.status === 401 && location.pathname !== '/login' && location.pathname !== '/setup') location.assign('/login');
    return response;
  };
  window.craftLogout = async () => { await fetch('/api/auth/logout', {method: 'POST'}); location.assign('/login'); };
})();
