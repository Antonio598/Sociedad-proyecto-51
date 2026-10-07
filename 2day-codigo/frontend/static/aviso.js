/* ==========================================================================
   2Day — Aviso sonoro de las notificaciones.

   POR QUÉ SINTETIZADO Y NO UN ARCHIVO: un .mp3 hay que descargarlo, cachearlo y
   servirlo, y en el teléfono del operador —que a veces está sin señal— sería un
   recurso más que puede faltar justo cuando hace falta avisar. Un tono generado
   con Web Audio pesa cero y suena siempre.

   EL PROBLEMA DEL PRIMER SONIDO: los navegadores no dejan reproducir audio hasta
   que la persona ha tocado algo en la página. No es un fallo que se pueda salvar
   con código: es una regla del navegador. Por eso el contexto se crea y se reanuda
   en el primer toque, y hasta entonces la vibración hace el trabajo.

   Expone:  Aviso.sonar()   ·  Aviso.desbloquear()   ·  Aviso.activo(v)
   ========================================================================== */
(function () {
  'use strict';

  var ctx = null;
  var listo = false;
  var CLAVE = 'tr-aviso-sonido';

  function permitido() {
    try { return localStorage.getItem(CLAVE) !== '0'; } catch (_) { return true; }
  }

  function activo(v) {
    if (v === undefined) return permitido();
    try { localStorage.setItem(CLAVE, v ? '1' : '0'); } catch (_) { }
    if (v) desbloquear();
    return v;
  }

  function desbloquear() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!ctx) ctx = new AC();
      if (ctx.state === 'suspended') ctx.resume();
      listo = ctx.state === 'running';
    } catch (_) { }
  }

  // Dos notas cortas ascendentes. Corto a propósito: esto avisa, no entretiene, y
  // el operador puede estar en una gasolinera con ruido pero también en una cabina
  // a las tres de la mañana.
  function tono(frec, empieza, dura, volumen) {
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine';
    o.frequency.value = frec;
    // Rampa de entrada y salida: un tono que arranca y corta en seco produce un
    // chasquido audible en el altavoz del teléfono.
    g.gain.setValueAtTime(0.0001, ctx.currentTime + empieza);
    g.gain.exponentialRampToValueAtTime(volumen, ctx.currentTime + empieza + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + empieza + dura);
    o.connect(g); g.connect(ctx.destination);
    o.start(ctx.currentTime + empieza);
    o.stop(ctx.currentTime + empieza + dura + 0.02);
  }

  function sonar(urgente) {
    if (!permitido()) return;
    // La vibración va SIEMPRE que se pueda, y va primero: es lo único que funciona
    // con el teléfono en silencio, que es como lo lleva medio mundo.
    try { if (navigator.vibrate) navigator.vibrate(urgente ? [90, 60, 90] : 70); } catch (_) { }
    try {
      desbloquear();
      if (!ctx || ctx.state !== 'running') return;   // aún sin permiso del navegador
      if (urgente) { tono(880, 0, 0.13, 0.16); tono(660, 0.16, 0.2, 0.16); }
      else { tono(660, 0, 0.1, 0.12); tono(880, 0.11, 0.14, 0.12); }
    } catch (_) { }
  }

  // El primer toque en cualquier parte habilita el audio para el resto de la sesión.
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (ev) {
    document.addEventListener(ev, desbloquear, { once: true, passive: true });
  });

  window.Aviso = { sonar: sonar, desbloquear: desbloquear, activo: activo };
})();
