/* ============================================================================
   2Day · dialog.js — el controlador único de diálogos
   Requiere dialog.css. Copiar a backend/frontend/static/dialog.js
   Cargar con:  <script src="/static/dialog.js" defer></script>

   API
     Dlg.open({size, eyebrow, title, sub, pills, tone, body, actions, onClose})
       → devuelve un id; el diálogo se apila sobre el que ya hubiera.
     Dlg.close(id)          cierra ese; sin id, el de arriba
     Dlg.closeAll()
     Dlg.setBody(id, html)  sustituye el cuerpo sin re-abrir (para cargas)
     Dlg.loading({...})     abre con esqueleto; devuelve el id
     Dlg.error(id, {title, text, onRetry})
     Dlg.confirm({...})     → Promise<boolean>
     Dlg.prompt({...})      → Promise<string|null>
     Dlg.el(id)             el nodo .dlg, por si hay que enganchar listeners

   Qué resuelve respecto al código actual
     · Una sola pila: se acabó modal(z-50) detrás de sheet(z-60).
     · Escape cierra SOLO el de arriba. Antes cada implementación registraba su
       propio listener global en document y cerraban en cascada.
     · Trampa de foco y devolución del foco al disparador.
     · Bloqueo de scroll del fondo con compensación de la barra.
     · role/aria-modal/aria-labelledby en todos los diálogos.
   ========================================================================== */
(function (global) {
  'use strict';

  var SEL_FOCUS = 'a[href],button:not([disabled]),input:not([disabled]),' +
    'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  var stack = [];          // [{id, scrim, box, opts, returnTo}]
  var seq = 0;
  var scrollLock = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var ICONS = {
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    warn: '<path d="M12 8v5M12 16v.01M10.3 3.9L2.8 17a1.5 1.5 0 0 0 1.3 2.3h15.8a1.5 1.5 0 0 0 1.3-2.3L13.7 3.9a1.5 1.5 0 0 0-2.6 0z"/>',
    info: '<path d="M12 11v6M12 7.5v.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"/>',
    ok: '<path d="M5 13l4 4L19 7"/>'
  };
  function svg(name, size) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"' +
      (size ? ' style="width:' + size + 'px;height:' + size + 'px"' : '') + '>' +
      (ICONS[name] || '') + '</svg>';
  }

  // ── scroll del fondo ────────────────────────────────────────────────────
  function lock() {
    if (scrollLock) return;
    var h = document.documentElement, b = document.body;
    var gap = window.innerWidth - h.clientWidth;
    scrollLock = { hOv: h.style.overflow, bOv: b.style.overflow, pr: b.style.paddingRight };
    if (gap > 0) b.style.paddingRight = gap + 'px';   // evita el salto lateral
    // SOLO en <html>. Ponerlo también en <body> ROMPE `position:sticky`: al volverse
    // ambos contenedores de desplazamiento, un elemento pegajoso (la barra lateral de
    // los paneles) pierde su anclaje y se va con el scroll, dejando un hueco vacío.
    // El viewport toma su overflow de <html>, así que con esto basta para bloquear.
    h.style.overflow = 'hidden';
  }
  function unlock() {
    if (!scrollLock) return;
    document.documentElement.style.overflow = scrollLock.hOv;
    document.body.style.overflow = scrollLock.bOv;
    document.body.style.paddingRight = scrollLock.pr;
    scrollLock = null;
  }

  // ── foco ────────────────────────────────────────────────────────────────
  function focusables(root) {
    if (!root) return [];
    return Array.prototype.slice.call(root.querySelectorAll(SEL_FOCUS))
      .filter(function (el) { return el.offsetParent !== null; });
  }
  function focusInto(box) {
    var f = focusables(box);
    var field = null;
    for (var i = 0; i < f.length; i++) {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(f[i].tagName)) { field = f[i]; break; }
    }
    // En pantallas táctiles NO se enfoca un campo al abrir: el teclado saltaba y tapaba
    // media hoja en todos los formularios. Se enfoca la caja (sigue funcionando Tab/Escape).
    if (field && TOUCH()) field = null;
    var target = field || (TOUCH() ? box : f[0]) || box;
    if (target === box) box.setAttribute('tabindex', '-1');
    requestAnimationFrame(function () {
      try { target.focus({ preventScroll: true }); } catch (e) { }
    });
  }

  function TOUCH() {
    try { return window.matchMedia('(hover: none) and (pointer: coarse)').matches; } catch (e) { return false; }
  }

  // ── construcción del marcado ────────────────────────────────────────────
  function buildHeader(o, id) {
    var bare = o.size === 'xs' && !o.eyebrow && !o.sub && !o.pills;
    var h = '<div class="dlg-hd"' + (bare ? ' data-bare' : '') + '>';
    if (o.tone) h += '<div class="icon" data-tone="' + esc(o.tone) + '">' + svg(o.tone === 'ok' ? 'ok' : o.tone === 'info' ? 'info' : 'warn') + '</div>';
    h += '<div class="t">';
    if (o.eyebrow) h += '<div class="eyebrow">' + esc(o.eyebrow) + '</div>';
    h += '<h3 id="' + id + '-t">' + esc(o.title || '') + '</h3>';
    if (o.sub) h += '<div class="sub">' + o.sub + '</div>';
    if (o.pills) h += '<div class="pills">' + o.pills + '</div>';
    h += '</div>';
    // La X existe siempre salvo que el diálogo sea una decisión obligatoria.
    if (o.dismissable !== false) {
      h += '<button class="dlg-x" data-dlg-close aria-label="Cerrar">' + svg('close') + '</button>';
    }
    return h + '</div>';
  }

  function buildFooter(o) {
    var acts = o.actions || [];
    if (!acts.length && !o.nota) return '';
    var left = [], right = [];
    // `nota`: una frase a la izquierda del pie que dice POR QUÉ la acción está bloqueada.
    // Un botón deshabilitado sin explicación deja a la persona buscando qué le falta.
    if (o.nota) left.push('<span class="dlg-nota">' + esc(o.nota) + '</span>');
    acts.forEach(function (a, i) {
      var cls = 'btn' + (a.variant === 'primary' ? ' primary' : a.variant === 'ghost' ? ' ghost' : '');
      var st = a.variant === 'danger'
        ? ' style="border-color:var(--danger);background:var(--danger);color:#fff"'
        : a.variant === 'danger-quiet'
          ? ' style="border-color:transparent;background:none;color:var(--danger)"'
          : '';
      var b = '<button class="' + cls + '"' + st + ' data-dlg-act="' + i + '"' +
        (a.disabled ? ' disabled' : '') +
        (a.id ? ' id="' + esc(a.id) + '"' : '') + '>' + esc(a.label) + '</button>';
      (a.align === 'left' ? left : right).push(b);
    });
    return '<div class="dlg-ft">' + left.join('') +
      '<div class="spacer"></div>' + right.join('') + '</div>';
  }

  // ── apertura ────────────────────────────────────────────────────────────
  function open(o) {
    o = o || {};
    var id = 'dlg' + (++seq);
    var level = stack.length;

    var scrim = document.createElement('div');
    scrim.className = 'dlg-scrim';
    scrim.id = id + '-scrim';
    if (level) scrim.setAttribute('data-level', String(Math.min(level, 2)));

    scrim.innerHTML =
      '<div class="dlg" id="' + id + '" role="dialog" aria-modal="true" ' +
      'aria-labelledby="' + id + '-t" data-size="' + esc(o.size || 'md') + '">' +
      buildHeader(o, id) +
      '<div class="dlg-bd"' + (o.flush ? ' data-flush' : '') + ' id="' + id + '-bd">' +
      (o.body || '') + '</div>' +
      buildFooter(o) +
      '</div>';

    document.body.appendChild(scrim);
    var box = scrim.querySelector('.dlg');
    var entry = {
      id: id, scrim: scrim, box: box, opts: o,
      returnTo: document.activeElement
    };
    stack.push(entry);
    if (stack.length === 1) lock();

    // clic en el velo — solo si el objetivo ES el velo
    scrim.addEventListener('mousedown', function (e) {
      if (e.target !== scrim) return;
      entry._scrimDown = true;
    });
    scrim.addEventListener('click', function (e) {
      if (e.target !== scrim || !entry._scrimDown) return;
      entry._scrimDown = false;
      if (o.dismissable !== false) close(id);
    });

    // cierre y acciones
    scrim.addEventListener('click', function (e) {
      var x = e.target.closest && e.target.closest('[data-dlg-close]');
      if (x) { close(id); return; }
      var a = e.target.closest && e.target.closest('[data-dlg-act]');
      if (!a) return;
      var act = (o.actions || [])[+a.dataset.dlgAct];
      if (!act) return;
      if (typeof act.onClick === 'function') {
        // devolver false desde onClick mantiene el diálogo abierto
        if (act.onClick(entry) === false) return;
      }
      if (act.keepOpen !== true) close(id);
    });

    if (o.dismissable !== false) swipeToClose(entry);
    focusInto(box);
    return id;
  }

  // Deslizar la hoja hacia abajo desde la cabecera la cierra (solo en modo hoja, <640px).
  function swipeToClose(entry) {
    var box = entry.box, hd = box.querySelector('.dlg-hd');
    if (!hd || !('ontouchstart' in window)) return;
    var y0 = null, dy = 0;
    hd.addEventListener('touchstart', function (e) {
      if (window.innerWidth > 640 || e.touches.length !== 1) return;
      if (e.target.closest && e.target.closest('button,a,input,select,textarea')) return;
      y0 = e.touches[0].clientY; dy = 0; box.style.transition = 'none';
    }, { passive: true });
    hd.addEventListener('touchmove', function (e) {
      if (y0 === null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      box.style.transform = dy ? 'translateY(' + dy + 'px)' : '';
    }, { passive: true });
    hd.addEventListener('touchend', function () {
      if (y0 === null) return;
      y0 = null; box.style.transition = 'transform .18s ease';
      if (dy > 90) { close(entry.id); return; }
      box.style.transform = '';
      setTimeout(function () { box.style.transition = ''; }, 200);
    });
  }

  function close(id) {
    var i = id
      ? stack.findIndex(function (e) { return e.id === id; })
      : stack.length - 1;
    if (i < 0) return;
    // cerrar uno de en medio cierra también lo que tenga encima
    var doomed = stack.splice(i);
    doomed.reverse().forEach(function (e) {
      if (e.scrim && e.scrim.parentNode) e.scrim.parentNode.removeChild(e.scrim);
      if (typeof e.opts.onClose === 'function') { try { e.opts.onClose(); } catch (x) { } }
    });
    if (!stack.length) {
      unlock();
      var back = doomed[doomed.length - 1] && doomed[doomed.length - 1].returnTo;
      if (back && document.contains(back)) {
        requestAnimationFrame(function () { try { back.focus({ preventScroll: true }); } catch (e) { } });
      }
    } else {
      focusInto(stack[stack.length - 1].box);
    }
  }

  function closeAll() { while (stack.length) close(); }

  // ── teclado: un solo listener para toda la pila ─────────────────────────
  document.addEventListener('keydown', function (e) {
    if (!stack.length) return;
    var top = stack[stack.length - 1];

    if (e.key === 'Escape') {
      if (top.opts.dismissable === false) return;
      e.preventDefault();
      close(top.id);          // solo el de arriba
      return;
    }
    if (e.key === 'Enter') {
      // Enter confirma SOLO si: el diálogo no lo desactivó (enterSubmits:false), el foco
      // está en un INPUT de texto DENTRO de este diálogo, nadie más manejó la tecla y no
      // se está componiendo texto (IME). Antes bastaba con estar en cualquier INPUT o
      // SELECT: en la solicitud del coordinador, corregir el odómetro y dar Intro
      // AUTORIZABA con la lectura vieja.
      if (e.defaultPrevented || e.isComposing || top.opts.enterSubmits === false) return;
      var t = document.activeElement;
      if (!t || t.tagName !== 'INPUT' || !top.box.contains(t)) return;
      if (/^(checkbox|radio|file|button|submit|range|color)$/i.test(t.type || '')) return;
      if (t.closest && t.closest('[data-noenter]')) return;
      var acts = top.opts.actions || [];
      var prim = null;
      for (var k = 0; k < acts.length; k++) {
        if (acts[k].variant === 'primary' || acts[k].variant === 'danger') { prim = k; break; }
      }
      if (prim === null) return;
      var btn = top.box.querySelector('[data-dlg-act="' + prim + '"]');
      if (btn) { e.preventDefault(); btn.click(); }
      return;
    }
    if (e.key !== 'Tab') return;

    // trampa de foco
    var f = focusables(top.box);
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  // ── estados ─────────────────────────────────────────────────────────────
  var SKEL = '<div class="dlg-skel"><i></i><i></i><i></i><i></i></div>';

  function el(id) {
    var e = stack.find(function (x) { return x.id === id; });
    return e ? e.box : null;
  }
  function setBody(id, html) {
    var bd = document.getElementById(id + '-bd');
    if (bd) { bd.innerHTML = html; bd.scrollTop = 0; }
  }
  function loading(o) {
    o = o || {};
    o.body = SKEL;
    return open(o);
  }
  // La cabecera y el pie, DESPUÉS de abrir. `open()` los arma una sola vez al construir el
  // diálogo, y por eso el patrón "abro cargando y relleno cuando llega la respuesta" no podía
  // darles forma: cada panel se inventó su propio ayudante local —dashboard.html tiene
  // dlgHead/dlgActions y coordinador.html no—, y de ahí que los diálogos del puente se
  // queden con una cabecera reducida a la X y las acciones sueltas dentro del cuerpo.
  function setHead(id, h) {
    var box = el(id);
    if (!box) return;
    var hd = box.querySelector('.dlg-hd');
    if (!hd) return;
    hd.removeAttribute('data-bridge');            // deja de ser una cabecera de puente
    var t = hd.querySelector('.t');
    if (!t) { t = document.createElement('div'); t.className = 't'; hd.insertBefore(t, hd.firstChild); }
    h = h || {};
    t.innerHTML =
      (h.eyebrow ? '<div class="eyebrow">' + esc(h.eyebrow) + '</div>' : '') +
      '<h3 id="' + id + '-t">' + esc(h.title || '') + '</h3>' +
      (h.sub ? '<div class="sub">' + h.sub + '</div>' : '') +
      (h.pills ? '<div class="pills">' + h.pills + '</div>' : '');
  }

  function setActions(id, acts, nota) {
    var entry = stack.find(function (x) { return x.id === id; });
    var box = el(id);
    if (!box || !entry) return;
    entry.opts.actions = acts || [];             // el manejador de clics las lee en vivo
    if (arguments.length > 2) entry.opts.nota = nota;
    var ft = box.querySelector('.dlg-ft');
    if ((!acts || !acts.length) && !entry.opts.nota) { if (ft) ft.remove(); return; }
    if (!ft) { ft = document.createElement('div'); ft.className = 'dlg-ft'; box.appendChild(ft); }
    // `outerHTML` DESTRUYE el nodo del pie, y con él el botón que tenía el foco. A
    // diferencia de `close()`, aquí no se recolocaba: `document.activeElement` quedaba en
    // <body> y la trampa de foco del keydown dejaba de enganchar -sólo actúa cuando el activo
    // es el primero o el último de la caja-, así que el primer Tab se iba fuera del diálogo.
    var teniaFoco = box.contains(document.activeElement);
    // si el pie era uno adoptado del cuerpo (adoptActions), sus botones se conservan
    var adoptados = ft.hasAttribute('data-adopted')
      ? Array.prototype.slice.call(ft.querySelectorAll('button')) : [];
    ft.outerHTML = buildFooter(entry.opts);
    if (adoptados.length) {
      var nf = box.querySelector('.dlg-ft');
      if (nf) adoptados.forEach(function (b) { nf.insertBefore(b, nf.querySelector('.spacer')); });
    }
    // Sólo se recoloca si el foco se quedó FUERA. Si quien llamó ya lo puso donde quería
    // -el alta de usuario lo manda al recuadro de la contraseña-, no se le pisa.
    if (teniaFoco && !box.contains(document.activeElement)) focusInto(box);
  }

  function error(id, o) {
    o = o || {};
    // El título se queda en «Cargando…» si no se toca: el patrón de la casa abre con
    // Dlg.loading({title:'Cargando…'}) y sustituye el cuerpo al fallar, así que el diálogo
    // acababa diciendo que carga encima de un mensaje de error.
    setHead(id, {title: o.title || 'No se pudo cargar', eyebrow: o.eyebrow});
    setActions(id, o.onRetry
      ? [{label: 'Cerrar'}, {label: 'Reintentar', variant: 'primary', keepOpen: true, onClick: o.onRetry}]
      : [{label: 'Cerrar'}]);
    var html = '<div class="dlg-state">' +
      '<div><div class="icon" data-tone="danger">' + svg('warn') + '</div>' +
      '<b>' + esc(o.title || 'No se pudo cargar') + '</b>' +
      '<p>' + esc(o.text || 'El servidor no respondió.') + '</p>' +
      '</div></div>';
    setBody(id, html);
  }
  function empty(id, o) {
    o = o || {};
    setBody(id, '<div class="dlg-state">' +
      '<div><div class="icon" data-tone="ok">' + svg('ok') + '</div>' +
      '<b>' + esc(o.title || 'Nada por aquí') + '</b>' +
      '<p>' + esc(o.text || '') + '</p></div></div>');
  }

  // ── confirm / prompt · reemplazan ask() ─────────────────────────────────
  function confirm(o) {
    o = o || {};
    return new Promise(function (resolve) {
      var done = false;
      var fin = function (v) { if (done) return; done = true; resolve(v); };
      open({
        size: 'xs',
        tone: o.tone || 'warn',
        title: o.title || 'Confirmar',
        body: o.text ? '<p style="font:var(--t-sm);color:var(--muted);margin:0;white-space:pre-line">' + esc(o.text) + '</p>' : '',
        dismissable: o.dismissable,
        actions: [
          { label: o.cancelLabel || 'Cancelar', onClick: function () { fin(false); } },
          { label: o.okLabel || o.okText || o.ok || 'Confirmar', variant: o.danger ? 'danger' : 'primary', onClick: function () { fin(true); } }
        ],
        onClose: function () { fin(false); }
      });
    });
  }

  function prompt(o) {
    o = o || {};
    return new Promise(function (resolve) {
      var done = false;
      var fin = function (v) { if (done) return; done = true; resolve(v); };
      var iid = 'dlg-in-' + (++seq);
      open({
        size: 'xs',
        title: o.title || '',
        body:
          (o.text ? '<p style="font:var(--t-sm);color:var(--muted);margin:0 0 12px;white-space:pre-line">' + esc(o.text) + '</p>' : '') +
          (o.label ? '<label for="' + iid + '">' + esc(o.label) + '</label>' : '') +
          '<input id="' + iid + '" type="' + esc(o.type || 'text') + '"' +
          ' value="' + esc(o.value || '') + '"' +
          ' placeholder="' + esc(o.placeholder || '') + '">',
        actions: [
          { label: o.cancelLabel || 'Cancelar', onClick: function () { fin(null); } },
          {
            label: o.okLabel || 'Guardar', variant: 'primary',
            onClick: function () {
              var i = document.getElementById(iid);
              fin(i ? i.value : null);
            }
          }
        ],
        onClose: function () { fin(null); }
      });
      var i = document.getElementById(iid);
      if (i) requestAnimationFrame(function () { i.focus(); try { i.select(); } catch (e) { } });
    });
  }

  // ── Puente: botones al pie ───────────────────────────────────────────────
  // Los cuerpos antiguos (openModal/openSheet) terminan con una fila de botones DENTRO del
  // cuerpo: se iban con el scroll y en el celular quedaban fuera de la vista. Si el último
  // bloque del cuerpo es SOLO botones, se mudan al pie fijo (con sus onclick intactos).
  // Un botón que solo cierra (closeModal()/closeSheet()) sobra: la X ya está.
  var SOLO_CIERRA = /^\s*(?:return\s+)?close(?:Modal|Sheet)\(\)\s*;?\s*$/;
  var PELIGRO = /eliminar|borrar|quitar|inhabilitar|anular|descartar/i;
  function adoptActions(id) {
    var box = el(id); if (!box) return;
    var old = box.querySelector('.dlg-ft[data-adopted]'); if (old) old.remove();
    if (box.querySelector('.dlg-ft')) return;             // ya tiene pie nativo
    var bd = box.querySelector('.dlg-bd'); if (!bd) return;
    var row = bd.lastElementChild;
    if (!row || row.tagName !== 'DIV' || row.id) return;
    if (row.querySelector('input,select,textarea,table,canvas,img,svg:not(button svg)')) return;
    var btns = Array.prototype.slice.call(row.querySelectorAll('button'));
    if (!btns.length || btns.length > 5) return;
    var resto = row.cloneNode(true);
    Array.prototype.forEach.call(resto.querySelectorAll('button'), function (b) { b.remove(); });
    if (resto.textContent.trim()) return;                 // la fila trae texto: no es solo botones
    var cierra = function (b) { return SOLO_CIERRA.test(b.getAttribute('onclick') || ''); };
    var otros = btns.filter(function (b) { return !cierra(b); }).length;
    // Se quita el «Cerrar» (repite la X). Un «Cancelar» junto a Guardar se conserva: en un
    // formulario es la salida que la gente busca. Si es el único botón, también sobra.
    var keep = btns.filter(function (b) {
      return !cierra(b) || (otros > 0 && !/^\s*cerrar\s*$/i.test(b.textContent));
    });
    row.remove();
    if (!keep.length) return;
    var ft = document.createElement('div');
    ft.className = 'dlg-ft'; ft.setAttribute('data-adopted', '');
    var izq = keep.filter(function (b) { return PELIGRO.test(b.textContent); });
    var der = keep.filter(function (b) { return izq.indexOf(b) < 0; });
    izq.forEach(function (b) { b.style.margin = ''; ft.appendChild(b); });
    var sp = document.createElement('div'); sp.className = 'spacer'; ft.appendChild(sp);
    der.forEach(function (b) { b.style.margin = ''; ft.appendChild(b); });
    box.appendChild(ft);
  }

  // ── «Clic que no hace nada» ─────────────────────────────────────────────
  // Muchas funciones que abren un diálogo hacen `await api(...)` sin try/catch: si el
  // servidor falla, la promesa se rechaza en silencio y el botón parece muerto. Si eso
  // pasa poco después de un clic de la persona, se le avisa con el toast del panel.
  var ultimoClic = 0;
  document.addEventListener('pointerdown', function () { ultimoClic = Date.now(); }, true);
  window.addEventListener('unhandledrejection', function (e) {
    if (Date.now() - ultimoClic > 8000) return;          // refrescos de fondo: no molestar
    var m = (e.reason && e.reason.message) || '';
    if (typeof global.toast === 'function') global.toast('No se pudo abrir: ' + (m || 'el servidor no respondió'));
  });

  global.Dlg = {
    adoptActions: adoptActions,
    open: open, close: close, closeAll: closeAll,
    setBody: setBody, setHead: setHead, setActions: setActions, el: el,
    loading: loading, error: error, empty: empty,
    confirm: confirm, prompt: prompt,
    get depth() { return stack.length; }
  };
})(window);
