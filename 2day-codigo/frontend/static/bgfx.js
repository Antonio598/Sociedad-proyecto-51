/* 2Day · bgfx.js — fondo animado único para las 6 pantallas.
   Copiar a backend/frontend/static/bgfx.js y cargar con:
     <div class="bgfx"><div class="glow"></div><canvas></canvas></div>
     <script src="/static/bgfx.js" defer></script>

   Qué cambió respecto a los canvas que había en cada archivo:
   · Los colores salen de --fx-dot / --fx-link / --fx-hot, calibrados por tema en
     base.css. Antes se leía --ink y se multiplicaba por .14–.28, así que en tema
     claro el resultado era gris casi blanco: invisible.
   · Se relee la paleta cuando cambia el tema (observa data-theme).
   · Una sola implementación: antes había cinco copias con densidades distintas. */
(function () {
  var host = document.querySelector('.bgfx');
  if (!host) return;
  var cv = host.querySelector('canvas');
  if (!cv) return;
  if (matchMedia('(prefers-reduced-motion:reduce)').matches) { cv.remove(); return; }

  var ctx = cv.getContext('2d'), W = 0, H = 0, dpr = Math.min(devicePixelRatio || 1, 2);
  var pal = {};
  function readPal() {
    var cs = getComputedStyle(document.documentElement);
    pal = {
      dot: cs.getPropertyValue('--fx-dot').trim() || 'rgba(16,20,24,.40)',
      link: cs.getPropertyValue('--fx-link').trim() || 'rgba(16,20,24,.16)',
      hot: cs.getPropertyValue('--fx-hot').trim() || 'rgba(236,109,29,.72)'
    };
  }
  readPal();
  new MutationObserver(readPal).observe(document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] });

  function resize() {
    W = innerWidth; H = innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  addEventListener('resize', resize);

  // Densidad proporcional al área: misma sensación en móvil y en escritorio.
  // Subidas acumuladas (2026-08): +50% (34000→22667, topes 20-46→30-69) y luego
  // +45% (22667→15632, topes 30-69→44-100). Total ≈ 2,2x la densidad original.
  // Se escalan divisor Y topes a la vez: si solo bajara el divisor, el tope máximo
  // anularía el aumento en pantallas grandes y el mínimo lo anularía en móvil.
  var N = Math.max(44, Math.min(100, Math.round(innerWidth * innerHeight / 15632)));
  // DOS velocidades por punto, y la diferencia es el fallo que esto arregla.
  //
  //   dx,dy — DERIVA: constante, nunca se amortigua. Es lo que mantiene vivo el fondo.
  //   vx,vy — EMPUJÓN del puntero: se amortigua hasta cero, como antes.
  //
  // Antes sólo existía la segunda. Con `v *= .96` cada fotograma, el recorrido total de una
  // partícula es la suma de la serie: 0.15 / (1 - 0.96) ≈ 3.75 px. O sea que cada punto se
  // movía menos de cuatro píxeles y se paraba en poco más de un segundo, y sólo volvía a
  // moverse si el ratón pasaba cerca. En un teléfono no hay `mousemove`, así que el fondo
  // quedaba congelado para siempre. También se retiró el muelle a la posición de origen
  // (hx,hy): con deriva constante, "volver a casa" la anula.
  var pts = [];
  for (var i = 0; i < N; i++) {
    var ang = Math.random() * Math.PI * 2;
    var vel = .05 + Math.random() * .13;      // px por fotograma ≈ 3 a 11 px por segundo
    pts.push({
      x: Math.random() * innerWidth, y: Math.random() * innerHeight,
      dx: Math.cos(ang) * vel, dy: Math.sin(ang) * vel,
      vx: 0, vy: 0
    });
  }

  var m = { x: innerWidth / 2, y: innerHeight / 2, on: false };
  // `pointermove` cubre ratón, dedo y lápiz con una sola escucha; `mousemove` dejaba fuera
  // el teléfono entero. `passive` para no retener el desplazamiento de la página.
  addEventListener('pointermove', function (e) { m.x = e.clientX; m.y = e.clientY; m.on = true; },
                   { passive: true });
  // Al levantar el dedo se deja de atraer: si no, los puntos se quedarían apelotonados en el
  // último sitio que se tocó.
  addEventListener('pointerup', function () { m.on = false; }, { passive: true });
  addEventListener('pointercancel', function () { m.on = false; }, { passive: true });

  // alfa de --fx-link modulada por distancia, sin volver a parsear el color
  function linkStroke(t) {
    var c = pal.link;
    if (c.indexOf('rgba(') === 0) {
      var p = c.slice(5, -1).split(',');
      return 'rgba(' + p[0] + ',' + p[1] + ',' + p[2] + ',' + (parseFloat(p[3]) * t).toFixed(3) + ')';
    }
    return c;
  }

  function tick() {
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < N; i++) {
      var p = pts[i], dx = m.x - p.x, dy = m.y - p.y, d = Math.hypot(dx, dy) || 1;
      if (m.on) {
        if (d < 230 && d > 90) { p.vx += dx / d * .006; p.vy += dy / d * .006; }
        if (d < 110) { var f = (110 - d) / 110 * .06; p.vx -= dx / d * f; p.vy -= dy / d * f; }
      }
      // Sólo el EMPUJÓN se amortigua. La deriva se suma aparte y por eso no muere.
      p.vx *= .94; p.vy *= .94;
      var sp = Math.hypot(p.vx, p.vy);
      if (sp > .55) { p.vx *= .55 / sp; p.vy *= .55 / sp; }
      p.x += p.dx + p.vx; p.y += p.dy + p.vy;
      if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
      if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
    }
    ctx.lineWidth = 1;
    for (var a = 0; a < N; a++) {
      for (var b = a + 1; b < N; b++) {
        var pa = pts[a], pb = pts[b], dd = Math.hypot(pa.x - pb.x, pa.y - pb.y);
        if (dd < 148) {
          ctx.strokeStyle = linkStroke(1 - dd / 148);
          ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
        }
      }
    }
    for (var k = 0; k < N; k++) {
      var q = pts[k], near = m.on && Math.hypot(m.x - q.x, m.y - q.y) < 160;
      ctx.fillStyle = near ? pal.hot : pal.dot;
      ctx.beginPath(); ctx.arc(q.x, q.y, near ? 2.6 : 2, 0, 7); ctx.fill();
    }
    requestAnimationFrame(tick);
  }
  tick();
})();
