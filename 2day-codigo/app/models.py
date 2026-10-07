import enum
from datetime import date, datetime
# Decimal, y no float, porque el libro mayor `AsientoConsumo` usa Numeric: SQLAlchemy
# devuelve Decimal para Numeric y float para Float, y las dos cosas conviven en este archivo.
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# ─────────────────────────────────────────────────────────────────────────────
# Este esquema es un espejo del Excel "Bitácora 25-26":
#   - Viaje              -> hoja BITACORA (un renglón = un viaje, tractocamiones)
#   - AuditoriaThermo    -> hoja THORTON AUDITORIA (horas de termo de refrigeradas)
#   - Operador           -> hoja "op" (catálogo con número)
#   - SeguimientoDescuento -> hoja "Seguimiento Descuentos"
# Los nombres de campo siguen los encabezados del Excel para regenerarlo fácil.
# ─────────────────────────────────────────────────────────────────────────────


class TipoUnidad(str, enum.Enum):
    TRACTO = "TRACTO"      # clave T### — tractocamión; se le enganchan remolques con termo
    CAMION = "CAMION"      # clave C### — camión rígido refrigerado; el termo va pegado a la unidad


# Capacidad FIJA del tanque de diésel por tipo de unidad (litros). No es variable:
# los camiones (C###) tienen tanque de 500 L y los tractos (T###) de 1000 L. Se usa
# para aproximar los litros en tanque a partir del nivel de la aguja (nivel × capacidad).
# Rendimiento que DEBERÍA alcanzar cada configuración de viaje (km/L), según el cliente.
# Vive aquí, junto al resto de constantes del dominio, porque lo usan el reporte ejecutivo,
# la bitácora y el importador del catálogo: tenerlo declarado tres veces era una bomba de
# tiempo — cambiar uno y que los otros dos siguieran con el valor viejo, en silencio.
RENDIMIENTO_IDEAL = {"FULL": 1.9, "SENCILLO": 2.8, "THORTON": 4.0}

CAPACIDAD_TANQUE_L = {TipoUnidad.CAMION: 500, TipoUnidad.TRACTO: 1000}


def capacidad_tanque(unidad) -> int:
    """Litros del tanque según el tipo de unidad (Camión=500, Tracto=1000)."""
    if unidad is None:
        return 0
    return CAPACIDAD_TANQUE_L.get(unidad.tipo, 1000)


class TipoConfig(str, enum.Enum):
    """Columna TIPO de la BITACORA: configuración del viaje."""
    SENCILLO = "SENCILLO"
    FULL = "FULL"
    THORTON = "THORTON"


class EstadoAnomalia(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    CONFIRMADA = "CONFIRMADA"
    RECHAZADA = "RECHAZADA"


# Los cuatro perfiles de la plataforma web (migración de WhatsApp a app). El permiso se
# valida SIEMPRE en el servidor; el rol solo acomoda lo que cada quien ve.
class RolUsuario(str, enum.Enum):
    ADMIN = "admin"              # todo
    COORDINADOR = "coordinador"  # operación diaria: valida, autoriza, catálogos
    COMBUSTIBLE = "combustible"  # despacha, registra litros reales, sube facturas
    OPERADOR = "operador"        # solo captura sus propias solicitudes desde el teléfono
    GERENTE = "gerente"          # SOLO LECTURA: audita y consulta toda la flota, nunca modifica


class EstadoSolicitud(str, enum.Enum):
    """Ciclo de vida de una solicitud de recarga. No se puede saltar estados: no se
    factura lo que no se despachó ni se despacha lo que no se autorizó."""
    BORRADOR = "borrador"            # el operador la está armando o espera señal
    ENVIADA = "enviada"             # llegó a la bandeja del coordinador
    EN_VALIDACION = "en_validacion"  # el coordinador la abrió
    DEVUELTA = "devuelta"           # regresa al operador con un motivo
    RECHAZADA = "rechazada"         # no procede; queda con su justificación
    AUTORIZADA = "autorizada"       # se generó la orden de despacho con folio
    DESPACHADA = "despachada"       # se cargó el diésel y se anotaron litros reales
    FACTURADA = "facturada"         # el comprobante del día ya incluye esta carga
    CONCILIADA = "conciliada"       # orden, despacho y factura coinciden: ciclo cerrado
    EN_DISCREPANCIA = "en_discrepancia"  # algo no cuadra; requiere revisión humana
    ANULADA = "anulada"             # se anuló con motivo (nada se borra)


# De dónde salió cada dato capturado. Es la base del indicador de confiabilidad de la
# lectura automática y del desempeño por operador (sección 8.2 del documento).
class Procedencia(str, enum.Enum):
    IA_ACEPTADA = "ia_aceptada"    # la IA leyó la foto y se aceptó sin cambios
    IA_CORREGIDA = "ia_corregida"  # la IA leyó pero una persona corrigió el valor
    MANUAL = "manual"              # capturado a mano desde el inicio
    # Leído de un QR pegado al activo. Merece valor propio y no cae en MANUAL: el campo
    # existe para medir de dónde sale cada dato, y un código escaneado no se teclea ni se
    # interpreta —llega exacto—, así que es la procedencia MÁS fiable de las cuatro.
    ESCANEADA = "escaneada"


class Unidad(Base):
    """Catálogo maestro de unidades motrices (consumen diésel). Enriquecido desde
    la hoja TRACTOS de FLOTILLA 2DAY."""

    __tablename__ = "unidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(20), unique=True, index=True)   # ECO: T205, C001
    tipo: Mapped[TipoUnidad] = mapped_column(Enum(TipoUnidad), default=TipoUnidad.TRACTO)

    # Catálogo de flotilla (FLOTILLA!TRACTOS)
    serie: Mapped[str | None] = mapped_column(String(40), nullable=True)          # B SERIE (VIN)
    motor: Mapped[str | None] = mapped_column(String(40), nullable=True)          # D MOTOR (texto)
    motor_cc: Mapped[str | None] = mapped_column(String(15), nullable=True)       # AUDITORIAS.cc (ISX07...)
    placa: Mapped[str | None] = mapped_column(String(20), nullable=True)          # E PLACA
    placas_nuevas: Mapped[str | None] = mapped_column(String(20), nullable=True)  # F PLACAS NUEVAS
    anio: Mapped[int | None] = mapped_column(Integer, nullable=True)              # G AÑO
    marca: Mapped[str | None] = mapped_column(String(40), nullable=True)          # H MARCA
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)          # I DESCRIPCION
    operador_asignado: Mapped[str | None] = mapped_column(String(120), nullable=True)  # C Operador (actual)

    usa_remolque: Mapped[bool] = mapped_column(default=True)   # T=True (engancha), C=False (termo pegado)
    rendimiento_objetivo: Mapped[float | None] = mapped_column(Float, nullable=True)  # km/l meta (auditoría)
    pct_tolerancia: Mapped[float | None] = mapped_column(Float, nullable=True)        # tolerancia por unidad (con signo)
    activo: Mapped[bool] = mapped_column(default=True)

    # Operador TITULAR asignado (relación real al catálogo). operador_asignado (texto,
    # arriba) se conserva por compatibilidad/importación; este FK es el que manda.
    operador_asignado_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True)

    # Procedencia del renglón: responde "¿esta fila la respalda el maestro oficial y
    # cuándo se verificó?" frente a "la inventó una importación vieja". No cambia
    # ningún valor existente, solo añade trazabilidad.
    fuente_catalogo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verificado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    viajes: Mapped[list["Viaje"]] = relationship(back_populates="unidad")
    operador_titular: Mapped["Operador | None"] = relationship(foreign_keys=[operador_asignado_id])


class Remolque(Base):
    """Catálogo de remolques que se enganchan a los tractos (FLOTILLA!REMOLQUES).
    Algunos son DOLLY (no consumen combustible); los refrigerados sí (tienen termo)."""

    __tablename__ = "remolques"

    id: Mapped[int] = mapped_column(primary_key=True)
    eco: Mapped[str] = mapped_column(String(20), unique=True, index=True)         # A ECO (532301 / D-01)
    eco_nuevo: Mapped[str | None] = mapped_column(String(20), nullable=True)      # B ECO NUEVO
    serie: Mapped[str | None] = mapped_column(String(40), nullable=True)          # C SERIE
    serie_thermo: Mapped[str | None] = mapped_column(String(40), nullable=True)   # D SERIE THERMO
    placa: Mapped[str | None] = mapped_column(String(20), nullable=True)          # E PLACA
    placas_nuevas: Mapped[str | None] = mapped_column(String(20), nullable=True)  # F PLACAS NUEVAS
    anio: Mapped[int | None] = mapped_column(Integer, nullable=True)              # G AÑO
    marca: Mapped[str | None] = mapped_column(String(40), nullable=True)          # H MARCA
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)          # I DESCRIPCION
    es_dolly: Mapped[bool] = mapped_column(default=False)         # ECO D-0X o DESCRIPCION contiene DOLLY
    usa_combustible: Mapped[bool] = mapped_column(default=False)  # refrigerado con termo y no dolly
    activo: Mapped[bool] = mapped_column(default=True)
    # Operador TITULAR (responsable habitual del remolque). El coordinador lo asigna; en un
    # viaje se puede sobreescribir vía AsignacionViaje.remolque_operadores.
    operador_asignado_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True)
    @property
    def medida_pies(self) -> int | None:
        """Largo de la caja en pies, deducido de la descripción. None si no se sabe.

        Se DEDUCE en vez de guardarse porque el dato ya está escrito y duplicarlo abriría
        la puerta a que las dos copias discrepen. Tolera que alguien haya tecleado la
        comilla doble en lugar del pie: en el catálogo hay un 53" que así se lee bien.

        Vale None en los 4 remolques sin descripción, y eso NO es un hueco que tapar: sin
        medida no se puede afirmar que sean de 40, así que la regla de enganche los deja
        fuera de las parejas por sí sola.

        NO DEDUZCAS LA MEDIDA DEL NÚMERO ECONÓMICO (comprobado el 3-sep-2026). Es tentador:
        casi todas las cajas de 53 pies empiezan por 53 y casi todas las de 40 por 40, y
        contra el económico VIEJO la regla acierta 43 de 44 veces. Pero la renumeración la
        rompió: cinco cajas pasaron de 53… a 40… sin cambiar de tamaño, y dos de ellas lo
        dicen por escrito en su propia descripción —530901→400916 ("53' C/EQREF ALTA") y
        531215→401215 ("LEGALIZADO 53\"")—. Contra el económico VIGENTE la regla falla 3 de
        44. Se comprueba con:
            SELECT left(eco,2), left(eco_nuevo,2), descripcion FROM remolques
            WHERE eco_nuevo IS NOT NULL AND left(eco,2) <> left(eco_nuevo,2);
        El prefijo es una serie administrativa, no una medida. Y aquí no se está eligiendo
        un dato de catálogo cualquiera: de esto depende si dos cajas pueden engancharse,
        así que adivinar mal engancha o desengancha equipo físico. Los cuatro sin
        descripción (400913, 5300121, 53113, 53114) los tiene que medir una persona.
        """
        import re as _re
        d = self.descripcion or ""
        m = _re.search(r"\b(\d{2})\s*['\"\u2019\u201d]", d)
        if m:
            return int(m.group(1))
        m = _re.search(r"\b(40|45|48|53)\b", d)
        return int(m.group(1)) if m else None

    # Procedencia del renglón: responde "¿esta fila la respalda el maestro oficial y
    # cuándo se verificó?" frente a "la inventó una importación vieja". No cambia
    # ningún valor existente, solo añade trazabilidad.
    fuente_catalogo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verificado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    operador_titular: Mapped["Operador | None"] = relationship()


class Operador(Base):
    """Hoja 'op': OPERADOR + NUMERO. Extendido con expediente editable (se llena a
    mano desde el panel; NO se inventa: nace vacío)."""

    __tablename__ = "operadores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), index=True)
    # NÚMERO DE EMPLEADO. Alfanumérico porque el de la empresa lo es: la gasolinera ya lo
    # imprime en cada carga (CFRUIT056, CFRUIT051…) y el importador lo guarda en
    # `empleados_proveedor.numero`. Adoptarlo en vez de inventar un formato hace que la
    # persona sea LA MISMA aquí y en el consumo de diésel.
    #
    # Se guarda NORMALIZADO (mayúsculas, sin espacios) por `_num_empleado` en main.py: en
    # texto '1000' y '01000' son dos filas válidas y distintas para el UNIQUE, y la misma
    # persona para quien las captura.
    numero: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    activo: Mapped[bool] = mapped_column(default=True)

    # Expediente (editable desde el panel; opcional, se captura manualmente)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    licencia: Mapped[str | None] = mapped_column(String(60), nullable=True)       # folio/tipo
    licencia_vence: Mapped[date | None] = mapped_column(Date, nullable=True)
    # FEDERAL / ESTATAL y la categoría (A-E). Separado del folio porque no es lo mismo:
    # una licencia estatal NO habilita para carga federal, y mientras ese dato viva dentro
    # de la cadena «LF-04412876 · Federal tipo E» ninguna consulta lo puede ver.
    licencia_tipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    licencia_expedida: Mapped[date | None] = mapped_column(Date, nullable=True)
    # El nombre no distingue a dos personas: el padrón tiene homónimos reales (MIGUEL y
    # LUIS VITAL ALCANTARA). El CURP sí. No es único a propósito: un CURP mal capturado no
    # debe poder bloquear el alta de otra persona.
    curp: Mapped[str | None] = mapped_column(String(18), nullable=True)
    # El documento (PDF/JPG/PNG) vive EN DISCO, bajo media_dir/licencias, igual que la
    # evidencia de las solicitudes; aquí sólo queda el nombre del archivo. Un avatar de 80px
    # cabe en una columna; 265 licencias escaneadas en base64 no.
    licencia_doc: Mapped[str | None] = mapped_column(String(120), nullable=True)
    licencia_doc_mime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    licencia_doc_nombre: Mapped[str | None] = mapped_column(String(160), nullable=True)
    licencia_doc_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    ingreso: Mapped[date | None] = mapped_column(Date, nullable=True)
    estatus: Mapped[str | None] = mapped_column(String(20), nullable=True)        # ACTIVO/VACACIONES/INCAPACIDAD/BAJA
    rol: Mapped[str | None] = mapped_column(String(20), nullable=True)            # TITULAR / RELEVO (situacional)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    foto: Mapped[str | None] = mapped_column(Text, nullable=True)                 # imagen codificada en base64 (guardada en la BD)
    foto_mime: Mapped[str | None] = mapped_column(String(40), nullable=True)      # tipo MIME de la foto (image/png, ...)
    # PROVISIONAL: se creó automáticamente de un nombre del chat que no casó con el
    # catálogo. Espera resolución humana desde el panel (darlo de alta o vincularlo a un
    # operador real). Así no se ensucia el catálogo con duplicados silenciosos.
    provisional: Mapped[bool] = mapped_column(default=False, index=True)

    viajes: Mapped[list["Viaje"]] = relationship(back_populates="operador")


class Viaje(Base):
    """Un renglón de la hoja BITACORA (31 columnas). Campos = encabezados del Excel.

    Origen de cada dato en el flujo del bot:
      - capturado (mensaje/foto): fecha, unidad, operador, tipo_config, kilometros, odometro,
        lts_scaner, lts_real
      - calculado (motor de validación): rto, dif, rto_real, pct, descuentos, pct_cummins
      - telemetría (reporte del motor): del odometro en adelante
    """

    __tablename__ = "viajes"

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Identificación ---
    fecha: Mapped[date] = mapped_column(Date, index=True)                       # A FECHA
    unidad_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"), index=True)  # B UNIDAD
    tipo_config: Mapped[TipoConfig | None] = mapped_column(Enum(TipoConfig), nullable=True)  # C TIPO
    operador_id: Mapped[int | None] = mapped_column(ForeignKey("operadores.id"), nullable=True)  # D OPERADOR

    # --- Cálculo de combustible ---
    kilometros: Mapped[float | None] = mapped_column(Float, nullable=True)      # E KILOMETROS
    lts_scaner: Mapped[float | None] = mapped_column(Float, nullable=True)      # F LTS SCANER
    rto: Mapped[float | None] = mapped_column(Float, nullable=True)             # G RTO (km/l estimado)
    dif: Mapped[float | None] = mapped_column(Float, nullable=True)             # H DIF (litros)
    lts_real: Mapped[float | None] = mapped_column(Float, nullable=True)        # I LTS REAL
    rto_real: Mapped[float | None] = mapped_column(Float, nullable=True)        # J RTO REAL (km/l real)
    pct: Mapped[float | None] = mapped_column(Float, nullable=True)             # K % (desviación, fracción)
    codigo_cv: Mapped[str | None] = mapped_column(String(10), nullable=True)    # L c (C-C, C-V, ...)
    descuentos: Mapped[float | None] = mapped_column(Float, nullable=True)      # M DESCUENTOS
    pct_cummins: Mapped[float | None] = mapped_column(Float, nullable=True)     # N % SOPORTE TECNICO CUMMINS
    analista: Mapped[str | None] = mapped_column(String(10), nullable=True)     # O ANALISTA (iniciales)

    # Nivel del tanque leído de la aguja del tablero (fracción 0.0=vacío a 1.0=lleno).
    # Base de la aproximación de litros: nivel_tanque * capacidad_tanque(unidad).
    nivel_tanque: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Equipo de refrigeración del remolque (columnas THERMO / HORAS FINALES del formato
    # nuevo). El THERMO se identifica por el económico del remolque, p.ej. '531834'.
    remolque_thermo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    horas_termo: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Telemetría del motor ---
    odometro: Mapped[float | None] = mapped_column(Float, nullable=True)        # P ODOMETRO
    vel_max: Mapped[float | None] = mapped_column(Float, nullable=True)         # Q VEL MAX
    rpm: Mapped[float | None] = mapped_column(Float, nullable=True)             # R R.P.M
    ralenti: Mapped[float | None] = mapped_column(Float, nullable=True)         # S RALENTI
    crucero: Mapped[float | None] = mapped_column(Float, nullable=True)         # T CRUCERO
    paradas_panico: Mapped[int | None] = mapped_column(Integer, nullable=True)  # U PARADAS DE PANICO
    num_frenadas: Mapped[int | None] = mapped_column(Integer, nullable=True)    # V NUMERO DE FRENADAS
    neutralizacion: Mapped[float | None] = mapped_column(Float, nullable=True)  # W NEUTRALIZACION
    top_gear: Mapped[float | None] = mapped_column(Float, nullable=True)        # X TOP GEAR
    km_top_gear: Mapped[float | None] = mapped_column(Float, nullable=True)     # Y KM TOP GEAR
    pct_top_gear: Mapped[float | None] = mapped_column(Float, nullable=True)    # Z % TOP GEAR
    gear_down: Mapped[float | None] = mapped_column(Float, nullable=True)       # AA GEAR DOWN
    km_gear_down: Mapped[float | None] = mapped_column(Float, nullable=True)    # AB KM GEAR DOWN
    pct_gear_down: Mapped[float | None] = mapped_column(Float, nullable=True)   # AC % GEAR DOWN
    cambios_descendentes: Mapped[float | None] = mapped_column(Float, nullable=True)  # AD CAMBIOS DESCENDENTES
    c_manejo: Mapped[float | None] = mapped_column(Float, nullable=True)        # AE C DE MANEJO

    reportado_por: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # número (JID) de quien reportó
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Mensaje de WhatsApp que originó este viaje. Sin esto no había forma de ir del mensaje
    # al viaje: si borraban un reporte, el viaje quedaba registrado como si nada.
    origen_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # RETRACTADO: el reporte que lo originó se borró en WhatsApp o se corrigió. El viaje NO
    # se elimina —borrarlo destruiría el rastro y un borrado puede ser accidental o
    # malintencionado—: se marca, se excluye de reportes e indicadores, y queda visible para
    # que una persona decida. `retractado_por` distingue quién lo pidió (el grupo o el panel).
    retractado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)
    retractado_motivo: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Bitácora de correcciones hechas desde el grupo (qué cambió, de qué a qué y quién).
    # Sin esto un dato corregido se vería idéntico a uno capturado bien de origen, y se
    # perdería justo la información que explica por qué cambió.
    correcciones: Mapped[str | None] = mapped_column(Text, nullable=True)

    unidad: Mapped[Unidad] = relationship(back_populates="viajes")
    operador: Mapped[Operador | None] = relationship(back_populates="viajes")
    anomalias: Mapped[list["Anomalia"]] = relationship(back_populates="viaje")


class AuditoriaThermo(Base):
    """Hoja THORTON AUDITORIA: horas y rendimiento del equipo de refrigeración."""

    __tablename__ = "auditoria_thermo"

    id: Mapped[int] = mapped_column(primary_key=True)
    unidad_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"), index=True)  # A TRACTO
    operador_id: Mapped[int | None] = mapped_column(ForeignKey("operadores.id"), nullable=True)  # B OPERADOR
    remolque: Mapped[str | None] = mapped_column(String(20), nullable=True)     # J REMOLQUE
    mes: Mapped[str | None] = mapped_column(String(20), nullable=True)          # P (mes de la auditoría)

    kilometros: Mapped[float | None] = mapped_column(Float, nullable=True)      # C KILOMETROS
    lts_scaner: Mapped[float | None] = mapped_column(Float, nullable=True)      # D LTS SCANER
    rto: Mapped[float | None] = mapped_column(Float, nullable=True)             # E RTO
    dif: Mapped[float | None] = mapped_column(Float, nullable=True)             # F DIF
    lts_real: Mapped[float | None] = mapped_column(Float, nullable=True)        # G LTS REAL
    rto_real: Mapped[float | None] = mapped_column(Float, nullable=True)        # H RTO REAL
    pct: Mapped[float | None] = mapped_column(Float, nullable=True)             # I %

    hrs_inic: Mapped[float | None] = mapped_column(Float, nullable=True)        # K HRS INIC
    hrs_fin: Mapped[float | None] = mapped_column(Float, nullable=True)         # L HRS FIN
    horas_trab: Mapped[float | None] = mapped_column(Float, nullable=True)      # M HORAS TRAB
    litros_thermo: Mapped[float | None] = mapped_column(Float, nullable=True)   # N LITROS
    rto_thermo: Mapped[float | None] = mapped_column(Float, nullable=True)      # O RTO THERMO

    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    unidad: Mapped[Unidad] = relationship()
    operador: Mapped[Operador | None] = relationship()


class SeguimientoDescuento(Base):
    """Hoja 'Seguimiento Descuentos': cobro/escalamiento de descuentos por litros."""

    __tablename__ = "seguimiento_descuentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date | None] = mapped_column(Date, nullable=True)             # A FECHA
    unidad_clave: Mapped[str | None] = mapped_column(String(20), nullable=True) # B UNIDAD (texto crudo)
    tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)         # C TIPO (TRACTOR/THERMO/...)
    operador_nombre: Mapped[str | None] = mapped_column(String(120), nullable=True)  # D OPERADOR
    lts: Mapped[float | None] = mapped_column(Float, nullable=True)             # E LTS
    contesta: Mapped[bool | None] = mapped_column(Boolean, nullable=True)       # F CONTESTA (SI/NO)
    area: Mapped[str | None] = mapped_column(String(60), nullable=True)         # G AREA

    # Auditoría de aplicación desde el panel de Combustible (Fase 2): QUIÉN aplicó la
    # penalización, CUÁNDO, y el VIAJE que la originó (para no re-sugerir un viaje ya
    # penalizado). Las filas importadas del Excel histórico quedan con estos campos en NULL.
    aplicada_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    aplicada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viaje_id: Mapped[int | None] = mapped_column(ForeignKey("viajes.id"), nullable=True, index=True)


class Anomalia(Base):
    """Valores fuera de rango que requieren confirmación humana (Etapa 2)."""

    __tablename__ = "anomalias"

    id: Mapped[int] = mapped_column(primary_key=True)
    viaje_id: Mapped[int] = mapped_column(ForeignKey("viajes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(60))          # p.ej. "km_imposible", "consumo_atipico"
    descripcion: Mapped[str] = mapped_column(Text)
    estado: Mapped[EstadoAnomalia] = mapped_column(
        Enum(EstadoAnomalia), default=EstadoAnomalia.PENDIENTE, index=True
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resuelto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Quién resolvió (confirmó/rechazó) la anomalía: base de la atribución de desempeño.
    resuelto_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    viaje: Mapped[Viaje] = relationship(back_populates="anomalias")


class Usuario(Base):
    """Usuarios del dashboard (login)."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nombre: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activo: Mapped[bool] = mapped_column(default=True)
    # Perfil de la persona (ver RolUsuario). El permiso se valida en el servidor.
    rol: Mapped[str] = mapped_column(String(20), default="admin")
    # Si la cuenta es de un OPERADOR, apunta a su ficha del padrón: así el sistema sabe
    # quién captura y qué unidad trae asignada sin que él lo escriba. El padrón existente
    # se convierte en la base de las cuentas, conservando el número consecutivo.
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True, index=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Personalización de la cuenta (NO el nombre/apellidos, que se conservan): foto de perfil,
    # datos personales editables y preferencias de la app (color de acento, tema).
    foto: Mapped[str | None] = mapped_column(Text, nullable=True)                  # base64 en la BD
    foto_mime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prefs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)               # {accent, ...}
    # Último login exitoso: base de la bitácora de acceso del admin ("saber quién ve y cuándo").
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Versión de las sesiones de la cuenta. La cookie de sesión no tiene estado en el servidor,
    # así que restablecer la contraseña no cerraba las sesiones ya abiertas: una cookie robada
    # seguía valiendo hasta 8 h. La cookie lleva este número y `require_user` lo compara;
    # subirlo invalida de golpe todas las sesiones de la cuenta. La columna la añade el
    # arranque (main.lifespan) con ADD COLUMN IF NOT EXISTS.
    sesion_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AnalisisReporte(Base):
    """Reporte de análisis de flota generado por Claude. Se guarda automáticamente
    en cada generación para conservar el historial/contexto."""

    __tablename__ = "analisis_reportes"

    id: Mapped[int] = mapped_column(primary_key=True)
    texto: Mapped[str] = mapped_column(Text)                                     # narrativa de Claude
    resumen: Mapped[dict | None] = mapped_column(JSONB, nullable=True)           # datos agregados usados
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EscaneoMotor(Base):
    """Lectura de la computadora del propio camión (Cummins PowerSpec / Detroit DDEC).

    Cada archivo cubre un PERÍODO CERRADO de la unidad (desde la extracción anterior),
    no un viaje. Es la fuente de verdad "del carro" para auditar contra lo REPORTADO en
    el grupo: km recorridos, litros que el motor dice haber quemado, ralentí y manejo.
    """

    __tablename__ = "escaneos_motor"

    id: Mapped[int] = mapped_column(primary_key=True)
    unidad_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"), index=True)
    archivo: Mapped[str] = mapped_column(String(200), unique=True)   # idempotencia al importar
    formato: Mapped[str] = mapped_column(String(10))                 # CUMMINS | DDEC
    motor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    serie_motor: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Período que cubre el escaneo (inicio: explícito en DDEC, inferido en Cummins)
    periodo_inicio: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    periodo_fin: Mapped[date] = mapped_column(Date, index=True)

    # Acumulados de por vida del motor (para encadenar períodos y validar)
    odometro_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    lts_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    lts_ralenti_total: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Medido por el motor EN EL PERÍODO
    km: Mapped[float] = mapped_column(Float)
    litros: Mapped[float] = mapped_column(Float)
    rendimiento: Mapped[float | None] = mapped_column(Float, nullable=True)   # km/L del motor
    tiempo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lts_ralenti: Mapped[float | None] = mapped_column(Float, nullable=True)   # combustible parado
    pct_ralenti: Mapped[float | None] = mapped_column(Float, nullable=True)
    tiempo_ralenti: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vel_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    vel_prom: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm_prom: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    carga_prom: Mapped[float | None] = mapped_column(Float, nullable=True)
    km_crucero: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_crucero: Mapped[float | None] = mapped_column(Float, nullable=True)
    km_top_gear: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_top_gear: Mapped[float | None] = mapped_column(Float, nullable=True)
    frenadas: Mapped[float | None] = mapped_column(Float, nullable=True)
    paradas_panico: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuera_marcha: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Columna ANALISIS GENERAL ESCANER: lectura en prosa del corte (qué explica el
    # rendimiento y qué conviene corregir). La redacta la IA a partir de este escaneo.
    analisis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # EL PDF DE ORIGEN, en disco (media_dir/escaneos/esc<id>.pdf). `archivo` de arriba es
    # el nombre con el que llegó —la primera puerta de idempotencia—, no el papel: sin esto,
    # borrar la carpeta de Descargas dejaba al sistema con el nombre escrito y nada que
    # enseñar. El nombre del fichero lo deriva el servidor del id, así que ningún nombre de
    # fuera toca el disco.
    pdf: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pdf_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    unidad: Mapped[Unidad] = relationship()


class UsoIA(Base):
    """Cada llamada a la IA, con lo que costó.

    Sin esto no había forma de responder "¿cuántas consultas se hicieron?" ni de saber a
    dónde se fue el crédito: el gasto solo era visible cuando se acababa. Se registra
    aunque la llamada falle — un error también se cobra si alcanzó a procesar tokens, y
    los fallos repetidos son justamente lo que dispara el consumo sin que nadie lo note.
    """

    __tablename__ = "uso_ia"

    id: Mapped[int] = mapped_column(primary_key=True)
    momento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    operacion: Mapped[str] = mapped_column(String(30), index=True)   # imagen | mensaje | analisis…
    modelo: Mapped[str] = mapped_column(String(60))
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_lectura: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_escritura: Mapped[int] = mapped_column(Integer, default=0)
    costo_usd: Mapped[float] = mapped_column(Float, default=0.0)
    evento_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class EventoWhatsapp(Base):
    """Todo mensaje crudo recibido del grupo: auditoría y reprocesamiento."""

    __tablename__ = "eventos_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    remote_jid: Mapped[str] = mapped_column(String(120), index=True)
    participante: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tipo_mensaje: Mapped[str] = mapped_column(String(60))   # conversation, imageMessage, etc.
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nombre que la persona tiene puesto en su WhatsApp. Llegaba en el payload y se perdía;
    # es la única pista de identidad además del número.
    push_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Mensaje al que este RESPONDE (stanzaId del citado). Es el dato que permite saber con
    # certeza —sin adivinar por el texto— qué reporte se está corrigiendo.
    responde_a: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    media_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Estado de procesamiento (cola DURABLE): pendiente -> procesado | fallido. Permite
    # rehidratar la cola al arrancar (reprocesar lo que quedó pendiente tras un reinicio)
    # y dejar rastro recuperable de lo que agotó los reintentos.
    # pendiente -> procesado | fallido | omitido. 'omitido' = a propósito no se procesa
    # (reenviado, sin contenido, sin API key); NUNCA se reencola al rehidratar la cola.
    estado_proceso: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    # Motivo del último fallo. Antes solo iba al log: el panel mostraba "falló" sin decir
    # por qué, que no sirve para diagnosticar ni para decidir si vale la pena reprocesar.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
# Migración a plataforma web — Fase A: solicitud de recarga y orden de despacho
# ─────────────────────────────────────────────────────────────────────────────
# Toda la operación gira alrededor de UN objeto: la solicitud de recarga. Su estado dice
# en todo momento quién la tiene y qué falta para cerrarla. Se monta sobre el modelo
# existente (unidades, viajes, operadores) sin alterarlo.


class SolicitudRecarga(Base):
    """Una petición de carga de diésel, con su ciclo de vida completo.

    Nace cuando alguien la crea (el operador desde su teléfono, o el coordinador en su
    nombre) y termina conciliada contra la factura. Nada se borra: una solicitud
    equivocada se ANULA con motivo y permanece en el historial.
    """

    __tablename__ = "solicitudes_recarga"

    id: Mapped[int] = mapped_column(primary_key=True)
    estado: Mapped[EstadoSolicitud] = mapped_column(
        Enum(EstadoSolicitud), default=EstadoSolicitud.BORRADOR, index=True)

    # Quién la creó (usuario autenticado) y a quién/qué se atribuye.
    creada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True, index=True)
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True, index=True)
    unidad_id: Mapped[int | None] = mapped_column(
        ForeignKey("unidades.id"), nullable=True, index=True)
    # La otra mitad de un «carga motor Y termo»: cada una apunta a la otra. El parentesco
    # vivía sólo como prosa dentro de la nota de la transición («Creada junto con la
    # solicitud 123»), así que el servidor no podía razonar sobre él y le exigía a la del
    # termo su propio económico — que el operador escanea UNA vez, sobre el mismo camión.
    # DE QUÉ VIAJE SALIÓ. Sin esto, el contexto y el tope resolvían el viaje como «la
    # asignación activa de ese operador ahora», así que levantar un viaje nuevo movía en
    # silencio las solicitudes vivas al destino, los kilómetros y el TOPE del siguiente.
    #
    # No vale `viaje_id`: ése apunta a `Viaje`, que es historia CONGELADA y está siempre
    # en nulo mientras la solicitud vive. Esto apunta al mundo operativo.
    asignacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("asignaciones_viaje.id"), nullable=True, index=True)

    hermana_id: Mapped[int | None] = mapped_column(
        ForeignKey("solicitudes_recarga.id"), nullable=True, index=True)
    # Remolque al que se liga una recarga de TERMO de un tracto (su económico y horas). Null en
    # recargas de motor y en camiones (ahí el termo comparte el económico de la unidad motriz).
    remolque_id: Mapped[int | None] = mapped_column(
        ForeignKey("remolques.id"), nullable=True, index=True)
    # El viaje al que pertenece la carga. Puede llegar después (en Fase A la captura pasa
    # por el coordinador y el viaje puede no estar definido al crear la solicitud).
    viaje_id: Mapped[int | None] = mapped_column(
        ForeignKey("viajes.id"), nullable=True, index=True)
    # Idempotencia de la cola offline: la app del operador genera un UUID por solicitud. Si
    # un reintento de sincronización repite el POST (la red se cayó tras crearla), el
    # servidor devuelve la existente en vez de duplicar la carga.
    client_uuid: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True)

    litros_solicitados: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Tipo de recarga: 'motor' (diésel del motor, por defecto) o 'termo' (diésel del equipo de
    # frío). Un camión refrigerado hace 2 recargas bajo el MISMO económico: motor y termo.
    tipo_recarga: Mapped[str] = mapped_column(String(10), default="motor", server_default="motor")
    # Lecturas de instrumentos ya consolidadas (el detalle con foto y procedencia va en
    # EvidenciaRecarga). Se copian aquí para el cálculo y la validación.
    odometro: Mapped[float | None] = mapped_column(Float, nullable=True)
    nivel_tanque: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Motivo cuando se devuelve, rechaza o anula: nunca se pierde el porqué.
    motivo: Mapped[str | None] = mapped_column(String(400), nullable=True)

    # Captura ASISTIDA: el coordinador captura por un operador que no puede/quiere usar la
    # app, sin romper la trazabilidad de a quién pertenece la carga.
    capturada_asistida: Mapped[bool] = mapped_column(Boolean, default=False)

    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    operador: Mapped["Operador | None"] = relationship()
    unidad: Mapped["Unidad | None"] = relationship()
    remolque: Mapped["Remolque | None"] = relationship()
    evidencias: Mapped[list["EvidenciaRecarga"]] = relationship(
        back_populates="solicitud", cascade="all, delete-orphan")
    transiciones: Mapped[list["TransicionSolicitud"]] = relationship(
        back_populates="solicitud", cascade="all, delete-orphan",
        order_by="TransicionSolicitud.id")
    orden: Mapped["OrdenDespacho | None"] = relationship(
        back_populates="solicitud", uselist=False)


class EvidenciaRecarga(Base):
    """Una lectura de instrumento de la solicitud: la foto, lo que leyó la IA, el valor
    final y DE DÓNDE salió (procedencia). Es la base para medir la confiabilidad de la
    lectura automática por tipo de instrumento."""

    __tablename__ = "evidencias_recarga"

    id: Mapped[int] = mapped_column(primary_key=True)
    solicitud_id: Mapped[int] = mapped_column(
        ForeignKey("solicitudes_recarga.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))   # odometro | nivel | termo | comprobante
    foto_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    valor_ia: Mapped[float | None] = mapped_column(Float, nullable=True)     # lo que leyó la IA
    valor_final: Mapped[float | None] = mapped_column(Float, nullable=True)  # lo que quedó
    procedencia: Mapped[Procedencia] = mapped_column(
        Enum(Procedencia), default=Procedencia.MANUAL)
    confianza: Mapped[str | None] = mapped_column(String(10), nullable=True)  # alta/media/baja
    # Fase 5a: evidencias de TEXTO (número económico rotulado) y su verificación contra lo que
    # asignó el coordinador. etiqueta = nombre legible del slot ("Odómetro", "ECO Remolque 1").
    etiqueta: Mapped[str | None] = mapped_column(String(60), nullable=True)
    esperado: Mapped[str | None] = mapped_column(String(40), nullable=True)   # ECO asignado
    texto_ia: Mapped[str | None] = mapped_column(String(60), nullable=True)   # lo que leyó la IA (texto)
    # Juicio de AUTENTICIDAD de la foto, separado del de LEGIBILIDAD (`confianza`). Una
    # recaptura —la foto de la pantalla de otro teléfono— se lee perfecta y es falsa: son dos
    # preguntas distintas y mezclarlas en un solo campo perdería una de las dos.
    sospechosa: Mapped[bool] = mapped_column(default=False)
    sospecha_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    coincide: Mapped[bool | None] = mapped_column(Boolean, nullable=True)     # ECO leído == esperado
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    solicitud: Mapped["SolicitudRecarga"] = relationship(back_populates="evidencias")


class OrdenDespacho(Base):
    """Se genera cuando el coordinador AUTORIZA una solicitud. Nace con un FOLIO automático
    e inmutable que ya lleva unidad, viaje y operador: eso convierte la atribución de la
    factura de una adivinanza a una consulta. Los campos de despacho (litros reales, quién
    cargó) se llenan en Fase C."""

    __tablename__ = "ordenes_despacho"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Folio consecutivo, generado por el sistema, NUNCA cambia. Es la llave que une orden,
    # despacho y factura. Formato p.ej. 'OD-000123'.
    folio: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(
        ForeignKey("solicitudes_recarga.id"), unique=True, index=True)

    autorizada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    autorizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    litros_autorizados: Mapped[float | None] = mapped_column(Float, nullable=True)

    # EL TOPE QUE REGÍA AL AUTORIZAR, congelado aquí.
    #
    # No se puede recalcular después: el tope nace del ÚLTIMO escáner del motor, y cada
    # escaneo nuevo lo mueve. Reconstruirlo sobre una orden de hace un mes inventaría un
    # exceso que quien firmó nunca vio.
    #
    # Se escriben SIEMPRE, se haya pasado o no: sin las órdenes que SÍ cupieron, el conteo
    # de las que no cupieron no tiene denominador. Y quedan en nulo cuando no había tope
    # —27 de las 53 unidades activas no tienen escáner—, que también es el dato: distingue
    # «no se pasó» de «no había con qué comparar».
    #
    # `excedido` NO es columna: es `litros_autorizados > tope_litros`, y una copia del
    # booleano sólo podría acabar desmintiendo a los dos números que tiene al lado.
    tope_litros: Mapped[float | None] = mapped_column(Float, nullable=True)
    tope_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)
    # El tope EN PALABRAS: los km, el km/l y de qué escáner salieron. Es lo que permite
    # leer una orden vieja sin reconstruir la base de aquel día, y cuando no hubo tope es
    # lo que dice por qué no lo hubo.
    tope_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)

    # EL RENGLÓN DEL PROVEEDOR QUE CERRÓ ESTA ORDEN.
    #
    # Antes el ciclo se cerraba contra un CFDI. Retiradas las facturas, la prueba de que
    # esa recarga existió y se cobró es la fila del Excel de OXXO o XYGA —que además es
    # mejor prueba: llega al detalle de la bomba y el folio, no al total del mes—.
    #
    # Se guarda el vínculo y no sólo el hecho de haber conciliado, porque «¿y esto quién lo
    # pagó?» es una pregunta que se hace un año después, cuando ya nadie se acuerda.
    carga_proveedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("cargas_proveedor.id"), nullable=True, index=True)
    conciliada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Lo que PROPUSO la IA, al lado de lo que decidió la persona. Mismo patrón que la
    # evidencia —donde `valor_ia` se conserva intacto y `valor_final` guarda la decisión—,
    # porque un solo campo no puede contestar «¿le hizo caso?», que es la pregunta que hace
    # útil el registro. `litros_sugeridos` NO se sobrescribe jamás.
    litros_sugeridos: Mapped[float | None] = mapped_column(Float, nullable=True)
    sugerencia_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Despacho (Fase C): se llenan al cargar el diésel.
    litros_reales: Mapped[float | None] = mapped_column(Float, nullable=True)
    despachada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    despachada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Fase 3 (conciliación): el comprobante fiscal (CFDI) que ampara esta carga. Una factura
    # del proveedor suele cubrir VARIAS órdenes del día, de ahí que la relación sea N→1.
    factura_id: Mapped[int | None] = mapped_column(
        ForeignKey("facturas.id"), nullable=True, index=True)

    solicitud: Mapped["SolicitudRecarga"] = relationship(back_populates="orden")


class TransicionSolicitud(Base):
    """Cada cambio de estado de una solicitud: quién, cuándo y qué cambió respecto al valor
    anterior. Es la bitácora que después alimenta el desempeño del personal (tiempo de
    respuesta del coordinador, tasa de corrección por operador) SIN captura adicional."""

    __tablename__ = "transiciones_solicitud"

    id: Mapped[int] = mapped_column(primary_key=True)
    solicitud_id: Mapped[int] = mapped_column(
        ForeignKey("solicitudes_recarga.id"), index=True)
    estado_anterior: Mapped[str | None] = mapped_column(String(20), nullable=True)
    estado_nuevo: Mapped[str] = mapped_column(String(20))
    por_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    momento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    # Qué campos cambiaron y de qué a qué (para trazabilidad de correcciones).
    cambios: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    nota: Mapped[str | None] = mapped_column(String(400), nullable=True)

    solicitud: Mapped["SolicitudRecarga"] = relationship(back_populates="transiciones")


# ─────────────────────────────────────────────────────────────────────────────
# Migración a plataforma web — Fase B: asignación de viajes (la app del operador)
# ─────────────────────────────────────────────────────────────────────────────
# El coordinador asigna a un operador una unidad y un DESTINO: es el "viaje en curso".
# Le da al operador su unidad y su destino ACTUALES sin que él los escriba, y al cerrarse
# queda como viaje previo. Es PROSPECTIVO —a diferencia de Viaje, que es el registro
# retrospectivo del consumo de la BITACORA—: por eso vive en su propia tabla y no se mezcla
# con aquél. Regla: una sola asignación ACTIVA por operador; asignar una nueva finaliza la
# anterior.


class EstadoAsignacion(str, enum.Enum):
    ACTIVA = "activa"          # el operador la trae en curso ahora
    FINALIZADA = "finalizada"  # llegó a destino / se cerró; queda como viaje previo
    CANCELADA = "cancelada"    # se canceló antes de realizarse


class AsignacionViaje(Base):
    """El coordinador asigna operador + unidad + destino: el viaje en curso del operador."""

    __tablename__ = "asignaciones_viaje"

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operadores.id"), index=True)
    unidad_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"), index=True)
    destino: Mapped[str] = mapped_column(String(160))
    origen: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Coordenadas opcionales elegidas en el mapa (Leaflet/OSM). El texto sigue siendo la
    # etiqueta legible; nullable = asignar sin mapa funciona igual que antes.
    origen_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origen_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    destino_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    destino_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Km estimado al destino (línea recta desde las coords del mapa), editable por el
    # coordinador: la referencia de "cuántos km son del destino".
    km_destino: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Km ESTIMADO de la ruta (OSRM por carretera, o línea recta si falla). km_destino es el
    # valor final que usa el coordinador; si lo cambia a mano se registra el ajuste (quién/cuándo).
    km_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)
    km_modificado: Mapped[bool] = mapped_column(default=False)
    km_modificado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    km_modificado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Tipo de viaje: retorno = la unidad regresa a la base (vs ida normal).
    es_retorno: Mapped[bool] = mapped_column(default=False)
    nota: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Remolques enganchados a este viaje (el operador fotografía el ECO de cada uno y la IA lo
    # verifica). Lista de ids de Remolque; se guarda como JSON porque son 0-2 cajas y no
    # ameritan una tabla puente.
    remolque_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Override de operador POR remolque en este viaje: {str(remolque_id): operador_id}. Si un
    # remolque no aparece aquí, su operador es el titular del remolque (o el del viaje).
    remolque_operadores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    estado: Mapped[EstadoAsignacion] = mapped_column(
        Enum(EstadoAsignacion), default=EstadoAsignacion.ACTIVA, index=True)

    creada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    finalizada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Quién cerró/canceló la asignación (antes no se guardaba el actor de la finalización).
    finalizada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    # Cuándo el OPERADOR vio por primera vez su asignación (abrió su viaje activo). Base del
    # "tiempo desde la notificación hasta que se enteró": visto_en − creada_en. NULL = no la ha visto.
    visto_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    operador: Mapped["Operador"] = relationship()
    unidad: Mapped["Unidad"] = relationship()


class RegistroActividad(Base):
    """Bitácora GENERAL de acciones de las personas (base del panorama de Desempeño).

    Complementa a TransicionSolicitud (que ya audita el pipeline de recarga con actor+tiempo):
    aquí quedan las demás acciones que antes no dejaban rastro — login, asignación creada/vista/
    finalizada, resolución de anomalía… Guarda el ROL en el instante de la acción (snapshot) para
    que la atribución histórica no se desvíe si la persona cambia de rol después. `latencia_seg`
    conserva el tiempo de respuesta calculado en el momento (p.ej. visto_en − creada_en) para no
    depender de reconstruirlo luego.
    """

    __tablename__ = "registro_actividad"

    id: Mapped[int] = mapped_column(primary_key=True)
    momento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True, index=True)
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True, index=True)
    rol: Mapped[str | None] = mapped_column(String(20), nullable=True)      # snapshot del rol
    accion: Mapped[str] = mapped_column(String(40), index=True)             # "login", "asignacion_vista"…
    entidad: Mapped[str | None] = mapped_column(String(30), nullable=True)  # "asignacion", "anomalia"…
    entidad_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latencia_seg: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
# Migración a plataforma web — Fase 3: facturación y conciliación por folio
# ─────────────────────────────────────────────────────────────────────────────
# El módulo de combustible sube el CFDI (factura electrónica) del proveedor y el sistema lo
# CONCILIA contra las órdenes despachadas del día: si los litros del comprobante cuadran con
# la suma de lo despachado (dentro de tolerancia), las solicitudes pasan a FACTURADA/CONCILIADA;
# si no, se marcan EN_DISCREPANCIA para revisión. El folio interno (OD-###) ata orden↔despacho
# y ahora la factura las agrupa: una factura del proveedor cubre varias cargas del día.


class Factura(Base):
    """Comprobante fiscal (CFDI) del proveedor de diésel. Sus datos se extraen del XML."""

    __tablename__ = "facturas"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid_cfdi: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True, index=True)  # folio fiscal (TimbreFiscalDigital)
    rfc_emisor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nombre_emisor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Litros amparados por el CFDI (suma de los conceptos de combustible). Es la cantidad
    # que se coteja contra la suma de litros_reales de las órdenes que ampara.
    litros: Mapped[float | None] = mapped_column(Float, nullable=True)
    fecha: Mapped[str | None] = mapped_column(String(40), nullable=True)   # fecha del CFDI (ISO del atributo Fecha)
    xml_path: Mapped[str | None] = mapped_column(String(300), nullable=True)

    subida_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    subida_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    # Queda conciliada cuando sus órdenes cuadran en litros dentro de la tolerancia.
    conciliada: Mapped[bool] = mapped_column(Boolean, default=False)


# ═════════════════════════════════════════════════════════════════════════════
# CATÁLOGO · resolución de identidad y corrección por propuestas  (etapa E1)
#
# El mismo activo llega escrito de varias formas: el económico viejo (53113), el
# nuevo (400917), la clave (T-135) o la placa. Antes cada importador conocía UNA
# columna, así que daba de alta como "nuevo" un remolque que ya existía. Aquí ese
# conocimiento se centraliza y —esto es lo importante— la corrección del catálogo
# deja de ser escritura directa: la importación PROPONE y una persona APLICA.
# ═════════════════════════════════════════════════════════════════════════════
class AliasEco(Base):
    """Un texto por el que se conoce a un activo. Es ADITIVO a propósito.

    La alternativa era consolidar `Remolque.eco` y `eco_nuevo` en una sola columna,
    pero sería destructivo: hay campos históricos que apuntan al remolque POR TEXTO y
    no por relación (`Viaje.remolque_thermo`, `SeguimientoDescuento.unidad_clave`), así
    que renombrar dejaría esas referencias apuntando al vacío en silencio. Un alias da
    el mismo poder de resolución con cero riesgo.
    """

    __tablename__ = "alias_eco"

    id: Mapped[int] = mapped_column(primary_key=True)
    texto_norm: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    unidad_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id"), nullable=True)
    remolque_id: Mapped[int | None] = mapped_column(ForeignKey("remolques.id"), nullable=True)
    # De dónde salió: el maestro de placas, el catálogo de flotilla, un reporte del
    # proveedor o una corrección a mano.
    origen: Mapped[str] = mapped_column(String(12), default="manual")
    confirmado: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class ImportacionPlacas(Base):
    """Una corrida del maestro de placas. La huella evita reprocesar el mismo archivo."""

    __tablename__ = "importaciones_placas"

    id: Mapped[int] = mapped_column(primary_key=True)
    archivo: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    importado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    n_unidades: Mapped[int] = mapped_column(Integer, default=0)
    n_remolques: Mapped[int] = mapped_column(Integer, default=0)
    vigente: Mapped[bool] = mapped_column(Boolean, default=True)


class PropuestaCatalogo(Base):
    """Un cambio SUGERIDO al catálogo, pendiente de que una persona lo apruebe.

    Misma disciplina que `TransicionSolicitud` aplicada al catálogo: queda escrito qué
    se propuso, por qué, quién lo aplicó y cuándo. Un importador que escribe directo es
    justo lo que produjo los duplicados que se quieren evitar.

    `accion` es una de:
      crear            · no coincide ni por económico ni por placa
      actualizar_placa · la placa del maestro difiere de la registrada
      registrar_alias  · el económico difiere pero la placa coincide (es el par viejo/nuevo)
      reactivar        · existe dado de baja y el maestro lo lista
      conflicto        · el económico casa con una fila y la placa con otra
    NUNCA se propone `desactivar` un activo con consumo registrado: se verificó que hay
    remolques que queman diésel y no aparecen en el maestro.
    """

    __tablename__ = "propuestas_catalogo"

    id: Mapped[int] = mapped_column(primary_key=True)
    importacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("importaciones_placas.id"), nullable=True, index=True)
    entidad: Mapped[str] = mapped_column(String(10))          # 'unidad' | 'remolque'
    accion: Mapped[str] = mapped_column(String(20), index=True)
    entidad_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eco_texto: Mapped[str | None] = mapped_column(String(24), nullable=True)
    placa_texto: Mapped[str | None] = mapped_column(String(24), nullable=True)
    valor_actual: Mapped[str | None] = mapped_column(String(120), nullable=True)
    valor_propuesto: Mapped[str | None] = mapped_column(String(120), nullable=True)
    motivo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    estado: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    aplicada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    aplicada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class EtiquetaActivo(Base):
    """El holograma pegado físicamente a un activo. Sustituye a teclear el económico.

    POR QUÉ ES UNA TABLA APARTE Y NO UN ALIAS MÁS: un alias es un texto por el que se
    conoce a un activo; una etiqueta es un OBJETO FÍSICO que alguien pegó, que se puede
    despegar, dañar o reemplazar, y que se lee con un aparato. Necesita estado y fecha
    propios. Mezclarla con `alias_eco` —que se siembra sola desde el catálogo— además
    contaminaría la siembra automática con datos que solo cambian a mano.

    POR QUÉ EL VÍNCULO PUEDE NACER VACÍO: en el maestro que entregó el cliente hay dos
    hologramas repetidos en dos tractos distintos cada uno. Un sticker no puede estar
    pegado en dos camiones, así que esas filas se guardan con `estado='conflicto'` y
    SIN activo: escanear ahí no resuelve nada hasta que una persona diga cuál es cuál.
    Vincular a la primera coincidencia habría sido peor que no vincular — atribuiría
    litros a la unidad equivocada sin que nadie se enterara.

    `uso` distingue las dos etiquetas de un mismo remolque refrigerado (una para el
    motor y otra para el termo), que es la regla que ya rige la captura: mismo económico,
    dos recargas distintas.
    """

    __tablename__ = "etiquetas_activo"

    id: Mapped[int] = mapped_column(primary_key=True)
    # El UID tal como lo entrega el lector, en mayúsculas y sin separadores.
    codigo: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(16), default="holograma")
    unidad_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id"), nullable=True)
    remolque_id: Mapped[int | None] = mapped_column(ForeignKey("remolques.id"), nullable=True)
    # 'vinculada'  · apunta a un activo y resuelve
    # 'conflicto'  · el maestro la asigna a más de un activo; NO resuelve hasta decidirlo
    # 'duplicada'  · el maestro da VARIOS códigos al MISMO activo. Es el caso simétrico del
    #                conflicto y se trata igual de estricto: mientras nadie declare cuál va en
    #                el motor y cuál en el termo, no se sabe qué sticker está pegado dónde
    # 'sin_activo' · el económico del maestro todavía no existe en el catálogo
    # 'retirada'   · se despegó o se reemplazó; se conserva para poder leer el pasado
    estado: Mapped[str] = mapped_column(String(12), default="vinculada", index=True)
    uso: Mapped[str | None] = mapped_column(String(10), nullable=True)   # 'motor' | 'termo'
    # El económico tal como venía escrito en el maestro, aunque no resuelva. Es lo que
    # permite volver a intentar la vinculación cuando el activo se dé de alta.
    # Text y no String(24): en un conflicto guarda TODOS los candidatos separados por coma, y
    # un arrastre de celda en Excel puede dejar cuatro o más. Con un varchar corto la escritura
    # reventaba entera, después de un dry-run impecable.
    eco_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    origen: Mapped[str] = mapped_column(String(12), default="holograma")
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    actualizada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class ImportacionEtiquetas(Base):
    """Una corrida del maestro de hologramas. La huella evita reprocesar el mismo archivo."""

    __tablename__ = "importaciones_etiquetas"

    id: Mapped[int] = mapped_column(primary_key=True)
    archivo: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    n_filas: Mapped[int] = mapped_column(Integer, default=0)
    n_vinculadas: Mapped[int] = mapped_column(Integer, default=0)
    n_conflicto: Mapped[int] = mapped_column(Integer, default=0)
    n_sin_activo: Mapped[int] = mapped_column(Integer, default=0)
    importado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


# ═════════════════════════════════════════════════════════════════════════════
# PROVEEDORES · ingesta VERBATIM de los archivos de despacho  (etapa E2)
#
# El problema: cada mes llegan dos Excel de proveedores distintos (OXXO y XYGA)
# con 639 cargas, 118,541.02 L y $3,218,987.99, y hasta hoy nadie sabía decir de
# qué renglón de qué archivo salió un litro. Estas seis tablas guardan CADA fila
# TAL COMO LLEGÓ —sin interpretarla— y encima anotan lo que se dedujo, sin que lo
# segundo pueda pisar lo primero.
#
# Las tres reglas que explican casi todas las decisiones de abajo:
#   1. VERBATIM Y DERIVADO CONVIVEN. Donde hay un `_txt` hay al lado un valor
#      tipado; el `_txt` es la prueba y el tipado es la consulta. Nunca se
#      sustituye uno por el otro.
#   2. SOLO SE ASCIENDE A COLUMNA LO QUE UNA CONSULTA FILTRA, AGRUPA O UNE. Todo
#      lo demás vive dentro de `fila_cruda` — igual de recuperable, pero fuera del
#      alcance de un SUM accidental. Los Kms de XYGA son basura demostrada ('123'
#      en 280 filas) y por eso NO tienen columna.
#   3. NADA SE SOBRESCRIBE Y NADA SE VINCULA SOLO. Una corrección del proveedor
#      inserta una revisión nueva y jubila la vieja; el vínculo de una tarjeta o
#      de un empleado a un activo o a una persona nace NULL y solo lo escribe
#      alguien de carne y hueso.
#
# E2 no calcula rendimiento, no empareja con órdenes de despacho (eso es E3) y no
# escribe una sola fila en `viajes`, `ordenes_despacho` ni `facturas`.
# ═════════════════════════════════════════════════════════════════════════════
class Proveedor(Base):
    """Quién emitió el archivo y CON QUÉ LAYOUT lo emite.

    El concepto de proveedor no existía en ninguna parte del código: `facturas` solo
    guarda `rfc_emisor`/`nombre_emisor` como texto libre, así que esta tabla es aditiva
    pura. Resuelve dos problemas que no pertenecen a la carga:

    (a) EL CONTRATO DE LECTURA (hoja, filas, ancho y encabezado literal). Sin él, el mes
        en que el proveedor inserte una columna la ingesta leería todo corrido —placas en
        la columna de litros— y cuadraría igual de bien. Con él se detiene y dice qué
        cambió. La ingesta NUNCA busca una columna por su nombre: compara el encabezado
        completo contra `encabezado_esperado` y luego lee POR POSICIÓN.
    (b) LO QUE ES CONSTANTE POR ARCHIVO. Cliente='FRUIT2DAY' y Grupo='UTILITARIOS' tienen
        cardinalidad 1 en 326/326 filas de OXXO: como columna de carga serían 326 copias
        del mismo texto.
    """

    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    # La llave humana: la teclea el importador (`--proveedor OXXO`) y aparece en todo
    # filtro. UNIQUE porque es la raíz del espacio de llaves de las otras cinco tablas —
    # un duplicado lo partiría en dos y las cargas de un mes irían a un proveedor fantasma.
    clave: Mapped[str] = mapped_column(String(12), unique=True, index=True)   # 'OXXO' | 'XYGA'
    # Razón comercial. Separada de `clave` porque cambia sin que cambie la clave.
    nombre: Mapped[str] = mapped_column(String(120))
    # Gancho para que E4 una contra `Factura.rfc_emisor`, hoy la única pista del vendedor
    # dentro del CFDI. Nace NULL: no lo tenemos de ninguno de los dos.
    rfc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cliente_texto: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 'FRUIT2DAY'
    grupo_texto: Mapped[str | None] = mapped_column(String(120), nullable=True)    # 'UTILITARIOS'
    # 'consolidada_mensual' (XYGA timbra A-1971351 en 313/313) | 'por_carga' | 'sin_dato'
    # (OXXO ni siquiera emite la columna). Es DATO, no constante del código: si XYGA pasa a
    # timbrar por carga cambia el valor de esta fila, no el esquema.
    regimen_factura: Mapped[str] = mapped_column(String(20), default="sin_dato")
    # La zona horaria DECLARADA por una persona, jamás adivinada. Es el único insumo con el
    # que la ingesta puede derivar `CargaProveedor.momento_ref`, y queda escrito cuál se usó.
    zona_horaria: Mapped[str] = mapped_column(String(40), default="America/Mexico_City")
    # NULL en OXXO, donde openpyxl entrega un datetime real (tipo datetime en 326/326, con
    # tzinfo None en 326/326). '%d/%m/%Y %I:%M:%S %p' en XYGA. Deja escrito en la base POR QUÉ
    # XYGA necesita traducir el sufijo 'a. m.'/'p. m.' antes de que strptime pueda con él.
    formato_fecha: Mapped[str | None] = mapped_column(String(60), nullable=True)
    hoja: Mapped[str] = mapped_column(String(60))            # 'Reporte' | 'worksheet'; hoja única en ambos
    fila_encabezado: Mapped[int] = mapped_column(Integer)    # 6 en OXXO (5 filas vacías antes), 5 en XYGA
    fila_datos: Mapped[int] = mapped_column(Integer)         # 7 en OXXO, 6 en XYGA
    # 26 en OXXO, 25 en XYGA. Cada fila llega como tupla de ese ancho EXACTO (el conjunto de
    # anchos medido es {26} y {25}); las posiciones 20..25 de OXXO están siempre vacías y su
    # encabezado es None, pero se leen y se guardan igual: el día que las rellene, aquí están.
    n_columnas: Mapped[int] = mapped_column(Integer)
    # Las 26/25 cadenas literales CON SUS ERRATAS, como ARRAY POSICIONAL. Las erratas son el
    # dato: XYGA escribe 'Departmento' (con una 'a' de más), 'No. Economico' con espacio tras
    # el punto pero 'No.Ticket' sin él, y 'Descripcion' sin acento; OXXO sí acentúa y termina
    # con SEIS None. Corregir una errata aquí sería romper el detector de cambio de layout.
    encabezado_esperado: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Se deja de recibir a un proveedor sin borrar sus cargas, que siguen siendo dinero real.
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class EstacionProveedor(Base):
    """La gasolinera, normalizada. Sobre todo: DONDE VIVE EL HUSO HORARIO.

    Se midió antes de decidir crearla: el 1:1 código↔nombre es EXACTO en ambos proveedores
    (41↔41 en OXXO, 50↔50 en XYGA, cero códigos con dos nombres), así que normalizar es
    riesgo cero; el nombre llega a 71 caracteres repetido hasta 53 veces; y los desfases
    entre 'Fecha Historica' y 'Fecha de Facturacion(CDMX)' se agrupan POR ESTACIÓN (de
    −2.46 h a +1.03 h, constantes dentro de cada una). Sin esta tabla ese conocimiento no
    tiene dónde acumularse y cada etapa lo redescubre — que es exactamente "rehacer".

    Lo único que la ingesta escribe aquí es EVIDENCIA (desfase mediano observado, contadores,
    rango de fechas) para que una persona decida `zona_horaria`. E2 no la inventa.

    `CargaProveedor` conserva además `estacion_txt` DENORMALIZADO porque es la mitad de la
    llave natural, y una llave no puede depender de un JOIN.
    """

    __tablename__ = "estaciones_proveedor"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    # TEXTO, y no es cosmético: 40 de los 41 códigos de OXXO NO son numéricos ('P02073',
    # 'ECO50036' de 8 caracteres, 'P07196-B' con guion); los 50 de XYGA sí lo son ('831').
    # Un cast a entero los rechaza y un VARCHAR(6) los trunca, mezclando dos gasolineras.
    codigo: Mapped[str] = mapped_column(String(24))
    # Text y no String(80): la longitud la decide el proveedor. Máximo medido 71 (OXXO), 36 (XYGA).
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    # La zona REAL de esta estación, si alguien la determina. NACE NULL. Cuando se llene,
    # `momento_ref` se recalcula solo para esas filas con un UPDATE: ningún campo verbatim se toca.
    zona_horaria: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Mediana observada de `cargas_proveedor.desfase_facturacion_min` (solo OXXO trae las dos
    # fechas). Es EVIDENCIA acumulada, no una regla: sirve para que una persona decida la zona
    # horaria en vez de adivinarla.
    desfase_mediano_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "las 6 estaciones principales concentran el 65%" deja de ser una frase y pasa a ser un ORDER BY.
    n_cargas: Mapped[int] = mapped_column(Integer, default=0)
    # SIN zona: es un momento observado del proveedor (su reloj de pared), no del sistema.
    primera_carga: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    # Delata la estación que dejó de operar sin necesidad de borrarla.
    ultima_carga: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # JAMÁS unique sobre el código solo: OXXO usa alfanuméricos y XYGA numéricos cortos,
        # y una colisión mezclaría las cargas de dos gasolineras distintas sin que nada fallara.
        UniqueConstraint("proveedor_id", "codigo", name="ux_estaciones_prov_codigo"),
    )


class TarjetaCombustible(Base):
    """El plástico con el que se despacha. EL NOMBRE Y DOS COLUMNAS SON CONTRATO YA ESCRITO.

    `app/catalogo.py` hace `from .models import TarjetaCombustible`, luego
    `db.get(TarjetaCombustible, tarjeta_id)` —o sea busca por CLAVE PRIMARIA, no por número
    de tarjeta— y lee EXACTAMENTE `t.unidad_id` y `t.remolque_id`. Hasta ahora ese import
    lanzaba ImportError en ejecución y el paso 1 de la cascada de resolución era código
    muerto. Declarar esta clase es lo único que lo revive; renombrar esos dos atributos lo
    vuelve a matar.

    Y es una entidad real, no un apaño: hay 97 pares (proveedor, tarjeta) para 62 activos,
    porque 32 activos tienen DOS tarjetas, una por proveedor.

    EL VÍNCULO AL ACTIVO NACE EN NULL y solo lo llena una persona. La razón no es pureza:
    `catalogo.py` resuelve por tarjeta ANTES de mirar el económico y devuelve confianza
    'alta', así que un vínculo desactualizado —una reasignación normal de operación— le
    ganaría a un económico correcto y mandaría litros al activo equivocado, en silencio.
    `vinculo_confirmado` es la válvula que impide esa única misatribución posible: mientras
    sea False, el importador NO le pasa `tarjeta_id` a `resolver_activo`.
    """

    __tablename__ = "tarjetas_combustible"

    # Este id ES el `tarjeta_id` que recibe `resolver_activo()`. El importador traduce
    # texto→id contra `numero_norm` ANTES de llamarlo; nunca le pasa el número.
    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    # VERBATIM, y aquí se juega la identidad de un proveedor entero: XYGA entrega str en
    # 313/313 filas y sus 52 tarjetas miden 5 caracteres empezando en '0' ('00006'..'00244'),
    # mientras OXXO entrega int en 326/326 (9-10 dígitos). int('00140') = 140 destruye la
    # tarjeta, y lo hace en silencio.
    numero_txt: Mapped[str] = mapped_column(Text)
    # strip + upper. NO quita los ceros a la izquierda: son parte del número. Es la mitad
    # comparable de la llave única y lo que el importador consulta en cada fila.
    numero_norm: Mapped[str] = mapped_column(String(48), index=True)
    # ATRIBUTOS OBLIGADOS POR app/catalogo.py — no se renombran. Nacen NULL: E2 observa y
    # propone, jamás vincula solo.
    unidad_id: Mapped[int | None] = mapped_column(
        ForeignKey("unidades.id"), nullable=True, index=True)
    remolque_id: Mapped[int | None] = mapped_column(
        ForeignKey("remolques.id"), nullable=True, index=True)
    # 'ninguno' | 'propuesta' | 'manual'. String(12) como `AliasEco.origen`, jamás Enum:
    # revertir E2 tiene que ser un DROP de seis tablas, y un Enum deja un tipo huérfano.
    vinculo_origen: Mapped[str] = mapped_column(String(12), default="ninguno")
    # LA VÁLVULA (ver el docstring). Mientras sea False, la tarjeta no resuelve nada.
    vinculo_confirmado: Mapped[bool] = mapped_column(Boolean, default=False)
    vinculado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    vinculado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # El económico que el proveedor imprime junto a esta tarjeta. Es OBSERVACIÓN, no vínculo:
    # alimenta la propuesta y permite detectar una reasignación. El 1:1 tarjeta↔económico es
    # exacto hoy, pero es el invariante de UN mes; guardarlo como vínculo sería creerle de más.
    eco_observado: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 'T301 // 55LA3F', 'CAJA REFRIGERADA  531832   89U'. Vive AQUÍ porque es constante por
    # tarjeta (52 textos para 52 tarjetas, 0 tarjetas con dos). NUNCA se parsea: solo 80 de
    # 313 filas siguen el patrón 'ECO // PLACA'. Text porque 12 filas traen un TABULADOR dentro.
    descripcion_proveedor: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lo prende la ingesta cuando el económico observado contradice el vínculo guardado.
    # No corrige: avisa. Corregir sería justo el pecado del importador viejo.
    revisar: Mapped[bool] = mapped_column(Boolean, default=False)
    nota_revision: Mapped[str | None] = mapped_column(String(300), nullable=True)
    n_cargas: Mapped[int] = mapped_column(Integer, default=0)
    # Momentos del proveedor: sin zona, igual que en `estaciones_proveedor`.
    primera_vista: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    ultima_vista: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # JAMÁS unique sobre el número solo: hoy hay 0 colisiones entre los dos formatos, pero
        # el rango de XYGA es 6..244 —una secuencia INTERNA del proveedor—, así que un tercer
        # proveedor con numeración corta colisiona el primer día y dos activos comparten llave
        # sin que nada falle.
        UniqueConstraint("proveedor_id", "numero_norm", name="ux_tarjetas_prov_numero"),
        # La bandeja de tarjetas en disputa: parcial porque pesa casi nada y casi siempre está vacía.
        Index("ix_tarjetas_revisar", "proveedor_id", postgresql_where=text("revisar")),
        # SIN unique sobre unidad_id ni remolque_id: el 1:1 tarjeta↔activo es un invariante
        # VERIFICADO que la ingesta REPORTA, no una restricción. Como restricción convertiría
        # una reasignación normal de operación en una ingesta que revienta a medio mes.
    )


class EmpleadoProveedor(Base):
    """Convierte un empate difuso POR FILA en 34 decisiones humanas de UNA sola vez.

    El proveedor ya emite un identificador estable de persona que nadie usaba: 'No.Empleado'
    de OXXO, formato 'CFRUIT###', 34 valores, mapeo 1:1 EXACTO con 'Nombre Conductor' (cero
    empleados con dos nombres, cero nombres con dos empleados).

    Sin esta tabla, cada importación reintenta la coincidencia por nombre y atribuye
    combustible a la persona equivocada: solo 97 de 326 filas empatan de forma única (30%),
    15 de los 34 nombres encajan con DOS O MÁS operadores ('JOSE LUIS GARCIA MARQUEZ' encaja
    con 4) y la propia tabla `operadores` tiene 5 nombres que colisionan entre sí. Aquí la
    pregunta se hace una vez, la contesta una persona y no se vuelve a hacer.

    XYGA no aporta persona ('No. Operador' es None en 313/313), así que esta tabla solo se
    puebla desde OXXO — y eso está bien: la mitad de la respuesta es mejor que una inventada.
    """

    __tablename__ = "empleados_proveedor"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    numero: Mapped[str] = mapped_column(String(24))   # 'CFRUIT123'; máximo medido 9 caracteres
    # Verbatim, tal como lo escribe el proveedor. Máximo medido 33 caracteres.
    nombre_proveedor: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SIEMPRE por decisión humana. Nace NULL y NINGUNA ingesta lo escribe, nunca.
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id"), nullable=True, index=True)
    # 'pendiente' | 'vinculado' | 'sin_equivalente'. El tercero es el que evita repetir
    # trabajo: "esta persona no existe en el catálogo" también es una respuesta, y no hay
    # que volver a preguntarla cada mes.
    vinculo_estado: Mapped[str] = mapped_column(String(12), default="pendiente")
    vinculado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    vinculado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Los dos contadores existen para PRIORIZAR las 34 decisiones: primero quien más despachó.
    n_cargas: Mapped[int] = mapped_column(Integer, default=0)
    litros: Mapped[float] = mapped_column(Float, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # El formato 'CFRUIT###' es de OXXO; otro proveedor puede numerar 1..N y chocar.
        UniqueConstraint("proveedor_id", "numero", name="ux_empleados_prov_numero"),
        # La cola de las 34 decisiones, como índice parcial: es la vista de trabajo real.
        Index("ix_empleados_pendientes", "proveedor_id",
              postgresql_where=text("vinculo_estado = 'pendiente'")),
    )


class ImportacionProveedor(Base):
    """Una corrida de lectura de UN archivo: unidad de reversión y prueba de origen.

    Calcada en estilo de `ImportacionPlacas` (archivo/sha256/importado_en/por_id/vigente),
    con cuatro divergencias justificadas: guarda el ARCHIVO ENTERO en base64, guarda el
    ENCABEZADO LEÍDO, deriva el PERIODO de las fechas de las filas (nunca del nombre del
    archivo), y su unique sobre `sha256` es PARCIAL.

    POR QUÉ EL UNIQUE ES PARCIAL: E1 importa un MAESTRO —una foto completa— mientras que E2
    importa EXPORTACIONES PERIÓDICAS cuyos rangos se traslapan. Con un unique simple, una
    corrida ANULADA seguiría ocupando la llave del sha e impediría volver a importar el mismo
    archivo, que es justo lo que "anular y volver a correr" promete. El índice
    `ux_importaciones_prov_sha256 ON (sha256) WHERE vigente` lo crea `scripts/migrate_e2.py`
    con text(): un UniqueConstraint no admite predicado.
    """

    __tablename__ = "importaciones_proveedor"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    # El nombre original tal cual llegó, extensión mentirosa incluida: XYGA se llama
    # '...xlsx.xls' y por dentro es xlsx, así que openpyxl la rechaza POR EL NOMBRE y hay que
    # abrirla pasándole el contenido con BytesIO. Guardar el nombre real conserva esa pista.
    archivo: Mapped[str] = mapped_column(String(300))
    # 49255 (OXXO) / 47034 (XYGA). El mismo nombre con distinto tamaño ya es sospechoso
    # antes siquiera de abrirlo.
    archivo_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Huella del CONTENIDO: 9ba67d24aab0... (OXXO) / 40931cdf5b6c... (XYGA). index a secas,
    # no unique: el unique vive en el índice PARCIAL (ver docstring) y este cubre además la
    # búsqueda entre las corridas ANULADAS, que el parcial no alcanza.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # EL ARCHIVO ENTERO en base64, ~64 KB por archivo (~1.5 MB al año con dos proveedores).
    # Ya hay precedente en la base: `Operador.foto` y `Usuario.foto` guardan base64 en Text.
    # Es lo ÚNICO que responde literalmente a "enséñame la fila exacta del archivo original":
    # un `xml_path` como el de `Factura` apunta a algo que cualquiera puede mover o editar, y
    # entonces el sha256 deja de ser verificable contra nada. deferred para que un SELECT
    # distraído del ORM no arrastre 64 KB por fila sin querer.
    archivo_b64: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    hoja_leida: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Las 26/25 cadenas REALMENTE leídas, como ARRAY POSICIONAL. Se compara contra
    # `Proveedor.encabezado_esperado` y contra la corrida anterior: es el detector de cambio
    # de layout. Y `encabezado_leido[i]` es EL NOMBRE de `CargaProveedor.fila_cruda[i]` —
    # sin esta columna, el array posicional de la carga sería ilegible dentro de un año.
    encabezado_leido: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # La celda A2 de XYGA ('Fecha Impresion:  06/08/2026 12:15') VERBATIM. Es la única fecha
    # de origen REAL: el archivo no trae docProps/core.xml y openpyxl INVENTA
    # creator='openpyxl' con el instante en que uno lo abre, así que usar wb.properties.created
    # produciría una bitácora falsa con apariencia de metadato.
    impresion_txt: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # DERIVADOS de min/max de `momento_local` de las filas, JAMÁS del nombre del archivo:
    # 'REPORTE+DE+CONSUMOS_06_08_2026' no contiene una sola fila de agosto.
    periodo_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    periodo_hasta: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    n_filas_leidas: Mapped[int] = mapped_column(Integer, default=0)   # 326 / 313
    n_nuevas: Mapped[int] = mapped_column(Integer, default=0)
    # Llave natural presente y sha256_fila IDÉNTICO: no se toca nada. Es la reimportación normal.
    n_repetidas: Mapped[int] = mapped_column(Integer, default=0)
    # Llave presente y sha256_fila DISTINTO: supersesión + revisión pendiente. Si supera el
    # 10% de las filas, la corrida se DETIENE y pide confirmación: un retoque cosmético del
    # proveedor no debe inundar la bandeja con 313 pendientes.
    n_corregidas: Mapped[int] = mapped_column(Integer, default=0)
    # DEBE ser 0. Existe para que "no se descarta una fila en silencio" sea VERIFICABLE con un
    # assert, no una promesa del que escribió el importador.
    n_omitidas: Mapped[int] = mapped_column(Integer, default=0)
    # {fila_num: motivo} por cada omitida. Sin esto, `n_omitidas` sería un número sin recurso.
    motivos_omision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Deben reproducir 57,910.09 L / $1,578,329.30 (OXXO) y 60,630.93 L / $1,640,658.69 (XYGA).
    # Se guardan aquí para poder auditar la corrida SIN reabrir el Excel.
    litros_total: Mapped[float] = mapped_column(Float, default=0)
    importe_total: Mapped[float] = mapped_column(Float, default=0)
    n_resueltas: Mapped[int] = mapped_column(Integer, default=0)
    n_cuarentena: Mapped[int] = mapped_column(Integer, default=0)
    n_fuera_flota: Mapped[int] = mapped_column(Integer, default=0)
    # 'simulada' (--dry-run: queda escrito que se miró y no se escribió) | 'aplicada' | 'anulada'.
    estado: Mapped[str] = mapped_column(String(12), default="aplicada")
    importado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    # Mismo campo que `ImportacionPlacas.vigente`, pero aquí además es EL DISCRIMINADOR del
    # unique parcial de `sha256`: anular una corrida libera la llave del archivo.
    vigente: Mapped[bool] = mapped_column(Boolean, default=True)
    anulada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    anulada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(300), nullable=True)
    nota: Mapped[str | None] = mapped_column(String(400), nullable=True)


class CargaProveedor(Base):
    """UNA fila del archivo del proveedor: tal como llegó, y además interpretada.

    Responde las dos preguntas centrales de la auditoría:
      · `importacion_id` + `fila_num` + `fila_cruda` → el renglón exacto del Excel;
      · `sha256_fila` + el `archivo_b64` de la importación y su sha → la prueba de que nadie
        lo alteró.

    LA REGLA DE QUÉ MERECE COLUMNA: solo lo que una consulta filtra, agrupa, indexa o une.
    Todo lo demás vive dentro de `fila_cruda`, igual de recuperable. Ese criterio es a la vez
    el mecanismo de defensa: 'Kms' y 'Kms/Lt' de XYGA NO tienen columna, así que nadie puede
    sumarlos por accidente en un GROUP BY — hay que ir a buscarlos a `fila_cruda->>17`. Son
    basura demostrada ('123' en 280 filas, '0' en 32, '15' en 1, con 261 filas donde Kms=123
    pero Kms/Lt=0: el archivo se contradice a sí mismo).

    LA RESOLUCIÓN CONTRA EL CATÁLOGO SE MATERIALIZA AQUÍ, no se calcula al leer, porque E3
    empareja en ventana de ±6 h y E4/E6 agrupan por activo y periodo: las dos cosas tienen
    que ser barridos de índice, no una función por fila.

    No calcula rendimiento, no toca `viajes` y no crea órdenes de despacho.
    """

    __tablename__ = "cargas_proveedor"

    # ── IDENTIDAD Y AUDITORÍA ────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(primary_key=True)
    # REDUNDANTE con la importación A PROPÓSITO: es componente de la llave natural, y una
    # llave no puede depender de un JOIN.
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    # Sin esto no se revierte una corrida — que es exactamente lo que
    # `PropuestaCatalogo.importacion_id` permite en E1.
    importacion_id: Mapped[int] = mapped_column(
        ForeignKey("importaciones_proveedor.id"), index=True)
    # Número de fila EN EL EXCEL (7..332 en OXXO, 6..318 en XYGA), no el índice del lector.
    # Es lo que permite decir "abre el archivo y vete a la fila 24".
    fila_num: Mapped[int] = mapped_column(Integer)
    # ARRAY POSICIONAL con las 26/25 celdas convertidas a texto, en el orden del archivo,
    # incluidas las 6 columnas fantasma de OXXO. POSICIONAL Y NO POR NOMBRE, y esto es
    # decisivo: el encabezado de OXXO trae SEIS entradas None (posiciones 20..25), así que un
    # objeto JSON con clave=nombre colapsaría las seis en una única clave `null` y perdería
    # datos. Además el encabezado miente ('Departmento') y algún día se corregirá; la POSICIÓN
    # es el contrato y `ImportacionProveedor.encabezado_leido[i]` la nombra.
    # Convención fija: valor → str(); datetime → isoformat COMPLETO con microsegundos;
    # celda vacía y '' → null.
    fila_cruda: Mapped[list] = mapped_column(JSONB)
    # sha256 de `fila_cruda` unida por \x1f, CON LOS DATETIME TRUNCADOS AL SEGUNDO y los
    # espacios internos colapsados. Se trunca porque 125 filas de OXXO cargan microsegundos
    # derivados del serial flotante del XML (46234.965024919 → .153000) que una reexportación
    # puede entregar distintos; se colapsan espacios para que un retoque cosmético del
    # proveedor no dispare 313 falsas correcciones. Truncado sigue discriminando 326/326 y
    # 313/313, así que no se pierde poder de detección.
    # NO es un sello anti-manipulación —quien tenga la base puede recalcularlo—: es un
    # detector de cambios ENTRE IMPORTACIONES. La prueba de no alteración es archivo_b64.
    sha256_fila: Mapped[str] = mapped_column(String(64))

    # ── SUPERSESIÓN (una corrección NUNCA sobrescribe) ───────────────────────
    revision: Mapped[int] = mapped_column(Integer, default=1)   # 1 = como llegó la primera vez
    # La versión que cuenta. ES EL DISCRIMINADOR DEL ÍNDICE ÚNICO PARCIAL, y a propósito NO se
    # usa `sustituye_a_id IS NULL` como predicado: ese no libera las llaves cuando se ANULA una
    # corrida completa (sus filas seguirían sin sustituto y seguirían ocupando la llave), y
    # este sí — anular es un solo UPDATE que pone vigente=False en la importación y sus filas.
    vigente: Mapped[bool] = mapped_column(Boolean, default=True)
    # Autorreferencia a la versión anterior. ondelete SET NULL es OBLIGATORIO: sin él, el
    # `DELETE FROM cargas_proveedor WHERE importacion_id = N` de la reversión dura se traba
    # contra sus propias autorreferencias y hay que borrar en orden topológico a mano.
    sustituye_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("cargas_proveedor.id", ondelete="SET NULL"), nullable=True)
    # NULL en una fila normal; 'pendiente' | 'aceptada' | 'rechazada' en una corrección. La
    # bandeja de correcciones es una consulta sobre esta columna: cero tablas extra.
    estado_revision: Mapped[str | None] = mapped_column(String(12), nullable=True)
    revisada_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True)
    revisada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # ── LLAVE NATURAL ────────────────────────────────────────────────────────
    # 'No.Estacion' VERBATIM y denormalizado. 0 vacíos en las 639 filas, máximo 8 caracteres.
    # Si alguna vez llega vacío, la ingesta pone el centinela '@fila:<n>', lo escribe en
    # `motivo_estado` y LA FILA ENTRA: perder una carga es peor que tener una llave fea.
    estacion_txt: Mapped[str] = mapped_column(String(24))
    # 'No.Transaccion' de OXXO (llega int, 5-9 dígitos) o 'No.Ticket' de XYGA (llega str,
    # 3-9 dígitos). SIEMPRE texto: no se opera aritméticamente y llega con tipos distintos por
    # proveedor, así que como Integer el mismo folio quedaría representado distinto y la
    # restricción no detectaría el duplicado.
    folio_txt: Mapped[str] = mapped_column(String(32))

    # ── ESTACIÓN Y BOMBA ─────────────────────────────────────────────────────
    # El asa para llegar al nombre y a la zona horaria de la gasolinera.
    estacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("estaciones_proveedor.id"), nullable=True)
    # Verbatim también aquí, por fidelidad: la normalización vive en la otra tabla y esto es
    # la copia auditable de lo que decía el papel.
    estacion_nombre_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TEXTO: llega int en OXXO y str en XYGA. Es lo que permite ver que la ráfaga del T203 del
    # 14/07 fue toda en la MISMA bomba 8 — o sea que era un llenado real, no un duplicado.
    bomba_txt: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ── TIEMPO ───────────────────────────────────────────────────────────────
    # El texto EXACTO cuando el proveedor manda texto ('01/07/2026 02:51:18 a. m.', 25
    # caracteres en 313/313 de XYGA); para OXXO, el isoformat de lo que entregó openpyxl.
    # Es la columna que delata si el reemplazo del sufijo 'a. m.'→'AM' falló.
    fecha_txt: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # EL RELOJ DE PARED DEL TICKET, sin convertir. ROMPE A PROPÓSITO la racha de timestamptz
    # del proyecto (30 columnas con zona, 0 sin ella): las 326 fechas de OXXO llegan con
    # tzinfo=None y vienen en hora LOCAL DE LA ESTACIÓN, así que meterlas en un timestamptz
    # las convertiría según el TimeZone de la sesión y movería cargas hasta 2.46 h en silencio.
    # NOT NULL: las 313 de XYGA parsean 313/313 tras traducir el sufijo, cubriendo las 24 horas.
    momento_local: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), index=True)
    # `momento_local` interpretado en la zona DECLARADA. Existe porque `OrdenDespacho.autorizada_en`
    # es timestamptz: sin esta columna, la ventana de ±6 h de E3 sería un cast por fila, no
    # indexable, y cada endpoint reinventaría la suposición de zona por su cuenta.
    momento_ref: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # QUÉ zona se usó para derivar `momento_ref` (la de la estación si está declarada, si no la
    # del proveedor). Sin esta columna, `momento_ref` sería una interpretación con apariencia de
    # dato; con ella es una derivación auditable y recalculable en masa con un UPDATE.
    zona_aplicada: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # date(momento_local). Los rollups por día y por mes de E4/E6 son la consulta más frecuente
    # que va a existir, y castear un timestamp por fila en cada una no se indexa igual.
    fecha_operacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 'Fecha de Facturacion(CDMX)', solo OXXO. COLUMNA SEPARADA porque NO son derivables una de
    # otra: truncando microsegundos difieren en 220 de 326 filas, 26 por más de una hora, y en
    # 27 la facturación es ANTERIOR al despacho. Un `assert facturacion >= despacho` abortaría
    # la importación de un archivo perfectamente legítimo.
    momento_facturacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True)
    # (momento_facturacion − momento_local) en minutos. Es una resta de dos valores ya
    # guardados, no una interpretación. Medido: mínimo −147.77, máximo +2446.35. Hace que
    # "cuál es el huso de esta estación" y "cuál fue la carga de contingencia" sean un
    # ORDER BY en vez de un script.
    desfase_facturacion_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── IDENTIDAD DEL ACTIVO (verbatim + normalizado, SIEMPRE los dos) ───────
    tarjeta_txt: Mapped[str | None] = mapped_column(Text, nullable=True)   # conserva '00140' y '485403732'
    tarjeta_norm: Mapped[str | None] = mapped_column(String(48), nullable=True)
    tarjeta_id: Mapped[int | None] = mapped_column(
        ForeignKey("tarjetas_combustible.id"), nullable=True)
    # VERBATIM CON LA SUCIEDAD: ' 531702' con espacio inicial (9 filas) y 'T203 ' con espacio
    # final (10 filas). NULLABLE porque 4 filas legítimas y facturadas ($5,341.91) no lo traen,
    # y un NOT NULL descuadraría la conciliación contra la factura consolidada.
    eco_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # norm_eco(). String(24) EXACTAMENTE como `alias_eco.texto_norm`, para que no exista una
    # longitud en la que el importador guarde un económico que el resolvedor nunca encontraría.
    eco_norm: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 61 filas de XYGA llegan en minúsculas y una con guiones ('94-ur-1e'); OXXO trunca la
    # placa de 401001 a '56UD5' contra el '56UD5B' de XYGA. Guardar el original es lo único
    # que permite explicar después por qué no casaron.
    placa_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # norm_placa(). SOLO corrobora, jamás resuelve por sí sola (regla de catalogo.py).
    placa_norm: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # SIN validar el formato. Las longitudes medidas son {3, 17, 18}: 5 filas traen el literal
    # 'XXX' y una trae 18 caracteres ('3H3V5332K2PJ471005') que EMPATA igual, porque
    # `remolques.serie` arrastra la misma errata. Un String(17) o un CHECK tiraría 6 cargas
    # legítimas. Corrobora 314/326 y contradice 0.
    vin_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 'Descripcion del Vehiculo' (OXXO, máx. 26) o 'Descripcion' (XYGA, máx. 40 con tabulador dentro).
    descripcion_txt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── PERSONA ──────────────────────────────────────────────────────────────
    empleado_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)   # 'CFRUIT###' verbatim
    empleado_id: Mapped[int | None] = mapped_column(
        ForeignKey("empleados_proveedor.id"), nullable=True, index=True)
    # 'Nombre Conductor' verbatim, máx. 33. Vive en la CARGA y no en el activo porque el
    # conductor es dato POR CARGA: 16 de 45 económicos rotan operador y DANIEL GONZALEZ PEREZ
    # aparece en 6 económicos distintos. NO hay `operador_id` aquí: el vínculo a personas se
    # hace UNA vez en `empleados_proveedor`, no 639 veces por coincidencia de nombres.
    conductor_txt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── DINERO Y LITROS (texto verbatim + Float derivado, lado a lado) ───────
    litros_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)   # '300.000' tal cual
    litros: Mapped[float | None] = mapped_column(Float, nullable=True)
    importe_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)  # 'Total' (XYGA) | 'Consumo en Pesos' (OXXO)
    importe: Mapped[float | None] = mapped_column(Float, nullable=True)         # el importe CON impuestos
    # Solo XYGA. Es precio CON impuestos: Lts × Precio cuadra contra el Total (265/313 exacto)
    # y JAMÁS contra el Subtotal (0/313).
    precio_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # NULL en OXXO, que no emite la columna. NO se rellena con importe/litros: el precio
    # agregado correcto es sum(importe)/sum(litros), y un promedio de razones por fila sería un
    # número equivocado con apariencia de dato.
    precio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Verbatim CON TODOS SUS DECIMALES: subtotal e IVA traen 6, el IEPS 7 ('147.2400000').
    # Redondear aquí perdería la única forma de reproducir el total del proveedor al centavo.
    subtotal_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    iva_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ieps_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # NULL en OXXO, y ese NULL es EN SÍ el dato: "OXXO no desglosa impuestos". Un 0 mentiría.
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    iva: Mapped[float | None] = mapped_column(Float, nullable=True)
    ieps: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── PRODUCTO ─────────────────────────────────────────────────────────────
    # VERBATIM. Máximo medido 26 ('Mobil Sinergy Diesel Nuevo').
    producto_txt: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # SOLO mayúsculas y espacios colapsados. Que 'Diesel' y 'DIESEL' sean el mismo texto no es
    # interpretar: XYGA trae 'DIESEL' 308 veces y 'Diesel' 1 vez, y agrupar por el literal
    # inventa un combustible fantasma perdiendo 143.7 L y $3,878.41. Lo que NO hace es colapsar
    # 'DieselAutomotriz' y 'Mobil Sinergy Diesel Nuevo' en un solo 'DIESEL': eso SÍ es
    # interpretar y vive en una tabla de equivalencias de E3.
    producto_norm: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # DERIVADA: 'diesel' | 'gasolina' | 'otro'. Es LA ÚNICA inferencia de E2, y está aquí
    # porque `fuera_de_flota` depende de ella y tiene que ser consultable. La regla que se
    # aplicó queda escrita en `motivo_estado` y siempre es rederivable desde `fila_cruda`.
    combustible: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # ── FACTURA Y EXTRAS ASCENDIDOS ──────────────────────────────────────────
    # 'A-1971351' en 313/313 de XYGA, NULL en OXXO. POR CARGA y nullable, no atributo del
    # proveedor: es el gancho de E4 y así no hay que migrar nada si XYGA pasa a CFDI por carga.
    factura_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Informativo, para buscar el ticket físico. JAMÁS llave ni UNIQUE: 11 filas de XYGA lo
    # traen vacío y como llave produce 335 colisiones sobre las 639.
    folio_qr_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Venta fuera de línea. Se asciende AUNQUE hoy tenga un solo valor en 326 filas
    # ('224727615') porque esa fila es precisamente la que E3 no podrá emparejar por hora:
    # +40.77 h entre sus dos fechas y sin conductor. Con una fila al mes el volumen es
    # despreciable; el patrón no.
    contingencia_txt: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # ── RESOLUCIÓN MATERIALIZADA ─────────────────────────────────────────────
    unidad_id: Mapped[int | None] = mapped_column(
        ForeignKey("unidades.id"), nullable=True)      # lo que devolvió resolver_activo()
    remolque_id: Mapped[int | None] = mapped_column(
        ForeignKey("remolques.id"), nullable=True)
    # 'motor' | 'termo' | 'indeterminado'. Proyección mecánica del TIPO de activo que resolvió,
    # no un cálculo. Medido sobre las 639: 281 motor (tracto) / 83,030.19 L · 124 termo
    # (remolque) / 15,198.19 L · 191 INDETERMINADO / 15,629.78 L. Los 191 son unidades CAMION,
    # donde el termo va pegado y comparte el económico del motor. Un booleano `es_termo`
    # mentiría sobre el 13.2% de los litros del mes.
    destino: Mapped[str | None] = mapped_column(String(14), nullable=True)
    # 'resuelta' | 'cuarentena'. NACE EN CUARENTENA: lo que no se demuestra no se da por bueno,
    # y así una fila que el importador no alcanzó a procesar aparece en la bandeja en vez de
    # contarse como buena.
    estado_resolucion: Mapped[str] = mapped_column(
        String(12), default="cuarentena", index=True)
    # 'tarjeta' | 'eco' | 'placa' | 'ninguna'. OJO: 'placa' lo escribe EL IMPORTADOR, no
    # catalogo.py — su rama de placa devuelve via='eco' con confianza='media', así que el
    # importador detecta ese caso y lo reetiqueta aquí, dejando constancia en `discrepancias`.
    # Sin eso, "cuántas cargas resolvieron solo por placa" daría 0 mirando el campo equivocado.
    resuelto_via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 'alta' | 'media' | 'nula', tal cual `Resuelto.confianza`.
    resuelto_confianza: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # DERIVADA DEL PRODUCTO, no de la ausencia de económico. CAMPO SEPARADO de
    # `estado_resolucion` a propósito: son ejes INDEPENDIENTES, y colapsarlos haría
    # irrepresentable el día en que alguien dé de alta las camionetas ("resuelta y fuera de
    # flota"). Hoy las 4 filas de '87 OCTANOS' son exactamente las 4 sin económico, pero la
    # causa es el producto: hay 39 filas de DIESEL sin resolver que SÍ son flota real por dar
    # de alta, y no deben caer en el mismo cajón.
    fuera_de_flota: Mapped[bool] = mapped_column(Boolean, default=False)
    # La regla que se aplicó, escrita: 'fuera de flota: producto 87 OCTANOS'. Deja la única
    # inferencia de E2 explícita y auditable en vez de escondida en el código del importador.
    motivo_estado: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # `Resuelto.discrepancias` más lo que observe la ingesta (VIN que no casa, placa vieja vs
    # nueva, económico que contradice el vínculo de la tarjeta). Nada se pierde y nada bloquea.
    discrepancias: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # La AÑADA de la resolución. Cuando se apliquen las propuestas pendientes del catálogo y la
    # cuarentena baje, esto dice exactamente qué filas traen la respuesta vieja y hay que
    # re-resolver — sin volver a tocar un solo campo verbatim.
    resuelto_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # LA LLAVE NATURAL —(proveedor_id, estacion_txt, folio_txt) WHERE vigente— NO se declara
        # aquí: es un UNIQUE PARCIAL y un UniqueConstraint no admite predicado. La crea
        # `scripts/migrate_e2.py` con text() como `ux_cargas_llave`, junto con
        # `ux_importaciones_prov_sha256`. Verificado sobre los dos archivos: 639 llaves distintas
        # de 639 filas, 0 componentes vacíos. Cada pieza está por una razón medida — el proveedor
        # porque la no-colisión entre No.Transaccion y No.Ticket es casualidad (rangos
        # solapados); la estación porque el folio es un contador POR ESTACIÓN y 47 pares de
        # estaciones de OXXO y 243 de XYGA tienen rangos solapados; y texto porque llega int en
        # uno y str en el otro.
        # OJO al insertar: con índice parcial hay que REPETIR el predicado en la sentencia
        # (`ON CONFLICT (proveedor_id, estacion_txt, folio_txt) WHERE vigente DO NOTHING`) o
        # Postgres responde "no unique or exclusion constraint matching the ON CONFLICT
        # specification" y la ingesta falla entera.

        # LA ventana de ±6 h de E3 contra OrdenDespacho.autorizada_en y el agrupado por
        # activo+periodo de E4, en un barrido de rango. Parciales porque la mitad de las filas
        # tiene el otro extremo en NULL.
        Index("ix_cargas_unidad_momento", "unidad_id", "momento_ref",
              postgresql_where=text("unidad_id IS NOT NULL")),
        # El mismo servicio para el termo, que son 15,198.19 L medidos: no es un caso marginal.
        Index("ix_cargas_remolque_momento", "remolque_id", "momento_ref",
              postgresql_where=text("remolque_id IS NOT NULL")),
        # El rollup mensual por proveedor: justo lo que E6 necesita para facturar distinto por
        # proveedor y lo que E4 concilia contra el CFDI consolidado.
        Index("ix_cargas_prov_fecha", "proveedor_id", "fecha_operacion"),
        # La bandeja de cuarentena como cola de trabajo. Parcial: pesa casi nada y "lo que quedó
        # sin resolver" nunca debe ser un seq scan sobre el mes entero.
        Index("ix_cargas_pendientes", "estado_resolucion", "fecha_operacion",
              postgresql_where=text("estado_resolucion <> 'resuelta'")),
        # La bandeja de correcciones del proveedor (las filas que llegaron distintas a como
        # estaban y esperan que una persona acepte o rechace la nueva versión).
        Index("ix_cargas_revision", "proveedor_id", "fecha_operacion",
              postgresql_where=text("estado_revision = 'pendiente'")),
        # Revertir o auditar una corrida completa, y localizar la fila exacta del Excel.
        Index("ix_cargas_importacion", "importacion_id", "fila_num"),
        # El detector de "el proveedor corrigió esta fila": se compara el sha de la fila que
        # llega contra el de la que ya está.
        Index("ix_cargas_sha_fila", "proveedor_id", "sha256_fila"),
        # El detector secundario de casi-duplicados (la misma tarjeta en una ventana corta) y la
        # auditoría por tarjeta. A 5 minutos debe reportar 0 pares y a 15 exactamente 1: la
        # ráfaga legítima del T203, que MARCA pero no bloquea.
        Index("ix_cargas_tarjeta_momento", "tarjeta_id", "momento_ref",
              postgresql_where=text("tarjeta_id IS NOT NULL")),
        # Re-resolver la cuarentena por económico cuando se apliquen las propuestas de alta del
        # catálogo, sin barrer la tabla entera.
        Index("ix_cargas_eco_norm", "eco_norm"),
        # El gancho de E4 hacia la consolidada A-1971351. Parcial porque OXXO lo deja NULL entero.
        Index("ix_cargas_factura", "factura_txt",
              postgresql_where=text("factura_txt IS NOT NULL")),
        # Los índices de una sola columna los declara la propia columna con index=True:
        # `momento_local` ("qué pasó el 14 de julio" sin pasar por un activo y sin depender de la
        # suposición de zona), `estado_resolucion`, `proveedor_id`, `importacion_id` y `empleado_id`.
        # SIN índice sobre `producto_norm` (6 valores distintos en 639 filas: inútil),
        # `tarjeta_norm` (la traducción texto→id va contra tarjetas_combustible.numero_norm),
        # `empleado_txt` (lo cubre `empleado_id`) ni `folio_qr_txt`.
        # Y SIN columna alguna para Kms, Kms/Lt, Cliente, Grupo, Centro de Costos, Departmento,
        # No.Operador, Referencia, Desc ni Comentario: viven en `fila_cruda`, inalcanzables para
        # un SUM accidental.
    )


class AsientoConsumo(Base):
    """E3 · EL LIBRO MAYOR DEL COMBUSTIBLE: un litro entró a UN activo, de UN tipo, UNA vez.

    Es el hecho contable único sobre el que E4 calculará el rendimiento. Hoy se alimenta de
    `cargas_proveedor`; mañana también de `ordenes_despacho`, y esas dos fuentes pueden
    describir LA MISMA recarga física. Por eso el diseño impide el doble conteo DESDE EL
    PRIMER DÍA, aunque hoy no haya con qué emparejar.

    ES UNA PROYECCIÓN DERIVADA, NO UNA VERDAD NUEVA. Cada columna se recalcula desde la línea
    que la produjo, así que se regenera con UPSERT sobre la llave de origen, JAMÁS con
    DELETE+INSERT. Ninguna verdad nace aquí: aquí solo se pone en UNA sola forma sumable lo
    que hoy vive en dos vocabularios distintos.

    DOS LLAVES, DOS PELIGROS DISTINTOS, Y LA BASE IMPIDE LOS DOS:
      · La LÍNEA DE ORIGEN manda la idempotencia. `ux_asientos_carga` / `ux_asientos_orden`
        (únicos PARCIALES, creados por `scripts/migrate_e3.py` con text()) garantizan que una
        línea produzca un asiento y solo uno, POR SIEMPRE. Correr el poblador diez veces, o
        reimportar julio entero, no puede crear un segundo asiento: Postgres lo rechaza.
      · El HECHO FÍSICO manda el anti-doble-conteo ENTRE FUENTES. `evento_id` +
        `ux_asientos_evento UNIQUE (evento_id) WHERE evento_id IS NOT NULL AND vigente AND
        contable`: un hecho físico, como mucho UN asiento contable vivo. Hoy `evento_id` es
        NULL en las 639 y en Postgres los NULL no colisionan, así que las 639 cuentan y el
        libro cuadra al centavo. El día que E3 empareje una orden con una carga sella el MISMO
        evento_id en los dos asientos y la base deja de admitir que los dos cuenten.

    LO QUE LA BASE NO PUEDE HACER, DICHO SIN ADORNOS: no puede DESCUBRIR que dos filas
    describen la misma recarga. Eso es el emparejador y ninguna restricción lo suple. Lo que sí
    garantiza es que, una vez descubierto y declarado, contarlas dos veces sea IMPOSIBLE. La
    puerta está cableada y vacía: entra el emparejador, no entra un ALTER.

    ⚠ GOTCHA OBLIGATORIO — Numeric DEVUELVE Decimal, Float DEVUELVE float. `litros` e
    `importe` son Numeric (los ÚNICOS de las 92 columnas numéricas del proyecto), así que
    `asiento.litros` es un `decimal.Decimal` y `carga.litros` es un `float`. SUMARLOS DIRECTO
    LANZA TypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'float'. Con 90
    columnas Float alrededor, la colisión es cuestión de tiempo: convierte a propósito
    (`float(asiento.litros)` para mezclar, `Decimal(str(x))` para acumular exacto) y nunca por
    accidente. La razón de romper la racha está medida: sum(litros) sobre Float da
    118541.02100000005 y sum(litros_txt::numeric) da 118541.021 exacto; el libro es lo que E6
    concilia contra el CFDI, donde el estándar que fijó E2 es 'al centavo', no 'con tolerancia'.

    NO calcula rendimiento ni km/L (eso es E4). NO escribe en `viajes`, `unidades`, `remolques`
    ni `cargas_proveedor`: es una proyección de solo lectura sobre su origen. Solo AGREGA —
    revertir la etapa es `DROP TABLE asientos_consumo; DROP SEQUENCE consumo_evento_seq;` sin
    un solo ALTER que deshacer.

    HOY (medido, no supuesto): 639 asientos · 118,541.021 L · $3,218,987.99, idéntico al
    centavo a sum(litros_txt::numeric)/sum(importe_txt::numeric) de `cargas_proveedor`.
    """

    __tablename__ = "asientos_consumo"

    # ── IDENTIDAD Y PROCEDENCIA ──────────────────────────────────────────────
    # Integer y NO BigInteger: esto es una PROYECCIÓN, una fila por línea de origen, ~639 al
    # mes, y no crece con las correcciones — se reescribe. (El id máximo de `cargas_proveedor`
    # hoy es 24,743.) Una tabla contable de cargos y contracargos sí habría necesitado bigint.
    id: Mapped[int] = mapped_column(primary_key=True)
    # 'proveedor' | 'orden'. String con CHECK y NO un Enum nativo A PROPÓSITO: la prueba 1 de
    # `scripts/verificar_e2.py` cuenta `SELECT count(*) FROM pg_type WHERE typtype='e'` = 6
    # antes y después del DROP de reversión, precisamente para detectar basura. Un Enum nuevo
    # dejaría un tipo huérfano y ensuciaría una reversión que hoy es limpia.
    # NO existe un tercer valor 'ajuste': un asiento sin línea de origen es un litro inventado
    # y rompe la trazabilidad. El vocabulario lo amarra `ck_asientos_origen`.
    origen: Mapped[str] = mapped_column(String(12), nullable=False)
    # EL ASA DE TRAZABILIDAD al renglón exacto del Excel:
    # carga_id → importacion_id + fila_num + fila_cruda + sha256_fila + el archivo_b64
    # archivado. Toda la cadena de custodia de E2 se hereda entera sin duplicar un solo dato
    # verbatim. SIN ondelete (o sea RESTRICT) y NO ON DELETE CASCADE: un libro no debe
    # evaporarse porque alguien borre su origen. El CASCADE además no compraba nada —
    # `tablas_que_apuntan()` de `scripts/revertir_importacion.py` consulta pg_constraint por
    # contype='f' SIN mirar confdeltype y aborta el borrado duro ante CUALQUIER FK ajena, así
    # que el CASCADE solo habría abierto la puerta a perder el libro con un DELETE manual.
    carga_id: Mapped[int | None] = mapped_column(
        ForeignKey("cargas_proveedor.id"), nullable=True)
    # La segunda fuente. HOY SIEMPRE NULL: verificado, 0 órdenes, 0 solicitudes, 0 facturas.
    # Existe desde el primer día porque añadirla después obligaría a rehacer el CHECK de origen
    # y el índice único; mismo criterio con el que `cargas_proveedor` ascendió
    # `contingencia_txt` teniendo un solo valor.
    orden_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordenes_despacho.id"), nullable=True)
    # sha256 de EXACTAMENTE los campos que esta fila copia del origen: (vigente,
    # fuera_de_flota, unidad_id, remolque_id, destino, combustible, litros_txt, importe_txt,
    # momento_local, momento_ref, fecha_operacion). Es el detector de desincronización y lo que
    # hace que una regeneración sin cambios toque CERO filas
    # (`DO UPDATE ... WHERE origen_huella IS DISTINCT FROM EXCLUDED.origen_huella`).
    # Mismo mecanismo y mismo tamaño que `CargaProveedor.sha256_fila`: el proyecto ya sabe leerlo.
    # ⚠ AL CALCULARLA: coalesce(x::text,'') campo por campo, unidos por chr(31). NO se puede
    # usar concat_ws a secas — concat_ws OMITE los argumentos NULL, así que una fila con
    # unidad_id NULL y otra con remolque_id NULL producen la misma cadena corrida y la huella
    # deja de discriminar justo en las 43 filas sin activo.
    # NO incluye `revision` ni `estado_resolucion` porque no se proyectan: la huella cubre
    # exactamente lo que la fila copia, ni más (falsos positivos) ni menos (deriva silenciosa).
    origen_huella: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── A QUÉ ACTIVO ENTRÓ EL LITRO ──────────────────────────────────────────
    # Materializado y no calculado al leer, por la misma razón por la que E2 materializó la
    # resolución: E4 agrupa por activo y periodo y eso tiene que ser barrido de índice, no una
    # función por fila. Y es la ÚNICA forma de que las dos fuentes (cargas y órdenes, con
    # nombres de columna distintos) queden en una sola forma sumable. Verificado: 34 unidades.
    unidad_id: Mapped[int | None] = mapped_column(
        ForeignKey("unidades.id"), nullable=True)
    # El termo del tracto: 124 asientos y 15,198.196 L. No es un caso marginal. 23 remolques.
    remolque_id: Mapped[int | None] = mapped_column(
        ForeignKey("remolques.id"), nullable=True)
    # 'activo' | 'cuarentena' | 'fuera_flota'. EJE INDEPENDIENTE de `destino`, igual que
    # `fuera_de_flota` es independiente de `estado_resolucion` en `cargas_proveedor`. Es la
    # columna que hace posible meter las 4 cargas de gasolina de las camionetas (227.130 L,
    # $5,341.91) SIN que se confundan con las 39 de cuarentena, que SÍ son flota real esperando
    # activo. Responde de una sola forma '¿por qué este litro no tiene activo?': 'todavía no lo
    # sabemos' (cuarentena, 39 filas) o 'no es flota' (fuera_flota, 4 filas).
    # E4 lee `WHERE atribucion = 'activo'`, punto.
    # Las 4 fuera de flota ENTRAN al libro porque sin ellas sumaría 118,313.891 L contra una
    # factura de 118,541.021 L y jamás cuadraría al centavo contra el CFDI, que es para lo que
    # E6 lo va a usar. Un libro que no reproduce el documento no es un libro.
    atribucion: Mapped[str] = mapped_column(String(12), nullable=False)
    # 'motor' | 'termo' | 'indeterminado'. MISMO nombre y MISMO vocabulario que
    # `cargas_proveedor.destino`, no un sinónimo ni un `tipo` renombrado: así 'el libro dice
    # termo y el origen dice motor' es un diff y no una traducción. NULL significa exactamente
    # una cosa: no hay activo — el POR QUÉ lo dice `atribucion`, que es otro eje.
    # Conserva 'indeterminado' porque 191 asientos / 15,629.756 L (13.2% del diésel del mes)
    # son camiones donde el termo comparte económico con el motor: forzar dos valores obligaría
    # a inventar la respuesta.
    destino: Mapped[str | None] = mapped_column(String(14), nullable=True)
    # 'diesel' | 'gasolina' | 'otro'. Medido: 635 diesel (118,313.891 L) / 4 gasolina (227.130 L).
    # COLUMNA PROPIA y no un JOIN: en cuanto alguien dé de alta las camionetas,
    # `atribucion='activo'` por sí sola dejaría entrar gasolina al consumo de diésel y al km/L.
    # Con esta columna, sumar gasolina dentro del diésel exige escribirlo a propósito. Y la
    # segunda fuente no la puede derivar: `SolicitudRecarga` tiene `tipo_recarga`
    # (motor/termo), no combustible.
    combustible: Mapped[str] = mapped_column(String(10), nullable=False)

    # ── LA MAGNITUD (Numeric, no Float — ver el GOTCHA del docstring) ────────
    # SE PUEBLA DESDE `litros_txt` (el texto verbatim), NUNCA desde la columna Float del origen,
    # para no heredar la deriva. Los datos lo permiten sin holgura sospechosa: máximo 3
    # decimales y 650 L. CHECK litros > 0: un litro NULL desaparece de un SUM sin dejar rastro
    # y un litro negativo es una devolución disfrazada de carga.
    litros: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # Mismo argumento: sum(importe) en Float da 3218987.9900000026 y en numeric 3218987.99
    # exacto. Máximo medido $17,550. Nullable por la fuente 'orden' (una orden se despacha antes
    # de que llegue la factura) pero OBLIGATORIO para 'proveedor' vía `ck_asientos_importe_proveedor`.
    # Se puebla desde `importe_txt`.
    importe: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # ── TIEMPO ───────────────────────────────────────────────────────────────
    # El reloj de pared del ticket, copiado sin convertir. Rompe la racha de timestamptz por la
    # MISMA razón que lo hizo E2 y está escrita en su modelo: las 326 fechas de OXXO llegan con
    # tzinfo=None en hora local de la estación, y meterlas en un timestamptz las movería hasta
    # 2.46 h en silencio. Verificado: 0 nulos.
    momento_local: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False)
    # El instante comparable, copiado de `cargas_proveedor.momento_ref`. NOT NULL aunque en el
    # origen sea nullable (verificado: 0 nulos en 639): un asiento sin instante comparable NO se
    # puede emparejar contra una orden, o sea que sería un agujero permanente en la garantía
    # anti-doble-conteo. Copiado y no unido porque mañana la mitad de las filas vendrá de
    # `ordenes_despacho.autorizada_en` y el libro necesita UNA columna de tiempo con UN significado.
    momento_ref: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    # date(momento_local), copiado. La llave del periodo contable y el eje de los rollups de
    # E4/E6. Se deriva de `momento_local` y NO de la zona, que es lo que hace inmune el corte de
    # mes a una corrección de huso. Verificado: 0 nulos.
    fecha_operacion: Mapped[date] = mapped_column(Date, nullable=False)

    # ── VIGENCIA Y ANTI-DOBLE-CONTEO ─────────────────────────────────────────
    # Espejo de `cargas_proveedor.vigente`, el idioma del proyecto. Cuando E2 jubila una línea
    # corregida, la re-proyección pone vigente=false en su asiento y proyecta la revisión nueva
    # como asiento propio: el libro conserva lo que decía antes y nadie borra nada.
    # ⚠ POR ESTO la consulta fuente del proyector NO puede ser `WHERE c.vigente` a secas: si el
    # origen jubila una carga, esa carga sale del SELECT y su asiento nunca se entera, quedándose
    # vigente=true para siempre. Va `WHERE c.vigente OR EXISTS (SELECT 1 FROM asientos_consumo a
    # WHERE a.carga_id = c.id)`. Sin eso, este espejo es decorativo.
    vigente: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # EL IDENTIFICADOR DEL HECHO FÍSICO (la recarga real), servido por la SEQUENCE
    # `consumo_evento_seq`. LA PUERTA DEL EMPAREJADOR. Hoy NULL en 639/639.
    # SIN FK a propósito: no hay tabla de eventos y hoy sería 1:1 con los 639 asientos — 639
    # filas de puro ceremonial y un JOIN en cada consulta de E4 para no aportar un dato. La
    # secuencia es un objeto real de la base (no un número inventado por el código) y cae con el
    # DROP de reversión. Generaliza a N fuentes, no solo a parejas, y no admite cadenas ni ciclos
    # porque un evento es un agrupamiento plano.
    evento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # EL ÚNICO EJE DEL DOBLE CONTEO. Un asiento no contable sigue existiendo, sigue teniendo sus
    # litros y sigue siendo consultable; simplemente no entra al SUM. No se usa para nada más —
    # ni para 'fuera de flota' ni para 'sin activo', que viven en `atribucion` — porque un flag
    # con dos significados hace que '¿por qué no cuenta esto?' deje de tener una respuesta única.
    contable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # POR QUÉ dejó de contar, en el molde de `cargas_proveedor.motivo_estado` y de
    # `importaciones_proveedor.motivo_anulacion`. Obligatorio cuando contable=false, por CHECK:
    # sacar litros del SUM deja de ser una edición y pasa a ser una afirmación firmada en la
    # propia fila ('duplica la orden OD-000412, misma recarga física'). La EVIDENCIA del
    # emparejamiento (método, confianza, quién) irá en la tabla propia de E3: el libro guarda la
    # CONCLUSIÓN, no la prueba, y así un DROP+rebuild se puede volver a sellar desde ahí.
    motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # La añada de la proyección; el poblador la pisa SOLO cuando la huella cambió. Responde 'qué
    # tan fresco está el libro' sin abrir un log, y es lo que permite comprobar que una
    # regeneración sin cambios no tocó una sola fila.
    proyectado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # LOS TRES ÍNDICES ÚNICOS PARCIALES NO SE DECLARAN AQUÍ —un UniqueConstraint no admite
        # predicado—: los crea `scripts/migrate_e3.py` con text(), exactamente como
        # `migrate_e2.py` hizo con `ux_cargas_llave`. Son:
        #   ux_asientos_carga  UNIQUE (carga_id)  WHERE carga_id IS NOT NULL
        #   ux_asientos_orden  UNIQUE (orden_id)  WHERE orden_id IS NOT NULL
        #   ux_asientos_evento UNIQUE (evento_id) WHERE evento_id IS NOT NULL AND vigente AND contable
        # OJO al insertar: con índice parcial hay que REPETIR el predicado en la sentencia
        # (`ON CONFLICT (carga_id) WHERE carga_id IS NOT NULL DO UPDATE`) o Postgres responde
        # "no unique or exclusion constraint matching the ON CONFLICT specification".
        # Y OJO al predicado de `ux_asientos_carga`: NO lleva `AND vigente`, a diferencia de
        # `ux_cargas_llave`, y la diferencia es deliberada. La identidad del asiento es el ID de
        # la línea (que nunca se reutiliza), no la llave natural del archivo (que sí se libera al
        # anular). Con `vigente` en el predicado, jubilar un asiento liberaría su carga_id y la
        # siguiente regeneración insertaría un SEGUNDO asiento para la misma línea: exactamente
        # el doble conteo que hay que impedir.

        # EL XOR DE PROCEDENCIA. Todo asiento tiene exactamente UNA línea de origen: ni cero
        # (litro inventado, sin rastro al Excel) ni dos (litro con dos padres). De paso amarra el
        # vocabulario de `origen` sin necesidad de un Enum nativo.
        CheckConstraint(
            "(origen = 'proveedor' AND carga_id IS NOT NULL AND orden_id IS NULL) OR "
            "(origen = 'orden' AND orden_id IS NOT NULL AND carga_id IS NULL)",
            name="ck_asientos_origen",
        ),
        # UN SOLO CHECK que amarra las TRES cosas de golpe: (a) un litro atribuido cae en
        # EXACTAMENTE un activo —el `<>` entre dos IS NULL es un XOR real, no un "al menos uno"—,
        # (b) no se puede declarar el TIPO de tanque de un litro sin decir de quién es, y (c) un
        # litro no atribuible no puede arrastrar activo por descuido. Amarra también el
        # vocabulario de `atribucion`.
        CheckConstraint(
            "(atribucion = 'activo' AND destino IS NOT NULL AND "
            " ((unidad_id IS NULL) <> (remolque_id IS NULL))) OR "
            "(atribucion IN ('cuarentena','fuera_flota') AND destino IS NULL AND "
            " unidad_id IS NULL AND remolque_id IS NULL)",
            name="ck_asientos_atribucion",
        ),
        CheckConstraint(
            "destino IS NULL OR destino IN ('motor','termo','indeterminado')",
            name="ck_asientos_destino_vocab",
        ),
        # UN REMOLQUE NO TIENE MOTOR. Verdad física permanente, medida 124/124 y 0 contraejemplos.
        # NO se pone la recíproca: destino='termo' CON unidad_id es LEGAL desde el primer día
        # (hoy 0 filas así) porque un camión refrigerado carga su termo bajo el mismo económico, y
        # `SolicitudRecarga.tipo_recarga` ya distingue motor/termo para ese caso.
        CheckConstraint(
            "remolque_id IS NULL OR destino = 'termo'",
            name="ck_asientos_remolque_termo",
        ),
        CheckConstraint(
            "combustible IN ('diesel','gasolina','otro')",
            name="ck_asientos_combustible",
        ),
        # Un litro NULL desaparece de un SUM sin dejar rastro y un litro negativo es una
        # devolución disfrazada de carga. Esa clase de error no mueve un total —y por eso tiene
        # que ser imposible, no improbable.
        CheckConstraint("litros > 0", name="ck_asientos_litros"),
        CheckConstraint("importe IS NULL OR importe >= 0", name="ck_asientos_importe"),
        # El dinero del proveedor no se pierde. Medido: 0 nulos en 639.
        CheckConstraint(
            "origen <> 'proveedor' OR importe IS NOT NULL",
            name="ck_asientos_importe_proveedor",
        ),
        # NADIE DESCUENTA UN LITRO SIN NOMBRAR EL HECHO FÍSICO AL QUE PERTENECE. No existe la
        # categoría 'no cuenta porque sí'.
        CheckConstraint("contable OR evento_id IS NOT NULL",
                        name="ck_asientos_no_contable_con_evento"),
        # Y no lo descuenta sin decir por qué, en la propia fila. El proyecto no acepta un flag
        # sin motivo (`cargas_proveedor.motivo_estado`, `importaciones_proveedor.motivo_anulacion`).
        CheckConstraint("contable OR motivo IS NOT NULL", name="ck_asientos_motivo"),

        # El rollup km/L de E4 y el barrido de candidatos de la ventana de ±6 h del emparejador.
        # Espeja `ix_cargas_unidad_momento` un nivel más arriba. `momento_ref` y no
        # `fecha_operacion` porque una ventana de ±6 h cruza la medianoche.
        Index("ix_asientos_unidad_momento", "unidad_id", "momento_ref",
              postgresql_where=text("unidad_id IS NOT NULL AND vigente AND contable")),
        # Lo mismo para termo: 23 remolques y 15,198.196 L.
        Index("ix_asientos_remolque_momento", "remolque_id", "momento_ref",
              postgresql_where=text("remolque_id IS NOT NULL AND vigente AND contable")),
        # El corte mensual y el reparto motor/termo/indeterminado: el reporte más frecuente que
        # va a existir.
        Index("ix_asientos_fecha_destino", "fecha_operacion", "destino",
              postgresql_where=text("vigente AND contable")),
        # La bandeja de 'litros que todavía no se pueden atribuir' (43 filas hoy) como cola de
        # trabajo, nunca un seq scan. Sobre la columna `atribucion` y no sobre una expresión de
        # NULLs, que es lo que permite distinguir cuarentena de fuera_flota sin un CASE.
        Index("ix_asientos_pendientes", "atribucion", "fecha_operacion",
              postgresql_where=text("vigente AND atribucion <> 'activo'")),
        # Ver los DOS lados de una pareja cuando el emparejador exista: el único de arriba solo
        # ve el lado contable.
        Index("ix_asientos_evento_par", "evento_id",
              postgresql_where=text("evento_id IS NOT NULL")),
        # SIN índice sobre `origen` (2 valores), `origen_huella` (se llega por carga_id, que ya
        # es único), `combustible` (2 valores), `vigente` ni `contable` (van como predicado de los
        # parciales de arriba), ni sobre `importe`.
    )
