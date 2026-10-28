// Copy buttons, the latest release number, the hero install tabs and the hero's notch illustration. Everything
// else on the page works without JavaScript.
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
          latest.textContent = 'Early software · latest release ' + rel.tag_name;
        }
      })
      .catch(function () {});
  }
  // Header: white pill once scrolled; the Start button appears only after the hero's install bar is out of view.
  var top = document.querySelector('.top');
  var heroGet = document.querySelector('.get');
  if (top) {
    var onScroll = function () {
      top.classList.toggle('scrolled', window.scrollY > 8);
      top.classList.toggle('past', !heroGet || heroGet.getBoundingClientRect().bottom < 72);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
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
  function copyText(text, cb) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { cb(true); }, function () { cb(fallback(text)); });
    } else {
      cb(fallback(text));
    }
  }
  var buttons = document.querySelectorAll('.copy');
  Array.prototype.forEach.call(buttons, function (btn) {
    btn.addEventListener('click', function () {
      var target = document.getElementById(btn.getAttribute('data-target'));
      if (!target) return;
      copyText(target.textContent.trim(), function (ok) { done(btn, ok); });
    });
  });

  // Hero install: tabs pick the command; the gooey copy button drops a check when it worked.
  var cmd = document.getElementById('hero-cmd');
  var heroCopy = document.getElementById('hero-copy');
  var tabs = document.querySelectorAll('.get-tabs [role=tab]');
  var then = document.getElementById('hero-then');
  var thenDefault = then ? then.innerHTML : '';
  Array.prototype.forEach.call(tabs, function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.forEach.call(tabs, function (t) { t.setAttribute('aria-selected', String(t === tab)); });
      cmd.textContent = tab.getAttribute('data-cmd');
      if (then) then.innerHTML = tab.getAttribute('data-then') || thenDefault;
      cmd.scrollLeft = 0;
    });
  });
  if (heroCopy && cmd) {
    var wrap = heroCopy.parentNode, timer;
    heroCopy.addEventListener('click', function () {
      copyText(cmd.textContent.trim(), function (ok) {
        heroCopy.textContent = ok ? 'Copied' : 'Ctrl+C';
        if (live) live.textContent = ok ? 'Command copied to clipboard' : 'Copy failed. Select the text and copy it.';
        if (ok) wrap.classList.add('done');
        clearTimeout(timer);
        timer = setTimeout(function () { wrap.classList.remove('done'); heroCopy.textContent = 'Copy'; }, 1800);
      });
    });
  }

  // Hero notch: an illustration of one note flowing through the company. Not live data. The approval step
  // waits for a click; with no click it moves on as "still waiting" and never approves by itself.
  var notch = document.getElementById('notch');
  if (notch) {
    var av = document.getElementById('n-av'), who = document.getElementById('n-who'), title = document.getElementById('n-title');
    var body = document.getElementById('n-body'), textEl = document.getElementById('n-text'), actions = document.getElementById('n-actions');
    var colors = { you: '#3950e6', ai: '#ff6a00', hr: '#ff8fc7', ask: '#ffc23d', ds: '#c6f36b' };
    var steps = [
      { av: 'You', c: 'you', who: 'You', title: 'sent a note', text: '<b>“Build a landing page for our first product.”</b>' },
      { av: 'AI', c: 'ai', who: 'AI Co-founder', title: 'made a plan', text: 'Set <b>1 goal</b> and <b>1 project</b>, picked a lead and replied to your note.' },
      { av: 'HR', c: 'hr', who: 'HR', title: 'hired a Designer', text: 'Found a <b>landing-page</b> skill, scanned it for safety and assigned it.' },
      { av: '$', c: 'ask', who: 'Approval needed', title: 'buy a domain', text: 'The team wants a domain for <b>$12.00</b>. Agents can’t approve their own spending.', ask: true },
      null, // filled in by the approval choice
      { av: 'D', c: 'ds', who: 'Designer', title: 'saved the work', text: '<b>landing-page.html</b> is ready, and the lead is reviewing it.' }
    ];
    var outcome = {
      yes: { av: '✓', c: 'ds', who: 'You', title: 'approved $12.00', text: 'Approved for <b>exactly $12.00</b> and written to the audit log.' },
      no: { av: '×', c: 'you', who: 'You', title: 'declined', text: 'Nothing was bought. The team <b>goes ahead without a domain</b>.' },
      wait: { av: '$', c: 'ask', who: 'Approval', title: 'still waiting for you', text: 'Nothing is bought until a person decides. <b>The rest of the work carries on.</b>' }
    };
    var i = 0, t;
    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var setOpen = function (open) {
      notch.classList.toggle('open', open);
      notch.style.height = open ? (52 + body.scrollHeight) + 'px' : '52px';
    };
    var show = function (st) {
      av.textContent = st.av; av.style.background = colors[st.c]; av.style.color = st.c === 'you' || st.c === 'ai' ? '#fff' : '#1e1e24';
      who.textContent = st.who; title.textContent = st.title; textEl.innerHTML = st.text;
      actions.innerHTML = '';
      if (st.ask) {
        [['Decline', 'no'], ['Approve $12.00', 'yes']].forEach(function (a) {
          var b = document.createElement('button');
          b.type = 'button'; b.textContent = a[0]; if (a[1] === 'yes') b.className = 'yes';
          b.addEventListener('click', function () { choose(a[1]); });
          actions.appendChild(b);
        });
      }
    };
    var choose = function (k) { clearTimeout(t); steps[4] = outcome[k]; next(); };
    var next = function () {
      i += 1;
      if (i >= steps.length) { i = -1; setOpen(false); t = setTimeout(next, 1400); return; }
      if (i === 4 && !steps[4]) steps[4] = outcome.wait;
      setOpen(false);
      t = setTimeout(function () {
        show(steps[i]);
        setOpen(true);
        t = setTimeout(function () { if (i === steps.length - 1) steps[4] = null; next(); }, steps[i].ask ? 7000 : 3200);
      }, reduceMotion ? 0 : 520);
    };
    i = -1;
    t = setTimeout(next, 900);
    window.addEventListener('resize', function () { if (notch.classList.contains('open')) setOpen(true); });
  }
})();
