/* 2Day — Reportes por periodo, con comparativo.
 *
 * Un solo componente para los cuatro paneles (coordinador, combustible, admin, gerente): el
 * documento del dueño pide los mismos reportes para todos los que los pueden ver, y copiarlos
 * en cada HTML haría que se separaran con el tiempo.
 *
 *   Reportes.montar(elemento)   → pinta los controles y carga el mes en curso.
 *
 * Usa sólo variables de /static/base.css y SVG propio para las gráficas (como el resto de la
 * app: sin librerías). Habla con GET /api/reportes y /api/reportes.xlsx.
 */
(function () {
  'use strict';

  var CSS = '' +
    '.rp-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end}' +
    '.rp-bar label{display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:var(--muted)}' +
    '.rp-bar select,.rp-bar input{padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit;font-size:13px;min-width:0}' +
    '.rp-btn{padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:var(--sunken);color:var(--ink);font:600 13px var(--f-sans,inherit);cursor:pointer}' +
    '.rp-btn.pri{background:var(--accent);border-color:var(--accent);color:#fff}' +
    '.rp-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:14px}' +
    '@media(max-width:820px){.rp-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}' +
    '.rp-kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 14px}' +
    '.rp-kpi .l{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}' +
    '.rp-kpi .v{font:600 22px var(--f-mono,monospace);margin-top:5px;color:var(--ink)}' +
    '.rp-kpi .c{font-size:11.5px;color:var(--muted);margin-top:3px}' +
    '.rp-up{color:var(--danger)}.rp-dn{color:var(--ok)}' +
    '.rp-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}' +
    '@media(max-width:900px){.rp-grid{grid-template-columns:1fr}}' +
    '.rp-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}' +
    '.rp-card h3{margin:0 0 8px;font-size:15px;display:flex;justify-content:space-between;align-items:center;gap:8px}' +
    '.rp-card .sub{font-size:11.5px;color:var(--muted);font-weight:400}' +
    '.rp-tbl{width:100%;border-collapse:collapse;font-size:12.5px}' +
    '.rp-tbl th{text-align:left;font-weight:600;color:var(--muted);font-size:11px;padding:5px 6px;border-bottom:1px solid var(--line)}' +
    '.rp-tbl td{padding:6px;border-bottom:1px solid var(--line);vertical-align:top}' +
    '.rp-tbl td.n,.rp-tbl th.n{text-align:right;font-family:var(--f-mono,monospace);white-space:nowrap}' +
    '.rp-wrap{overflow-x:auto}' +
    '.rp-orden{font-size:11.5px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:999px;padding:3px 9px;cursor:pointer}' +
    '.rp-empty{color:var(--muted);font-size:12.5px;padding:10px 2px}' +
    '.rp-warn{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:999px;background:var(--warn-bg,#fff3cd);color:var(--warn,#8a6d00)}';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function num(x, d) {
    if (x == null || isNaN(x)) return '—';
    return Number(x).toLocaleString('es-MX', { maximumFractionDigits: d == null ? 0 : d, minimumFractionDigits: 0 });
  }
  function money(x) { return x == null ? '—' : '$' + num(x, 0); }
  function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  var MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  function mesLbl(m) { var p = String(m).split('-'); return MESES[(+p[1] || 1) - 1] + ' ' + String(p[0]).slice(2); }

  // Rango de un preset, contado hacia atrás desde hoy (mes en curso incluido).
  function rango(preset) {
    var h = new Date(), d;
    var finMes = function (y, m) { return new Date(y, m + 1, 0); };
    switch (preset) {
      case 'mes': d = new Date(h.getFullYear(), h.getMonth(), 1); break;
      case 'mes_ant':
        d = new Date(h.getFullYear(), h.getMonth() - 1, 1); h = finMes(d.getFullYear(), d.getMonth()); break;
      case 'bimestre': d = new Date(h.getFullYear(), h.getMonth() - 1, 1); break;
      case 'trimestre': d = new Date(h.getFullYear(), h.getMonth() - 2, 1); break;
      case 'semestre': d = new Date(h.getFullYear(), h.getMonth() - 5, 1); break;
      case 'anio': d = new Date(h.getFullYear(), 0, 1); break;
      case 'anio_ant':
        d = new Date(h.getFullYear() - 1, 0, 1); h = new Date(h.getFullYear() - 1, 11, 31); break;
      default: return null;
    }
    return { desde: iso(d), hasta: iso(h) };
  }

  function variacion(pct, menosEsMejor) {
    if (pct == null) return '';
    var sube = pct > 0, cls = (sube === !!menosEsMejor) ? 'rp-up' : 'rp-dn';
    if (pct === 0) cls = '';
    return '<span class="' + cls + '">' + (sube ? '▲ ' : pct < 0 ? '▼ ' : '') + num(Math.abs(pct), 1) + '%</span>';
  }

  // Barras simples en SVG (la serie actual y, si la hay, la de comparación en gris).
  function barras(labels, a, b) {
    if (!labels.length) return '<div class="rp-empty">Sin datos en el periodo.</div>';
    var W = 560, H = 210, top = 12, base = 178, izq = 6;
    var max = Math.max.apply(null, a.concat(b || []).concat([1]));
    var paso = (W - izq * 2) / labels.length, ancho = Math.max(4, paso * (b ? 0.36 : 0.62));
    var out = '';
    labels.forEach(function (l, i) {
      var x = izq + i * paso + (paso - ancho * (b ? 2 : 1)) / 2;
      var ha = (a[i] || 0) / max * (base - top);
      out += '<rect x="' + x.toFixed(1) + '" y="' + (base - ha).toFixed(1) + '" width="' + ancho.toFixed(1) + '" height="' + ha.toFixed(1) + '" rx="2" style="fill:var(--accent)"><title>' + esc(l) + ': ' + num(a[i]) + ' L</title></rect>';
      if (b) {
        var hb = (b[i] || 0) / max * (base - top);
        out += '<rect x="' + (x + ancho).toFixed(1) + '" y="' + (base - hb).toFixed(1) + '" width="' + ancho.toFixed(1) + '" height="' + hb.toFixed(1) + '" rx="2" style="fill:var(--line-2,var(--line))"><title>' + esc(l) + ' (comparación): ' + num(b[i]) + ' L</title></rect>';
      }
      if (labels.length <= 14 || i % 2 === 0)
        out += '<text x="' + (izq + i * paso + paso / 2).toFixed(1) + '" y="198" text-anchor="middle" style="fill:var(--muted);font:500 9.5px var(--f-mono,monospace)">' + esc(l) + '</text>';
    });
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;display:block"><path d="M0 ' + base + 'H' + W + '" style="stroke:var(--line)"/>' + out + '</svg>';
  }

  function tablaRanking(filas, cols, vacio) {
    if (!filas.length) return '<div class="rp-empty">' + esc(vacio) + '</div>';
    return '<div class="rp-wrap"><table class="rp-tbl"><thead><tr><th>#</th>' +
      cols.map(function (c) { return '<th class="' + (c.n ? 'n' : '') + '">' + esc(c.t) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + filas.map(function (f, i) {
        return '<tr><td class="n">' + (i + 1) + '</td>' + cols.map(function (c) {
          return '<td class="' + (c.n ? 'n' : '') + '">' + c.v(f) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  }

  function montar(el) {
    if (!el) return;
    if (!document.getElementById('rp-css')) {
      var st = document.createElement('style'); st.id = 'rp-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    var r0 = rango('mes');
    el.innerHTML =
      '<div class="rp-card"><div class="rp-bar">' +
      '<label>Periodo<select data-rp="preset">' +
      '<option value="mes">Mes en curso</option><option value="mes_ant">Mes anterior</option>' +
      '<option value="bimestre">Bimestre</option><option value="trimestre">Trimestre</option>' +
      '<option value="semestre">Semestre</option><option value="anio">Año en curso</option>' +
      '<option value="anio_ant">Año anterior</option><option value="libre">Rango libre</option></select></label>' +
      '<label>Desde<input type="date" data-rp="desde" value="' + r0.desde + '"></label>' +
      '<label>Hasta<input type="date" data-rp="hasta" value="' + r0.hasta + '"></label>' +
      '<label>Comparar con<select data-rp="comparar">' +
      '<option value="anio_anterior">Mismo periodo del año anterior</option>' +
      '<option value="anterior">Periodo inmediato anterior</option><option value="no">Sin comparar</option></select></label>' +
      '<button class="rp-btn pri" data-rp="ir">Generar</button>' +
      '<button class="rp-btn" data-rp="xlsx">Descargar Excel</button>' +
      '</div></div><div data-rp="cuerpo"><div class="rp-empty">Cargando…</div></div>';

    var q = function (k) { return el.querySelector('[data-rp="' + k + '"]'); };
    var orden = { tractos: 1, camiones: 1, remolques: 1, tcamiones: 1 };   // 1 = mejor primero
    var ultimo = null;

    function params() {
      return 'desde=' + encodeURIComponent(q('desde').value) + '&hasta=' + encodeURIComponent(q('hasta').value) +
        '&comparar=' + encodeURIComponent(q('comparar').value);
    }

    q('preset').addEventListener('change', function () {
      var r = rango(this.value);
      if (r) { q('desde').value = r.desde; q('hasta').value = r.hasta; cargar(); }
    });
    ['desde', 'hasta'].forEach(function (k) {
      q(k).addEventListener('change', function () { q('preset').value = 'libre'; });
    });
    q('comparar').addEventListener('change', cargar);
    q('ir').addEventListener('click', cargar);
    q('xlsx').addEventListener('click', function () { location.href = '/api/reportes.xlsx?' + params(); });
    el.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-rp-orden]');
      if (!b || !ultimo) return;
      var k = b.getAttribute('data-rp-orden'); orden[k] = -orden[k]; pintar(ultimo);
    });

    function cargar() {
      var c = q('cuerpo'); c.innerHTML = '<div class="rp-empty">Calculando…</div>';
      fetch('/api/reportes?' + params(), { credentials: 'same-origin' })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; throw 0; }
          return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || ('Error ' + r.status)); return d; });
        })
        .then(function (d) { ultimo = d; pintar(d); })
        .catch(function (e) { if (e) c.innerHTML = '<div class="rp-empty">No se pudo generar el reporte: ' + esc(e.message || e) + '</div>'; });
    }

    function ordenar(lista, campo, k, menorEsMejor) {
      var s = lista.slice().sort(function (a, b) { return menorEsMejor ? a[campo] - b[campo] : b[campo] - a[campo]; });
      return orden[k] === 1 ? s : s.reverse();
    }
    function botonOrden(k) {
      return '<button class="rp-orden" data-rp-orden="' + k + '">' + (orden[k] === 1 ? 'Mejor primero ⇅' : 'Peor primero ⇅') + '</button>';
    }

    function pintar(d) {
      var a = d.actual, c = d.comparacion, v = d.variacion || {};
      var con = a.consumo, kml = (v.kml_flota || {});
      var etqComp = c ? ('vs ' + c.desde + ' a ' + c.hasta) : 'sin comparación';
      var html = '<div class="rp-kpis">' +
        '<div class="rp-kpi"><div class="l">Litros</div><div class="v">' + num(con.litros) + '</div><div class="c">' + (c ? variacion(v.litros_pct, true) + ' · ' + num(c.consumo.litros) + ' L antes' : etqComp) + '</div></div>' +
        '<div class="rp-kpi"><div class="l">Costo</div><div class="v">' + money(con.importe) + '</div><div class="c">' + (c ? variacion(v.importe_pct, true) + ' · ' + money(c.consumo.importe) + ' antes' : etqComp) + '</div></div>' +
        '<div class="rp-kpi"><div class="l">Precio promedio</div><div class="v">' + (con.precio_promedio != null ? '$' + num(con.precio_promedio, 2) : '—') + '</div><div class="c">por litro, con impuestos</div></div>' +
        '<div class="rp-kpi"><div class="l">Rendimiento flota</div><div class="v">' + (kml.actual != null ? num(kml.actual, 2) + ' km/l' : '—') + '</div><div class="c">' + (c && kml.comparacion != null ? variacion(v.kml_flota_pct, false) + ' · ' + num(kml.comparacion, 2) + ' antes' : 'lecturas de motor') + '</div></div>' +
        '</div>';

      // Consumo por mes (actual vs comparación, alineados por posición).
      var labels = con.mensual.map(function (m) { return mesLbl(m.mes); });
      var serieA = con.mensual.map(function (m) { return m.litros; });
      var serieB = c ? c.consumo.mensual.map(function (m) { return m.litros; }) : null;
      if (serieB) while (serieB.length < serieA.length) serieB.push(0);
      html += '<div class="rp-grid">' +
        '<div class="rp-card"><h3>Consumo por mes <span class="sub">' + esc(a.desde) + ' a ' + esc(a.hasta) + (c ? ' · gris: comparación' : '') + '</span></h3>' + barras(labels, serieA, serieB) + '</div>' +
        '<div class="rp-card"><h3>Por plataforma</h3>' + tablaRanking(con.por_plataforma.filter(function (p) { return p.litros; }), [
          { t: 'Plataforma', v: function (p) { return esc(p.nombre) + (p.importe_estimado ? ' <span class="rp-warn">costo estimado</span>' : ''); } },
          { t: 'Litros', n: 1, v: function (p) { return num(p.litros); } },
          { t: 'Costo', n: 1, v: function (p) { return money(p.importe); } }], 'Sin cargas en el periodo.') +
        '<div class="sub" style="margin-top:8px;font-size:11.5px;color:var(--muted)">Motor: ' + num(con.por_destino.motor || 0) + ' L · Termo: ' + num(con.por_destino.termo || 0) + ' L' +
        (con.por_destino.sin_clasificar ? ' · Sin clasificar: ' + num(con.por_destino.sin_clasificar) + ' L' : '') + '</div></div>' +
        '</div>';

      // Rankings.
      var colsMotor = [
        { t: 'Económico', v: function (f) { return '<b>' + esc(f.eco) + '</b>' + (f.poca_muestra ? ' <span class="rp-warn" title="Menos de 500 km medidos: muestra corta">poca muestra</span>' : ''); } },
        { t: 'Km/l', n: 1, v: function (f) { return num(f.kml, 2); } },
        { t: 'Km', n: 1, v: function (f) { return num(f.km); } },
        { t: 'Litros', n: 1, v: function (f) { return num(f.litros); } },
        { t: 'Lecturas', n: 1, v: function (f) { return f.lecturas; } }];
      var colsTermo = [
        { t: 'Económico', v: function (f) { return '<b>' + esc(f.eco) + '</b>'; } },
        { t: 'L/h', n: 1, v: function (f) { return num(f.lh, 2); } },
        { t: 'Horas', n: 1, v: function (f) { return num(f.horas, 1); } },
        { t: 'Litros', n: 1, v: function (f) { return num(f.litros); } },
        { t: 'Lecturas', n: 1, v: function (f) { return f.lecturas; } }];
      html += '<div class="rp-grid">' +
        '<div class="rp-card"><h3>Tractocamiones por rendimiento ' + botonOrden('tractos') + '</h3>' +
        tablaRanking(ordenar(a.motor.tractos, 'kml', 'tractos', false), colsMotor, 'Sin lecturas de motor de tractocamiones en el periodo.') + '</div>' +
        '<div class="rp-card"><h3>Camiones por rendimiento ' + botonOrden('camiones') + '</h3>' +
        tablaRanking(ordenar(a.motor.camiones, 'kml', 'camiones', false), colsMotor, 'Sin lecturas de motor de camiones en el periodo.') + '</div>' +
        '<div class="rp-card"><h3>Termos de remolque por consumo ' + botonOrden('remolques') + '</h3>' +
        tablaRanking(ordenar(a.termos.remolques, 'lh', 'remolques', true), colsTermo, 'Hacen falta al menos dos cargas de termo con foto de horas en el periodo.') + '</div>' +
        '<div class="rp-card"><h3>Termos de camión por consumo ' + botonOrden('tcamiones') + '</h3>' +
        tablaRanking(ordenar(a.termos.camiones, 'lh', 'tcamiones', true), colsTermo, 'Hacen falta al menos dos cargas de termo con foto de horas en el periodo.') + '</div>' +
        '</div>';

      // Alertas de exceso.
      html += '<div class="rp-card" style="margin-top:14px"><h3>Alertas de exceso de carga <span class="sub">litros por encima de lo que el viaje debía gastar</span></h3>' +
        tablaRanking(a.alertas_exceso, [
          { t: 'Folio', v: function (x) { return esc(x.folio); } },
          { t: 'Fecha', v: function (x) { return esc(x.fecha); } },
          { t: 'Económico', v: function (x) { return esc(x.eco || '—'); } },
          { t: 'Tope', n: 1, v: function (x) { return num(x.tope); } },
          { t: 'Autorizados', n: 1, v: function (x) { return num(x.autorizados); } },
          { t: 'Cargados', n: 1, v: function (x) { return num(x.reales); } },
          { t: 'Exceso', n: 1, v: function (x) { return '<span class="rp-up">' + num(x.exceso) + ' L (' + num(x.exceso_pct, 1) + '%)</span>'; } }],
          'Sin cargas por encima del tope en el periodo.') + '</div>';

      // Histórico.
      var h = d.historico || [];
      html += '<div class="rp-card" style="margin-top:14px"><h3>Histórico de consumo <span class="sub">últimos 24 meses</span></h3>' +
        barras(h.map(function (m) { return mesLbl(m.mes); }), h.map(function (m) { return m.litros; }), null) + '</div>';

      q('cuerpo').innerHTML = html;
    }

    cargar();
  }

  window.Reportes = { montar: montar };
})();
