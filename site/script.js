// Copy buttons for the install commands and the latest release number. Everything else on the page works
// without JavaScript.
(function () {
  document.documentElement.classList.add('js');
  // Latest published release from the GitHub API (one anonymous request per page view). On any failure the
  // static text stays. Only a plain version tag is ever shown.
  var latest = document.getElementById('latest-release');
  if (latest && window.fetch) {
    fetch('https://api.github.com/repos/suriyavasan/foundry-os/releases/latest', { headers: { Accept: 'application/vnd.github+json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        if (rel && typeof rel.tag_name === 'string' && /^v\d+\.\d+\.\d+$/.test(rel.tag_name)) {
          latest.textContent = 'Early software. Latest release: ' + rel.tag_name;
        }
      })
      .catch(function () {});
  }
  var live = document.getElementById('copy-status');
  function fallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function done(btn, ok) {
    var old = btn.dataset.label || btn.textContent;
    btn.dataset.label = old;
    btn.textContent = ok ? 'Copied' : 'Press Ctrl+C';
    if (ok) btn.classList.add('ok');
    if (live) live.textContent = ok ? 'Command copied to clipboard' : 'Copy failed. Select the text and copy it.';
    setTimeout(function () { btn.textContent = old; btn.classList.remove('ok'); }, 1800);
  }
  var buttons = document.querySelectorAll('.copy');
  Array.prototype.forEach.call(buttons, function (btn) {
    btn.addEventListener('click', function () {
      var target = document.getElementById(btn.getAttribute('data-target'));
      if (!target) return;
      var text = target.textContent.trim();
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { done(btn, true); }, function () { done(btn, fallback(text)); });
      } else {
        done(btn, fallback(text));
      }
    });
  });
})();
