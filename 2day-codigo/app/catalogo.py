"""Resolución de identidad de un activo. UN solo punto para toda la aplicación.

EL PROBLEMA QUE RESUELVE
Un mismo remolque llega escrito de cuatro maneras: el económico viejo (53113), el
nuevo (400917), con guiones o espacios (53-113), o identificado solo por su placa.
Cada importador conocía UNA columna, así que daba de alta como "nuevo" algo que ya
existía. La regla de oro es que ese conocimiento viva aquí y no repartido.

LA CASCADA, en orden estricto y con su porqué medido sobre las 639 cargas de julio:

  0. ETIQUETA   — el holograma pegado al activo. Es la vía de MÁXIMA confianza porque
                  sustituye a teclear el económico: no hay dedo humano entre el activo y
                  el dato. Una etiqueta en 'conflicto' NO resuelve — en el maestro venían
                  dos hologramas repetidos en dos tractos cada uno, y resolver al primero
                  que apareciera atribuiría litros a la unidad equivocada.
  1. TARJETA    — tarjeta↔económico resultó 1:1 EXACTO en ambos proveedores, cero
                  excepciones, así que una tarjeta ya vista identifica el activo aunque
                  la fila venga incompleta.
                  CORRECCIÓN (verificada al medirlo): aquí se afirmaba que la tarjeta
                  resolvía las 4 cargas sin económico. Resuelve CERO. Esas 4 usan tres
                  tarjetas que NUNCA aparecen con económico en todo el archivo —son
                  gasolina de camionetas, no flota—, así que no hay nada de donde
                  deducir el activo. Quedan en cuarentena, que es lo correcto.
  2. ECONÓMICO  — normalizado, contra `alias_eco`, que contiene el viejo Y el nuevo.
                  Aquí es donde 53113 y 400917 caen en el mismo remolque.
  3. PLACA      — corrobora; solo resuelve cuando NO hay económico, y entonces se
                  declara `via='placa'` con confianza MEDIA para que quien lea el dato
                  sepa que se apoyó en la pista débil. La base guarda la placa
                  vieja y el maestro la nueva; las discrepancias contra el proveedor
                  están entre el 10% y el 30%. Si la placa fuera llave de unión, una
                  quinta parte de los litros caería en la unidad equivocada.
  4. CUARENTENA — no resuelve: la fila NO se descarta, queda señalada para alta.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AliasEco, EtiquetaActivo, Remolque, TipoUnidad, Unidad


def norm_eco(v) -> str:
    """Normaliza un económico. Quita separadores y el '.0' que Excel le pega a los
    numéricos ('531834.0' -> '531834'), que era una fuente silenciosa de no-coincidencia."""
    s = str(v if v is not None else "").strip().upper()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return re.sub(r"[^A-Z0-9]", "", s)


def norm_placa(v) -> str:
    """'99-AF-6E' -> '99AF6E'."""
    return re.sub(r"[^A-Z0-9]", "", str(v if v is not None else "").strip().upper())


def norm_etiqueta(v) -> str:
    """'04-BF:0f e2a91e94' -> '04BF0FE2A91E94'. Los lectores separan los bytes de formas
    distintas y el mismo sticker llegaría como textos diferentes."""
    return re.sub(r"[^A-F0-9]", "", str(v if v is not None else "").strip().upper())


@dataclass
class Resuelto:
    unidad_id: int | None = None
    remolque_id: int | None = None
    via: str = "ninguna"          # 'etiqueta' | 'tarjeta' | 'eco' | 'placa' | 'ninguna'
    confianza: str = "nula"       # 'alta' | 'media' | 'nula'
    discrepancias: list | None = None

    @property
    def ok(self) -> bool:
        return self.unidad_id is not None or self.remolque_id is not None


def sembrar_alias(db: Session) -> int:
    """Crea los alias que se deducen del catálogo actual. Idempotente.

    De la unidad: su clave y sus dos placas. Del remolque: económico viejo, nuevo y
    sus dos placas. Las placas entran como alias porque el proveedor a veces reporta
    un activo SOLO por placa; pero se marcan sin confirmar, y la cascada nunca las usa
    para resolver por sí solas.
    """
    ya = {a.texto_norm for a in db.execute(select(AliasEco)).scalars()}
    nuevos = 0

    def add(texto, uid=None, rid=None, origen="flotilla"):
        nonlocal nuevos
        t = norm_eco(texto)
        if not t or t in ya:
            return
        db.add(AliasEco(texto_norm=t, unidad_id=uid, remolque_id=rid,
                        origen=origen, confirmado=True))
        ya.add(t)
        nuevos += 1

    for u in db.execute(select(Unidad)).scalars():
        add(u.clave, uid=u.id)
        for pl in (u.placa, u.placas_nuevas):
            if pl:
                add(norm_placa(pl), uid=u.id, origen="flotilla")
    for r in db.execute(select(Remolque)).scalars():
        add(r.eco, rid=r.id)
        add(r.eco_nuevo, rid=r.id)
        for pl in (r.placa, r.placas_nuevas):
            if pl:
                add(norm_placa(pl), rid=r.id, origen="flotilla")
    return nuevos


def contenido_qr(texto) -> str:
    """Saca el identificador útil de lo que devuelve el lector de QR.

    No se fija el formato del código a propósito: los stickers los manda imprimir el
    cliente y todavía no está decidido si llevan el económico en texto, un identificador
    propio o una dirección web. Aceptar las tres formas cuesta cuatro líneas hoy; exigir
    una y equivocarse cuesta reimprimir la flota entera.
    """
    t = str(texto if texto is not None else "").strip()
    if not t:
        return ""
    # una dirección web: se queda con el último tramo con contenido
    if "://" in t:
        t = [x for x in t.split("?")[0].rstrip("/").split("/") if x][-1:] or [""]
        t = t[0]
    # un par clave=valor ("eco=T205", "u:T205")
    for sep in ("=", ":"):
        if sep in t and len(t.split(sep)) == 2:
            izq, der = t.split(sep)
            if der.strip():
                t = der
    return t.strip()


def resolver_qr(db: Session, texto) -> Resuelto:
    """Resuelve el activo desde el contenido de un QR.

    Prueba las dos formas posibles en el orden que las hace seguras: primero como ETIQUETA
    registrada —un código propio, que es lo que hoy hay pegado en la flota— y sólo si no
    resuelve, como ECONÓMICO rotulado. Al revés sería peor: un QR con un identificador que
    por casualidad se pareciera a un económico resolvería al activo equivocado.
    """
    t = contenido_qr(texto)
    if not t:
        return Resuelto(discrepancias=["el código QR vino vacío"])
    r = resolver_activo(db, etiqueta=t)
    if r.ok:
        return r
    r2 = resolver_activo(db, eco=t)
    if r2.ok:
        return r2
    # Ninguna de las dos resolvió. Si el código SÍ está registrado como etiqueta pero no
    # resuelve (en conflicto, duplicada, retirada), ese motivo es la información útil y se
    # conserva. Si ni siquiera existe, se habla del texto QUE SE ESCANEÓ: la discrepancia
    # del intento por etiqueta nombraría el código filtrado a hexadecimal —"NOEXISTE123"
    # sale como "EE123"— y quien lo leyera no reconocería lo que escaneó.
    e = db.execute(select(EtiquetaActivo).where(
        EtiquetaActivo.codigo == norm_etiqueta(t))).scalar_one_or_none()
    if e is not None:
        return Resuelto(discrepancias=list(r.discrepancias or []))
    return Resuelto(discrepancias=[f"el código {t!r} no corresponde a ningún activo: "
                                   f"no está registrado como etiqueta ni es un económico conocido"])


def resolver_activo(db: Session, eco=None, placa=None, tarjeta_id=None,
                    etiqueta=None) -> Resuelto:
    """Devuelve a qué activo pertenece una carga, y por qué vía se supo.

    La placa NUNCA resuelve por sí sola: si el económico ya resolvió y la placa apunta
    a otro sitio, se deja constancia en `discrepancias` y se confía en el económico.
    """
    disc = []

    # 0 · etiqueta física: sustituye a teclear el económico, así que manda sobre todo
    te_ = norm_etiqueta(etiqueta)
    if te_:
        e = db.execute(select(EtiquetaActivo).where(
            EtiquetaActivo.codigo == te_)).scalar_one_or_none()
        if e is not None and e.estado == "vinculada" and (e.unidad_id or e.remolque_id):
            # Un activo dado de baja SIGUE resolviendo: sus cargas ya registradas necesitan a
            # qué apuntar, y si alguien escanea el sticker es que el camión existe. Lo que no
            # se hace es callarlo — que un activo retirado vuelva a cargar diésel es
            # exactamente el tipo de cosa que hay que ver, no descubrir tres meses después.
            obj = (db.get(Unidad, e.unidad_id) if e.unidad_id
                   else db.get(Remolque, e.remolque_id))
            if obj is not None and obj.activo is False:
                disc.append("el activo de esta etiqueta está dado de baja en el catálogo")
            return Resuelto(unidad_id=e.unidad_id, remolque_id=e.remolque_id,
                            via="etiqueta", confianza="alta",
                            discrepancias=disc or None)
        # Una etiqueta que existe pero no resuelve NO se ignora en silencio: se dice por qué,
        # porque el operador la escaneó y merece saber que su lectura fue buena y el dato no.
        if e is not None:
            # Lo lee un operador en el patio, no quien escribió esto: el estado interno no le
            # dice nada y lo que necesita saber es si el problema es suyo o de la oficina.
            disc.append({
                "conflicto": "Este código está registrado en dos unidades a la vez, así que no "
                              "se puede saber de cuál es. Avisa a tu coordinador: lo resuelve él.",
                "duplicada": "Esta unidad tiene dos códigos y falta declarar cuál es el del motor "
                              "y cuál el del termo. Avisa a tu coordinador.",
                "sin_activo": "Este código es de una unidad que todavía no está dada de alta. "
                               "Avisa a tu coordinador.",
                "retirada": "Este código está dado de baja. Si sigue pegado en la unidad, avisa a "
                             "tu coordinador.",
            }.get(e.estado, f"este código no se puede usar todavía (estado '{e.estado}')"))
        else:
            disc.append(f"la etiqueta {te_} no está registrada")

    # 1 · tarjeta (la resuelve quien la llame: aquí solo se respeta si viene dada)
    if tarjeta_id is not None:
        from .models import TarjetaCombustible  # import diferido: E2 crea esta tabla
        t = db.get(TarjetaCombustible, tarjeta_id)
        if t is not None and (t.unidad_id or t.remolque_id):
            return Resuelto(unidad_id=t.unidad_id, remolque_id=t.remolque_id,
                            via="tarjeta", confianza="alta")

    # 2 · económico normalizado contra los alias
    r = Resuelto()
    te = norm_eco(eco)
    if te:
        a = db.execute(select(AliasEco).where(AliasEco.texto_norm == te)).scalar_one_or_none()
        if a is not None and (a.unidad_id or a.remolque_id):
            r = Resuelto(unidad_id=a.unidad_id, remolque_id=a.remolque_id,
                         via="eco", confianza="alta")

    # 3 · placa, solo para corroborar
    tp = norm_placa(placa)
    if tp:
        ap = db.execute(select(AliasEco).where(AliasEco.texto_norm == tp)).scalar_one_or_none()
        if ap is not None and (ap.unidad_id or ap.remolque_id):
            if not r.ok:
                # Sin económico, la placa es lo único que hay: se acepta pero se declara
                # de confianza MEDIA, nunca alta.
                r = Resuelto(unidad_id=ap.unidad_id, remolque_id=ap.remolque_id,
                             via="placa", confianza="media")
                disc.append(f"resuelto solo por placa {tp}")
            elif (ap.unidad_id, ap.remolque_id) != (r.unidad_id, r.remolque_id):
                disc.append(f"la placa {tp} apunta a otro activo que el economico {te}")
    r.discrepancias = disc or None
    return r


# ── aplicación de propuestas ────────────────────────────────────────────────────

def _alias(db: Session, texto, uid=None, rid=None, origen="placas") -> bool:
    """Registra un alias si no existe. Devuelve si lo creó."""
    t = norm_eco(texto)
    if not t:
        return False
    ya = db.execute(select(AliasEco).where(AliasEco.texto_norm == t)).scalar_one_or_none()
    if ya is not None:
        return False
    db.add(AliasEco(texto_norm=t, unidad_id=uid, remolque_id=rid,
                    origen=origen, confirmado=True))
    return True


def aplicar_propuesta(db: Session, prop, usuario_id=None):
    """Aplica UNA propuesta al catálogo. Devuelve (ok: bool, mensaje: str).

    NINGÚN camino destruye información. La placa del maestro entra SIEMPRE como alias
    —así el proveedor resuelve aunque el campo ya esté ocupado— y solo se escribe en
    `placas_nuevas` cuando está libre. `conflicto` jamás se aplica solo: lo resuelve
    una persona, que para eso se registró como conflicto.
    """
    if prop.estado != "pendiente":
        return False, f"ya estaba en estado '{prop.estado}'"
    if prop.accion == "conflicto":
        return False, "un conflicto se resuelve a mano, no se aplica automáticamente"

    ahora = datetime.now(timezone.utc)
    es_uni = prop.entidad == "unidad"
    eco, placa = prop.eco_texto, prop.placa_texto
    obj = db.get(Unidad if es_uni else Remolque, prop.entidad_id) if prop.entidad_id else None

    if prop.accion == "crear":
        if es_uni:
            # El prefijo del económico dice qué es: C = camión con termo pegado (no
            # engancha), T = tracto. Es la misma regla que ya usa el catálogo.
            camion = eco.startswith("C")
            obj = Unidad(clave=eco, placa=placa,
                         tipo=TipoUnidad.CAMION if camion else TipoUnidad.TRACTO,
                         usa_remolque=not camion)
        else:
            # `usa_combustible` NO se puede deducir del maestro: no dice qué remolque
            # lleva termo. Queda en falso a propósito, pendiente de que el cliente lo
            # declare, en vez de inventar un dato que después contamina el rendimiento.
            obj = Remolque(eco=eco, placa=placa, es_dolly=eco.startswith("D"),
                           usa_combustible=False)
        obj.fuente_catalogo = "placas"
        obj.verificado_en = ahora
        db.add(obj)
        db.flush()
        _alias(db, eco, uid=obj.id if es_uni else None, rid=None if es_uni else obj.id)
        if placa:
            _alias(db, placa, uid=obj.id if es_uni else None, rid=None if es_uni else obj.id)
        msg = f"alta de {prop.entidad} {eco}"

    elif obj is None:
        return False, "el activo referido ya no existe"

    elif prop.accion == "actualizar_placa":
        uid, rid = (obj.id, None) if es_uni else (None, obj.id)
        _alias(db, placa, uid=uid, rid=rid)
        if not obj.placas_nuevas:
            obj.placas_nuevas = placa
            msg = f"placa {placa} registrada como placa nueva"
        else:
            # El campo ya está ocupado por otro valor: no se pisa. El alias basta para
            # que el proveedor resuelva, y se conserva lo que ya había.
            msg = (f"placa {placa} registrada como alias; "
                   f"`placas_nuevas` ya tenía {obj.placas_nuevas} y NO se sobrescribió")
        obj.fuente_catalogo = "placas"
        obj.verificado_en = ahora

    elif prop.accion == "registrar_alias":
        uid, rid = (obj.id, None) if es_uni else (None, obj.id)
        _alias(db, eco, uid=uid, rid=rid)
        if not es_uni and not obj.eco_nuevo and norm_eco(obj.eco) != norm_eco(eco):
            obj.eco_nuevo = eco
        obj.verificado_en = ahora
        msg = f"{eco} queda como alias del mismo activo"

    elif prop.accion == "reactivar":
        obj.activo = True
        obj.verificado_en = ahora
        msg = "reactivado"

    else:
        return False, f"acción desconocida: {prop.accion}"

    prop.estado = "aplicada"
    prop.aplicada_por_id = usuario_id
    prop.aplicada_en = ahora
    if prop.entidad_id is None and obj is not None:
        prop.entidad_id = obj.id
    return True, msg
