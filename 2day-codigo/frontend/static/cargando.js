/* El indicador de carga de 2Day. Dibujado, no una imagen.
 *
 * POR QUÉ NO ES UN PNG NI UN SPINNER GENÉRICO. La marca ya tiene un gesto propio en toda la
 * aplicación: las barras inclinadas 22 grados —en la portada del login, en cada viñeta de la
 * lista de características, en los acentos de los paneles—. Aquí esas mismas barras se ponen
 * en movimiento y pasan como las rayas de una carretera vistas desde la cabina, que es
 * literalmente lo que hace la flota mientras el sistema espera. Al ser SVG y CSS pesa cero,
 * se tiñe solo con los colores del tema y no se pixela en la pantalla del teléfono.
 *
 * Uso:
 *     Cargando.mostrar('Entrando…');   // lo levanta
 *     Cargando.ocultar();              // lo baja
 *     Cargando.enlazar(form, 'Texto'); // lo levanta al enviar ese formulario
 *
 * Es idempotente: llamar dos veces a mostrar() no apila dos capas.
 */
(function (global) {
  'use strict';

  var CSS = [
    '.cg-capa{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;',
    'justify-content:center;flex-direction:column;gap:22px;',
    'background:color-mix(in oklab,var(--bg) 93%,transparent);',
    'backdrop-filter:blur(7px) saturate(1.05);-webkit-backdrop-filter:blur(7px) saturate(1.05);',
    'opacity:0;pointer-events:none;transition:opacity .22s ease}',
    '.cg-capa.on{opacity:1;pointer-events:auto}',

    /* La ventana por la que pasan las barras. El recorte es lo que las hace "pasar" en vez
       de aparecer y desaparecer en el aire. */
    '.cg-pista{position:relative;width:132px;height:46px;overflow:hidden}',
    '.cg-barra{position:absolute;top:7px;width:17px;height:32px;transform:skewX(-22deg);',
    'will-change:transform,opacity;animation:cg-pasar 1.25s linear infinite}',
    '.cg-barra:nth-child(1){background:var(--u-tracto);animation-delay:0s}',
    '.cg-barra:nth-child(2){background:var(--u-tracto);opacity:.55;animation-delay:.16s}',
    '.cg-barra:nth-child(3){background:var(--accent);animation-delay:.32s}',
    '@keyframes cg-pasar{',
    '0%{transform:translateX(-34px) skewX(-22deg);opacity:0}',
    '18%{opacity:1}',
    '82%{opacity:1}',
    '100%{transform:translateX(150px) skewX(-22deg);opacity:0}}',

    /* La raya de la carretera por debajo: da el suelo sobre el que pasan las barras. */
    '.cg-via{width:132px;height:2px;margin-top:-16px;border-radius:2px;overflow:hidden;',
    'background:linear-gradient(90deg,var(--line-2) 0 12px,transparent 12px 26px);',
    'background-size:26px 2px;animation:cg-via 1.1s linear infinite;opacity:.7}',
    '@keyframes cg-via{to{background-position:-26px 0}}',

    '.cg-txt{font:700 20px/1 "IBM Plex Sans",system-ui,sans-serif;letter-spacing:.04em;',
    'color:var(--ink)}',
    '.cg-txt b{color:var(--accent);font-weight:700}',
    '.cg-pie{font:500 10.5px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.2em;',
    'text-transform:uppercase;color:var(--muted);min-height:13px}',

    /* Quien pidió menos movimiento recibe el mismo indicador, latiendo en vez de viajando:
       sigue diciendo "estoy trabajando" sin barrer la pantalla. */
    '@media (prefers-reduced-motion:reduce){',
    '.cg-barra{animation:cg-latir 1.6s ease-in-out infinite;transform:skewX(-22deg)}',
    '.cg-barra:nth-child(1){left:14px}.cg-barra:nth-child(2){left:57px}.cg-barra:nth-child(3){left:100px}',
    '.cg-via{animation:none}',
    '@keyframes cg-latir{0%,100%{opacity:.25}50%{opacity:1}}}'
  ].join('');

  var capa = null;

  function construir() {
    if (capa) return capa;
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);

    capa = document.createElement('div');
    capa.className = 'cg-capa';
    // `status` + `polite`: anuncia que se está cargando sin interrumpir lo que el lector de
    // pantalla esté diciendo. `aria-hidden` mientras está abajo, para no leer una capa oculta.
    capa.setAttribute('role', 'status');
    capa.setAttribute('aria-live', 'polite');
    capa.setAttribute('aria-hidden', 'true');
    capa.innerHTML =
      '<div class="cg-pista"><i class="cg-barra"></i><i class="cg-barra"></i><i class="cg-barra"></i></div>' +
      '<div class="cg-via"></div>' +
      '<div class="cg-txt">2<b>Day</b></div>' +
      '<div class="cg-pie" data-cg-pie></div>';
    document.body.appendChild(capa);
    return capa;
  }

  function mostrar(texto) {
    var c = construir();
    var pie = c.querySelector('[data-cg-pie]');
    if (pie) pie.textContent = texto || '';
    c.setAttribute('aria-hidden', 'false');
    // Un fotograma de margen: sin él la transición de opacidad no arranca y la capa
    // aparecería de golpe.
    requestAnimationFrame(function () { c.classList.add('on'); });
  }

  function ocultar() {
    if (!capa) return;
    capa.classList.remove('on');
    capa.setAttribute('aria-hidden', 'true');
  }

  function enlazar(form, texto, demora) {
    if (!form) return;
    var yendo = false;
    form.addEventListener('submit', function (ev) {
      if (yendo) return;
      mostrar(texto || 'Un momento…');
      // Sin demora el formulario sigue su camino normal y la capa sólo cubre la espera real.
      if (!demora) return;
      // Con demora se retiene el envío para que la marca se vea. El evento `submit` sólo
      // llega DESPUÉS de que el navegador validó el formulario, así que aquí ya se sabe que
      // los campos están bien; por eso `form.submit()` —que se salta la validación— es
      // seguro en este punto y no lo sería antes.
      ev.preventDefault();
      yendo = true;
      setTimeout(function () { form.submit(); }, demora);
    });
  }

  // Volver con el botón "atrás" restaura la página desde la caché del navegador SIN recargar,
  // así que la capa seguiría puesta sobre un formulario perfectamente usable.
  global.addEventListener('pageshow', function (e) { if (e.persisted) ocultar(); });

  global.Cargando = { mostrar: mostrar, ocultar: ocultar, enlazar: enlazar };
})(window);
