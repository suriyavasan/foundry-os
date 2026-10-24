// Applies the saved theme before first paint. Kept as a file (not inline) so the Content-Security-Policy
// can forbid inline scripts.
try {
  var t = localStorage.getItem('company-os.theme');
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
} catch (e) {}
