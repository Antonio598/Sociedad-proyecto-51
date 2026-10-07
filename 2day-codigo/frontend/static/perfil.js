/* Personalización de la cuenta, COMPARTIDA por todos los paneles (#7).
   - Aplica el color de acento y el tema guardados del usuario.
   - Monta el bloque de perfil: foto, teléfono, color y tema. NO permite cambiar nombre/apellidos
     ni la contraseña (esos los gestiona el administrador).
   Expone en window: TR_aplicarPrefs(prefs) y TR_montarPerfil(mountEl, me). */
(function () {
  var ACENTOS = [
    ['#F2620F', 'Naranja'], ['#C2410C', 'Terracota'], ['#B45309', 'Ámbar'],
    ['#0F766E', 'Teal'], ['#1D4ED8', 'Azul'], ['#7C3AED', 'Violeta'],
    ['#BE185D', 'Magenta'], ['#15803D', 'Verde']
  ];
  var e = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };
  function darken(hex, f) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || ''); if (!m) return hex;
    var n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    f = f == null ? 0.16 : f;
    r = Math.round(r * (1 - f)); g = Math.round(g * (1 - f)); b = Math.round(b * (1 - f));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }
  // Aplica el tema guardado: usa la función del panel si existe, si no, cae al modo directo.
  function aplicarTema() {
    if (typeof window.applyTheme === 'function') { window.applyTheme(); return; }
    try {
      var t = localStorage.getItem('tr-theme') || 'system';
      var dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme:dark)').matches);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    } catch (_) {}
  }
  // ── Aplica el acento (y el tema si viene en las prefs de la cuenta) ──
  window.TR_aplicarPrefs = function (prefs) {
    prefs = prefs || {};
    var root = document.documentElement;
    if (prefs.accent && /^#[0-9A-Fa-f]{6}$/.test(prefs.accent)) {
      root.style.setProperty('--accent', prefs.accent);
      root.style.setProperty('--accent2', darken(prefs.accent, 0.16));
    } else {
      root.style.removeProperty('--accent');
      root.style.removeProperty('--accent2');
    }
    if (prefs.tema && ['light', 'dark', 'system'].indexOf(prefs.tema) >= 0) {
      try {
        // SEMILLA: solo aplica el tema de la cuenta si el dispositivo aún no tiene preferencia
        // local. Así el toggle rápido del encabezado (que escribe localStorage) NO se revierte
        // en la siguiente carga. El local manda; la cuenta solo siembra en un equipo nuevo.
        if (!localStorage.getItem('tr-theme')) {
          localStorage.setItem('tr-theme', prefs.tema);
          aplicarTema();
        }
      } catch (_) {}
    }
  };

  function estiloUnaVez() {
    if (document.getElementById('tr-perfil-css')) return;
    var s = document.createElement('style'); s.id = 'tr-perfil-css';
    s.textContent =
      '.tr-pf{display:block}' +
      '.tr-pf .tr-row{display:flex;align-items:center;gap:14px;margin-bottom:14px}' +
      '.tr-av{width:64px;height:64px;border-radius:50%;background:var(--card2,var(--panel));border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font:700 22px "Barlow Condensed",sans-serif;color:var(--muted);overflow:hidden;flex:0 0 auto}' +
      '.tr-av img{width:100%;height:100%;object-fit:cover}' +
      '.tr-lbl{display:block;margin:12px 0 5px;font-size:12.5px;font-weight:600;color:var(--muted)}' +
      '.tr-ro{background:var(--card2,var(--panel));border:1px dashed var(--line2);border-radius:9px;padding:10px 11px;color:var(--muted);font-weight:600}' +
      '.tr-in{width:100%;background:var(--card2,var(--panel));border:1px solid var(--line);border-radius:9px;padding:10px 11px;color:var(--ink);font:inherit}' +
      '.tr-sw{display:flex;gap:9px;flex-wrap:wrap;margin-top:4px}' +
      '.tr-sw button{width:30px;height:30px;border-radius:50%;border:2px solid transparent;cursor:pointer;padding:0;position:relative}' +
      '.tr-sw button.on{border-color:var(--ink);box-shadow:0 0 0 2px var(--card)}' +
      '.tr-th{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}' +
      '.tr-th button{padding:8px 14px;border-radius:9px;border:1px solid var(--line);background:var(--card2,var(--panel));color:var(--ink);font-weight:600;font-size:13px;cursor:pointer}' +
      '.tr-th button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}' +
      '.tr-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--accent);color:#fff;border:0;border-radius:10px;padding:11px 16px;font-weight:700;font-size:14px;cursor:pointer;margin-top:16px}' +
      '.tr-note{font-size:11.5px;color:var(--muted);margin-top:10px;display:flex;align-items:center;gap:6px}' +
      // En el teléfono esto se toca con el dedo, no con el ratón: 44 px de lado es el mínimo
      // que recomiendan Apple y Google. Los círculos de color pueden crecer sin solaparse
      // porque su fila ya lleva flex-wrap.
      '@media (max-width:820px){' +
        '.tr-in,.tr-th button,.tr-btn{min-height:44px}' +
        '.tr-sw button{width:44px;height:44px}' +
      '}';
    document.head.appendChild(s);
  }

  function iniciales(n) {
    return (n || '?').split(' ').map(function (x) { return x[0]; }).slice(0, 2).join('').toUpperCase();
  }
  function toastSafe(m) { if (typeof window.toast === 'function') window.toast(m); }

  // ── Comprime la foto en el cliente antes de subir (igual criterio que el operador) ──
  async function comprimir(file) {
    try {
      if (!file || !/^image\//.test(file.type || '')) return file;
      var bmp = null, w = 0, h = 0, src = null;
      try { bmp = await createImageBitmap(file); w = bmp.width; h = bmp.height; src = bmp; } catch (_) { bmp = null; }
      if (!src) {
        var url = URL.createObjectURL(file);
        var img = await new Promise(function (res) { var i = new Image(); i.onload = function () { res(i); }; i.onerror = function () { res(null); }; i.src = url; });
        URL.revokeObjectURL(url);
        if (!img || !img.naturalWidth) return file;
        w = img.naturalWidth; h = img.naturalHeight; src = img;
      }
      var esc = Math.min(1, 512 / Math.max(w, h)), nw = Math.max(1, Math.round(w * esc)), nh = Math.max(1, Math.round(h * esc));
      var cv = document.createElement('canvas'); cv.width = nw; cv.height = nh;
      cv.getContext('2d').drawImage(src, 0, 0, nw, nh);
      if (bmp && bmp.close) bmp.close();
      var blob = await new Promise(function (res) { cv.toBlob(res, 'image/jpeg', 0.85); });
      if (!blob) return file;
      return new File([blob], 'perfil.jpg', { type: 'image/jpeg' });
    } catch (_) { return file; }
  }

  // ── Monta el bloque de perfil dentro de mountEl ──
  window.TR_montarPerfil = function (mountEl, me) {
    if (!mountEl) return;
    estiloUnaVez();
    me = me || {};
    var prefs = me.prefs || {};
    var nombre = me.operador_nombre || me.nombre || me.username || '';
    var accent = (prefs.accent || '').toUpperCase();
    var tema = prefs.tema || (function () { try { return localStorage.getItem('tr-theme') || 'system'; } catch (_) { return 'system'; } })();
    var avatarHTML = me.tiene_foto
      ? '<img id="tr-av-img" src="/api/perfil/foto?t=' + Date.now() + '" alt="">'
      : e(iniciales(nombre));
    mountEl.innerHTML =
      '<div class="tr-pf">' +
        '<div class="tr-row"><div class="tr-av" id="tr-av">' + avatarHTML + '</div>' +
          '<div><button class="tr-btn" id="tr-foto-btn" style="margin-top:0;padding:8px 13px;font-size:13px">Cambiar foto</button>' +
          '<input type="file" id="tr-foto-file" accept="image/*" style="display:none">' +
          '<div class="tr-note" style="margin-top:6px">Tu foto de perfil.</div></div></div>' +
        '<label class="tr-lbl">Nombre y apellidos (no editable)</label>' +
        '<div class="tr-ro">' + (e(nombre) || '—') + '</div>' +
        '<label class="tr-lbl">Teléfono</label>' +
        '<input class="tr-in" id="tr-tel" type="tel" inputmode="tel" placeholder="Tu teléfono" value="' + e(me.telefono || '') + '">' +
        '<label class="tr-lbl">Color de acento</label>' +
        '<div class="tr-sw" id="tr-sw">' +
          ACENTOS.map(function (a) {
            return '<button data-hex="' + a[0] + '" title="' + a[1] + '" style="background:' + a[0] + '"' + (accent === a[0] ? ' class="on"' : '') + '></button>';
          }).join('') +
        '</div>' +
        '<label class="tr-lbl">Tema</label>' +
        '<div class="tr-th" id="tr-th">' +
          ['light', 'dark', 'system'].map(function (t) {
            var l = { light: 'Claro', dark: 'Oscuro', system: 'Sistema' }[t];
            return '<button data-t="' + t + '"' + (tema === t ? ' class="on"' : '') + '>' + l + '</button>';
          }).join('') +
        '</div>' +
        '<button class="tr-btn" id="tr-save">Guardar cambios</button>' +
        '<div class="tr-note"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5z"></path></svg> La contraseña la gestiona el administrador.</div>' +
      '</div>';

    var elFile = mountEl.querySelector('#tr-foto-file');
    mountEl.querySelector('#tr-foto-btn').addEventListener('click', function () { elFile.click(); });
    elFile.addEventListener('change', async function () {
      if (!elFile.files || !elFile.files[0]) return;
      var f = await comprimir(elFile.files[0]);
      var fd = new FormData(); fd.append('file', f);
      try {
        var r = await fetch('/api/perfil/foto', { method: 'POST', body: fd });
        if (!r.ok) throw 0;
        var av = mountEl.querySelector('#tr-av');
        av.innerHTML = '<img src="/api/perfil/foto?t=' + Date.now() + '" alt="">';
        // Refresca cualquier avatar del encabezado si el panel lo tiene.
        document.querySelectorAll('[data-tr-avatar]').forEach(function (n) {
          n.innerHTML = '<img src="/api/perfil/foto?t=' + Date.now() + '" style="width:100%;height:100%;object-fit:cover;border-radius:inherit" alt="">';
        });
        toastSafe('Foto actualizada');
      } catch (_) { toastSafe('No se pudo subir la foto'); }
    });

    // Selección de acento (aplica al instante).
    mountEl.querySelectorAll('#tr-sw button').forEach(function (b) {
      b.addEventListener('click', function () {
        mountEl.querySelectorAll('#tr-sw button').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        window.TR_aplicarPrefs({ accent: b.getAttribute('data-hex') });
      });
    });
    // Selección de tema (aplica al instante vía la función del panel).
    mountEl.querySelectorAll('#tr-th button').forEach(function (b) {
      b.addEventListener('click', function () {
        mountEl.querySelectorAll('#tr-th button').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        try { localStorage.setItem('tr-theme', b.getAttribute('data-t')); } catch (_) {}
        aplicarTema();
      });
    });
    // Guardar (persiste teléfono + prefs de personalización).
    mountEl.querySelector('#tr-save').addEventListener('click', async function () {
      var selSw = mountEl.querySelector('#tr-sw button.on');
      var selTh = mountEl.querySelector('#tr-th button.on');
      var prefsOut = {
        accent: selSw ? selSw.getAttribute('data-hex') : '',
        tema: selTh ? selTh.getAttribute('data-t') : 'system'
      };
      var fd = new FormData();
      fd.append('telefono', mountEl.querySelector('#tr-tel').value || '');
      fd.append('prefs', JSON.stringify(prefsOut));
      try {
        var r = await fetch('/api/perfil', { method: 'POST', body: fd });
        if (!r.ok) throw 0;
        toastSafe('Cambios guardados');
      } catch (_) { toastSafe('No se pudieron guardar los cambios'); }
    });
  };
})();
