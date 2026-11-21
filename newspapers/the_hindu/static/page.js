(function () {
  var sheet = document.getElementById('sheet'), widths = [700, 900, 1100, 1300, 1600, 2000, 2600], zi = 0;
  function setW(i) {
    zi = Math.max(0, Math.min(widths.length - 1, i)); sheet.style.setProperty('--w', widths[zi] + 'px');
    requestAnimationFrame(fitAll);
  }
  var vw = Math.min(window.innerWidth - 28, 1300); widths.forEach(function (w, i) { if (w <= vw) zi = i; });
  sheet.style.setProperty('--w', widths[zi] + 'px');
  document.getElementById('zin').onclick = function () { setW(zi + 1) };
  document.getElementById('zout').onclick = function () { setW(zi - 1) };
  var mode = document.getElementById('mode');
  if (mode) mode.onclick = function () {
    var on = sheet.classList.toggle('scan');
    mode.textContent = on ? 'Show reconstructed layout' : 'Show original scan';
  };
  var pb = document.getElementById('print'); if (pb) pb.onclick = function () { window.print(); };

  function K(a, v) { a.style.setProperty('--k', v); }
  /* plain articles: headline + balanced columns, grow text until the box is full */
  function fitSimple(art) {
    var inner = art.querySelector('.inner'), H = art.clientHeight; if (!inner || !H) return;
    var lo = .4, hi = 1.15, best = lo;
    for (var i = 0; i < 8; i++) { var m = (lo + hi) / 2; K(art, m); if (inner.offsetHeight <= H) { best = m; lo = m; } else { hi = m; } }
    K(art, best);
  }
  /* promo cards: text sized to the coloured panel */
  function fitPanel(art) {
    var pp = art.querySelector('.pp'), pin = pp && pp.querySelector('.pin'); if (!pin) return;
    var cs = getComputedStyle(pp), H = pp.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    var lo = .3, hi = 2.4, best = lo;
    for (var i = 0; i < 8; i++) {
      var m = (lo + hi) / 2; K(art, m);
      if (pin.offsetHeight <= H && pp.scrollWidth <= pp.clientWidth + 1) { best = m; lo = m; } else { hi = m; }
    }
    K(art, best);
  }
  function subtract(ivs, a, b) {
    var r = []; ivs.forEach(function (v) {
      if (b <= v[0] || a >= v[1]) { r.push(v); return; }
      if (a > v[0]) r.push([v[0], a]); if (b < v[1]) r.push([b, v[1]]);
    }); return r;
  }
  /* articles with photos: photos stay at their printed rectangles, text flows column by column around them */
  function layout(art, k) {
    K(art, k);
    var W = art.clientWidth, H = art.clientHeight, head = art.querySelector('.head'),
      flow = art.querySelector('.flow'), src = art.querySelector('.src');
    var fs = parseFloat(getComputedStyle(art).fontSize), lh = fs * 1.22,
      nc = +art.dataset.nc || 1, gut = W * (+art.dataset.gut || 0), cw = (W - (nc - 1) * gut) / nc;
    if (!art._p) art._p = Array.prototype.map.call(src.children, function (p) { return { t: p.textContent, c: p.className }; });
    var figEls = Array.prototype.slice.call(art.querySelectorAll('.ph'));
    figEls.forEach(function (f) { if (!f.dataset.t) f.dataset.t = f.style.top; f.style.top = f.dataset.t; });
    var rects = figEls.map(function (f) {
      return { x: f.offsetLeft, y: f.offsetTop, w: f.offsetWidth, h: f.offsetHeight };
    });
    var headB = 0;
    if (head) {
      /* a wide photo at the very top: the headline goes underneath it */
      var topFig = 0; rects.forEach(function (r) { if (r.w > .65 * W && r.y < .035 * H) topFig = Math.max(topFig, r.y + r.h + fs * .3); });
      head.style.marginTop = topFig ? topFig + 'px' : ''; art.style.setProperty('--hs', 1);
      var bottom = function () { return head.offsetTop + head.offsetHeight + fs * .25; };
      var clash = function () {
        var hl = head.offsetLeft, hr = hl + head.offsetWidth;
        return rects.some(function (r) { return r.y >= .035 * H && r.y < headB - 1 && r.x < hr && r.x + r.w > hl; });
      };
      headB = bottom();
      /* a photo meant to sit below the headline: shrink the headline (our font is wider than print) */
      var steps = [.9, .8, .7, .62, .55];
      for (var si = 0; si < steps.length && clash(); si++) { art.style.setProperty('--hs', steps[si]); headB = bottom(); }
      /* still touching: nudge the photo down by the shortfall (at most 10% of the box) */
      var hl0 = head.offsetLeft, hr0 = hl0 + head.offsetWidth;
      rects.forEach(function (r, i) {
        if (r.y >= .035 * H && r.y < headB - 1 && r.x < hr0 && r.x + r.w > hl0) {
          var d = Math.min(headB - r.y, .1 * H); r.y += d; figEls[i].style.top = r.y + 'px';
        }
      });
    }
    var slots = [];
    for (var c = 0; c < nc; c++) {
      var x0 = c * (cw + gut), x1 = x0 + cw,
        under = !head || (x1 > head.offsetLeft + 2 && x0 < head.offsetLeft + head.offsetWidth - 2),  /* column sits below the headline? */
        ivs = [[under ? headB : 0, H]];
      rects.forEach(function (r) { if (r.x < x1 - 2 && r.x + r.w > x0 + 2) ivs = subtract(ivs, r.y - fs * .3, r.y + r.h + fs * .3); });
      ivs.forEach(function (v) { if (v[1] - v[0] >= lh * 2) slots.push({ x: x0, y: v[0], w: cw, h: v[1] - v[0] }); });
    }
    flow.innerHTML = '';
    var pi = 0, rest = null;
    for (var s = 0; s < slots.length && pi < art._p.length; s++) {
      var S = slots[s], el = document.createElement('div'); el.className = 'slot';
      el.style.cssText = 'left:' + S.x + 'px;top:' + S.y + 'px;width:' + S.w + 'px;height:' + S.h + 'px';
      flow.appendChild(el);
      if (S.x > 0) {
        var rl = document.createElement('div'); rl.className = 'crule';
        rl.style.cssText = 'left:' + (S.x - gut / 2) + 'px;top:' + S.y + 'px;height:' + S.h + 'px'; flow.appendChild(rl);
      }
      while (pi < art._p.length) {
        var words = rest || art._p[pi].t.split(' '); rest = null;
        var p = document.createElement('p'); p.className = art._p[pi].c; el.appendChild(p);
        p.textContent = words.join(' ');
        if (el.scrollHeight <= el.clientHeight + .5) { pi++; continue; }
        var lo = 0, hi = words.length - 1;
        while (lo < hi) {
          var m = (lo + hi + 1) >> 1; p.textContent = words.slice(0, m).join(' ');
          if (el.scrollHeight <= el.clientHeight + .5) lo = m; else hi = m - 1;
        }
        if (lo === 0) { el.removeChild(p); } else { p.textContent = words.slice(0, lo).join(' '); }
        rest = words.slice(lo); break;
      }
    }
    var ok = pi >= art._p.length; art.dataset.over = ok ? '0' : '1'; return ok;
  }
  function fitPlaced(art) {
    if (layout(art, 1.15)) return;
    var lo = .45, hi = 1.15, best = 0;
    for (var i = 0; i < 7; i++) { var m = (lo + hi) / 2; if (layout(art, m)) { best = m; lo = m; } else { hi = m; } }
    layout(art, best || .45);
  }
  function fitOne(art) {
    if (art.classList.contains('bleed')) return;
    if (art.classList.contains('pcard')) fitPanel(art);
    else if (art.classList.contains('placed')) fitPlaced(art);
    else fitSimple(art);
  }
  function fitAll() {
    var a = document.querySelectorAll('.art'); for (var i = 0; i < a.length; i++)fitOne(a[i]);
    document.documentElement.setAttribute('data-fit', '1');
  }
  window.__fit = fitAll;
  window.addEventListener('beforeprint', fitAll);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll); else window.addEventListener('load', fitAll);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowLeft' && window.PREV) location.href = window.PREV;
    if (e.key === 'ArrowRight' && window.NEXT) location.href = window.NEXT;
  });
})();
