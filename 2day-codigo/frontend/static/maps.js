/* ============================================================================
   2Day · maps.js — el mapa, una sola vez para las tres pantallas.

   Antes esta suite estaba COPIADA en dashboard.html, coordinador.html y
   gerente.html (15 funciones idénticas por archivo). Cualquier arreglo había que
   hacerlo tres veces. Aquí vive una sola.

   DOS PROVEEDORES, LA MISMA INTERFAZ
     · google → Maps JavaScript API (el SDK oficial de Google para web)
     · osm    → Leaflet + OpenStreetMap (lo que se venía usando)
   Cuál se usa lo decide el SERVIDOR en /api/geo/config según haya clave o no.
   Si no hay clave de Google, la app sigue funcionando igual que siempre: nunca
   se queda sin mapa por un trámite pendiente.

   LAS RUTAS Y DIRECCIONES NO SE PIDEN DESDE AQUÍ
   Van por /api/geo/* (ver app/geo.py). Son las llamadas que Google cobra, así que
   la clave que las autoriza se queda en el servidor y se puede topar el gasto.
   Este archivo solo DIBUJA.

   SUPERFICIE PÚBLICA (idéntica a la que había, para no tocar el resto del código)
     mapBtn(id) · pickMapa(input) · asMapInit() · asFijar(cual,lat,lng,rev)
     asRuta() · asDibujarRuta(coords,recta) · asSetTipo(t) · asMarcar(cual) · _hav()
   Y el estado que el formulario de asignación consulta al guardar:
     window._asEstKm (km calculados) y window._asTipo ('ida' | 'retorno')
   ========================================================================== */
(function () {
  'use strict';

  var CENTRO = { lat: 25.6866, lng: -100.3161 };   // Monterrey, la base de la flota
  var VERDE = '#0f7a4f', NARANJA = '#EC6D1D';

  // ── Configuración del proveedor (se pregunta una sola vez) ────────────────
  var _cfg = null, _cfgProm = null;
  function config() {
    if (_cfg) return Promise.resolve(_cfg);
    if (_cfgProm) return _cfgProm;
    _cfgProm = fetch('/api/geo/config', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (c) { _cfg = c || { proveedor: 'osm', clave: '' }; return _cfg; })
      .catch(function () { _cfg = { proveedor: 'osm', clave: '' }; return _cfg; });
    return _cfgProm;
  }

  // ── Carga perezosa de la librería que toque ───────────────────────────────
  var _libProm = null;
  function libreria() {
    if (_libProm) return _libProm;
    _libProm = config().then(function (c) {
      return c.proveedor === 'google' ? cargarGoogle(c.clave) : cargarLeaflet();
    });
    return _libProm;
  }

  function cargarLeaflet() {
    if (window.L) return Promise.resolve('osm');
    return new Promise(function (res, rej) {
      var c = document.createElement('link');
      c.rel = 'stylesheet'; c.href = '/static/leaflet.css';
      document.head.appendChild(c);
      var s = document.createElement('script');
      s.src = '/static/leaflet.js';
      s.onload = function () { res('osm'); };
      s.onerror = function () { rej(new Error('no se pudo cargar Leaflet')); };
      document.head.appendChild(s);
    });
  }

  // El script de Google pesa ~315 KB y luego pide tiles a otro dominio. Resolver DNS y
  // abrir TLS por adelantado quita ese costo del momento en que el usuario abre el panel.
  function precalentar() {
    if (document.getElementById('_gmaps_pre')) return;
    var d = document.createElement('div');
    d.id = '_gmaps_pre';
    d.style.display = 'none';
    document.head.appendChild(d);
    ['https://maps.googleapis.com', 'https://maps.gstatic.com',
     'https://fonts.gstatic.com'].forEach(function (h) {
      var l = document.createElement('link');
      l.rel = 'preconnect'; l.href = h; l.crossOrigin = '';
      document.head.appendChild(l);
    });
  }

  function cargarGoogle(clave) {
    if (window.google && window.google.maps) return Promise.resolve('google');
    precalentar();
    return new Promise(function (res, rej) {
      // Google avisa de la carga por callback global; se limpia al terminar.
      var cb = '_gmapsListo' + Date.now();
      window[cb] = function () { try { delete window[cb]; } catch (e) { } res('google'); };
      var s = document.createElement('script');
      s.async = true;
      s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(clave) +
        '&callback=' + cb + '&language=es&region=MX&loading=async';
      s.onerror = function () { rej(new Error('no se pudo cargar Google Maps')); };
      document.head.appendChild(s);
    });
  }

  function esGoogle() { return _cfg && _cfg.proveedor === 'google'; }

  // ── Adaptador: las mismas operaciones sobre cualquiera de los dos ─────────
  var M = {
    crear: function (el, zoom) {
      if (esGoogle()) {
        return new google.maps.Map(el, {
          center: CENTRO, zoom: zoom || 10,
          mapTypeControl: false, streetViewControl: false, fullscreenControl: false,
          // 'greedy' = la rueda hace zoom directo. El valor por defecto de Google es
          // 'auto', que dentro de una página con scroll obliga a Ctrl+rueda; Leaflet
          // nunca lo pidió, así que sin esto cambiar de proveedor empeora el manejo.
          gestureHandling: 'greedy',
          // Sin esto, un clic sobre un negocio abre su ficha en vez de fijar el punto,
          // que es justo para lo que se usa este mapa.
          clickableIcons: false,
          zoomControl: true
        });
      }
      var m = L.map(el).setView([CENTRO.lat, CENTRO.lng], zoom || 10);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        { maxZoom: 19, attribution: '&copy; OpenStreetMap' }).addTo(m);
      return m;
    },
    alClic: function (mapa, cb) {
      if (esGoogle()) mapa.addListener('click', function (e) { cb(e.latLng.lat(), e.latLng.lng()); });
      else mapa.on('click', function (e) { cb(e.latlng.lat, e.latlng.lng); });
    },
    marcador: function (mapa, lat, lng, color, alArrastrar) {
      if (esGoogle()) {
        var mk = new google.maps.Marker({
          position: { lat: lat, lng: lng }, map: mapa, draggable: true,
          icon: {
            path: google.maps.SymbolPath.CIRCLE, scale: 8,
            fillColor: color, fillOpacity: 1, strokeColor: '#fff', strokeWeight: 2
          }
        });
        if (alArrastrar) mk.addListener('dragend', function (e) { alArrastrar(e.latLng.lat(), e.latLng.lng()); });
        return mk;
      }
      var ic = L.divIcon({
        className: '', iconSize: [18, 18], iconAnchor: [9, 16],
        html: '<div style="width:16px;height:16px;border-radius:50% 50% 50% 0;background:' +
          color + ';border:2px solid #fff;transform:rotate(-45deg);box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>'
      });
      var m2 = L.marker([lat, lng], { draggable: true, icon: ic }).addTo(mapa);
      if (alArrastrar) m2.on('dragend', function (ev) {
        var p = ev.target.getLatLng(); alArrastrar(p.lat, p.lng);
      });
      return m2;
    },
    mover: function (mk, lat, lng) {
      if (esGoogle()) mk.setPosition({ lat: lat, lng: lng });
      else mk.setLatLng([lat, lng]);
    },
    quitar: function (mapa, capa) {
      if (!capa) return;
      if (esGoogle()) capa.setMap(null);
      else mapa.removeLayer(capa);
    },
    // coords llegan como [[lng,lat],...] (formato de /api/geo/ruta, igual para ambos)
    linea: function (mapa, coords, recta) {
      if (esGoogle()) {
        var path = coords.map(function (c) { return { lat: c[1], lng: c[0] }; });
        return new google.maps.Polyline({
          path: path, map: mapa, strokeColor: NARANJA,
          strokeWeight: recta ? 3 : 5, strokeOpacity: recta ? 0.6 : 0.85
        });
      }
      return L.polyline(coords.map(function (c) { return [c[1], c[0]]; }), {
        color: NARANJA, weight: recta ? 3 : 5, opacity: .85,
        dashArray: recta ? '6 6' : null
      }).addTo(mapa);
    },
    encuadrar: function (mapa, coords) {
      try {
        if (esGoogle()) {
          var b = new google.maps.LatLngBounds();
          coords.forEach(function (c) { b.extend({ lat: c[1], lng: c[0] }); });
          mapa.fitBounds(b, 40);
        } else {
          mapa.fitBounds(coords.map(function (c) { return [c[1], c[0]]; }), { padding: [26, 26] });
        }
      } catch (e) { }
    },
    centrar: function (mapa, lat, lng, zoom) {
      if (esGoogle()) { mapa.setCenter({ lat: lat, lng: lng }); if (zoom) mapa.setZoom(zoom); }
      else mapa.setView([lat, lng], zoom || mapa.getZoom());
    },
    // Leaflet necesita recalcular su tamaño si nació dentro de algo oculto
    remedir: function (mapa) {
      if (esGoogle()) { try { google.maps.event.trigger(mapa, 'resize'); } catch (e) { } }
      else { try { mapa.invalidateSize(); } catch (e) { } }
    }
  };

  // ── Servicios del backend (nunca se llama a Google desde aquí) ────────────
  function apiRuta(oLat, oLng, dLat, dLng) {
    var fd = new FormData();
    fd.append('o_lat', oLat); fd.append('o_lng', oLng);
    fd.append('d_lat', dLat); fd.append('d_lng', dLng);
    return fetch('/api/geo/ruta', { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('ruta'); return r.json(); });
  }
  function apiDireccion(lat, lng) {
    return fetch('/api/geo/direccion?lat=' + lat + '&lng=' + lng, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); }).catch(function () { return {}; });
  }
  function apiBuscar(q) {
    return fetch('/api/geo/buscar?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); }).catch(function () { return {}; });
  }

  function _hav(aLat, aLng, bLat, bLng) {
    var R = 6371, t = Math.PI / 180;
    var dLat = (bLat - aLat) * t, dLng = (bLng - aLng) * t;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(aLat * t) * Math.cos(bLat * t) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return Math.round(2 * R * Math.asin(Math.sqrt(h)) * 10) / 10;
  }

  // ══ A · SELECTOR de ubicación ═════════════════════════════════════════════
  var _pMapa = null, _pMk = null, _pDestino = null;

  function mapBtn(id) {
    return '<button type="button" title="Elegir en mapa" onclick="pickMapa(document.getElementById(\'' + id + '\'))" ' +
      'style="flex:0 0 auto;display:grid;place-items:center;width:38px;border:1px solid var(--line-2,var(--line));' +
      'background:var(--card);color:inherit;border-radius:6px;cursor:pointer">' +
      '<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;' +
      'stroke-linecap:round;stroke-linejoin:round"><path d="M12 21s-6-5.7-6-10a6 6 0 0 1 12 0c0 4.3-6 10-6 10z"></path>' +
      '<circle cx="12" cy="11" r="2.4"></circle></svg></button>';
  }

  function _cerrar() {
    var o = document.getElementById('mapov');
    if (o) o.style.display = 'none';
    if (_pMk && _pMapa) { M.quitar(_pMapa, _pMk); _pMk = null; }
  }

  function _dom() {
    var o = document.getElementById('mapov');
    if (o) return o;
    o = document.createElement('div');
    o.id = 'mapov';
    o.style.cssText = 'position:fixed;inset:0;z-index:130;display:none;align-items:center;' +
      'justify-content:center;background:rgba(10,12,15,.55);padding:16px';
    o.innerHTML =
      '<div style="background:var(--card);border:1px solid var(--line-2,var(--line));border-radius:14px;' +
      'width:100%;max-width:640px;max-height:92vh;overflow:hidden;display:flex;flex-direction:column">' +
      '<div style="display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line)">' +
      '<b style="flex:1;font-size:15px">Elegir ubicación</b>' +
      '<button id="mapx" style="background:transparent;border:0;color:var(--muted);cursor:pointer;font-size:20px;line-height:1">&times;</button></div>' +
      '<div style="padding:10px 14px"><input id="mapq" placeholder="Buscar dirección y pulsar Enter" ' +
      'style="width:100%;padding:9px 11px;border:1px solid var(--line-2,var(--line));border-radius:6px;' +
      'background:var(--card-2,var(--card));color:inherit;font:inherit;box-sizing:border-box"></div>' +
      '<div id="mapc" style="height:340px;margin:0 14px;border:1px solid var(--line);border-radius:8px"></div>' +
      '<div id="mapsel" style="padding:10px 14px;font-size:12.5px;color:var(--muted);min-height:20px"></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;padding:0 14px 14px">' +
      '<button id="mapcancel" class="btn" style="background:var(--card-2,var(--card));color:var(--ink);' +
      'border:1px solid var(--line)">Cancelar</button>' +
      '<button id="mapok" class="btn primary">Usar esta ubicación</button></div></div>';
    document.body.appendChild(o);
    o.addEventListener('click', function (e) { if (e.target === o) _cerrar(); });
    o.querySelector('#mapx').onclick = _cerrar;
    o.querySelector('#mapcancel').onclick = _cerrar;
    o.querySelector('#mapok').onclick = function () {
      var s = document.getElementById('mapsel'), tgt = _pDestino, v = s.dataset.val || '';
      if (tgt && v) {
        tgt.value = v;
        if (s.dataset.lat && s.dataset.lng) {
          tgt.dataset.lat = s.dataset.lat; tgt.dataset.lng = s.dataset.lng;
          // Si luego se escribe a mano, las coordenadas dejan de ser válidas.
          if (!tgt._cc) {
            tgt._cc = 1;
            tgt.addEventListener('input', function () {
              delete tgt.dataset.lat; delete tgt.dataset.lng;
            });
          }
        }
      }
      if (window.onMapPick) { try { onMapPick(tgt); } catch (e) { } }
      _cerrar();
    };
    o.querySelector('#mapq').addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      var t = ev.target.value.trim();
      if (!t) return;
      apiBuscar(t).then(function (d) {
        if (d && d.lat != null) { M.centrar(_pMapa, d.lat, d.lng, 14); _punto(d.lat, d.lng, false, d.texto); }
      });
    });
    return o;
  }

  function _punto(lat, lng, rev, texto) {
    if (!_pMk) _pMk = M.marcador(_pMapa, lat, lng, NARANJA, function (a, b) { _punto(a, b, true); });
    else M.mover(_pMk, lat, lng);
    var s = document.getElementById('mapsel');
    s.dataset.lat = lat; s.dataset.lng = lng;
    if (texto) { s.dataset.val = texto; s.textContent = texto; return; }
    if (rev) {
      s.textContent = 'Buscando dirección…';
      apiDireccion(lat, lng).then(function (d) {
        var n = (d && d.texto) || (Number(lat).toFixed(5) + ', ' + Number(lng).toFixed(5));
        s.dataset.val = n; s.textContent = n;
      });
    }
  }

  function pickMapa(inp) {
    if (!inp) return;
    _pDestino = inp;
    libreria().then(function () {
      var o = _dom();
      o.style.display = 'flex';
      var s = document.getElementById('mapsel');
      s.dataset.val = inp.value || '';
      s.textContent = inp.value || 'Toca el mapa para fijar el punto';
      document.getElementById('mapq').value = '';
      if (!_pMapa) {
        _pMapa = M.crear(document.getElementById('mapc'), 11);
        M.alClic(_pMapa, function (lat, lng) { _punto(lat, lng, true); });
      }
      setTimeout(function () { M.remedir(_pMapa); }, 120);
    }).catch(function () {
      if (window.toast) toast('No se pudo cargar el mapa');
    });
  }

  // ══ B · MAPA INCRUSTADO de "Asignar viaje" ════════════════════════════════
  function asSetTipo(t) {
    window._asTipo = t;
    document.querySelectorAll('#as-tipo button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.t === t);
    });
  }
  function asMarcar(w) {
    window._asMarca = w;
    document.querySelectorAll('#as-marca button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.m === w);
    });
  }

  function asMapInit() {
    window._asMap = null;
    window._asMk = { inicio: null, final: null };
    window._asLine = null;
    window._asMarca = 'inicio';
    window._asEstKm = null;
    window._asTipo = 'ida';
    var info = document.getElementById('as-route-info');
    return libreria().then(function () {
      var el = document.getElementById('as-map');
      if (!el) return;
      window._asMap = M.crear(el, 10);
      M.alClic(window._asMap, function (lat, lng) { asFijar(window._asMarca, lat, lng, true); });
      setTimeout(function () { M.remedir(window._asMap); }, 160);
    }).catch(function () {
      if (info) info.textContent = 'No se pudo cargar el mapa';
    });
  }

  // `texto` es opcional: cuando ya se sabe cómo se llama el sitio —porque se eligió del
  // desplegable— se pone y nos ahorramos la geocodificación inversa entera.
  function asFijar(cual, lat, lng, rev, texto) {
    if (!window._asMap) return;
    cual = (cual === 'inicio' || cual === 'final') ? cual : (window._asMarca || 'inicio');
    lat = Number(lat); lng = Number(lng);
    if (!isFinite(lat) || !isFinite(lng)) return;
    // El CAMPO primero y el marcador después. Es lo que la persona está esperando ver, y
    // así un tropiezo dibujando el mapa no deja el destino vacío sin explicación.
    var inp = document.getElementById(cual === 'inicio' ? 'as-origen' : 'as-destino');
    if (inp) {
      inp.dataset.lat = lat; inp.dataset.lng = lng;
      if (texto) inp.value = texto;
      else if (rev) inp.value = lat.toFixed(5) + ', ' + lng.toFixed(5);
    }
    var color = cual === 'inicio' ? VERDE : NARANJA;
    if (window._asMk[cual]) M.mover(window._asMk[cual], lat, lng);
    else window._asMk[cual] = M.marcador(window._asMap, lat, lng, color, function (a, b) {
      asFijar(cual, a, b, true);
    });
    if (cual === 'inicio' && !window._asMk.final) asMarcar('final');
    if (rev && !texto) {
      apiDireccion(lat, lng).then(function (d) {
        // Sólo se sustituye si el campo sigue teniendo LAS COORDENADAS que pusimos: si la
        // persona ya escribió otra cosa, o movió el marcador, no se le pisa encima.
        var puestas = lat.toFixed(5) + ', ' + lng.toFixed(5);
        if (inp && d && d.texto && inp.value === puestas) {
          inp.value = d.texto.split(',').slice(0, 3).join(',');
        }
      }).catch(function () { });
    }
    asRuta();
  }

  // Billete del trazado. `as-km` se busca DESPUÉS de la espera, así que sin esto una ruta
  // que ya nadie mira escribe en el formulario que haya en pantalla: cancelas mientras pone
  // «Trazando ruta…», abres otro «Asignar viaje», y el campo Km se rellena solo con los
  // kilómetros del viaje que descartaste. Ese número se guarda.
  var _asTic = 0;

  function asRuta() {
    var tic = ++_asTic;
    var o = document.getElementById('as-origen'),
      d = document.getElementById('as-destino'),
      info = document.getElementById('as-route-info');
    if (!(o && d && o.dataset.lat && o.dataset.lng && d.dataset.lat && d.dataset.lng)) return;
    if (info) info.textContent = 'Trazando ruta…';
    return apiRuta(o.dataset.lat, o.dataset.lng, d.dataset.lat, d.dataset.lng)
      .then(function (r) {
        if (tic !== _asTic) return;   // llegó tarde: su formulario ya no está
        asDibujarRuta(r.puntos, r.fuente === 'recta');
        window._asEstKm = r.km;
        var k = document.getElementById('as-km');
        if (k) k.value = r.km;
        if (info) {
          info.textContent = (r.fuente === 'recta'
            ? 'Línea recta (ruta no disponible): '
            : 'Ruta por carretera: ') + r.km + ' km';
        }
      })
      .catch(function () {
        if (tic !== _asTic) return;   // llegó tarde: ni la recta de reserva
        // Último recurso: ni el servidor pudo. Se dibuja la recta en el navegador
        // y se DICE que es una recta, para no dar por buenos km que no lo son.
        var km = _hav(+o.dataset.lat, +o.dataset.lng, +d.dataset.lat, +d.dataset.lng);
        asDibujarRuta([[+o.dataset.lng, +o.dataset.lat], [+d.dataset.lng, +d.dataset.lat]], true);
        window._asEstKm = km;
        var k = document.getElementById('as-km');
        if (k) k.value = km;
        if (info) info.textContent = 'Línea recta (ruta no disponible): ' + km + ' km';
      });
  }

  function asDibujarRuta(coords, recta) {
    if (!window._asMap || !coords || !coords.length) return;
    if (window._asLine) { M.quitar(window._asMap, window._asLine); window._asLine = null; }
    window._asLine = M.linea(window._asMap, coords, recta);
    M.encuadrar(window._asMap, coords);
  }

  // ── Se exponen como globales: el resto del código las llama por su nombre ──
  // ── Buscador de lugares con sugerencias ────────────────────────────────────
  // Vive aquí y no en cada pantalla porque las tres tienen el mismo formulario y la versión
  // anterior estaba copiada —con el mismo error— en los tres archivos.
  var _sugTimer = null, _sugSeq = 0, _sugItems = [], _sugSel = -1;

  function _sugCaja() {
    var caja = document.getElementById('as-sug');
    if (caja) return caja;
    var inp = document.getElementById('as-buscar');
    if (!inp || !inp.parentNode) return null;
    var cont = inp.parentNode;                 // .as-busca
    cont.style.position = 'relative';
    caja = document.createElement('div');
    caja.id = 'as-sug';
    caja.setAttribute('role', 'listbox');
    cont.appendChild(caja);
    return caja;
  }

  function _sugCerrar() {
    var c = document.getElementById('as-sug');
    if (c) { c.innerHTML = ''; c.classList.remove('abierta'); }
    _sugItems = []; _sugSel = -1;
  }

  function _sugPintar(items) {
    var caja = _sugCaja();
    if (!caja) return;
    _sugItems = items || []; _sugSel = -1;
    if (!_sugItems.length) { _sugCerrar(); return; }
    caja.innerHTML = _sugItems.map(function (x, i) {
      var resto = (x.texto || '').slice((x.principal || '').length).replace(/^,\s*/, '');
      return '<button type="button" role="option" data-i="' + i + '">'
        + '<b>' + _esc(x.principal || x.texto) + '</b>'
        + (resto ? '<span>' + _esc(resto) + '</span>' : '') + '</button>';
    }).join('');
    caja.classList.add('abierta');
    Array.prototype.forEach.call(caja.querySelectorAll('button'), function (b) {
      // mousedown y no click: el click llega después del blur del input, y el blur cierra
      // el desplegable, así que con click la sugerencia desaparecía antes de poder elegirla.
      b.addEventListener('mousedown', function (e) {
        e.preventDefault();
        asElegir(Number(b.dataset.i));
      });
    });
  }

  function _esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function asElegir(i) {
    var x = _sugItems[i];
    if (!x) return;
    var inp = document.getElementById('as-buscar');
    if (inp) inp.value = x.principal || x.texto || '';
    _sugCerrar();
    // El punto viene YA en la sugerencia, así que no hay segunda llamada ni espera.
    asFijar(window._asMarca || 'inicio', x.lat, x.lng, false, x.texto);
    var info = document.getElementById('as-route-info');
    if (info && !document.getElementById('as-origen').dataset.lat) info.textContent = x.texto;
    if (window._asMap) M.centrar(window._asMap, x.lat, x.lng);
  }

  function _sugPedir(q) {
    var mio = ++_sugSeq;
    fetch('/api/geo/sugerencias?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : { sugerencias: [] }; })
      .then(function (d) {
        if (mio !== _sugSeq) return;   // llegó tarde: ya hay una consulta más nueva
        _sugPintar(d.sugerencias || []);
      })
      .catch(function () { if (mio === _sugSeq) _sugCerrar(); });
  }

  // Se llama desde asMapInit, cuando el formulario ya está en el DOM.
  function asBuscarInit() {
    var inp = document.getElementById('as-buscar');
    if (!inp || inp.dataset.listo) return;
    inp.dataset.listo = '1';
    inp.setAttribute('autocomplete', 'off');
    inp.addEventListener('input', function () {
      var q = inp.value.trim();
      clearTimeout(_sugTimer);
      if (q.length < 3) { _sugSeq++; _sugCerrar(); return; }
      // 320 ms de espera: cada pulsación sería una llamada facturable a Google.
      _sugTimer = setTimeout(function () { _sugPedir(q); }, 320);
    });
    inp.addEventListener('keydown', function (e) {
      var caja = document.getElementById('as-sug');
      var abierta = caja && caja.classList.contains('abierta');
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!abierta) return;
        e.preventDefault();
        _sugSel += (e.key === 'ArrowDown' ? 1 : -1);
        if (_sugSel < 0) _sugSel = _sugItems.length - 1;
        if (_sugSel >= _sugItems.length) _sugSel = 0;
        Array.prototype.forEach.call(caja.querySelectorAll('button'), function (b, i) {
          b.classList.toggle('sel', i === _sugSel);
        });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (abierta && _sugItems.length) asElegir(_sugSel >= 0 ? _sugSel : 0);
        else asBuscar();
      } else if (e.key === 'Escape') {
        _sugCerrar();
      }
    });
    inp.addEventListener('blur', function () { setTimeout(_sugCerrar, 120); });
  }

  // El botón "Buscar" y el Enter sin desplegable abierto: una sola respuesta, la mejor.
  function asBuscar() {
    var inp = document.getElementById('as-buscar');
    var q = (inp && inp.value || '').trim();
    var info = document.getElementById('as-route-info');
    if (!q) { if (window.toast) toast('Escribe qué buscar'); return; }
    _sugCerrar();
    if (info) info.textContent = 'Buscando “' + q + '”…';
    return apiBuscar(q).then(function (r) {
      if (!r || r.lat == null) {
        if (info) info.textContent = 'No se encontró “' + q + '”.';
        if (window.toast) toast('Sin resultados');
        return;
      }
      asFijar(window._asMarca || 'inicio', r.lat, r.lng, false, r.texto || q);
      if (window._asMap) M.centrar(window._asMap, r.lat, r.lng);
    }).catch(function (e) {
      if (info) info.textContent = 'No se pudo buscar: ' + (e && e.message || '');
    });
  }

  // El CSS del desplegable se inyecta una vez: así las tres pantallas lo tienen sin repetir
  // las reglas en tres <style> distintos.
  (function () {
    if (document.getElementById('as-sug-css')) return;
    var st = document.createElement('style');
    st.id = 'as-sug-css';
    st.textContent = '#as-sug{display:none;position:absolute;top:100%;left:0;right:0;z-index:60;'
      + 'margin-top:4px;background:var(--panel,var(--card,#fff));border:1px solid var(--line-2,#c9d0d8);'
      + 'border-radius:8px;box-shadow:0 8px 24px rgba(16,20,24,.18);overflow:hidden;max-height:264px;overflow-y:auto}'
      + '#as-sug.abierta{display:block}'
      + '#as-sug button{display:block;width:100%;text-align:left;border:0;background:none;cursor:pointer;'
      + 'padding:9px 12px;font:inherit;color:var(--ink,#101418);border-top:1px solid var(--line,#e2e6eb)}'
      + '#as-sug button:first-child{border-top:0}'
      + '#as-sug button:hover,#as-sug button.sel{background:var(--sunken,#f4f6f9)}'
      + '#as-sug b{display:block;font:600 13px/1.3 inherit}'
      + '#as-sug span{display:block;font-size:11.5px;color:var(--muted,#6b7581);margin-top:1px}';
    document.head.appendChild(st);
  })();


  // ══════════════════════════════════════════════════════════════════════════
  // ASIGNAR VIAJE · la figura 8 del informe de diálogos, rehecha
  //
  // Vivía copiada en coordinador, gerente y dashboard, cada una con su propio bloque de CSS
  // `.as-*`. Ahora es una sola, aquí, porque el mapa ya vive en este módulo.
  //
  // Los cinco defectos que arregla, en el orden del informe:
  //   1. nacía en `sm` y se estiraba con style.maxWidth  ->  nace en 'lg'
  //   2. «Asignar viaje» estaba al final de la columna izquierda y en 768 px de alto quedaba
  //      bajo el pliegue  ->  va al PIE, siempre visible, deshabilitado hasta que hay
  //      destino, y el pie dice qué falta
  //   3. los km vivían en un campo del formulario Y en la barra bajo el mapa  ->  sólo en la
  //      barra, como cifra mono grande y editable
  //   4. tres segmentados con estilos distintos  ->  uno solo (fondo hundido, activa blanca)
  //   5. inputs de 13/9 px porque `.as-2col input` pisaba a base.css  ->  sin CSS propio
  //
  // Y dos más del informe: el mapa se queda fijo mientras el formulario baja, y el remolque
  // que no cabe se TACHA en lugar de rechazarse con un aviso después de haberlo marcado.
  // ══════════════════════════════════════════════════════════════════════════

  var _asgCfg = null, _asgDlg = null;

  function _asgCss() {
    if (document.getElementById('asg-css')) return;
    var st = document.createElement('style');
    st.id = 'asg-css';
    st.textContent = [
      '.asg{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);gap:26px;align-items:start}',
      '.asg .sech{margin:20px 0 10px;font:700 11px/1 var(--f-sans,"IBM Plex Sans");text-transform:uppercase;',
      ' color:var(--muted);letter-spacing:.14em;padding-top:16px;border-top:1px solid var(--line)}',
      '.asg .sech:first-child{margin-top:0;padding-top:0;border-top:0}',
      '.asg .field{margin-bottom:12px}',
      '.seg{display:inline-flex;background:var(--sunken);border:1px solid var(--line);',
      ' border-radius:8px;padding:3px;gap:3px;width:100%}',
      '.seg button{flex:1;border:0;background:transparent;color:var(--muted);border-radius:6px;',
      ' padding:7px 10px;font:600 12.5px var(--f-sans,"IBM Plex Sans");cursor:pointer;transition:background .12s,color .12s}',
      '.seg button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 2px rgba(16,20,24,.10)}',
      '.seg button.on[data-pin="inicio"]{color:#00806a}',
      '.seg button.on[data-pin="final"]{color:var(--accent)}',
      '.asg input[data-pin="inicio"]:focus{border-color:#00806a;box-shadow:0 0 0 3px rgba(0,128,106,.18)}',
      '.asg input[data-pin="final"]:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg)}',
      '.pinlbl{display:flex;align-items:center;gap:7px}',
      '.pinlbl i{width:8px;height:8px;border-radius:999px;flex:0 0 auto}',
      '.remchips{display:flex;flex-wrap:wrap;gap:7px}',
      '.remchip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line-2);',
      ' background:var(--sunken);border-radius:999px;padding:6px 12px;color:var(--ink);',
      ' font:500 12.5px var(--f-mono,"IBM Plex Mono");cursor:pointer;user-select:none}',
      '.remchip .m{font:400 11px var(--f-sans,"IBM Plex Sans");color:var(--muted)}',
      '.remchip.on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent)}',
      '.remchip.on .m{color:var(--accent)}',
      '.remchip[aria-disabled="true"]{text-decoration:line-through;opacity:.42;cursor:not-allowed}',
      '.asgmapa{position:sticky;top:0;display:flex;flex-direction:column;gap:9px}',
      '#as-map{height:326px;border-radius:10px;border:1px solid var(--line);background:var(--sunken)}',
      '.kmbar{display:flex;align-items:center;gap:12px;border:1px solid var(--line);',
      ' border-radius:10px;padding:9px 14px;background:var(--card)}',
      '.kmbar .lbl{font:600 10.5px var(--f-mono,"IBM Plex Mono");letter-spacing:.12em;',
      ' text-transform:uppercase;color:var(--muted);flex:0 0 auto}',
      '.kmbar input{width:118px;padding:3px 8px;font:600 21px/1.15 var(--f-mono,"IBM Plex Mono");',
      ' background:transparent;border:1px solid transparent;border-radius:6px}',
      '.kmbar input:hover{border-color:var(--line-2)}',
      '.kmbar .est{font:400 12px var(--f-sans,"IBM Plex Sans");color:var(--muted);flex:1;text-align:right}',
      '@media (max-width:900px){.asg{grid-template-columns:1fr}.asgmapa{position:static}}'
    ].join('\n');
    document.head.appendChild(st);
  }

  function _asgEsc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Medida en pies del remolque. Se lee de la DESCRIPCIÓN y nunca del número económico: la
  // renumeración de la flota desmintió esa correlación (está documentado en models.py).
  function _asgPies(r) {
    if (r.medida_pies != null) return r.medida_pies;
    var d = r.descripcion || '';
    var m = /\b(\d{2})\s*['"’”]/.exec(d);
    if (m) return Number(m[1]);
    m = /\b(40|45|48|53)\b/.exec(d);
    return m ? Number(m[1]) : null;
  }

  function _asgMarcados() {
    return Array.prototype.map.call(
      document.querySelectorAll('#asg-rem .remchip.on'),
      function (c) { return Number(c.dataset.rem); });
  }

  // Qué remolques ya NO caben, dado lo marcado. Es la regla 40/40: dos remolques sólo si los
  // dos son de 40 pies. Se calcula ANTES de marcar, para poder tacharlos en vez de rechazar
  // el clic con un aviso que obliga a deshacer.
  function _asgNoCabe(rem, marcados) {
    if (!marcados.length) return false;
    if (marcados.length >= 2) return marcados.indexOf(rem.id) < 0;
    var otro = null;
    for (var i = 0; i < _asgCfg.remolques.length; i++) {
      if (_asgCfg.remolques[i].id === marcados[0]) { otro = _asgCfg.remolques[i]; break; }
    }
    if (!otro || rem.id === otro.id) return false;
    return !(_asgPies(otro) === 40 && _asgPies(rem) === 40);
  }

  function _asgPintarRem() {
    var box = document.getElementById('asg-rem');
    if (!box) return;
    var marcados = _asgMarcados();
    box.innerHTML = _asgCfg.remolques.map(function (r) {
      var pies = _asgPies(r);
      var on = marcados.indexOf(r.id) >= 0;
      var no = !on && _asgNoCabe(r, marcados);
      return '<span class="remchip' + (on ? ' on' : '') + '"' +
        (no ? ' aria-disabled="true" title="No encaja con lo ya marcado: dos remolques sólo si ambos son de 40 pies"' : '') +
        ' data-rem="' + r.id + '" role="checkbox" aria-checked="' + on + '" tabindex="0">' +
        _asgEsc(r.eco_nuevo || r.eco) +
        '<span class="m">' + (pies ? pies + "'" : 'sin medida') + '</span></span>';
    }).join('');
  }

  function _asgTipoUnidad() {
    var s = document.getElementById('asg-unidad');
    var o = s && s.selectedOptions[0];
    return o ? o.dataset.tipo : '';
  }

  // El pie manda: la primaria se habilita cuando hay con qué asignar y dice qué falta. Un
  // botón bloqueado sin explicación deja a la persona buscando qué le falta.
  function _asgRevisar() {
    if (!_asgDlg) return;
    var g = function (id) { var e = document.getElementById(id); return e ? e.value : ''; };
    // OJO CON LOS IDS: la plantilla declara `asg-origen/destino/km` y el propio diálogo los
    // RENOMBRA a `as-*` unas líneas más abajo, para que la maquinaria del mapa los encuentre.
    // Esta comprobación se quedó leyendo `asg-destino`, que ya no existe cuando alguien
    // escribe: devolvía siempre cadena vacía, así que la nota decía «Falta el destino» pasara
    // lo que pasara y el botón no se habilitaba NUNCA, en los tres paneles.
    var falta = !g('asg-oper') ? 'Falta el operador'
      : !g('asg-unidad') ? 'Falta la unidad'
        : !String(g('as-destino')).trim() ? 'Falta el destino' : '';
    Dlg.setActions(_asgDlg, [
      { label: 'Cancelar' },
      {
        label: 'Asignar viaje', variant: 'primary', keepOpen: true,
        disabled: !!falta, onClick: _asgGuardar
      }
    ], falta);
  }

  // `cfg` trae los catálogos que la página ya cargó y qué hacer al guardar, así las tres
  // pantallas comparten esta implementación sin que este módulo sepa de sus variables.
  function asignarViaje(cfg) {
    cfg = cfg || {};
    _asgCfg = {
      operadores: cfg.operadores || [],
      unidades: cfg.unidades || [],
      remolques: cfg.remolques || [],
      onCreado: cfg.onCreado || function () { }
    };
    _asgCss();

    var ops = _asgCfg.operadores.slice().sort(function (a, b) {
      return String(a.nombre || '').localeCompare(String(b.nombre || ''));
    });
    var oOpts = '<option value="">— Elige operador —</option>' + ops.map(function (o) {
      return '<option value="' + o.id + '">' + _asgEsc(o.nombre) +
        (o.numero ? ' (#' + _asgEsc(o.numero) + ')' : '') + '</option>';
    }).join('');
    var uOpts = '<option value="">— Elige unidad —</option>' + _asgCfg.unidades.map(function (u) {
      return '<option value="' + u.id + '" data-tipo="' + _asgEsc(u.tipo) + '">' +
        _asgEsc(u.clave) + ' · ' + _asgEsc(u.tipo || '') + '</option>';
    }).join('');

    var body = '<div class="asg"><div>' +
      '<div class="sech">Quién va</div>' +
      '<div class="field"><label>Operador</label><select id="asg-oper">' + oOpts + '</select></div>' +
      '<div class="field"><label>Unidad</label><select id="asg-unidad">' + uOpts + '</select></div>' +
      '<div class="field" id="asg-remwrap" hidden><label>Remolques a enganchar</label>' +
      '<div class="remchips" id="asg-rem"></div>' +
      '<p style="margin:8px 0 0;font:400 11.5px/1.45 var(--f-sans,\'IBM Plex Sans\');color:var(--muted)">' +
      'Dos sólo si ambos son de 40 pies. El que no encaja con lo marcado sale tachado.</p></div>' +

      '<div class="sech">A dónde</div>' +
      '<div class="field"><label>Tipo de viaje</label><div class="seg" id="asg-tipo">' +
      '<button type="button" class="on" data-t="ida">Ida</button>' +
      '<button type="button" data-t="retorno">Retorno</button></div></div>' +
      '<div class="field"><label class="pinlbl"><i style="background:#00806a"></i>Inicio</label>' +
      '<input id="asg-origen" data-pin="inicio" placeholder="Planta o patio — o toca el mapa"></div>' +
      '<div class="field"><label class="pinlbl"><i style="background:var(--accent)"></i>Destino</label>' +
      '<input id="asg-destino" data-pin="final" placeholder="Ciudad o cliente — o toca el mapa"></div>' +

      '<div class="sech">Para el operador</div>' +
      '<div class="field"><label>Nota (opcional)</label>' +
      '<input id="asg-nota" placeholder="Instrucción para el operador"></div>' +
      '</div>' +

      '<div class="asgmapa">' +
      '<div class="as-busca" style="position:relative;margin:0">' +
      '<input id="as-buscar" placeholder="Buscar dirección, ciudad o cliente">' +
      '<button type="button" onclick="asBuscar()">Buscar</button></div>' +
      '<div class="seg" id="asg-marca">' +
      '<button type="button" class="on" data-m="inicio" data-pin="inicio">Fijar inicio</button>' +
      '<button type="button" data-m="final" data-pin="final">Fijar final</button></div>' +
      '<div id="as-map"></div>' +
      '<div class="kmbar"><span class="lbl">Km</span>' +
      '<input id="asg-km" type="number" inputmode="decimal" min="0" step="0.1" placeholder="—">' +
      '<span class="est" id="as-route-info">Fija el destino para trazar la ruta</span></div>' +
      '</div></div>';

    _asgDlg = Dlg.open({
      size: 'lg',
      eyebrow: 'Viajes · nueva asignación',
      title: 'Asignar viaje',
      sub: 'El operador lo ve en su teléfono en cuanto lo guardes.',
      body: body,
      onClose: function () { _asgDlg = null; }
    });

    // Los campos del mapa conservan los ids que la maquinaria ya conoce (as-origen,
    // as-destino, as-km): se les ponen aquí para no duplicar toda esa lógica.
    ['origen', 'destino', 'km'].forEach(function (k) {
      var e = document.getElementById('asg-' + k);
      if (e) e.id = 'as-' + k;
    });
    asMapInit();
    if (window.asBuscarInit) asBuscarInit();

    var box = Dlg.el(_asgDlg);
    if (box) {
      box.addEventListener('click', function (e) {
        var seg = e.target.closest && e.target.closest('#asg-tipo button');
        if (seg) {
          Array.prototype.forEach.call(seg.parentNode.children, function (b) {
            b.classList.toggle('on', b === seg);
          });
          asSetTipo(seg.dataset.t);
          return;
        }
        var mar = e.target.closest && e.target.closest('#asg-marca button');
        if (mar) {
          Array.prototype.forEach.call(mar.parentNode.children, function (b) {
            b.classList.toggle('on', b === mar);
          });
          window._asMarca = mar.dataset.m;
          return;
        }
        var chip = e.target.closest && e.target.closest('.remchip');
        if (chip) {
          // El que no cabe está tachado y no responde: la regla se ve ANTES de tocarlo.
          if (chip.getAttribute('aria-disabled') === 'true') return;
          chip.classList.toggle('on');
          _asgPintarRem();
        }
      });
      box.addEventListener('input', _asgRevisar);
      box.addEventListener('change', function (e) {
        if (e.target && e.target.id === 'asg-unidad') {
          var esTracto = _asgTipoUnidad() === 'TRACTO';
          var w = document.getElementById('asg-remwrap');
          if (w) w.hidden = !esTracto;
          if (esTracto) _asgPintarRem();
        }
        _asgRevisar();
      });
    }
    _asgRevisar();
    return _asgDlg;
  }

  function _asgGuardar() {
    var g = function (id) { var e = document.getElementById(id); return e ? String(e.value).trim() : ''; };
    var fd = new FormData();
    fd.append('operador_id', g('asg-oper'));
    fd.append('unidad_id', g('asg-unidad'));
    fd.append('destino', g('as-destino'));
    if (g('as-origen')) fd.append('origen', g('as-origen'));
    if (g('asg-nota')) fd.append('nota', g('asg-nota'));
    var rems = _asgMarcados();
    if (rems.length) fd.append('remolque_ids', rems.join(','));
    if (g('as-km')) fd.append('km_destino', g('as-km'));
    if (window._asEstKm != null) fd.append('km_estimado', window._asEstKm);
    if (window._asTipo === 'retorno') fd.append('es_retorno', '1');
    ['origen', 'destino'].forEach(function (k) {
      var e = document.getElementById('as-' + k);
      if (e && e.dataset.lat && e.dataset.lng) {
        fd.append(k + '_lat', e.dataset.lat);
        fd.append(k + '_lng', e.dataset.lng);
      }
    });
    return _asgEnviar(fd)
      .then(function (d) {
        if (!d) return;                         // lo canceló: ni error ni ruido
        Dlg.close(_asgDlg);
        if (window.toast) toast('Viaje asignado');
        _asgCfg.onCreado(d);
      })
      .catch(function (e) {
        if (window.toast) toast(e.message || 'No se pudo asignar');
      });
  }

  // Crea el viaje y, si el camión o un remolque ya van en otro viaje abierto, lo dice con
  // nombres y deja confirmar. Cerrar el viaje de otro chofer en silencio lo dejaría delante
  // de la bomba con un «Aún no tienes un viaje asignado» que nadie le explicó.
  //
  // Vive aquí porque este archivo lo cargan los tres paneles que asignan viajes: escribir
  // la pregunta tres veces es escribir tres versiones que se van separando.
  function _asgEnviar(fd) {
    return fetch('/api/asignaciones', { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (r.ok) return d;
          var det = d && d.detail;
          if (r.status === 409 && det && det.codigo === 'activo_en_otro_viaje' && !fd.has('confirmar')) {
            if (!window.ask) throw new Error(det.mensaje);   // sin diálogo, al menos se lee
            return window.ask({
              title: 'Ese activo ya va en otro viaje',
              msg: det.mensaje,
              okText: 'Asignar y cerrar el anterior',
              danger: true
            }).then(function (ok) {
              if (!ok) return null;
              fd.append('confirmar', '1');
              return _asgEnviar(fd);
            });
          }
          throw new Error(typeof det === 'string' ? det
                          : ((det && det.mensaje) || ('Error ' + r.status)));
        });
      });
  }

  window.asignarViaje = asignarViaje;
  // Lo usan los paneles con su propio formulario de asignar (el gerente).
  window.asignarViajePost = _asgEnviar;

  window.asBuscar = asBuscar;
  window.asBuscarInit = asBuscarInit;
  window.asElegir = asElegir;
  window.mapBtn = mapBtn;
  window.pickMapa = pickMapa;
  window.asMapInit = asMapInit;
  window.asFijar = asFijar;
  window.asRuta = asRuta;
  window.asDibujarRuta = asDibujarRuta;
  window.asSetTipo = asSetTipo;
  window.asMarcar = asMarcar;
  window._hav = _hav;
  window._asTipo = 'ida';
  window._asEstKm = null;
  window._asMarca = 'inicio';
  // ── Ruta en SÓLO LECTURA ──────────────────────────────────────────────────
  // Para quien únicamente tiene que VER por dónde va: dibuja origen, destino y el trazo,
  // encuadra los dos extremos y no engancha ningún manejador de clic. Devuelve los km y
  // de dónde salieron, para que quien la llame pueda declararlo en vez de dar por bueno
  // un número que quizá sea una recta.
  // `ruta` opcional: si quien llama ya la tiene calculada (porque su rol no puede pedirla
  // al endpoint general), se dibuja esa y no se vuelve a pedir.
  function verRuta(el, oLat, oLng, dLat, dLng, ruta) {
    return libreria().then(function () {
      var mapa = M.crear(el, 6);
      // Un mapa que nace dentro de un modal mide mal hasta que el modal termina de abrirse.
      setTimeout(function () { M.remedir(mapa); }, 160);
      var hayO = oLat != null && oLng != null, hayD = dLat != null && dLng != null;
      if (hayO) M.marcador(mapa, oLat, oLng, '#1d6b4f');
      if (hayD) M.marcador(mapa, dLat, dLng, '#c9560d');
      if (!hayO || !hayD) {
        if (hayO) M.centrar(mapa, oLat, oLng, 12);
        else if (hayD) M.centrar(mapa, dLat, dLng, 12);
        return { km: null, fuente: null, mapa: mapa };
      }
      var pedir = ruta ? Promise.resolve(ruta) : apiRuta(oLat, oLng, dLat, dLng);
      return pedir.then(function (r) {
        var pts = (r && r.puntos) || [];
        if (pts.length) M.linea(mapa, pts, r.fuente === 'recta');
        M.encuadrar(mapa, pts.length ? pts : [[oLng, oLat], [dLng, dLat]]);
        return { km: r && r.km, fuente: r && r.fuente, mapa: mapa };
      }).catch(function () {
        // Sin ruta se dibuja la recta entre los dos puntos, PERO se declara como tal:
        // presentar una recta como si fuera carretera es inventarse kilómetros.
        M.linea(mapa, [[oLng, oLat], [dLng, dLat]], true);
        M.encuadrar(mapa, [[oLng, oLat], [dLng, dLat]]);
        return { km: null, fuente: 'recta', mapa: mapa };
      });
    });
  }

  window.Mapas = { config: config, esGoogle: esGoogle, ruta: apiRuta, buscar: apiBuscar, direccion: apiDireccion, verRuta: verRuta };
})();
