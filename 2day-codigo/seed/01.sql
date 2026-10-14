SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET row_security = off;
SET search_path = public;

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: estadoanomalia; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.estadoanomalia AS ENUM (
    'PENDIENTE',
    'CONFIRMADA',
    'RECHAZADA'
);


--
-- Name: estadoasignacion; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.estadoasignacion AS ENUM (
    'ACTIVA',
    'FINALIZADA',
    'CANCELADA'
);


--
-- Name: estadosolicitud; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.estadosolicitud AS ENUM (
    'BORRADOR',
    'ENVIADA',
    'EN_VALIDACION',
    'DEVUELTA',
    'RECHAZADA',
    'AUTORIZADA',
    'DESPACHADA',
    'FACTURADA',
    'CONCILIADA',
    'EN_DISCREPANCIA',
    'ANULADA'
);


--
-- Name: procedencia; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.procedencia AS ENUM (
    'IA_ACEPTADA',
    'IA_CORREGIDA',
    'MANUAL',
    'ESCANEADA'
);


--
-- Name: tipoconfig; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.tipoconfig AS ENUM (
    'SENCILLO',
    'FULL',
    'THORTON'
);


--
-- Name: tipounidad; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.tipounidad AS ENUM (
    'TRACTO',
    'CAMION'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alias_eco; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alias_eco (
    id integer NOT NULL,
    texto_norm character varying(24) NOT NULL,
    unidad_id integer,
    remolque_id integer,
    origen character varying(12) NOT NULL,
    confirmado boolean NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alias_eco_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alias_eco_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alias_eco_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alias_eco_id_seq OWNED BY public.alias_eco.id;


--
-- Name: analisis_reportes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analisis_reportes (
    id integer NOT NULL,
    texto text NOT NULL,
    resumen jsonb,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: analisis_reportes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analisis_reportes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analisis_reportes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analisis_reportes_id_seq OWNED BY public.analisis_reportes.id;


--
-- Name: anomalias; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.anomalias (
    id integer NOT NULL,
    viaje_id integer NOT NULL,
    tipo character varying(60) NOT NULL,
    descripcion text NOT NULL,
    estado public.estadoanomalia NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    resuelto_en timestamp with time zone,
    resuelto_por_id integer
);


--
-- Name: anomalias_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.anomalias_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: anomalias_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.anomalias_id_seq OWNED BY public.anomalias.id;


--
-- Name: asientos_consumo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asientos_consumo (
    id integer NOT NULL,
    origen character varying(12) NOT NULL,
    carga_id integer,
    orden_id integer,
    origen_huella character varying(64) NOT NULL,
    unidad_id integer,
    remolque_id integer,
    atribucion character varying(12) NOT NULL,
    destino character varying(14),
    combustible character varying(10) NOT NULL,
    litros numeric(12,3) NOT NULL,
    importe numeric(14,2),
    momento_local timestamp without time zone NOT NULL,
    momento_ref timestamp with time zone NOT NULL,
    fecha_operacion date NOT NULL,
    vigente boolean NOT NULL,
    evento_id integer,
    contable boolean NOT NULL,
    motivo character varying(200),
    proyectado_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_asientos_atribucion CHECK (((((atribucion)::text = 'activo'::text) AND (destino IS NOT NULL) AND ((unidad_id IS NULL) <> (remolque_id IS NULL))) OR (((atribucion)::text = ANY (ARRAY[('cuarentena'::character varying)::text, ('fuera_flota'::character varying)::text])) AND (destino IS NULL) AND (unidad_id IS NULL) AND (remolque_id IS NULL)))),
    CONSTRAINT ck_asientos_combustible CHECK (((combustible)::text = ANY (ARRAY[('diesel'::character varying)::text, ('gasolina'::character varying)::text, ('otro'::character varying)::text]))),
    CONSTRAINT ck_asientos_destino_vocab CHECK (((destino IS NULL) OR ((destino)::text = ANY (ARRAY[('motor'::character varying)::text, ('termo'::character varying)::text, ('indeterminado'::character varying)::text])))),
    CONSTRAINT ck_asientos_importe CHECK (((importe IS NULL) OR (importe >= (0)::numeric))),
    CONSTRAINT ck_asientos_importe_proveedor CHECK ((((origen)::text <> 'proveedor'::text) OR (importe IS NOT NULL))),
    CONSTRAINT ck_asientos_litros CHECK ((litros > (0)::numeric)),
    CONSTRAINT ck_asientos_motivo CHECK ((contable OR (motivo IS NOT NULL))),
    CONSTRAINT ck_asientos_no_contable_con_evento CHECK ((contable OR (evento_id IS NOT NULL))),
    CONSTRAINT ck_asientos_origen CHECK (((((origen)::text = 'proveedor'::text) AND (carga_id IS NOT NULL) AND (orden_id IS NULL)) OR (((origen)::text = 'orden'::text) AND (orden_id IS NOT NULL) AND (carga_id IS NULL)))),
    CONSTRAINT ck_asientos_remolque_termo CHECK (((remolque_id IS NULL) OR ((destino)::text = 'termo'::text)))
);


--
-- Name: asientos_consumo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asientos_consumo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asientos_consumo_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asientos_consumo_id_seq OWNED BY public.asientos_consumo.id;


--
-- Name: asignaciones_viaje; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asignaciones_viaje (
    id integer NOT NULL,
    operador_id integer NOT NULL,
    unidad_id integer NOT NULL,
    destino character varying(160) NOT NULL,
    origen character varying(160),
    nota character varying(400),
    estado public.estadoasignacion NOT NULL,
    creada_por_id integer,
    creada_en timestamp with time zone DEFAULT now() NOT NULL,
    finalizada_en timestamp with time zone,
    remolque_ids jsonb,
    origen_lat double precision,
    origen_lng double precision,
    destino_lat double precision,
    destino_lng double precision,
    remolque_operadores jsonb,
    km_destino double precision,
    km_estimado double precision,
    km_modificado boolean DEFAULT false NOT NULL,
    km_modificado_por_id integer,
    km_modificado_en timestamp with time zone,
    es_retorno boolean DEFAULT false NOT NULL,
    finalizada_por_id integer,
    visto_en timestamp with time zone
);


--
-- Name: asignaciones_viaje_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asignaciones_viaje_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asignaciones_viaje_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asignaciones_viaje_id_seq OWNED BY public.asignaciones_viaje.id;


--
-- Name: auditoria_thermo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auditoria_thermo (
    id integer NOT NULL,
    unidad_id integer NOT NULL,
    operador_id integer,
    remolque character varying(20),
    mes character varying(20),
    kilometros double precision,
    lts_scaner double precision,
    rto double precision,
    dif double precision,
    lts_real double precision,
    rto_real double precision,
    pct double precision,
    hrs_inic double precision,
    hrs_fin double precision,
    horas_trab double precision,
    litros_thermo double precision,
    rto_thermo double precision,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: auditoria_thermo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.auditoria_thermo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: auditoria_thermo_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.auditoria_thermo_id_seq OWNED BY public.auditoria_thermo.id;


--
-- Name: cargas_proveedor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cargas_proveedor (
    id integer NOT NULL,
    proveedor_id integer NOT NULL,
    importacion_id integer NOT NULL,
    fila_num integer NOT NULL,
    fila_cruda jsonb NOT NULL,
    sha256_fila character varying(64) NOT NULL,
    revision integer NOT NULL,
    vigente boolean NOT NULL,
    sustituye_a_id integer,
    estado_revision character varying(12),
    revisada_por_id integer,
    revisada_en timestamp with time zone,
    estacion_txt character varying(24) NOT NULL,
    folio_txt character varying(32) NOT NULL,
    estacion_id integer,
    estacion_nombre_txt text,
    bomba_txt character varying(8),
    fecha_txt character varying(40),
    momento_local timestamp without time zone NOT NULL,
    momento_ref timestamp with time zone,
    zona_aplicada character varying(40),
    fecha_operacion date,
    momento_facturacion timestamp without time zone,
    desfase_facturacion_min double precision,
    tarjeta_txt text,
    tarjeta_norm character varying(48),
    tarjeta_id integer,
    eco_txt text,
    eco_norm character varying(24),
    placa_txt text,
    placa_norm character varying(24),
    vin_txt character varying(24),
    descripcion_txt text,
    empleado_txt character varying(24),
    empleado_id integer,
    conductor_txt text,
    litros_txt character varying(24),
    litros double precision,
    importe_txt character varying(24),
    importe double precision,
    precio_txt character varying(24),
    precio double precision,
    subtotal_txt character varying(24),
    iva_txt character varying(24),
    ieps_txt character varying(24),
    subtotal double precision,
    iva double precision,
    ieps double precision,
    producto_txt character varying(60),
    producto_norm character varying(60),
    combustible character varying(10),
    factura_txt character varying(24),
    folio_qr_txt character varying(24),
    contingencia_txt character varying(24),
    unidad_id integer,
    remolque_id integer,
    destino character varying(14),
    estado_resolucion character varying(12) NOT NULL,
    resuelto_via character varying(10),
    resuelto_confianza character varying(10),
    fuera_de_flota boolean NOT NULL,
    motivo_estado character varying(160),
    discrepancias jsonb,
    resuelto_en timestamp with time zone,
    creada_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cargas_proveedor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cargas_proveedor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cargas_proveedor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cargas_proveedor_id_seq OWNED BY public.cargas_proveedor.id;


--
-- Name: consumo_evento_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.consumo_evento_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: empleados_proveedor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.empleados_proveedor (
    id integer NOT NULL,
    proveedor_id integer NOT NULL,
    numero character varying(24) NOT NULL,
    nombre_proveedor text,
    operador_id integer,
    vinculo_estado character varying(12) NOT NULL,
    vinculado_por_id integer,
    vinculado_en timestamp with time zone,
    n_cargas integer NOT NULL,
    litros double precision NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: empleados_proveedor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.empleados_proveedor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: empleados_proveedor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.empleados_proveedor_id_seq OWNED BY public.empleados_proveedor.id;


--
-- Name: escaneos_motor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.escaneos_motor (
    id integer NOT NULL,
    unidad_id integer NOT NULL,
    archivo character varying(200) NOT NULL,
    formato character varying(10) NOT NULL,
    motor character varying(40),
    serie_motor character varying(40),
    periodo_inicio date,
    periodo_fin date NOT NULL,
    odometro_total double precision,
    lts_total double precision,
    lts_ralenti_total double precision,
    km double precision NOT NULL,
    litros double precision NOT NULL,
    rendimiento double precision,
    tiempo character varying(20),
    lts_ralenti double precision,
    pct_ralenti double precision,
    tiempo_ralenti character varying(20),
    vel_max double precision,
    vel_prom double precision,
    rpm_prom double precision,
    rpm_max double precision,
    carga_prom double precision,
    km_crucero double precision,
    km_top_gear double precision,
    frenadas double precision,
    paradas_panico double precision,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    pct_crucero double precision,
    pct_top_gear double precision,
    fuera_marcha double precision,
    analisis text,
    pdf character varying(120),
    pdf_en timestamp with time zone
);


--
-- Name: escaneos_motor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.escaneos_motor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: escaneos_motor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.escaneos_motor_id_seq OWNED BY public.escaneos_motor.id;


--
-- Name: estaciones_proveedor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.estaciones_proveedor (
    id integer NOT NULL,
    proveedor_id integer NOT NULL,
    codigo character varying(24) NOT NULL,
    nombre text,
    zona_horaria character varying(40),
    desfase_mediano_min double precision,
    n_cargas integer NOT NULL,
    primera_carga timestamp without time zone,
    ultima_carga timestamp without time zone,
    creada_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: estaciones_proveedor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.estaciones_proveedor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: estaciones_proveedor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.estaciones_proveedor_id_seq OWNED BY public.estaciones_proveedor.id;


--
-- Name: etiquetas_activo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.etiquetas_activo (
    id integer NOT NULL,
    codigo character varying(64) NOT NULL,
    tipo character varying(16) NOT NULL,
    unidad_id integer,
    remolque_id integer,
    estado character varying(12) NOT NULL,
    uso character varying(10),
    eco_texto text,
    nota text,
    origen character varying(12) NOT NULL,
    creada_en timestamp with time zone DEFAULT now() NOT NULL,
    actualizada_en timestamp with time zone
);


--
-- Name: etiquetas_activo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.etiquetas_activo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: etiquetas_activo_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.etiquetas_activo_id_seq OWNED BY public.etiquetas_activo.id;


--
-- Name: eventos_whatsapp; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eventos_whatsapp (
    id integer NOT NULL,
    message_id character varying(120) NOT NULL,
    remote_jid character varying(120) NOT NULL,
    participante character varying(120),
    tipo_mensaje character varying(60) NOT NULL,
    texto text,
    media_path character varying(300),
    payload jsonb NOT NULL,
    recibido_en timestamp with time zone DEFAULT now() NOT NULL,
    estado_proceso character varying(12) DEFAULT 'pendiente'::character varying,
    intentos integer DEFAULT 0,
    error text,
    push_name character varying(120),
    responde_a character varying(120)
);


--
-- Name: eventos_whatsapp_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eventos_whatsapp_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eventos_whatsapp_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eventos_whatsapp_id_seq OWNED BY public.eventos_whatsapp.id;


--
-- Name: evidencias_recarga; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidencias_recarga (
    id integer NOT NULL,
    solicitud_id integer NOT NULL,
    tipo character varying(20) NOT NULL,
    foto_path character varying(300),
    valor_ia double precision,
    valor_final double precision,
    procedencia public.procedencia NOT NULL,
    confianza character varying(10),
    creada_en timestamp with time zone DEFAULT now() NOT NULL,
    etiqueta character varying(60),
    esperado character varying(40),
    texto_ia character varying(60),
    coincide boolean,
    sospechosa boolean DEFAULT false NOT NULL,
    sospecha_motivo text
);


--
-- Name: evidencias_recarga_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evidencias_recarga_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidencias_recarga_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evidencias_recarga_id_seq OWNED BY public.evidencias_recarga.id;


--
-- Name: facturas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facturas (
    id integer NOT NULL,
    uuid_cfdi character varying(40),
    rfc_emisor character varying(20),
    nombre_emisor character varying(200),
    total double precision,
    subtotal double precision,
    moneda character varying(10),
    litros double precision,
    fecha character varying(40),
    xml_path character varying(300),
    subida_por_id integer,
    subida_en timestamp with time zone DEFAULT now() NOT NULL,
    conciliada boolean NOT NULL
);


--
-- Name: facturas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.facturas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: facturas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.facturas_id_seq OWNED BY public.facturas.id;


--
-- Name: folio_orden_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.folio_orden_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: importaciones_etiquetas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.importaciones_etiquetas (
    id integer NOT NULL,
    archivo character varying(300) NOT NULL,
    sha256 character varying(64) NOT NULL,
    n_filas integer NOT NULL,
    n_vinculadas integer NOT NULL,
    n_conflicto integer NOT NULL,
    n_sin_activo integer NOT NULL,
    importado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: importaciones_etiquetas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.importaciones_etiquetas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: importaciones_etiquetas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.importaciones_etiquetas_id_seq OWNED BY public.importaciones_etiquetas.id;


--
-- Name: importaciones_placas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.importaciones_placas (
    id integer NOT NULL,
    archivo character varying(300) NOT NULL,
    sha256 character varying(64) NOT NULL,
    importado_en timestamp with time zone DEFAULT now() NOT NULL,
    por_id integer,
    n_unidades integer NOT NULL,
    n_remolques integer NOT NULL,
    vigente boolean NOT NULL
);


--
-- Name: importaciones_placas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.importaciones_placas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: importaciones_placas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.importaciones_placas_id_seq OWNED BY public.importaciones_placas.id;


--
-- Name: importaciones_proveedor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.importaciones_proveedor (
    id integer NOT NULL,
    proveedor_id integer NOT NULL,
    archivo character varying(300) NOT NULL,
    archivo_bytes integer,
    sha256 character varying(64) NOT NULL,
    archivo_b64 text,
    hoja_leida character varying(60),
    encabezado_leido jsonb,
    impresion_txt character varying(120),
    periodo_desde timestamp without time zone,
    periodo_hasta timestamp without time zone,
    n_filas_leidas integer NOT NULL,
    n_nuevas integer NOT NULL,
    n_repetidas integer NOT NULL,
    n_corregidas integer NOT NULL,
    n_omitidas integer NOT NULL,
    motivos_omision jsonb,
    litros_total double precision NOT NULL,
    importe_total double precision NOT NULL,
    n_resueltas integer NOT NULL,
    n_cuarentena integer NOT NULL,
    n_fuera_flota integer NOT NULL,
    estado character varying(12) NOT NULL,
    importado_en timestamp with time zone DEFAULT now() NOT NULL,
    por_id integer,
    vigente boolean NOT NULL,
    anulada_en timestamp with time zone,
    anulada_por_id integer,
    motivo_anulacion character varying(300),
    nota character varying(400)
);


--
-- Name: importaciones_proveedor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.importaciones_proveedor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: importaciones_proveedor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.importaciones_proveedor_id_seq OWNED BY public.importaciones_proveedor.id;


--
-- Name: operadores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operadores (
    id integer NOT NULL,
    nombre character varying(120) NOT NULL,
    numero character varying(30),
    activo boolean NOT NULL,
    telefono character varying(30),
    licencia character varying(60),
    licencia_vence date,
    ingreso date,
    estatus character varying(20),
    notas text,
    foto text,
    foto_mime character varying(40),
    rol character varying(20),
    provisional boolean DEFAULT false,
    licencia_tipo character varying(30),
    licencia_expedida date,
    curp character varying(18),
    licencia_doc character varying(120),
    licencia_doc_mime character varying(60),
    licencia_doc_nombre character varying(160),
    licencia_doc_en timestamp with time zone
);


--
-- Name: operadores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.operadores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: operadores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.operadores_id_seq OWNED BY public.operadores.id;


--
-- Name: ordenes_despacho; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ordenes_despacho (
    id integer NOT NULL,
    folio character varying(20) NOT NULL,
    solicitud_id integer NOT NULL,
    autorizada_por_id integer,
    autorizada_en timestamp with time zone DEFAULT now() NOT NULL,
    litros_autorizados double precision,
    litros_reales double precision,
    despachada_por_id integer,
    despachada_en timestamp with time zone,
    factura_id integer,
    litros_sugeridos double precision,
    sugerencia_motivo text,
    tope_litros double precision,
    tope_estimado double precision,
    tope_motivo text,
    carga_proveedor_id integer,
    conciliada_en timestamp with time zone
);


--
-- Name: ordenes_despacho_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ordenes_despacho_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ordenes_despacho_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ordenes_despacho_id_seq OWNED BY public.ordenes_despacho.id;


--
-- Name: propuestas_catalogo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.propuestas_catalogo (
    id integer NOT NULL,
    importacion_id integer,
    entidad character varying(10) NOT NULL,
    accion character varying(20) NOT NULL,
    entidad_id integer,
    eco_texto character varying(24),
    placa_texto character varying(24),
    valor_actual character varying(120),
    valor_propuesto character varying(120),
    motivo character varying(300),
    estado character varying(12) NOT NULL,
    aplicada_por_id integer,
    aplicada_en timestamp with time zone,
    creada_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: propuestas_catalogo_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.propuestas_catalogo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: propuestas_catalogo_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.propuestas_catalogo_id_seq OWNED BY public.propuestas_catalogo.id;


--
-- Name: proveedores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proveedores (
    id integer NOT NULL,
    clave character varying(12) NOT NULL,
    nombre character varying(120) NOT NULL,
    rfc character varying(20),
    cliente_texto character varying(120),
    grupo_texto character varying(120),
    regimen_factura character varying(20) NOT NULL,
    zona_horaria character varying(40) NOT NULL,
    formato_fecha character varying(60),
    hoja character varying(60) NOT NULL,
    fila_encabezado integer NOT NULL,
    fila_datos integer NOT NULL,
    n_columnas integer NOT NULL,
    encabezado_esperado jsonb,
    activo boolean NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: proveedores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proveedores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proveedores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proveedores_id_seq OWNED BY public.proveedores.id;


--
-- Name: registro_actividad; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registro_actividad (
    id integer NOT NULL,
    momento timestamp with time zone DEFAULT now() NOT NULL,
    usuario_id integer,
    operador_id integer,
    rol character varying(20),
    accion character varying(40) NOT NULL,
    entidad character varying(30),
    entidad_id integer,
    meta jsonb,
    latencia_seg integer
);


--
-- Name: registro_actividad_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.registro_actividad_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: registro_actividad_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.registro_actividad_id_seq OWNED BY public.registro_actividad.id;


--
-- Name: remolques; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.remolques (
    id integer NOT NULL,
    eco character varying(20) NOT NULL,
    eco_nuevo character varying(20),
    serie character varying(40),
    serie_thermo character varying(40),
    placa character varying(20),
    placas_nuevas character varying(20),
    anio integer,
    marca character varying(40),
    descripcion text,
    es_dolly boolean NOT NULL,
    usa_combustible boolean NOT NULL,
    activo boolean NOT NULL,
    operador_asignado_id integer,
    fuente_catalogo character varying(16),
    verificado_en timestamp with time zone
);


--
-- Name: remolques_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.remolques_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: remolques_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.remolques_id_seq OWNED BY public.remolques.id;


--
-- Name: seguimiento_descuentos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seguimiento_descuentos (
    id integer NOT NULL,
    fecha date,
    unidad_clave character varying(20),
    tipo character varying(20),
    operador_nombre character varying(120),
    lts double precision,
    contesta boolean,
    area character varying(60),
    aplicada_por_id integer,
    aplicada_en timestamp with time zone,
    viaje_id integer
);


--
-- Name: seguimiento_descuentos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seguimiento_descuentos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seguimiento_descuentos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seguimiento_descuentos_id_seq OWNED BY public.seguimiento_descuentos.id;


--
-- Name: solicitudes_recarga; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.solicitudes_recarga (
    id integer NOT NULL,
    estado public.estadosolicitud NOT NULL,
    creada_por_id integer,
    operador_id integer,
    unidad_id integer,
    viaje_id integer,
    litros_solicitados double precision,
    odometro double precision,
    nivel_tanque double precision,
    motivo character varying(400),
    capturada_asistida boolean NOT NULL,
    creada_en timestamp with time zone DEFAULT now() NOT NULL,
    actualizada_en timestamp with time zone DEFAULT now() NOT NULL,
    client_uuid character varying(64),
    tipo_recarga character varying(10) DEFAULT 'motor'::character varying NOT NULL,
    remolque_id integer,
    hermana_id integer,
    asignacion_id integer
);


--
-- Name: solicitudes_recarga_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.solicitudes_recarga_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: solicitudes_recarga_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.solicitudes_recarga_id_seq OWNED BY public.solicitudes_recarga.id;


--
-- Name: tarjetas_combustible; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tarjetas_combustible (
    id integer NOT NULL,
    proveedor_id integer NOT NULL,
    numero_txt text NOT NULL,
    numero_norm character varying(48) NOT NULL,
    unidad_id integer,
    remolque_id integer,
    vinculo_origen character varying(12) NOT NULL,
    vinculo_confirmado boolean NOT NULL,
    vinculado_por_id integer,
    vinculado_en timestamp with time zone,
    eco_observado character varying(24),
    descripcion_proveedor text,
    revisar boolean NOT NULL,
    nota_revision character varying(300),
    n_cargas integer NOT NULL,
    primera_vista timestamp without time zone,
    ultima_vista timestamp without time zone,
    creada_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tarjetas_combustible_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tarjetas_combustible_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tarjetas_combustible_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tarjetas_combustible_id_seq OWNED BY public.tarjetas_combustible.id;


--
-- Name: transiciones_solicitud; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transiciones_solicitud (
    id integer NOT NULL,
    solicitud_id integer NOT NULL,
    estado_anterior character varying(20),
    estado_nuevo character varying(20) NOT NULL,
    por_usuario_id integer,
    momento timestamp with time zone DEFAULT now() NOT NULL,
    cambios jsonb,
    nota character varying(400)
);


--
-- Name: transiciones_solicitud_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transiciones_solicitud_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transiciones_solicitud_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transiciones_solicitud_id_seq OWNED BY public.transiciones_solicitud.id;


--
-- Name: unidades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unidades (
    id integer NOT NULL,
    clave character varying(20) NOT NULL,
    tipo public.tipounidad NOT NULL,
    activo boolean NOT NULL,
    serie character varying(40),
    motor character varying(40),
    motor_cc character varying(15),
    placa character varying(20),
    placas_nuevas character varying(20),
    anio integer,
    marca character varying(40),
    descripcion text,
    operador_asignado character varying(120),
    usa_remolque boolean DEFAULT true NOT NULL,
    rendimiento_objetivo double precision,
    pct_tolerancia double precision,
    operador_asignado_id integer,
    fuente_catalogo character varying(16),
    verificado_en timestamp with time zone
);


--
-- Name: unidades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.unidades_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: unidades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.unidades_id_seq OWNED BY public.unidades.id;


--
-- Name: uso_ia; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uso_ia (
    id integer NOT NULL,
    momento timestamp with time zone DEFAULT now() NOT NULL,
    operacion character varying(30) NOT NULL,
    modelo character varying(60) NOT NULL,
    tokens_entrada integer NOT NULL,
    tokens_salida integer NOT NULL,
    tokens_cache_lectura integer NOT NULL,
    tokens_cache_escritura integer NOT NULL,
    costo_usd double precision NOT NULL,
    evento_id integer,
    ok boolean NOT NULL,
    error character varying(300)
);


--
-- Name: uso_ia_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.uso_ia_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: uso_ia_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.uso_ia_id_seq OWNED BY public.uso_ia.id;


--
-- Name: usuarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usuarios (
    id integer NOT NULL,
    username character varying(60) NOT NULL,
    password_hash character varying(255) NOT NULL,
    nombre character varying(120),
    activo boolean NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    rol character varying(20) DEFAULT 'admin'::character varying NOT NULL,
    operador_id integer,
    foto text,
    foto_mime character varying(40),
    telefono character varying(30),
    prefs jsonb,
    ultimo_acceso timestamp with time zone
);


--
-- Name: usuarios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.usuarios_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: usuarios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.usuarios_id_seq OWNED BY public.usuarios.id;


--
-- Name: viajes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.viajes (
    id integer NOT NULL,
    fecha date NOT NULL,
    unidad_id integer NOT NULL,
    tipo_config public.tipoconfig,
    operador_id integer,
    kilometros double precision,
    lts_scaner double precision,
    rto double precision,
    dif double precision,
    lts_real double precision,
    rto_real double precision,
    pct double precision,
    codigo_cv character varying(10),
    descuentos double precision,
    pct_cummins double precision,
    analista character varying(10),
    odometro double precision,
    vel_max double precision,
    rpm double precision,
    ralenti double precision,
    crucero double precision,
    paradas_panico integer,
    num_frenadas integer,
    neutralizacion double precision,
    top_gear double precision,
    km_top_gear double precision,
    pct_top_gear double precision,
    gear_down double precision,
    km_gear_down double precision,
    pct_gear_down double precision,
    cambios_descendentes double precision,
    c_manejo double precision,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    reportado_por character varying(40),
    nivel_tanque double precision,
    remolque_thermo character varying(20),
    horas_termo double precision,
    origen_message_id character varying(120),
    retractado_en timestamp with time zone,
    retractado_motivo character varying(300),
    correcciones text
);


--
-- Name: viajes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.viajes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: viajes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.viajes_id_seq OWNED BY public.viajes.id;


--
-- Name: alias_eco id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alias_eco ALTER COLUMN id SET DEFAULT nextval('public.alias_eco_id_seq'::regclass);


--
-- Name: analisis_reportes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analisis_reportes ALTER COLUMN id SET DEFAULT nextval('public.analisis_reportes_id_seq'::regclass);


--
-- Name: anomalias id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomalias ALTER COLUMN id SET DEFAULT nextval('public.anomalias_id_seq'::regclass);


--
-- Name: asientos_consumo id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asientos_consumo ALTER COLUMN id SET DEFAULT nextval('public.asientos_consumo_id_seq'::regclass);


--
-- Name: asignaciones_viaje id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asignaciones_viaje ALTER COLUMN id SET DEFAULT nextval('public.asignaciones_viaje_id_seq'::regclass);


--
-- Name: auditoria_thermo id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auditoria_thermo ALTER COLUMN id SET DEFAULT nextval('public.auditoria_thermo_id_seq'::regclass);


--
-- Name: cargas_proveedor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cargas_proveedor ALTER COLUMN id SET DEFAULT nextval('public.cargas_proveedor_id_seq'::regclass);


--
-- Name: empleados_proveedor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.empleados_proveedor ALTER COLUMN id SET DEFAULT nextval('public.empleados_proveedor_id_seq'::regclass);


--
-- Name: escaneos_motor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escaneos_motor ALTER COLUMN id SET DEFAULT nextval('public.escaneos_motor_id_seq'::regclass);


--
-- Name: estaciones_proveedor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.estaciones_proveedor ALTER COLUMN id SET DEFAULT nextval('public.estaciones_proveedor_id_seq'::regclass);


--
-- Name: etiquetas_activo id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etiquetas_activo ALTER COLUMN id SET DEFAULT nextval('public.etiquetas_activo_id_seq'::regclass);


--
-- Name: eventos_whatsapp id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eventos_whatsapp ALTER COLUMN id SET DEFAULT nextval('public.eventos_whatsapp_id_seq'::regclass);


--
-- Name: evidencias_recarga id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidencias_recarga ALTER COLUMN id SET DEFAULT nextval('public.evidencias_recarga_id_seq'::regclass);


--
-- Name: facturas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facturas ALTER COLUMN id SET DEFAULT nextval('public.facturas_id_seq'::regclass);


--
-- Name: importaciones_etiquetas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.importaciones_etiquetas ALTER COLUMN id SET DEFAULT nextval('public.importaciones_etiquetas_id_seq'::regclass);


--
-- Name: importaciones_placas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.importaciones_placas ALTER COLUMN id SET DEFAULT nextval('public.importaciones_placas_id_seq'::regclass);


--
-- Name: importaciones_proveedor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.importaciones_proveedor ALTER COLUMN id SET DEFAULT nextval('public.importaciones_proveedor_id_seq'::regclass);


--
-- Name: operadores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operadores ALTER COLUMN id SET DEFAULT nextval('public.operadores_id_seq'::regclass);


--
-- Name: ordenes_despacho id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ordenes_despacho ALTER COLUMN id SET DEFAULT nextval('public.ordenes_despacho_id_seq'::regclass);


--
-- Name: propuestas_catalogo id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.propuestas_catalogo ALTER COLUMN id SET DEFAULT nextval('public.propuestas_catalogo_id_seq'::regclass);


--
-- Name: proveedores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proveedores ALTER COLUMN id SET DEFAULT nextval('public.proveedores_id_seq'::regclass);


--
-- Name: registro_actividad id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registro_actividad ALTER COLUMN id SET DEFAULT nextval('public.registro_actividad_id_seq'::regclass);


--
-- Name: remolques id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.remolques ALTER COLUMN id SET DEFAULT nextval('public.remolques_id_seq'::regclass);


--
-- Name: seguimiento_descuentos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seguimiento_descuentos ALTER COLUMN id SET DEFAULT nextval('public.seguimiento_descuentos_id_seq'::regclass);


--
-- Name: solicitudes_recarga id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.solicitudes_recarga ALTER COLUMN id SET DEFAULT nextval('public.solicitudes_recarga_id_seq'::regclass);


--
-- Name: tarjetas_combustible id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tarjetas_combustible ALTER COLUMN id SET DEFAULT nextval('public.tarjetas_combustible_id_seq'::regclass);


--
-- Name: transiciones_solicitud id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transiciones_solicitud ALTER COLUMN id SET DEFAULT nextval('public.transiciones_solicitud_id_seq'::regclass);


--
-- Name: unidades id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unidades ALTER COLUMN id SET DEFAULT nextval('public.unidades_id_seq'::regclass);


--
-- Name: uso_ia id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uso_ia ALTER COLUMN id SET DEFAULT nextval('public.uso_ia_id_seq'::regclass);


--
-- Name: usuarios id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios ALTER COLUMN id SET DEFAULT nextval('public.usuarios_id_seq'::regclass);


--
-- Name: viajes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viajes ALTER COLUMN id SET DEFAULT nextval('public.viajes_id_seq'::regclass);
INSERT INTO public.alias_eco (id, texto_norm, unidad_id, remolque_id, origen, confirmado, creado_en) VALUES
('1', 'T205', '1', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('2', '69AJ4S', '1', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('3', '33BG7Y', '1', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('5', 'T224', '4', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('6', '20AK9C', '4', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('7', 'T230', '5', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('8', '44AS1E', '5', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('9', '26BG7Y', '5', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('10', 'T142', '6', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('11', '88FA4A', '6', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('12', '35BG2M', '6', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('13', 'T190', '7', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('14', '03AC8N', '7', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('15', 'C078', '12', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('16', '64BB1J', '12', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('17', '34BJ9S', '12', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('18', 'T219', '15', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('19', '15AK9C', '15', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('20', 'C014', '18', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('21', '78AK4P', '18', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('22', 'C012', '21', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('23', '70AK4P', '21', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('24', 'C015', '22', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('25', '73AK4P', '22', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('26', 'C016', '23', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('27', '74AK4P', '23', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('28', 'T145', '25', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('29', '79FA5B', '25', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('30', '33BG2M', '25', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('31', 'C018', '28', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('32', '72AK4P', '28', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('41', 'T301', '55', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('43', 'T211', '2', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('44', '09AK4D', '2', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('45', 'T150', '3', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('46', '87FA4A', '3', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('47', '30BG2M', '3', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('48', 'C003', '8', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('49', '702FE2', '8', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('50', '30BG4M', '8', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('51', 'C007', '9', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('52', '598FE1', '9', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('53', '63BB3J', '9', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('54', 'C019', '10', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('55', '76AK4P', '10', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('56', 'T183', '13', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('57', '43AS1E', '13', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('58', '27BG7Y', '13', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('59', 'T182', '14', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('60', '46AS1E', '14', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('61', 'C008', '17', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('62', '595FE1', '17', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('63', '92BG2P', '17', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('64', 'C077', '19', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('65', '254FE8', '19', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('66', '89BC9L', '19', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('67', 'C020', '20', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('68', '71AK4P', '20', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('69', 'C011', '27', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('70', '599FE1', '27', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('71', '71BB1J', '27', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('72', 'C006', '29', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('73', '596FE1', '29', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('74', '92BC9L', '29', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('75', 'T210', '30', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('76', '73AJ4S', '30', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('77', '30BG7Y', '30', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('78', 'T206', '31', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('79', '77AJ4S', '31', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('80', '32BG7Y', '31', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('81', 'T228', '34', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('82', '71AZ1S', '34', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('83', 'T161', '36', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('84', '80FA5B', '36', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('85', '29BG2M', '36', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('86', 'T203', '45', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('87', '72AJ4S', '45', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('88', '34BG7Y', '45', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('89', 'T181', '46', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('90', '21AD4V', '46', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('91', '38BG6X', '46', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('92', 'T160', '49', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('93', '016FE3', '49', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('94', '12AX8B', '49', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('95', 'T222', '56', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('96', '47AS1E', '56', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('97', 'T141', '59', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('98', '378FD2', '59', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('99', '36BG2M', '59', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('100', 'T212', '60', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('101', '11AK4D', '60', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('102', 'T213', '61', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('103', '10AK4D', '61', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('104', 'T220', '62', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('105', '45AS1E', '62', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('106', 'T144', '53', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('107', '89FA4A', '53', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('108', '34BG2M', '53', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('109', 'C001', '51', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('110', '704FE2', '51', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('111', '32BG4M', '51', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('112', 'T225', '33', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('113', '19AK9C', '33', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('114', 'T223', '37', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('115', '18AK9C', '37', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('116', 'T214', '38', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('117', '22AK9C', '38', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('118', '50BH9N', '38', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('122', 'T208', '40', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('123', '78AJ4S', '40', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('124', '31BG7Y', '40', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('125', 'T227', '41', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('126', '69AV1E', '41', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('127', 'C017', '42', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('128', '77AK4P', '42', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('129', 'T163', '43', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('130', '013FE3', '43', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('131', '28BG2M', '43', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('132', 'T135', '48', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('133', '99AF6E', '48', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('134', 'C002', '52', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('135', '703FE2', '52', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('136', '31BG4M', '52', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('137', 'T140', '58', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('138', '81AF8W', '58', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('139', '28BG7Y', '58', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('140', 'C013', '44', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('141', '75AK4P', '44', NULL, 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('142', '532301', NULL, '1', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('143', '91UP5K', NULL, '1', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('144', '532302', NULL, '2', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('145', '39UP6Z', NULL, '2', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('146', '531701', NULL, '3', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('147', '94UR1E', NULL, '3', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('148', '53113', NULL, '4', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('149', '400917', NULL, '4', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('150', '64UD5B', NULL, '4', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('151', '53114', NULL, '5', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('152', '400918', NULL, '5', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('153', '63UD5B', NULL, '5', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('154', '531107', NULL, '6', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('155', '27UR5J', NULL, '6', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('156', '531306', NULL, '7', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('157', '67UR7V', NULL, '7', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('158', '531215', NULL, '8', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('159', '401215', NULL, '8', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('160', '62UD5B', NULL, '8', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('161', '53131', NULL, '9', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('162', '531802', NULL, '9', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('163', '62UN6V', NULL, '9', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('164', '53133', NULL, '10', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('165', '531804', NULL, '10', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('166', '15YA1C', NULL, '10', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('167', '62UX6K', NULL, '10', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('168', '53134', NULL, '11', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('169', '531805', NULL, '11', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('170', '14YA1C', NULL, '11', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('171', '65UX6K', NULL, '11', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('172', '53136', NULL, '12', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('173', '531807', NULL, '12', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('174', '99UP8K', NULL, '12', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('175', '5300137', NULL, '13', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('176', '531808', NULL, '13', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('177', '454YF5', NULL, '13', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('178', '64UX6K', NULL, '13', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('179', '53139', NULL, '14', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('180', '531810', NULL, '14', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('181', '462YF5', NULL, '14', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('182', '63UX6K', NULL, '14', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('183', '400907', NULL, '15', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('184', '53UD5B', NULL, '15', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('185', '400908', NULL, '16', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('186', '52UD5B', NULL, '16', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('187', '400909', NULL, '17', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('188', '73TZ2M', NULL, '17', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('189', '400910', NULL, '18', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('190', '72TZ2M', NULL, '18', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('191', '401206', NULL, '20', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('192', '61UD5B', NULL, '20', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('193', '530901', NULL, '21', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('194', '400916', NULL, '21', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('195', '85UB8S', NULL, '21', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('196', '400913', NULL, '22', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('197', '67TZ8L', NULL, '22', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('198', '5300121', NULL, '23', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('199', '400914', NULL, '23', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('200', '75TZ2M', NULL, '23', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('201', '5311801', NULL, '24', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('202', '531811', NULL, '24', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('203', '45UD6S', NULL, '24', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('204', '82UX1R', NULL, '24', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('205', '5311802', NULL, '25', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('206', '531812', NULL, '25', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('207', '42UD6S', NULL, '25', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('208', '81UX1R', NULL, '25', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('209', '5311803', NULL, '26', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('210', '531813', NULL, '26', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('211', '43UD6S', NULL, '26', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('212', '80UX1R', NULL, '26', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('213', '5311804', NULL, '27', 'flotilla', 't', '2026-09-01 04:58:13.728007+00');
INSERT INTO public.alias_eco (id, texto_norm, unidad_id, remolque_id, origen, confirmado, creado_en) VALUES
('214', '531814', NULL, '27', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('215', '38UD6S', NULL, '27', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('216', '79UX1R', NULL, '27', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('217', '5311805', NULL, '28', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('218', '531815', NULL, '28', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('219', '36UD6S', NULL, '28', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('220', '78UX1R', NULL, '28', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('221', '5311806', NULL, '29', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('222', '531816', NULL, '29', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('223', '41UD6S', NULL, '29', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('224', '77UX1R', NULL, '29', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('225', '5311807', NULL, '30', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('226', '531817', NULL, '30', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('227', '40UD6S', NULL, '30', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('228', '76UX1R', NULL, '30', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('229', '5311808', NULL, '31', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('230', '531818', NULL, '31', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('231', '44UD6S', NULL, '31', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('232', '75UX1R', NULL, '31', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('233', '5311809', NULL, '32', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('234', '531819', NULL, '32', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('235', '39UD6S', NULL, '32', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('236', '74UX1R', NULL, '32', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('237', '5311810', NULL, '33', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('238', '531820', NULL, '33', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('239', '37UD6S', NULL, '33', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('240', '73UX1R', NULL, '33', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('241', '5311811', NULL, '34', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('242', '531821', NULL, '34', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('243', '85UE1C', NULL, '34', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('244', '5311812', NULL, '35', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('245', '531822', NULL, '35', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('246', '86UE1C', NULL, '35', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('247', '59UX7T', NULL, '35', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('248', '5311813', NULL, '36', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('249', '531823', NULL, '36', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('250', '82UE1C', NULL, '36', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('251', '5311815', NULL, '37', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('252', '531825', NULL, '37', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('253', '88UE1C', NULL, '37', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('254', '60UX7T', NULL, '37', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('255', '5311817', NULL, '38', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('256', '531827', NULL, '38', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('257', '15UE1C', NULL, '38', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('258', '5311818', NULL, '39', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('259', '531828', NULL, '39', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('260', '84UE1C', NULL, '39', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('261', '5311819', NULL, '40', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('262', '531829', NULL, '40', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('263', '80UE1C', NULL, '40', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('264', '5311821', NULL, '41', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('265', '531831', NULL, '41', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('266', '83UE1C', NULL, '41', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('267', '5311822', NULL, '42', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('268', '531832', NULL, '42', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('269', '89UE1C', NULL, '42', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('270', '5311823', NULL, '43', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('271', '531833', NULL, '43', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('272', '14UE1C', NULL, '43', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('273', '5311824', NULL, '44', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('274', '531834', NULL, '44', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('275', '17UE1C', NULL, '44', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('276', '5311825', NULL, '45', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('277', '531835', NULL, '45', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('278', '81UE1C', NULL, '45', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('279', '532304', NULL, '46', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('280', '23UR7V', NULL, '46', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('281', '532305', NULL, '47', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('282', '22UR7V', NULL, '47', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('283', '532306', NULL, '48', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('284', '21UR7V', NULL, '48', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('285', '532319', NULL, '49', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('286', '76UR3T', NULL, '49', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('287', '532320', NULL, '50', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('288', '74UR3T', NULL, '50', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('289', '532321', NULL, '51', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('290', '75UR3T', NULL, '51', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('291', '532307', NULL, '52', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('292', '73UR3T', NULL, '52', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('293', 'D01', NULL, '53', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('294', 'D02', NULL, '54', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('295', 'D04', NULL, '55', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('296', 'D05', NULL, '56', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('297', 'D06', NULL, '57', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('298', 'D07', NULL, '58', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('299', 'D08', NULL, '59', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('300', 'D09', NULL, '60', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('301', '400211', NULL, '19', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('302', '47UD4P', NULL, '19', 'flotilla', 't', '2026-09-01 04:58:13.728007+00'),
('313', 'T147', '32', NULL, 'manual', 't', '2026-09-17 04:57:31.905482+00'),
('314', '32BG2M', '32', NULL, 'manual', 't', '2026-09-17 04:57:31.905482+00'),
('315', 'C004', '1068', NULL, 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('316', '701FE2', '1068', NULL, 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('317', 'C005', '1069', NULL, 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('318', '700FE2', '1069', NULL, 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('319', 'T302', '1070', NULL, 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('320', '400112', NULL, '320', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('321', '14UJ9G', NULL, '320', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('322', '400805', NULL, '321', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('323', '85TZ8L', NULL, '321', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('324', '401001', NULL, '322', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('325', '56UD5B', NULL, '322', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('326', '401002', NULL, '323', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('327', '57UD5B', NULL, '323', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('328', '401003', NULL, '324', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('329', '58UD5B', NULL, '324', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('330', '401004', NULL, '325', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('331', '55UD5B', NULL, '325', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('332', '53117', NULL, '326', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('333', '54UD5B', NULL, '326', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('334', '53118', NULL, '327', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('335', '60UD5B', NULL, '327', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('336', '53119', NULL, '328', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('337', '74TZ2M', NULL, '328', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('338', '531210', NULL, '329', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('339', '5060540', NULL, '329', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('340', '531308', NULL, '330', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('341', '3235358', NULL, '330', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('342', '531309', NULL, '331', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('343', '54UR3E', NULL, '331', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('344', '531380', NULL, '332', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('345', '187C939', NULL, '332', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('346', '531403', NULL, '333', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('347', 'C150466', NULL, '333', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('348', '531404', NULL, '334', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('349', 'C150465', NULL, '334', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('350', '531702', NULL, '335', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('351', '5197059', NULL, '335', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('352', '531705', NULL, '336', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('353', 'C150931', NULL, '336', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('354', '531806', NULL, '337', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('355', '51UN4W', NULL, '337', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('356', '531809', NULL, '338', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('357', '453YF5', NULL, '338', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('358', '531824', NULL, '339', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('359', '13UE1C', NULL, '339', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('360', '531830', NULL, '340', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('361', '87UE1C', NULL, '340', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('363', '532303', NULL, '342', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('364', '24UR7V', NULL, '342', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('365', '532322', NULL, '343', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('366', '5245445', NULL, '343', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('367', '532323', NULL, '344', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('368', '5245446', NULL, '344', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('369', '532324', NULL, '345', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('370', '5245447', NULL, '345', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('371', '532325', NULL, '346', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('372', '5245448', NULL, '346', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('373', '53316', NULL, '347', 'placas', 't', '2026-09-20 08:01:34.25109+00'),
('374', '10UA8A', NULL, '347', 'placas', 't', '2026-09-20 08:01:34.25109+00');
INSERT INTO public.asientos_consumo (id, origen, carga_id, orden_id, origen_huella, unidad_id, remolque_id, atribucion, destino, combustible, litros, importe, momento_local, momento_ref, fecha_operacion, vigente, evento_id, contable, motivo, proyectado_en) VALUES
('44809', 'proveedor', '24530', NULL, '80197966da97ba953e08c0fe37f54ba0f745572fee2b9e3e7e9d951e6ab2c2da', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3240.00', '2026-07-10 23:28:08', '2026-07-11 05:28:08+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44810', 'proveedor', '24531', NULL, '0aae3a3da2d8f77dd07ef1997d755014c174e45b9fa1dde81a62fa4e392b9fff', '36', NULL, 'activo', 'motor', 'diesel', '330.000', '8910.00', '2026-07-10 23:40:29', '2026-07-11 05:40:29+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44811', 'proveedor', '24109', NULL, '0fcc8218aa01a670fda3875d65479da2bd2febf9b7fd75fda1d53e497c4fa5bf', '56', NULL, 'activo', 'motor', 'diesel', '117.000', '3159.00', '2026-07-31 23:09:38', '2026-08-01 05:09:38+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44812', 'proveedor', '24110', NULL, 'e2020588c07830fb1dbbdaf312b0471caeb562269c1bff233b8b5c9dfadb8a38', '56', NULL, 'activo', 'motor', 'diesel', '282.070', '7615.89', '2026-07-31 23:00:00', '2026-08-01 05:00:00+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44813', 'proveedor', '24111', NULL, '1c9e3c54a5000a0937f536c5639df04e5047bdbcf03495a542495e917748d45c', '21', NULL, 'activo', 'indeterminado', 'diesel', '127.730', '3448.82', '2026-07-31 18:16:18', '2026-08-01 00:16:18+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44814', 'proveedor', '24112', NULL, '6909f6f6325fb28a9c406ab82178e08d9be16ec354280cf9cd5beb3f03b44b94', '20', NULL, 'activo', 'indeterminado', 'diesel', '135.020', '3645.41', '2026-07-31 18:11:37', '2026-08-01 00:11:37+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44815', 'proveedor', '24113', NULL, '22826e90223df625fd0b66790cf89bc4b9b4c735b163e6b707261533edb8058a', '21', NULL, 'activo', 'indeterminado', 'diesel', '32.620', '880.66', '2026-07-31 18:09:48', '2026-08-01 00:09:48+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44816', 'proveedor', '24114', NULL, '2bb94ead4a55b9413cdb8508d39327d0990defbee5000675fefa650c8c61bd0c', '20', NULL, 'activo', 'indeterminado', 'diesel', '15.170', '409.46', '2026-07-31 18:04:22', '2026-08-01 00:04:22+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44817', 'proveedor', '24115', NULL, '847b8fa3e829a47a1fc6ed9d0c55e4d612b32fb1dd9b33c05bb49057861ef92a', '44', NULL, 'activo', 'indeterminado', 'diesel', '32.980', '890.46', '2026-07-31 17:24:01', '2026-07-31 23:24:01+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44818', 'proveedor', '24116', NULL, '3e81aa0a38cd3c7e587cf2adf89175da461309f374b73a730d8689cd1c599427', '44', NULL, 'activo', 'indeterminado', 'diesel', '160.860', '4343.17', '2026-07-31 17:17:49', '2026-07-31 23:17:49+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44819', 'proveedor', '24117', NULL, 'c7d816048b914325ce7daa7670fa32cf91baab2b1062789f265bce1e824e3685', NULL, '48', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-31 10:23:00', '2026-07-31 16:23:00+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44820', 'proveedor', '24118', NULL, 'a92fcb53ec4fb4af9c9f9d01979797ecc2e3773cf2612db7e2344957977c8b2a', '3', NULL, 'activo', 'motor', 'diesel', '370.340', '9999.00', '2026-07-31 10:13:00', '2026-07-31 16:13:00+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44821', 'proveedor', '24119', NULL, '7f191255ac8583c4542e6d2785f56ac0beebc9a5dadd175c7a5cdac3522f09da', '3', NULL, 'activo', 'motor', 'diesel', '1.000', '27.00', '2026-07-31 09:57:00', '2026-07-31 15:57:00+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44822', 'proveedor', '24120', NULL, '3fb3cb2aeec3408c916f838a59a156e467d75e82d8d6d36747afe6bcc0d93149', '14', NULL, 'activo', 'motor', 'diesel', '230.000', '6210.00', '2026-07-30 21:54:00', '2026-07-31 03:54:00+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44823', 'proveedor', '24121', NULL, '2fc943349dece72d6087babc8a87e6ace08966d64c5dd1914cd2b7f6de770783', '14', NULL, 'activo', 'motor', 'diesel', '370.000', '9990.00', '2026-07-30 21:46:00', '2026-07-31 03:46:00+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44824', 'proveedor', '24122', NULL, '771cd32188d7ab3fd96131d2de19c5fffcbc2715c8b84f0826114ea4aa4c46ae', '10', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-30 21:30:28', '2026-07-31 03:30:28+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44825', 'proveedor', '24123', NULL, '38156ecf7f4e57bd9707ca7e7a7ca722b1de1d0d52a456555a80bc784e32fe35', '10', NULL, 'activo', 'indeterminado', 'diesel', '150.000', '4050.00', '2026-07-30 21:14:29', '2026-07-31 03:14:29+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44826', 'proveedor', '24124', NULL, 'ee3b303ce97b1f7d431ed1180762b53aca91f28a1e353c93c4dcdf9f9976fca9', NULL, '30', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-30 16:28:31', '2026-07-30 22:28:31+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44827', 'proveedor', '24125', NULL, '63f10384e647d8069b007b466e79e49f6a201f41cedd4ba65a9d9fd3f6c5a968', '5', NULL, 'activo', 'motor', 'diesel', '134.000', '3618.00', '2026-07-30 15:37:51', '2026-07-30 21:37:51+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44828', 'proveedor', '24126', NULL, '5dea7d918b45cbbade34e41d08e775a867e2671128031db45da0f6cfb3c3cc36', '5', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-30 15:30:54', '2026-07-30 21:30:54+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44829', 'proveedor', '24127', NULL, '45ec2e84e6fefac790093cb9d2c29bf0cbb1f1a278b216090caf13245aea6079', '8', NULL, 'activo', 'indeterminado', 'diesel', '32.540', '878.58', '2026-07-30 14:26:50', '2026-07-30 20:26:50+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44830', 'proveedor', '24128', NULL, 'fb4529fdcb58fdf021f07d5f4dd5b9b259053d84db13b2d1bb63df70f357729e', '8', NULL, 'activo', 'indeterminado', 'diesel', '45.340', '1224.21', '2026-07-30 14:21:52', '2026-07-30 20:21:52+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44831', 'proveedor', '24129', NULL, '6a2fcf903660da1b243192f837d6998771265ee1d74f605b9b982c6bfd135a4a', '21', NULL, 'activo', 'indeterminado', 'diesel', '116.810', '3153.92', '2026-07-30 12:00:27', '2026-07-30 18:00:27+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44832', 'proveedor', '24130', NULL, '491f344344fa9cc23f143513852110db0be362eae100949ef35c4166845eab96', '20', NULL, 'activo', 'indeterminado', 'diesel', '185.020', '4995.59', '2026-07-29 23:07:26', '2026-07-30 05:07:26+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44833', 'proveedor', '24131', NULL, '2437f5f9e78ed5568e0d3fc8b8e311a8b247f94a0289f614da31aa3ff11f86ef', '20', NULL, 'activo', 'indeterminado', 'diesel', '75.080', '2027.21', '2026-07-29 23:01:43', '2026-07-30 05:01:43+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44834', 'proveedor', '24132', NULL, '409a79a300af46b0386682bdd9d96d59a4fd615632d1a292fb04da4f214881c3', '44', NULL, 'activo', 'indeterminado', 'diesel', '41.180', '1111.83', '2026-07-29 21:25:50', '2026-07-30 03:25:50+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44835', 'proveedor', '24133', NULL, '986c059b553863bce42f7de7fa0daef719c2d91f7042e52d120bb4bb5a8c2737', '44', NULL, 'activo', 'indeterminado', 'diesel', '200.240', '5406.45', '2026-07-29 21:19:15', '2026-07-30 03:19:15+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44836', 'proveedor', '24134', NULL, 'e00fb2bbd1f84bada3b79fa67f8e50199bda025a7cf034a428b44eba7bd42ff3', '41', NULL, 'activo', 'motor', 'diesel', '364.450', '9840.15', '2026-07-29 17:57:42', '2026-07-29 23:57:42+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44837', 'proveedor', '24135', NULL, '2c4e0151944e104bdde69685bcda72e075dbe1b7c2849d1405aeef7dc92508dc', '41', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-29 17:48:02', '2026-07-29 23:48:02+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44838', 'proveedor', '24136', NULL, 'd1b156e00f3be5fbeafc6f905d9a5b40a3aa3ab48394d61a3c54d06c33554e9d', '55', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-28 23:42:09', '2026-07-29 05:42:09+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44839', 'proveedor', '24137', NULL, '6fb6f3a08c30eac401ccfd182164d9a93b6bb5d518708013ab1e5851828970e8', '36', NULL, 'activo', 'motor', 'diesel', '259.000', '6993.00', '2026-07-28 21:57:00', '2026-07-29 03:57:00+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44840', 'proveedor', '24138', NULL, 'b5441b6a3552f380c059982c0ee88a5b099a221f564afb2e9945407f922d7c73', '36', NULL, 'activo', 'motor', 'diesel', '370.340', '9999.00', '2026-07-28 21:45:00', '2026-07-29 03:45:00+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44841', 'proveedor', '24139', NULL, '2fdc2fd074b844debbe18fcab861e53ebebd651baf01daee9e6fb2ca69ac8825', NULL, '45', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-28 20:32:57', '2026-07-29 02:32:57+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44842', 'proveedor', '24140', NULL, '75066fb28bd867bf3ec8b2b409788d22388c901e009c4693d6c1afa7202c5bad', '25', NULL, 'activo', 'motor', 'diesel', '380.000', '10260.00', '2026-07-28 20:20:46', '2026-07-29 02:20:46+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44843', 'proveedor', '24141', NULL, 'a9e5a67a8bb981cae24602a4bc05f127597b8aeff38980450152b0f8857ac2bd', '21', NULL, 'activo', 'indeterminado', 'diesel', '7.280', '196.67', '2026-07-28 20:16:27', '2026-07-29 02:16:27+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44844', 'proveedor', '24142', NULL, '60c11bde6d69e139acd4bbb5104d1644f5a347198977dc2ab9abd06e83232c16', '10', NULL, 'activo', 'indeterminado', 'diesel', '23.470', '633.66', '2026-07-28 19:51:53', '2026-07-29 01:51:53+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44845', 'proveedor', '24143', NULL, '5090333cf4aef5782bfe866b96742a1b4f37fcb494af3df894043a806338704b', '10', NULL, 'activo', 'indeterminado', 'diesel', '99.480', '2685.88', '2026-07-28 19:44:21', '2026-07-29 01:44:21+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44846', 'proveedor', '24144', NULL, '1c47b7d220325371e3a0bc095d8a43138b1adf2d4509926b3bace8f0bbd2490d', '8', NULL, 'activo', 'indeterminado', 'diesel', '24.340', '657.13', '2026-07-28 13:30:58', '2026-07-28 19:30:58+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44847', 'proveedor', '24145', NULL, '2a446dc5b74e6fbfe2e1b9531c94201f3f985f69c99c50def019107842d9ed46', '8', NULL, 'activo', 'indeterminado', 'diesel', '52.240', '1410.48', '2026-07-28 13:19:27', '2026-07-28 19:19:27+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44848', 'proveedor', '24146', NULL, '8fa656e08b77279ea2151f7339f3aecc0bdf31e9b22323242166b1d0993072ca', '34', NULL, 'activo', 'motor', 'diesel', '330.060', '9073.35', '2026-07-28 08:35:38', '2026-07-28 14:35:38+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44849', 'proveedor', '24147', NULL, 'a9fa8dd1bc088915ced035fb0c0e8d662e2d23f925658298815d5f418565e621', '20', NULL, 'activo', 'indeterminado', 'diesel', '115.130', '3108.38', '2026-07-27 16:51:22', '2026-07-27 22:51:22+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44850', 'proveedor', '24148', NULL, 'e064e52f8b34eb9986c1a370e1e43859ccd24f45aef5909b2d5d4174b691fa6b', '20', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-27 16:42:48', '2026-07-27 22:42:48+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44851', 'proveedor', '24149', NULL, 'e26d01c05956d416b2f9d7224fd1bc151e38d750447799511c3c7863656d8c03', '52', NULL, 'activo', 'indeterminado', 'diesel', '26.810', '723.90', '2026-07-27 16:16:02', '2026-07-27 22:16:02+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44852', 'proveedor', '24150', NULL, '656c0f0e1fb96f323c7030c4f790dd3109bba874e7aa4ded71afd75b8e9539a9', '52', NULL, 'activo', 'indeterminado', 'diesel', '76.970', '2078.24', '2026-07-27 16:05:11', '2026-07-27 22:05:11+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44853', 'proveedor', '24151', NULL, '893189623128b08322a003cd196b29f831f831d7f68043a556f698e58b388606', '41', NULL, 'activo', 'motor', 'diesel', '480.000', '13195.20', '2026-07-27 02:41:00', '2026-07-27 08:41:00+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44854', 'proveedor', '24152', NULL, '5467215df262f71430cd04ce5ce3aa4a0798c1db2f286edd293026a0d8fdbc56', '10', NULL, 'activo', 'indeterminado', 'diesel', '360.000', '9720.00', '2026-07-26 22:52:42', '2026-07-27 04:52:42+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44855', 'proveedor', '24153', NULL, 'cbb95de608cafb579bcea5507f24daa493b68febbe6aaf2d75393e6f85dfd3c0', '10', NULL, 'activo', 'indeterminado', 'diesel', '20.000', '540.00', '2026-07-26 22:45:14', '2026-07-27 04:45:14+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44856', 'proveedor', '24154', NULL, '90a1cf0855b0ec21a84e77ffc1ee8d24ed9e30c3ad7b44b24db60f1453994938', NULL, '42', 'activo', 'termo', 'diesel', '141.330', '3815.78', '2026-07-26 20:59:00', '2026-07-27 02:59:00+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44857', 'proveedor', '24155', NULL, '39e2fe0b6cc65e489f63febccbc812ce1b0326780f53ddb43e8956800de5c39a', '34', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-26 20:46:00', '2026-07-27 02:46:00+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44858', 'proveedor', '24156', NULL, '2f1619e59ade103722527f79f73e6303c4c877380f89cd5e2345665b49c9e886', '8', NULL, 'activo', 'indeterminado', 'diesel', '13.020', '351.54', '2026-07-26 10:07:37', '2026-07-26 16:07:37+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44859', 'proveedor', '24157', NULL, 'b91265841b26c6a5a9146a087bf43728f521b37f3579ef3be120667637dcdee2', '8', NULL, 'activo', 'indeterminado', 'diesel', '121.750', '3287.17', '2026-07-26 09:53:11', '2026-07-26 15:53:11+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44860', 'proveedor', '24158', NULL, '2a4a8985cc8406d00f683b653cf691e2294430dd89b3d9a365140bfcafb96a2d', '44', NULL, 'activo', 'indeterminado', 'diesel', '334.910', '9042.44', '2026-07-26 09:50:28', '2026-07-26 15:50:28+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44861', 'proveedor', '24159', NULL, 'db890d97469a3ea82618cb80a0a0e74f4049bc516de40ee98c572f4c681f94f5', '55', NULL, 'activo', 'motor', 'diesel', '129.000', '3483.00', '2026-07-25 22:07:00', '2026-07-26 04:07:00+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44862', 'proveedor', '24160', NULL, 'bba04ee50c17f338b6bf00bf1b3e0e931da5ee14d8d6d8bb6fb101b9499372ee', '55', NULL, 'activo', 'motor', 'diesel', '370.340', '9999.00', '2026-07-25 21:51:00', '2026-07-26 03:51:00+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44863', 'proveedor', '24161', NULL, 'dcd2c03b4c3e4bde3fc802bc64563ecad4103e7f1c1e0d8639ced7c23166b60c', '40', NULL, 'activo', 'motor', 'diesel', '279.650', '7575.66', '2026-07-25 20:42:00', '2026-07-26 02:42:00+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44864', 'proveedor', '24162', NULL, '7c37a52dc8bb38ae25fa1114a949aaf7a84f095c3b17b46837dd2c107b6c25db', '20', NULL, 'activo', 'indeterminado', 'diesel', '140.520', '3793.91', '2026-07-25 19:46:17', '2026-07-26 01:46:17+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44865', 'proveedor', '24163', NULL, 'daea3ca16c163941f0430bbdf61bbe58684772ba953c72a458150ffec9e47f1e', '20', NULL, 'activo', 'indeterminado', 'diesel', '19.020', '513.59', '2026-07-25 19:42:04', '2026-07-26 01:42:04+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44866', 'proveedor', '24164', NULL, 'bded5e5641c21bee69d9803a6f9a32c6b0da34e9a864f5715aeebc4316c149be', '5', NULL, 'activo', 'motor', 'diesel', '223.200', '6026.45', '2026-07-25 18:22:21', '2026-07-26 00:22:21+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44867', 'proveedor', '24165', NULL, 'c6573e8159945fc125171b88b25766c85e29f3f3e016c83daaef87e90ca4e2c2', '5', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-25 18:09:55', '2026-07-26 00:09:55+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44868', 'proveedor', '24166', NULL, '877146966523b96f280b6cc1c93615d3257aa81f2d8a24a9205cb98dfc73ba43', '36', NULL, 'activo', 'motor', 'diesel', '650.000', '17550.00', '2026-07-25 11:09:00', '2026-07-25 17:09:00+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44869', 'proveedor', '24167', NULL, '945c1e304236f1836030ce93217939bf864a0a4d4b0bb046daec0add70d8f7ab', '55', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-25 02:28:46', '2026-07-25 08:28:46+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44870', 'proveedor', '24168', NULL, 'ffe506702ed445d4f5026441024071ba9c2564ac24a4cc39939a9eb62c2100b6', '52', NULL, 'activo', 'indeterminado', 'diesel', '20.250', '546.86', '2026-07-24 19:22:15', '2026-07-25 01:22:15+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44871', 'proveedor', '24169', NULL, 'd26e96717e8d1e8d6fa48aab7043e7587aeb1c4b2e5383c0e88e708f1e2fa390', '52', NULL, 'activo', 'indeterminado', 'diesel', '68.900', '1860.38', '2026-07-24 19:17:23', '2026-07-25 01:17:23+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44872', 'proveedor', '24170', NULL, 'debeb465f283fd1afddaabc0064680f7f6f170be2247a83c8c31ce733d14c29e', NULL, '45', 'activo', 'termo', 'diesel', '120.000', '3240.00', '2026-07-24 17:30:17', '2026-07-24 23:30:17+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44873', 'proveedor', '24171', NULL, '41ff745dee7f3a9187a2bd552a5ebd01cb8ebd28198166c1cb6f27db7add0dea', '25', NULL, 'activo', 'motor', 'diesel', '270.000', '7290.00', '2026-07-24 17:14:11', '2026-07-24 23:14:11+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44874', 'proveedor', '24172', NULL, '13f6d933906485e7c6c3e856fe6b2cf32922cc3de85b580960ad228dc19d33ee', '25', NULL, 'activo', 'motor', 'diesel', '360.000', '9720.00', '2026-07-24 16:59:22', '2026-07-24 22:59:22+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44875', 'proveedor', '24173', NULL, '09e252f843640132023c88b84a9a4d3d87b3bbd4076d13be2559ca2aa4a4a9cb', '8', NULL, 'activo', 'indeterminado', 'diesel', '27.000', '729.00', '2026-07-24 15:32:44', '2026-07-24 21:32:44+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44876', 'proveedor', '24174', NULL, 'd18c2746107548801a257b31b95b8019ed621c4c71b5b37600838acfc25154ff', '8', NULL, 'activo', 'indeterminado', 'diesel', '91.030', '2457.81', '2026-07-24 15:27:52', '2026-07-24 21:27:52+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44877', 'proveedor', '24175', NULL, '1b0934b73d861c4bd35df6c2a26deba0fdadd77b79cfbb4581699e83e36fd8b0', '10', NULL, 'activo', 'indeterminado', 'diesel', '59.050', '1594.22', '2026-07-24 12:26:41', '2026-07-24 18:26:41+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44878', 'proveedor', '24176', NULL, '912bc4ff3ed5c97188a1bfcf69d2b5e03c81b88fc4cad680d90fca7fdac9734b', '10', NULL, 'activo', 'indeterminado', 'diesel', '24.000', '648.00', '2026-07-24 12:15:31', '2026-07-24 18:15:31+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44879', 'proveedor', '24177', NULL, '839462cd5b2f80a64f594d909897923fd42ec2b0e0efc7023ce29c6000177ae0', NULL, '44', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-24 12:14:43', '2026-07-24 18:14:43+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44880', 'proveedor', '24178', NULL, '7d8568c2c42f412026bed1528fdac17e4ab5dc9c843c6a5ca9229987b1ff40cd', '10', NULL, 'activo', 'indeterminado', 'diesel', '366.670', '9900.00', '2026-07-24 12:11:17', '2026-07-24 18:11:17+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44881', 'proveedor', '24179', NULL, '0cad2940126516d1e5ca018c1922ece945765c5e99697492a24d35da8de216ca', '5', NULL, 'activo', 'motor', 'diesel', '264.000', '7128.00', '2026-07-24 11:48:23', '2026-07-24 17:48:23+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44882', 'proveedor', '24180', NULL, '59b766b480dcb3e5e5c281f3b330f48a65dbf5848c40f33b51b9857e7fe9e5a6', '5', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-24 11:40:25', '2026-07-24 17:40:25+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44883', 'proveedor', '24181', NULL, 'c56b6acf0f6fdc42b722337bf9c32de30add3aff78f34525044e4d22498a3d34', '40', NULL, 'activo', 'motor', 'diesel', '480.000', '12960.00', '2026-07-24 12:26:11', '2026-07-24 18:26:11+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44884', 'proveedor', '24182', NULL, 'd40b9f74fc901c9073affcfd9cfbcd0ce9844fdc7561242e35ed35fed6149390', '20', NULL, 'activo', 'indeterminado', 'diesel', '90.090', '2432.51', '2026-07-23 18:47:02', '2026-07-24 00:47:02+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44885', 'proveedor', '24183', NULL, 'cbd71dcb1ce8a48d981ebd46aa67a9eeb8eaee12d1d7e55a36601716a2267a34', '44', NULL, 'activo', 'indeterminado', 'diesel', '41.280', '1114.67', '2026-07-23 16:55:04', '2026-07-23 22:55:04+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44886', 'proveedor', '24184', NULL, 'fc4d5089c91e7e06dc27ad312412a9117f2bee2898de959be65b835e91506211', '44', NULL, 'activo', 'indeterminado', 'diesel', '107.640', '2906.20', '2026-07-23 14:53:45', '2026-07-23 20:53:45+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44887', 'proveedor', '24185', NULL, 'cabea372215a24b77356dbba86b5c38929491c2fb1fb71786e43a135ec816427', '20', NULL, 'activo', 'indeterminado', 'diesel', '85.080', '2297.03', '2026-07-23 10:32:00', '2026-07-23 16:32:00+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44888', 'proveedor', '24186', NULL, '2c1768196517d5eb6e76256826a536fd57d805ea298373cab49fa9eac2b391a5', '20', NULL, 'activo', 'indeterminado', 'diesel', '5.280', '142.48', '2026-07-23 10:28:00', '2026-07-23 16:28:00+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44889', 'proveedor', '24187', NULL, 'eb2b330405c29e9381e7c88c86fd9e3c1ef1089a818b1a5066dbc184d050d9a2', '8', NULL, 'activo', 'indeterminado', 'diesel', '11.090', '299.43', '2026-07-22 22:38:14', '2026-07-23 04:38:14+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44890', 'proveedor', '24188', NULL, '317214aed6af8be5c6df93401cf590e334a72d21bf87a7de09cf644dc8891ba6', '8', NULL, 'activo', 'indeterminado', 'diesel', '55.250', '1491.86', '2026-07-22 22:25:34', '2026-07-23 04:25:34+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44891', 'proveedor', '24189', NULL, '977bef9846f2ae674b09ab9f9d87bac3af7baf72b54b9a1f44d2348a3fe1e5d0', '13', NULL, 'activo', 'motor', 'diesel', '340.000', '9180.00', '2026-07-22 22:17:39', '2026-07-23 04:17:39+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44892', 'proveedor', '24190', NULL, '39fde5ea9015d09b0d7bc554e57b20cc06444284dbf357288b91437070b1b8b9', '34', NULL, 'activo', 'motor', 'diesel', '287.000', '8377.53', '2026-07-21 22:48:00', '2026-07-22 04:48:00+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44893', 'proveedor', '24191', NULL, 'fabf4c79c51d072f5f40e71180aea348155228b441c0f7631f772fe58dabd1b0', '34', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-21 22:37:00', '2026-07-22 04:37:00+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44894', 'proveedor', '24192', NULL, '4693d42f0e30ad7a60538e85904630fc447b233a5b45f58c13766be257a8c48a', '30', NULL, 'activo', 'motor', 'diesel', '98.000', '2646.00', '2026-07-21 18:10:15', '2026-07-22 00:10:15+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44895', 'proveedor', '24193', NULL, '978a202406ebd25ad99991cf64255f8014da5d5a3f40cd4057055f970a64f5f2', '30', NULL, 'activo', 'motor', 'diesel', '281.440', '7598.75', '2026-07-21 18:03:39', '2026-07-22 00:03:39+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44896', 'proveedor', '24194', NULL, '85229d374efa61b4edd2279ddb42b227c5be2987a520417b04e0a136e2ac696b', '10', NULL, 'activo', 'indeterminado', 'diesel', '130.270', '3517.26', '2026-07-21 17:16:47', '2026-07-21 23:16:47+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44897', 'proveedor', '24195', NULL, 'f5edd309cbcd0e269ca6e88448db471912fbde9cb89541b8e6e472866009848b', '44', NULL, 'activo', 'indeterminado', 'diesel', '58.740', '1586.09', '2026-07-21 16:29:29', '2026-07-21 22:29:29+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44898', 'proveedor', '24196', NULL, '2d6f417c31674c9756b248f3221162c1db548f6fe248a134b5d8bb593688f5f3', '44', NULL, 'activo', 'indeterminado', 'diesel', '30.890', '834.00', '2026-07-21 16:22:37', '2026-07-21 22:22:37+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44899', 'proveedor', '24197', NULL, '01b45c753b33a561ea60a2a62795a5b7631ca2292452d511cfeacd9325d6e473', '8', NULL, 'activo', 'indeterminado', 'diesel', '116.750', '3152.12', '2026-07-21 16:20:09', '2026-07-21 22:20:09+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44900', 'proveedor', '24198', NULL, '7b46bc47df872dc9015c6683c9745497639bb2b8ab504d2a7a2ec776823d35d2', '25', NULL, 'activo', 'motor', 'diesel', '264.000', '7128.00', '2026-07-21 14:23:58', '2026-07-21 20:23:58+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44901', 'proveedor', '24199', NULL, 'fce484c6f0b716e056e7c40c54725175e9dd228b67514c97911d5d914bbf45e4', '25', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-21 14:13:52', '2026-07-21 20:13:52+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44902', 'proveedor', '24200', NULL, '02ea54466c781539ff87c5562d1d089f95c6d4d96d30ecbf74cbb7ae6b011960', NULL, '47', 'activo', 'termo', 'diesel', '140.000', '3780.00', '2026-07-20 20:45:58', '2026-07-21 02:45:58+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44904', 'proveedor', '24202', NULL, '11049a6174b8ed666b24363acb2d5b0fdcfca20a88357026ef38e5f8ba34b83f', NULL, '30', 'activo', 'termo', 'diesel', '50.000', '1350.00', '2026-07-20 18:50:54', '2026-07-21 00:50:54+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44905', 'proveedor', '24203', NULL, '9e7fa9bf78280b8dd7149a728202168e11ccd21a3fbe77d66d65b9a041fcd081', '13', NULL, 'activo', 'motor', 'diesel', '14.000', '378.00', '2026-07-20 18:47:11', '2026-07-21 00:47:11+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44906', 'proveedor', '24204', NULL, 'cdc61163ae03b66b6b36df2dd4ef94126184c0976b2e4bb088886184582c5bac', '13', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-20 18:42:34', '2026-07-21 00:42:34+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44907', 'proveedor', '24205', NULL, 'd14435129d87e3c763356a2ae34ab21a55d89ea05c3ed9cb14791831bc4c74f8', '40', NULL, 'activo', 'motor', 'diesel', '214.000', '5778.00', '2026-07-20 17:10:59', '2026-07-20 23:10:59+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44908', 'proveedor', '24206', NULL, 'ac55418325299238a3ff944e68e57fb4a1a82710e631cd827c7703baa2fe385e', '40', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-20 17:04:07', '2026-07-20 23:04:07+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44909', 'proveedor', '24207', NULL, 'c27c325416c50d6de9a9a6973bcd195454d9d3e5f85ee2884bae4d80420110e3', '18', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1119.60', '2026-07-20 14:36:58', '2026-07-20 20:36:58+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44910', 'proveedor', '24208', NULL, 'ed00352cd1fd6ebc289d7f5d9f23af5bef2b5ed8004e44dc34a5c7c29ec59cbb', '18', NULL, 'activo', 'indeterminado', 'diesel', '80.000', '2239.20', '2026-07-20 14:34:01', '2026-07-20 20:34:01+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44911', 'proveedor', '24209', NULL, 'f22c60a621a6a11b35bdbe3e0a991923f7b52117a83aa87311f62b6a45b7930c', '19', NULL, 'activo', 'indeterminado', 'diesel', '50.280', '1407.34', '2026-07-20 14:27:20', '2026-07-20 20:27:20+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44912', 'proveedor', '24210', NULL, 'ba971006545f774dc5099075eb970868cbfa1dadde54aadd3ff5dd09757dc789', '19', NULL, 'activo', 'indeterminado', 'diesel', '30.000', '839.70', '2026-07-20 14:23:25', '2026-07-20 20:23:25+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44913', 'proveedor', '24211', NULL, '423bf5e9916f40920bf2030a52336e4101956013e1719f1bf50b8e95f4185aa8', '22', NULL, 'activo', 'indeterminado', 'diesel', '46.410', '1299.02', '2026-07-20 14:16:16', '2026-07-20 20:16:16+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44914', 'proveedor', '24212', NULL, 'ddadd73b3914595000887aafb1121aa93ebae3031cf046314aca5d0eb46629d9', '22', NULL, 'activo', 'indeterminado', 'diesel', '194.380', '5440.70', '2026-07-20 14:12:01', '2026-07-20 20:12:01+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44915', 'proveedor', '24213', NULL, '42f7fe26d65051038f643f5c28ba4c6826c3c4a63506f1141cc76be3e545ddbb', '21', NULL, 'activo', 'indeterminado', 'diesel', '36.650', '989.47', '2026-07-20 13:48:00', '2026-07-20 19:48:00+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44916', 'proveedor', '24214', NULL, '2d736cfe8336e93d40950e4dda735ba0e162f8bdfeef4ebf286fe6f28b5bdd54', '21', NULL, 'activo', 'indeterminado', 'diesel', '186.920', '5046.73', '2026-07-20 13:45:00', '2026-07-20 19:45:00+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44917', 'proveedor', '24215', NULL, 'd7855e89b3697068ba8b61e217b288041828cfa201bef86d56ebdc1005deb914', '44', NULL, 'activo', 'indeterminado', 'diesel', '14.210', '397.74', '2026-07-20 12:58:32', '2026-07-20 18:58:32+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44918', 'proveedor', '24216', NULL, '9210407b96c8e8a74126d0f3e2f8b0b06428782beb2702bcbcea100bea681a34', '44', NULL, 'activo', 'indeterminado', 'diesel', '50.580', '1415.73', '2026-07-20 12:56:07', '2026-07-20 18:56:07+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44919', 'proveedor', '24217', NULL, '2082831e84aead7edb37c9fff0dd5b1a0e5ee55774538f9bdce3a1674528708b', '10', NULL, 'activo', 'indeterminado', 'diesel', '125.090', '3377.54', '2026-07-19 16:01:00', '2026-07-19 22:01:00+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44920', 'proveedor', '24218', NULL, 'f044408aa962ad96542baef14d187213ad0f0b7f8867242dc90e6126ff66b0d4', '10', NULL, 'activo', 'indeterminado', 'diesel', '30.260', '817.10', '2026-07-19 15:56:00', '2026-07-19 21:56:00+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44921', 'proveedor', '24219', NULL, '144b4c7e41381bcfe09fb1898ffb8ad5ec3651369715a8c72b5b2ce3dfdc7457', '46', NULL, 'activo', 'motor', 'diesel', '400.000', '10996.00', '2026-07-19 08:52:00', '2026-07-19 14:52:00+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44922', 'proveedor', '24220', NULL, '62e5c3d27e7828200b11ea96c7a7b343311ea22faaee4223998c072e7ef5e603', '40', NULL, 'activo', 'motor', 'diesel', '630.000', '17010.00', '2026-07-19 04:09:00', '2026-07-19 10:09:00+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44923', 'proveedor', '24221', NULL, '030b03a22ae4a44d956a04dea83f31461e159877450edcefcf4c544723c02b60', '30', NULL, 'activo', 'motor', 'diesel', '287.000', '8377.53', '2026-07-18 19:59:00', '2026-07-19 01:59:00+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44924', 'proveedor', '24222', NULL, '43c6ee2fe8f54221ae64862dec9959a9b533e44e87ab39049956ed23f234c2ed', '30', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-18 19:44:00', '2026-07-19 01:44:00+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44925', 'proveedor', '24223', NULL, 'f33d24a2d8d66a2c74acc33a8ea5c67e518aa8d9db6bf45b82512641f8ea4d85', '21', NULL, 'activo', 'indeterminado', 'diesel', '20.940', '565.38', '2026-07-18 19:53:49', '2026-07-19 01:53:49+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44926', 'proveedor', '24224', NULL, '995ef9288a9a0b73d346c708c6d8fcf43a8c341548c48442f778e724d2218658', '21', NULL, 'activo', 'indeterminado', 'diesel', '130.660', '3527.93', '2026-07-18 19:49:48', '2026-07-19 01:49:48+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44927', 'proveedor', '24225', NULL, '0ca0f1c111f7c7562ec7c53b3ae8e29e13530bfea87a6acda4f651628677acc5', '10', NULL, 'activo', 'indeterminado', 'diesel', '189.070', '5104.86', '2026-07-18 18:16:25', '2026-07-19 00:16:25+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44928', 'proveedor', '24226', NULL, '592168a29c5fac56d7576233028aba9bc58d62fd72c97de4a707bb11f14ac429', '10', NULL, 'activo', 'indeterminado', 'diesel', '54.010', '1458.30', '2026-07-18 18:09:54', '2026-07-19 00:09:54+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44929', 'proveedor', '24227', NULL, 'f027381efbb87079e11d76cafde9347af6dd774d7145b7303dffdbb58f690380', '37', NULL, 'activo', 'motor', 'diesel', '450.000', '12150.00', '2026-07-18 12:15:34', '2026-07-18 18:15:34+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44930', 'proveedor', '24228', NULL, '0e13f7229badf3df39937f139f867de070bf38c6519c525161909740bd995e55', '52', NULL, 'activo', 'indeterminado', 'diesel', '29.950', '808.60', '2026-07-17 22:51:00', '2026-07-18 04:51:00+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44931', 'proveedor', '24229', NULL, '1773aa7a742e5b858517747c2d5bae2b1fd1f037493e6976dd782941d7140529', '52', NULL, 'activo', 'indeterminado', 'diesel', '70.220', '1895.86', '2026-07-17 22:47:00', '2026-07-18 04:47:00+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44932', 'proveedor', '24230', NULL, 'b5f6e392682ca73ce56916a39927bdff6e6b6e98941630ede48b6d04e628fc55', '56', NULL, 'activo', 'motor', 'diesel', '600.000', '16200.00', '2026-07-17 04:31:00', '2026-07-17 10:31:00+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44933', 'proveedor', '24231', NULL, '1a2021a5b4d238c9a675fd5b34800aeeaeb149587c0a38063cb2a51f35f67e89', '34', NULL, 'activo', 'motor', 'diesel', '400.000', '10996.00', '2026-07-17 03:15:00', '2026-07-17 09:15:00+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44934', 'proveedor', '24232', NULL, '84f2b6983ca0882eae6be090d78bd665132ada85e8ad4520bcae3874c5ef6ffc', '8', NULL, 'activo', 'indeterminado', 'diesel', '143.420', '3872.37', '2026-07-16 21:15:00', '2026-07-17 03:15:00+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44935', 'proveedor', '24233', NULL, '44c34512b4ba96f66afa47bf02129f205efd0862c51963fcc9d8ce3c417764aa', '8', NULL, 'activo', 'indeterminado', 'diesel', '44.460', '1200.45', '2026-07-16 21:11:00', '2026-07-17 03:11:00+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44936', 'proveedor', '24234', NULL, '3f54824d442a66a35ea5979d4c7555fbf1f930766ce81735169a2fb88e943b5f', '9', NULL, 'activo', 'indeterminado', 'diesel', '74.310', '2006.37', '2026-07-16 19:44:29', '2026-07-17 01:44:29+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44937', 'proveedor', '24235', NULL, 'e7e326d1a319c2a7261cdf21023d6e440d6989190f1d0c7bee2b019fd1158968', '9', NULL, 'activo', 'indeterminado', 'diesel', '114.050', '3079.35', '2026-07-16 19:40:07', '2026-07-17 01:40:07+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44938', 'proveedor', '24236', NULL, '958b0c5ad30af8d58724cd6e4d04ac60fb3cca98b3a682da35ac9a9975cafe16', '21', NULL, 'activo', 'indeterminado', 'diesel', '48.190', '1301.05', '2026-07-16 19:19:45', '2026-07-17 01:19:45+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44939', 'proveedor', '24237', NULL, '441c014e2d3b288c9b05dfd5b65263971d60e65649d7177b92b4e6282602fb91', '21', NULL, 'activo', 'indeterminado', 'diesel', '69.870', '1886.46', '2026-07-16 19:15:21', '2026-07-17 01:15:21+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44940', 'proveedor', '24238', NULL, '4ba07b9e31ebbe99b1236fb353f9eb0202fd5b32d2d0616ea229ffbf46c3e919', '18', NULL, 'activo', 'indeterminado', 'diesel', '22.000', '594.00', '2026-07-16 17:42:30', '2026-07-16 23:42:30+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44941', 'proveedor', '24239', NULL, '54d025d7f76c5f372c1c185ecf9682cc31327ffa422561ec18478689ff00353a', '40', NULL, 'activo', 'motor', 'diesel', '435.000', '11958.15', '2026-07-16 17:40:00', '2026-07-16 23:40:00+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44942', 'proveedor', '24240', NULL, '6368f6a3951a3faccb8bd7a0833de9e97a74b58ff96b955041e908d8cf772410', '18', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-16 17:36:41', '2026-07-16 23:36:41+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44943', 'proveedor', '24241', NULL, 'c2aeb266661df5befe42d8bb77b393359754583cf66ef7266b82409fd626ddfb', '10', NULL, 'activo', 'indeterminado', 'diesel', '110.010', '2970.19', '2026-07-15 15:30:42', '2026-07-15 21:30:42+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44944', 'proveedor', '24242', NULL, '25abe0a05b55b98e133db73f71ceede96c1d1a14d0d786de6e43456c4a91c24f', '10', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-15 17:01:20', '2026-07-15 23:01:20+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44945', 'proveedor', '24243', NULL, 'fc68ee25fe80bff3c0d26ea6cdb51b7a4cd220ff59b2c3f98278870fee1a21eb', '22', NULL, 'activo', 'indeterminado', 'diesel', '55.680', '1503.39', '2026-07-15 11:08:00', '2026-07-15 17:08:00+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44946', 'proveedor', '24244', NULL, 'b0614ec4c9ad5fbf70c0f16945ad23d62af29c350b5e7634f676665b5204f4cb', '22', NULL, 'activo', 'indeterminado', 'diesel', '126.650', '3419.47', '2026-07-15 11:02:00', '2026-07-15 17:02:00+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44947', 'proveedor', '24245', NULL, '864145786b1e429cd6e97459dae40b640ab1252ccacf9e78750dd7f16e42ad82', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8397.00', '2026-07-15 09:44:33', '2026-07-15 15:44:33+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44948', 'proveedor', '24246', NULL, 'c2281d424f72b697dc7219bd66aad76e3a2f2f56201ce38825cec0c71381e17e', '46', NULL, 'activo', 'motor', 'diesel', '277.000', '7753.23', '2026-07-15 04:43:04', '2026-07-15 10:43:04+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44949', 'proveedor', '24247', NULL, '3848e07baaa0688bff2f1fb10af8728d490b2d4222b8d06b5cd08492156bc545', '46', NULL, 'activo', 'motor', 'diesel', '353.000', '9880.47', '2026-07-15 04:36:25', '2026-07-15 10:36:25+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44950', 'proveedor', '24248', NULL, '03d1c6d104115909c47b743a04f692c3b352fc872d1154376355cd5e4f964390', '18', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-14 21:27:06', '2026-07-15 03:27:06+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44951', 'proveedor', '24249', NULL, '8ecb6f6e195e245409ccf829c7ff167c315c26dffbe1e23829d03a617ab94d1c', '18', NULL, 'activo', 'indeterminado', 'diesel', '60.000', '1620.00', '2026-07-14 21:23:47', '2026-07-15 03:23:47+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44952', 'proveedor', '24250', NULL, '8bd0259dba9ad6d141faf0cd6f1edff711f135ec354e272861664546745ba39e', '37', NULL, 'activo', 'motor', 'diesel', '247.000', '6913.53', '2026-07-14 21:16:07', '2026-07-15 03:16:07+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44953', 'proveedor', '24251', NULL, 'bbac51f5d55c8184cc01f0c430501186dfb547540f9f202d22d3c2cad4bd48dc', '37', NULL, 'activo', 'motor', 'diesel', '353.000', '9880.47', '2026-07-14 21:10:54', '2026-07-15 03:10:54+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44954', 'proveedor', '24252', NULL, 'b012eb1db790bc6b141398083a8c2855a945a6de5e5d17caad87b649b0c53789', '44', NULL, 'activo', 'indeterminado', 'diesel', '33.650', '908.55', '2026-07-14 20:51:27', '2026-07-15 02:51:27+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44955', 'proveedor', '24253', NULL, '89e2ce0a8196ad87bac2e1cef0a56ad6f829f69579884e73609c64a7f2583138', '44', NULL, 'activo', 'indeterminado', 'diesel', '22.370', '603.99', '2026-07-14 20:47:10', '2026-07-15 02:47:10+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44956', 'proveedor', '24254', NULL, '007e645363f0c7a7bf749d90eab25d29534a4347840f8474a9d1c1defa36229c', '44', NULL, 'activo', 'indeterminado', 'diesel', '15.200', '410.40', '2026-07-14 20:41:43', '2026-07-15 02:41:43+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44958', 'proveedor', '24256', NULL, 'a639e1b9f29a8a8e3916ab0e17c0f36bc48bfc46fb671be846cc293b8daaadcc', '9', NULL, 'activo', 'indeterminado', 'diesel', '28.080', '758.16', '2026-07-14 18:56:03', '2026-07-15 00:56:03+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44959', 'proveedor', '24257', NULL, 'ba428a806dc70c1de6b9d1a4ce084aa0c371d0ae8df8db23d4a19e1d719bdb14', '9', NULL, 'activo', 'indeterminado', 'diesel', '56.120', '1515.24', '2026-07-14 18:52:37', '2026-07-15 00:52:37+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44960', 'proveedor', '24258', NULL, 'fe2edef1ce303bfd10333f3046e055584804b6c06de16d805718613334498c59', '40', NULL, 'activo', 'motor', 'diesel', '300.000', '8247.00', '2026-07-14 18:47:00', '2026-07-15 00:47:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44961', 'proveedor', '24259', NULL, '59e872f9ef50199d460ac5d417f8182b491bb98175ed4c6bd3eba464590bb546', '34', NULL, 'activo', 'motor', 'diesel', '250.000', '6750.00', '2026-07-14 18:01:00', '2026-07-15 00:01:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44962', 'proveedor', '24260', NULL, 'afa48436db82f4eb1ac8a80d8b7356e154daa6aeabdbc895b01ded07094d2a25', '8', NULL, 'activo', 'indeterminado', 'diesel', '49.180', '1327.78', '2026-07-14 15:14:00', '2026-07-14 21:14:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44963', 'proveedor', '24261', NULL, 'f534261356ddbfb0fdf432910d60f2b88c07cef32dccfbbd9ae4d5ebdd803525', '8', NULL, 'activo', 'indeterminado', 'diesel', '26.400', '712.80', '2026-07-14 15:10:00', '2026-07-14 21:10:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44964', 'proveedor', '24262', NULL, '7353e8d971c62708ebd4ea95a1fb32d05f7013b996bf7f4068a568250edb80aa', '19', NULL, 'activo', 'indeterminado', 'diesel', '120.000', '3240.16', '2026-07-14 14:18:52', '2026-07-14 20:18:52+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44965', 'proveedor', '24263', NULL, '275cba7fa77d1eed5cec86d728b31ea8c210c7e60b0a77075ae92dcb8147b4e3', '19', NULL, 'activo', 'indeterminado', 'diesel', '60.010', '1620.30', '2026-07-14 14:10:00', '2026-07-14 20:10:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44966', 'proveedor', '24264', NULL, 'ecd53589aa404d384ac850b319c3f9c4fede4e0185d6d3babac1e9afdd3d1460', '19', NULL, 'activo', 'indeterminado', 'diesel', '1.000', '27.00', '2026-07-14 13:55:25', '2026-07-14 19:55:25+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44967', 'proveedor', '24265', NULL, '3d6f6eaa446318c606ca23a5d50c44d3a3e85396292b9c961e25334be88d65e6', '30', NULL, 'activo', 'motor', 'diesel', '181.860', '5090.26', '2026-07-14 12:58:17', '2026-07-14 18:58:17+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44968', 'proveedor', '24266', NULL, '540162641bb8feb202bc380768b7e314f64b7e7d240c50baebea119c1fd2cc9f', '30', NULL, 'activo', 'motor', 'diesel', '353.000', '9880.47', '2026-07-14 12:54:51', '2026-07-14 18:54:51+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44969', 'proveedor', '24267', NULL, '54807f5483abaa7ead97a9fff51a428dc538e7e40770be6454c5769e9f7e210c', '10', NULL, 'activo', 'indeterminado', 'diesel', '133.030', '3591.81', '2026-07-14 13:31:33', '2026-07-14 19:31:33+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44970', 'proveedor', '24268', NULL, 'b9cdc2579b7b6976863fecc9d9f49348c0ffad2fd32750d89bddd8ed2582a032', '10', NULL, 'activo', 'indeterminado', 'diesel', '22.000', '594.00', '2026-07-14 13:24:18', '2026-07-14 19:24:18+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44971', 'proveedor', '24269', NULL, '4414fea85127ddec4b20326c82db807b6dc407ea8c11cedf5be328120dbddfe0', '45', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-14 08:30:00', '2026-07-14 14:30:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44972', 'proveedor', '24270', NULL, '7ac59bd7c48bb44f3bbe0e88c19942983f340fe9061a4b0ec533edebf68a2286', '45', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-14 08:21:00', '2026-07-14 14:21:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44973', 'proveedor', '24271', NULL, 'fa756c926adf5bad4348ba4e9ff74475be3e5dbacc97eeaf3266170b4d0118ec', '45', NULL, 'activo', 'motor', 'diesel', '92.470', '2495.79', '2026-07-14 08:14:00', '2026-07-14 14:14:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44974', 'proveedor', '24272', NULL, '74b1f9df836a6083c076e0fdebc69ef9a7e5bce375965c47c784069046307cb8', '45', NULL, 'activo', 'motor', 'diesel', '237.680', '6415.04', '2026-07-14 08:02:00', '2026-07-14 14:02:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44975', 'proveedor', '24273', NULL, '6181073e60e9be2981f20ce00979e989532e23ee09cabaee8c388a77619efbc8', '45', NULL, 'activo', 'motor', 'diesel', '370.140', '9990.00', '2026-07-14 07:53:00', '2026-07-14 13:53:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44976', 'proveedor', '24274', NULL, '8a7c23796e77e11840323b015a884ba7db3d98d2475744feb5e5927b3f116094', '13', NULL, 'activo', 'motor', 'diesel', '9.000', '243.00', '2026-07-14 07:26:00', '2026-07-14 13:26:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44977', 'proveedor', '24275', NULL, 'd628482ae010fb90d3bed8f787f34976ce40c3664b52ea7276df54eeb9293ff5', '13', NULL, 'activo', 'motor', 'diesel', '370.330', '9999.00', '2026-07-14 07:24:00', '2026-07-14 13:24:00+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44978', 'proveedor', '24276', NULL, 'e1050754c23dfcd9041ee953ecb70b9e4f63c46036e330252b14bec504100200', '21', NULL, 'activo', 'indeterminado', 'diesel', '61.810', '1668.76', '2026-07-14 06:20:11', '2026-07-14 12:20:11+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44979', 'proveedor', '24277', NULL, '112fa15683c8ca150489d7ab7677653dbc1479e1074f9fc7909b28811255fcb4', '21', NULL, 'activo', 'indeterminado', 'diesel', '107.080', '2891.21', '2026-07-14 06:12:11', '2026-07-14 12:12:11+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44980', 'proveedor', '24278', NULL, '4449d48566ec0af147ad3f1e7ef11502fa169fe329864a6a9c065f547f62bc5f', NULL, '52', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-13 22:10:17', '2026-07-14 04:10:17+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44981', 'proveedor', '24279', NULL, '14535d78dfac82632eac5259b2b342955147a40a94bb10c32567418de5141a1b', '41', NULL, 'activo', 'motor', 'diesel', '264.000', '7128.00', '2026-07-13 22:09:20', '2026-07-14 04:09:20+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44982', 'proveedor', '24280', NULL, 'd5c3dc17bf8917a7620457a304b5f591768b5c5717a1ab423e75f8b4b6e0670f', '41', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-13 21:57:39', '2026-07-14 03:57:39+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44983', 'proveedor', '24281', NULL, 'd9c49ada37e4416e78c2e84a4a5c2eab26250431fe9827b74d3393f14aa7bd51', '3', NULL, 'activo', 'motor', 'diesel', '234.000', '6318.00', '2026-07-13 21:56:19', '2026-07-14 03:56:19+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44984', 'proveedor', '24282', NULL, 'f778993b3ca08bc68b8e9ada0fbd9e390bd874c4f29b75f449d479124f756c04', '3', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-13 21:48:50', '2026-07-14 03:48:50+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44985', 'proveedor', '24283', NULL, 'b24cc8f6d35ed9ffb6957fb10bfc620f1d14edd89d8d68f1ef1a8f28f5f01f5c', '1', NULL, 'activo', 'motor', 'diesel', '33.000', '891.00', '2026-07-13 21:27:55', '2026-07-14 03:27:55+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44986', 'proveedor', '24284', NULL, '905ed9bc24c6deed8f7cfa7c85b95a9738b8e608c430f1fec0f5ce1bab3467bc', '1', NULL, 'activo', 'motor', 'diesel', '366.100', '9884.70', '2026-07-13 21:25:53', '2026-07-14 03:25:53+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44987', 'proveedor', '24285', NULL, '669ce02d55c33546033f444a7071732dae9602315c4f63fdd779025b3e527c98', '41', NULL, 'activo', 'motor', 'diesel', '251.470', '6789.69', '2026-07-11 21:30:00', '2026-07-12 03:30:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44988', 'proveedor', '24286', NULL, '494acee1fc32040c1f88f58d2908a5fbaa5f7cab6b2abc35cf171eaee0e66ad8', '22', NULL, 'activo', 'indeterminado', 'diesel', '36.320', '980.56', '2026-07-13 11:10:00', '2026-07-13 17:10:00+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44989', 'proveedor', '24287', NULL, '2cbacd66b9f8928e1a147b41f968d37785451165f674aa771bde0c7ea39dfb12', '22', NULL, 'activo', 'indeterminado', 'diesel', '74.760', '2018.52', '2026-07-13 10:56:00', '2026-07-13 16:56:00+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44990', 'proveedor', '24288', NULL, '2934042e90718ce79da0f3b90d4df9c8e7c736ded1cf6fa925df45eb9bb3fde1', '21', NULL, 'activo', 'indeterminado', 'diesel', '173.130', '4674.46', '2026-07-13 10:54:15', '2026-07-13 16:54:15+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44991', 'proveedor', '24289', NULL, '11452e0ce683eb69bfe23b54a395eb71b0f8065e8bf59c4d3efa49305d21ab3f', '56', NULL, 'activo', 'motor', 'diesel', '270.120', '7293.24', '2026-07-12 22:34:32', '2026-07-13 04:34:32+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44992', 'proveedor', '24290', NULL, '8477fa6adf67ab76fc7f53952bab008075d8ebbb8c398eddd03dd30d877b2e61', '56', NULL, 'activo', 'motor', 'diesel', '360.000', '9720.00', '2026-07-12 22:26:11', '2026-07-13 04:26:11+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44993', 'proveedor', '24291', NULL, '6e0098c1c32dac9d11c036a46812c5b2a5801902a2ece39a1a78eb095e594799', '8', NULL, 'activo', 'indeterminado', 'diesel', '12.180', '328.81', '2026-07-12 22:08:00', '2026-07-13 04:08:00+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44994', 'proveedor', '24292', NULL, '0ffc12075fde2bcf83083c48878177abeb138211e19f2e16793139ac3ce4c141', '8', NULL, 'activo', 'indeterminado', 'diesel', '40.850', '1103.03', '2026-07-12 22:03:00', '2026-07-13 04:03:00+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44995', 'proveedor', '24293', NULL, 'ba72b94b7253b393a410b4f61656513033f382d3cbca376dc3c3bcd4b4d4ad52', '52', NULL, 'activo', 'indeterminado', 'diesel', '31.760', '857.60', '2026-07-12 20:10:00', '2026-07-13 02:10:00+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44996', 'proveedor', '24294', NULL, '02d8153f23cb512138b4011e900043a944cfde5c94d640460d49e72084d4180f', '52', NULL, 'activo', 'indeterminado', 'diesel', '62.420', '1685.34', '2026-07-12 20:05:00', '2026-07-13 02:05:00+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44997', 'proveedor', '24296', NULL, '40dfcaf50e3c1f69ad00eb85fd16007432e38b8dbaa2e695e9a34dcfca8c6d7a', '10', NULL, 'activo', 'indeterminado', 'diesel', '65.000', '1786.90', '2026-07-12 12:21:13', '2026-07-12 18:21:13+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44998', 'proveedor', '24297', NULL, '94566b7071293a56e75d66139a877d4930cda2cea548843246418c9c3df20c00', '10', NULL, 'activo', 'indeterminado', 'diesel', '360.130', '9900.00', '2026-07-12 12:12:45', '2026-07-12 18:12:45+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('44999', 'proveedor', '24298', NULL, 'd344793bc748582a8235b16366e09e633749e48226b303333677a0878b75ffbf', '10', NULL, 'activo', 'indeterminado', 'diesel', '44.260', '1216.76', '2026-07-12 12:04:42', '2026-07-12 18:04:42+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45000', 'proveedor', '24299', NULL, '5114e1211c407d897dca0f5a4c5f92f72c6dd6707589bc1b42c38c8e3a411758', NULL, '45', 'activo', 'termo', 'diesel', '95.020', '2565.54', '2026-07-12 09:46:48', '2026-07-12 15:46:48+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45001', 'proveedor', '24300', NULL, '09ad0d6334b186dd83921c28e4965a177e5acb743a16f18fdf550fe36914dc7b', '25', NULL, 'activo', 'motor', 'diesel', '253.570', '6846.39', '2026-07-12 09:37:47', '2026-07-12 15:37:47+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45002', 'proveedor', '24301', NULL, '01f53a2ddca28ea6cfac384641f5d18988cd7e43629c28d6cc55f2416135215c', '25', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-12 09:30:02', '2026-07-12 15:30:02+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45003', 'proveedor', '24302', NULL, '68b24c72943183f3914ad010b744293ec2599fc75f1ab28b2220fec95093e7a8', '13', NULL, 'activo', 'motor', 'diesel', '380.000', '10446.20', '2026-07-11 22:04:00', '2026-07-12 04:04:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45004', 'proveedor', '24303', NULL, '24864f365786e82adf6437c1a35821e607a9643aa53a107441569f10480848d6', '8', NULL, 'activo', 'indeterminado', 'diesel', '27.900', '753.22', '2026-07-11 17:35:00', '2026-07-11 23:35:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45005', 'proveedor', '24304', NULL, 'fd006a3dc3ffd3869b4f2b8bc05e8c29570b7e821010db26f53bf8bf15ee3fbc', '8', NULL, 'activo', 'indeterminado', 'diesel', '108.080', '2918.05', '2026-07-11 17:30:00', '2026-07-11 23:30:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45006', 'proveedor', '24305', NULL, '494aaf5a471841e3a1b19dad110032baa4e7bcabb4c5dc7ad9a688c34a3ea734', NULL, NULL, 'cuarentena', NULL, 'diesel', '40.000', '1080.00', '2026-07-11 17:09:46', '2026-07-11 23:09:46+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45007', 'proveedor', '24306', NULL, 'e423ff6464c73d7a22247f2bc5cee8ae991e87157fd694d4029fc70ba7d880a9', '41', NULL, 'activo', 'motor', 'diesel', '360.090', '9722.43', '2026-07-11 16:47:19', '2026-07-11 22:47:19+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45008', 'proveedor', '24307', NULL, '2094ff9528b095fe010acbae4a8f785092e8b96d14f90ee6066eeb3c7c36669c', NULL, '15', 'activo', 'termo', 'diesel', '220.980', '6450.32', '2026-07-11 14:55:00', '2026-07-11 20:55:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45009', 'proveedor', '24308', NULL, '4b5092b3be083a8afb8e8f3e632ed04e776c4b3f2dd279e9d471a01c2ed9fdd4', '46', NULL, 'activo', 'motor', 'diesel', '157.000', '4582.83', '2026-07-11 14:23:00', '2026-07-11 20:23:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45010', 'proveedor', '24309', NULL, '269219d7e495b6433a70c5cb0008260eadcfe88b1a65b6588a1c3799db2cc4db', '46', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-11 14:17:00', '2026-07-11 20:17:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00');
INSERT INTO public.asientos_consumo (id, origen, carga_id, orden_id, origen_huella, unidad_id, remolque_id, atribucion, destino, combustible, litros, importe, momento_local, momento_ref, fecha_operacion, vigente, evento_id, contable, motivo, proyectado_en) VALUES
('45011', 'proveedor', '24310', NULL, '167d1dc7a68267ac8052b19cff2a37fb2605f541acc6cba3ada7410e67c245b7', '18', NULL, 'activo', 'indeterminado', 'diesel', '12.000', '324.00', '2026-07-11 13:20:59', '2026-07-11 19:20:59+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45012', 'proveedor', '24311', NULL, '989ae8627dd7fb77f2f349070a8ff136c311b5ef903cb97843545d6a2b021ad2', '18', NULL, 'activo', 'indeterminado', 'diesel', '42.000', '1134.00', '2026-07-11 13:18:39', '2026-07-11 19:18:39+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45013', 'proveedor', '24312', NULL, '35585e5fbafb5e00115f7f97214390629b73f9688c93406a8c8d1caa3ac32470', NULL, '52', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-11 11:38:00', '2026-07-11 17:38:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45014', 'proveedor', '24313', NULL, '6eef7d3dee5f6a5cb76ac964954a48234cfb51348fc47b63bd7b872256ee4e58', '3', NULL, 'activo', 'motor', 'diesel', '630.000', '17010.00', '2026-07-11 11:27:00', '2026-07-11 17:27:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45015', 'proveedor', '24314', NULL, 'd2e3ec48a06ce9fc2ea9b339c0d358dc9843e2f2a2fab7277f8df20e1e9f748c', '21', NULL, 'activo', 'indeterminado', 'diesel', '14.570', '393.31', '2026-07-11 09:34:36', '2026-07-11 15:34:36+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45016', 'proveedor', '24315', NULL, '61eb35e20a33eb9199c9005932a1b1a320c7a9541e0ed3a52f5e461ab299d6db', '21', NULL, 'activo', 'indeterminado', 'diesel', '146.850', '3964.98', '2026-07-11 09:27:10', '2026-07-11 15:27:10+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45017', 'proveedor', '24316', NULL, '30dcb5317d43063573cc0a7a12b3789642661a76613d38979416f28c5e10e063', '40', NULL, 'activo', 'motor', 'diesel', '450.000', '12370.50', '2026-07-11 05:30:00', '2026-07-11 11:30:00+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45018', 'proveedor', '24317', NULL, '5bda652ceecab7a0d60f22efb46f71d173d3922d48cf33a8a28de2f19381612d', NULL, '2', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-10 23:29:55', '2026-07-11 05:29:55+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45019', 'proveedor', '24318', NULL, 'd01ba16dda52969b733f6d928ec203f4904af16c3469d753ef23275152fd3e0e', '53', NULL, 'activo', 'motor', 'diesel', '234.000', '6318.00', '2026-07-10 23:23:17', '2026-07-11 05:23:17+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45020', 'proveedor', '24319', NULL, '13d977d1964fd008689b92d35cc0b836b9db3432b6db43dc7b583032e15fbee8', '53', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-10 23:16:52', '2026-07-11 05:16:52+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45021', 'proveedor', '24320', NULL, '2ef8eafe6b081c53cbd35901b4e0c96d6d050f832ab4760fb4f23a96fe859630', '52', NULL, 'activo', 'indeterminado', 'diesel', '37.610', '1015.47', '2026-07-10 21:42:00', '2026-07-11 03:42:00+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45022', 'proveedor', '24321', NULL, 'aaf576a6d718eecb8268a0c6ed1ead8ee538a7db0f0bfa3893e8b38aca78da23', '52', NULL, 'activo', 'indeterminado', 'diesel', '60.850', '1642.98', '2026-07-10 21:37:00', '2026-07-11 03:37:00+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45023', 'proveedor', '24322', NULL, '9e0efebb38bba2bcf573b38e12033eb3d67e9c7bf874cf41db521b53bc2f7b42', '18', NULL, 'activo', 'indeterminado', 'diesel', '78.000', '2106.00', '2026-07-10 21:34:30', '2026-07-11 03:34:30+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45024', 'proveedor', '24323', NULL, '1badea819f4c88fb4e082a7088c124a6f75b59e23483ecf2bb872e2ff79af8e1', '18', NULL, 'activo', 'indeterminado', 'diesel', '80.000', '2160.00', '2026-07-10 21:28:51', '2026-07-11 03:28:51+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45026', 'proveedor', '24325', NULL, '66d35b6aea93dd0b2704fee97c19cf6bc82458b3397990d6cd072d3254578c17', NULL, '31', 'activo', 'termo', 'diesel', '100.000', '2750.00', '2026-07-10 16:03:00', '2026-07-10 22:03:00+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45027', 'proveedor', '24326', NULL, '9837e47314e138643caaeabb270349fce9847c95c57c633ce937d234441b2f7a', '44', NULL, 'activo', 'indeterminado', 'diesel', '21.650', '584.47', '2026-07-10 15:41:00', '2026-07-10 21:41:00+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45028', 'proveedor', '24327', NULL, '9066949dbda86b121385eda66265305c1eacaa9df22ea9b2347bca7b9b83cdf4', '44', NULL, 'activo', 'indeterminado', 'diesel', '36.530', '986.42', '2026-07-10 15:37:00', '2026-07-10 21:37:00+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45029', 'proveedor', '24328', NULL, '397d96d3cff0a603ca3da277cfbd0c46ba29a6e3806335cfe6dd9ce86ffff9af', '13', NULL, 'activo', 'motor', 'diesel', '256.610', '6928.34', '2026-07-10 12:34:12', '2026-07-10 18:34:12+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45031', 'proveedor', '24330', NULL, 'a1c39a5c9f36342183f34a61e8be903f2c3ddd77afbaecb00c28092e1c0fbb01', '13', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-10 12:17:12', '2026-07-10 18:17:12+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45032', 'proveedor', '24331', NULL, '08087882b5cfe4fe83fd0d92afc940dc15b278bdb5f547c9855ac9316ccb7a4d', '21', NULL, 'activo', 'indeterminado', 'diesel', '251.830', '7048.72', '2026-07-10 09:14:13', '2026-07-10 15:14:13+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45033', 'proveedor', '24332', NULL, 'f031ad018e67a472132041d8557fa3bbff401ed5be8384139500d4b5c29a2706', '21', NULL, 'activo', 'indeterminado', 'diesel', '66.090', '1849.86', '2026-07-10 09:09:44', '2026-07-10 15:09:44+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45034', 'proveedor', '24333', NULL, '7bf27f89627d54b012a541a96623bde9284533204c249e60e0b15907b383cdc1', '10', NULL, 'activo', 'indeterminado', 'diesel', '80.010', '2160.19', '2026-07-09 14:31:15', '2026-07-09 20:31:15+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45035', 'proveedor', '24334', NULL, 'da88b32027113a618b28ea9ae3921a6982962ae7c6bc6ec23afb6204eaf9786a', '10', NULL, 'activo', 'indeterminado', 'diesel', '18.200', '491.43', '2026-07-09 14:25:29', '2026-07-09 20:25:29+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45036', 'proveedor', '24335', NULL, '26e4eae057406eef6142107c4acb5d9fa82e9e567917ecf359aed332af38c873', '40', NULL, 'activo', 'motor', 'diesel', '630.000', '17010.00', '2026-07-09 06:52:00', '2026-07-09 12:52:00+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45037', 'proveedor', '24336', NULL, 'c21111855771766d28d71fc72027415d63a3d5572c51339e3ecc01c72ac1835a', '25', NULL, 'activo', 'motor', 'diesel', '500.000', '13745.00', '2026-07-08 21:15:00', '2026-07-09 03:15:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45038', 'proveedor', '24337', NULL, 'ccd21249fc91d04dac23385daf1773ccd7557202bcb1ee030c4886dfab61d58d', '34', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-08 21:06:00', '2026-07-09 03:06:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45039', 'proveedor', '24338', NULL, '8ac6b127aeb7faf865dac37b9c543707193b0d3ee73efc5f4649457e2efb1c6e', '52', NULL, 'activo', 'indeterminado', 'diesel', '91.680', '2475.28', '2026-07-08 20:16:00', '2026-07-09 02:16:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45040', 'proveedor', '24339', NULL, 'be3b28127008e6dbe260a7f655496d6b99744c0a26ad1c899fde8b953dd7c319', '52', NULL, 'activo', 'indeterminado', 'diesel', '41.470', '1119.61', '2026-07-08 20:12:00', '2026-07-09 02:12:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45041', 'proveedor', '24340', NULL, '32efff7390df568cc87f3d39c5f768040e0f5150cfe18db3377a25564fae19b7', '53', NULL, 'activo', 'motor', 'diesel', '347.180', '9373.86', '2026-07-08 15:28:09', '2026-07-08 21:28:09+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45042', 'proveedor', '24341', NULL, '214487fe9e431949b8bb4bfeb0a8cfce7bffcb12ca75421c230aa1d49474e3be', '53', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-08 15:12:58', '2026-07-08 21:12:58+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45043', 'proveedor', '24342', NULL, '2081f02af93ac131a5320acaecdd53a5866e13ae95817621355bc5a93231fa36', '44', NULL, 'activo', 'indeterminado', 'diesel', '72.980', '1970.43', '2026-07-08 15:08:00', '2026-07-08 21:08:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45044', 'proveedor', '24343', NULL, 'e906036e6c0043ce981ee5047521c1469a5941605726fbc3e2730472f80ec98b', '44', NULL, 'activo', 'indeterminado', 'diesel', '236.980', '6398.57', '2026-07-08 15:05:00', '2026-07-08 21:05:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45045', 'proveedor', '24344', NULL, '917a0c8adfb2969684c5adb267a5e0614d369cfe58a052ac68d2de5b11455e0c', '9', NULL, 'activo', 'indeterminado', 'diesel', '48.330', '1304.83', '2026-07-08 14:48:00', '2026-07-08 20:48:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45046', 'proveedor', '24345', NULL, 'dd32514671d0b0a7295a1c5e5675071db1ac5be44af79718a8c7befcdda17898', '9', NULL, 'activo', 'indeterminado', 'diesel', '264.000', '7128.08', '2026-07-08 14:42:00', '2026-07-08 20:42:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45047', 'proveedor', '24346', NULL, 'e56fc37ec7e8e78630d10845fd7b79fb89d8e5585cf4bc6f236867f00ba8c6c2', '22', NULL, 'activo', 'indeterminado', 'diesel', '77.590', '2094.93', '2026-07-08 14:41:00', '2026-07-08 20:41:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45048', 'proveedor', '24347', NULL, '90a279364ef5ab0b3518b33bd7ff432ada98d43aadf94e56a55fd62776afc42d', '22', NULL, 'activo', 'indeterminado', 'diesel', '59.810', '1614.74', '2026-07-08 14:36:00', '2026-07-08 20:36:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45049', 'proveedor', '24348', NULL, '5d7482b53c0fd04679e7f2f14042686cfd8b37ce0cc6d3c79bd87527ef52fd3b', NULL, NULL, 'cuarentena', NULL, 'diesel', '40.000', '1080.00', '2026-07-08 01:19:00', '2026-07-08 07:19:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45050', 'proveedor', '24349', NULL, '6db16bf6fb6e2a956c111d64760809098a4e93d259951807624e18801136c16a', '41', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-08 01:13:00', '2026-07-08 07:13:00+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45051', 'proveedor', '24350', NULL, '4965ef8715a04dc71895f465d13cae1c6717eb4e2270040fbcddc1377e09baf5', '10', NULL, 'activo', 'indeterminado', 'diesel', '20.010', '540.19', '2026-07-07 22:27:40', '2026-07-08 04:27:40+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45052', 'proveedor', '24351', NULL, 'a3cfd0a1c2040c5b433d3eab71ccce8566187ec513310a3d09df9c1c531268d7', '10', NULL, 'activo', 'indeterminado', 'diesel', '345.000', '9315.05', '2026-07-07 22:23:25', '2026-07-08 04:23:25+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45053', 'proveedor', '24352', NULL, '97db3a03d53b41c13f1ae53b9f20f331a45265488ab27fe9c7babadff13379dc', '18', NULL, 'activo', 'indeterminado', 'diesel', '46.000', '1242.00', '2026-07-07 12:18:34', '2026-07-07 18:18:34+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45054', 'proveedor', '24353', NULL, '28f87a212df1f42435b983f5133550025ff96b4ac2c22506466c6f5bad072920', '18', NULL, 'activo', 'indeterminado', 'diesel', '80.000', '2160.00', '2026-07-07 12:16:12', '2026-07-07 18:16:12+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45055', 'proveedor', '24354', NULL, '8959d9d8c3b8a475d8913d7d32af0fd7bc76213a4c25ba635f0dc3da70f8240b', '46', NULL, 'activo', 'motor', 'diesel', '110.000', '2970.00', '2026-07-07 11:23:00', '2026-07-07 17:23:00+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45056', 'proveedor', '24355', NULL, 'f0ad0931fa9be86f1d6db166842202764ac8bc7d641d06af54d4de9e5ec1193e', '46', NULL, 'activo', 'motor', 'diesel', '370.000', '9990.00', '2026-07-07 11:17:00', '2026-07-07 17:17:00+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45057', 'proveedor', '24356', NULL, '07c0fca9c3cc21551df9c62d6eafc4cbf020e99e8ec52c54fb4a18625804e668', '9', NULL, 'activo', 'indeterminado', 'diesel', '99.130', '2774.65', '2026-07-07 09:50:07', '2026-07-07 15:50:07+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45058', 'proveedor', '24357', NULL, '90b761e256dcca587e54a9d36f3673666644819c1cae2230894e219f6403ccec', '55', NULL, 'activo', 'motor', 'diesel', '366.670', '9900.00', '2026-07-07 07:32:37', '2026-07-07 13:32:37+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45059', 'proveedor', '24358', NULL, '0f12fbe498f2034c3b79c85758540ff1f8a82ce49e4c419ff6ea7ff16ff2b394', '55', NULL, 'activo', 'motor', 'diesel', '232.780', '6285.17', '2026-07-07 07:06:51', '2026-07-07 13:06:51+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45060', 'proveedor', '24359', NULL, '989eb71428668caef8034e37c0e5b923d7538403891f74679554aaf509d6aa1b', NULL, NULL, 'cuarentena', NULL, 'diesel', '40.000', '1080.00', '2026-07-07 00:33:19', '2026-07-07 06:33:19+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45061', 'proveedor', '24360', NULL, '8409840f3d8a5ce78d0ab33b74ee2792db3895d747ca556fe82490395496ca1b', NULL, NULL, 'cuarentena', NULL, 'diesel', '40.000', '1080.00', '2026-07-07 00:28:35', '2026-07-07 06:28:35+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45062', 'proveedor', '24361', NULL, 'b6a5ea746bafeb2d47a26b4426cbeaff21d2616fb67a41df0e521e842372ba6b', '41', NULL, 'activo', 'motor', 'diesel', '298.000', '8046.00', '2026-07-07 00:24:13', '2026-07-07 06:24:13+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45063', 'proveedor', '24362', NULL, 'b79aa6c9006002a63662b99a3fe6b61e7b58abbaae81687d4b9e52c91ad3ad83', '41', NULL, 'activo', 'motor', 'diesel', '351.880', '9500.76', '2026-07-07 00:15:01', '2026-07-07 06:15:01+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45064', 'proveedor', '24363', NULL, 'bf1c5993ef1593ded6babf77f2da16b4efd1debfdd6e5d3f4770d598f7b3725c', '25', NULL, 'activo', 'motor', 'diesel', '100.000', '2700.00', '2026-07-07 00:04:59', '2026-07-07 06:04:59+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45065', 'proveedor', '24364', NULL, '89e6e42636d1c0ab5261cd10cbd8d95c4d65391326d3d194653ccf9bde4e2013', '25', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-07 00:01:19', '2026-07-07 06:01:19+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45066', 'proveedor', '24365', NULL, '7d04225c76af34be990fbe896be1c22ca6c8be9ae644564f1a35e06c9ef09566', '41', NULL, 'activo', 'motor', 'diesel', '200.000', '5400.00', '2026-07-06 19:59:58', '2026-07-07 01:59:58+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45067', 'proveedor', '24366', NULL, '1d16ce43837ddbb6fcd21a3101c98516bca5f22b88f4af160934e557f4d0869c', NULL, NULL, 'cuarentena', NULL, 'diesel', '40.000', '1080.00', '2026-07-06 15:59:11', '2026-07-06 21:59:11+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45068', 'proveedor', '24367', NULL, 'ccb08682efe3c50d3e2b1914f76b95e0eceb522f86fb8dfd734840de3e08791d', '3', NULL, 'activo', 'motor', 'diesel', '264.000', '7128.00', '2026-07-06 15:55:16', '2026-07-06 21:55:16+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45069', 'proveedor', '24368', NULL, 'f06d4c9b0ad00c0dc00ca35b19ec8d57b6dc4783b9d06f6e5a8b1c94f1e14b13', '3', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-06 15:48:39', '2026-07-06 21:48:39+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45070', 'proveedor', '24369', NULL, '8d655faef9c2a32f4fa93a28bdbe820a7c55dc57c8ff96c8f86b9287d7019ddf', NULL, '31', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-06 10:10:00', '2026-07-06 16:10:00+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45072', 'proveedor', '24371', NULL, '24b87604ca1644e5c5e0126ffeee339c6c1e3ca97a1db956b35ae7faf436b59b', '10', NULL, 'activo', 'indeterminado', 'diesel', '58.700', '1585.01', '2026-07-06 09:37:00', '2026-07-06 15:37:00+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45073', 'proveedor', '24372', NULL, 'bd45edd4e3a58712a8f99b319c7cb116809c5de314968801341b0f0204fb2ec5', '10', NULL, 'activo', 'indeterminado', 'diesel', '38.010', '1026.46', '2026-07-06 09:32:00', '2026-07-06 15:32:00+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45074', 'proveedor', '24373', NULL, 'e76469924f6f3f7ccdc05e3f3ca745a05c82e5c55add3d7d6cd1dcfb2a646c23', '10', NULL, 'activo', 'indeterminado', 'diesel', '370.370', '9999.99', '2026-07-06 09:29:00', '2026-07-06 15:29:00+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45075', 'proveedor', '24374', NULL, '1a79e6c99adb1a7fe4fc221131a21752d999850eff3c5bd415ad8b823c7666ad', '21', NULL, 'activo', 'indeterminado', 'diesel', '97.370', '2628.96', '2026-07-05 22:59:00', '2026-07-06 04:59:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45076', 'proveedor', '24375', NULL, 'c4f99b3e1f7979bd0642e9c21257c23d4643b6192b11cfdc28d854c9639ceb5c', '21', NULL, 'activo', 'indeterminado', 'diesel', '108.940', '2941.27', '2026-07-05 22:54:00', '2026-07-06 04:54:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45077', 'proveedor', '24376', NULL, 'e9599dd16ca8a794345578b3a9e0f6e19d623938ad00c6eae0cbcfbb146728ee', '34', NULL, 'activo', 'motor', 'diesel', '257.000', '7501.83', '2026-07-05 13:44:00', '2026-07-05 19:44:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45078', 'proveedor', '24377', NULL, '60f9e8add30e68fc9d7265770a8ef0e5c3dda7a25d7fd2c2c0feaf5c9cd8c374', '34', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-05 13:35:00', '2026-07-05 19:35:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45079', 'proveedor', '24378', NULL, 'da073ed72441448845bcf944d171a88ecb4f9363432e1e297cbdea7219abd26b', '8', NULL, 'activo', 'indeterminado', 'diesel', '11.060', '298.49', '2026-07-05 09:10:00', '2026-07-05 15:10:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45080', 'proveedor', '24379', NULL, '2ba9336d8659e6146ddd1faf425e683c236706ad9732831b741778366ef182be', '8', NULL, 'activo', 'indeterminado', 'diesel', '185.730', '5014.82', '2026-07-05 09:06:00', '2026-07-05 15:06:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45081', 'proveedor', '24380', NULL, 'b8fdc032dd908e1ab2bac0afc9ed7e395fed88ccc7669d5d5569119117ab2507', '8', NULL, 'activo', 'indeterminado', 'diesel', '1.000', '27.00', '2026-07-05 08:32:00', '2026-07-05 14:32:00+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45082', 'proveedor', '24381', NULL, 'eda0d0faa09751a904425c4194241ce6e73b31a00eb93cb05e514bc3b6e3041e', '52', NULL, 'activo', 'indeterminado', 'diesel', '55.560', '1500.15', '2026-07-04 17:58:00', '2026-07-04 23:58:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45083', 'proveedor', '24382', NULL, '4e4e1b69b96884efb5f086319eb533c180043f07b41591af30af738fc191ddf7', '52', NULL, 'activo', 'indeterminado', 'diesel', '95.920', '2589.87', '2026-07-04 17:54:00', '2026-07-04 23:54:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45084', 'proveedor', '24383', NULL, '2104bed1b1ec0c24d96b5280b418e21c320e243b31a28f088028f25123645ec1', '19', NULL, 'activo', 'indeterminado', 'diesel', '146.020', '4087.13', '2026-07-04 16:23:01', '2026-07-04 22:23:01+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45085', 'proveedor', '24384', NULL, 'a20d2a20ba36ff2342381247f4e6c0e7d0b257e2632c6c6a373c3e45d1c46f35', '19', NULL, 'activo', 'indeterminado', 'diesel', '100.000', '2799.00', '2026-07-04 16:16:20', '2026-07-04 22:16:20+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45086', 'proveedor', '24385', NULL, '2b6ae8d6bac80967c412c6d4b26eb0200012693b8bfca6664c9971e3e218aaa0', '18', NULL, 'activo', 'indeterminado', 'diesel', '160.000', '4320.00', '2026-07-04 15:03:00', '2026-07-04 21:03:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45087', 'proveedor', '24386', NULL, '4877237ecac96446c274c6aa884a6fc0cfb8e099788525d7a9c26c2d2289cb0b', '18', NULL, 'activo', 'indeterminado', 'diesel', '46.000', '1242.00', '2026-07-04 15:00:00', '2026-07-04 21:00:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45088', 'proveedor', '24387', NULL, '8535b21ff8dcc8017a2003f96b85e7db83426caed20eda9ff5f0b1d6da68df0d', '18', NULL, 'activo', 'indeterminado', 'diesel', '44.000', '1188.00', '2026-07-04 14:57:00', '2026-07-04 20:57:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45089', 'proveedor', '24388', NULL, '62f6441aca06b3349e91adddb8a94da17d2b0807587c97383b799a4cd3996943', '18', NULL, 'activo', 'indeterminado', 'diesel', '20.000', '540.00', '2026-07-04 14:55:00', '2026-07-04 20:55:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45090', 'proveedor', '24389', NULL, '68dc715d82ff7e44b0b79a75332ef6057def285b4c9468356e0acb92ad1fda16', '44', NULL, 'activo', 'indeterminado', 'diesel', '67.050', '1810.40', '2026-07-04 10:06:10', '2026-07-04 16:06:10+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45091', 'proveedor', '24390', NULL, '118baeaa850d68fcb15ed129c8ec8b4e1306a526df2dc90021f12bc5d36fb0b3', '44', NULL, 'activo', 'indeterminado', 'diesel', '289.280', '7810.67', '2026-07-04 10:02:38', '2026-07-04 16:02:38+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45092', 'proveedor', '24391', NULL, 'f18e9a3105b6b4744cd80771ec6c346b40c43742c1c6a40cb3f5604589cc4e54', '56', NULL, 'activo', 'motor', 'diesel', '207.000', '6042.33', '2026-07-03 23:28:00', '2026-07-04 05:28:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45093', 'proveedor', '24392', NULL, '0fe6d6d34c4b073617a41090549a4bc21834e886b770fe211201c95f1cddad5a', '56', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-03 23:19:00', '2026-07-04 05:19:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45094', 'proveedor', '24393', NULL, '8e54b5bd91b83eed2c6f8e59faacb6e922291dc23595883ebf09601091eb0826', '1', NULL, 'activo', 'motor', 'diesel', '46.880', '1289.17', '2026-07-03 20:50:00', '2026-07-04 02:50:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45095', 'proveedor', '24394', NULL, 'ba197bd70ccebdd8a6d3d0bd6cbc0b23ff5d9f1b75273bdbafadad8b5a8a61b0', '1', NULL, 'activo', 'motor', 'diesel', '363.640', '9999.99', '2026-07-03 20:47:00', '2026-07-04 02:47:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45096', 'proveedor', '24395', NULL, 'abdb7a72810ed85d2bf42e9723a45a870b3753fc29316c71b1f5873b9d052ca8', '8', NULL, 'activo', 'indeterminado', 'diesel', '35.960', '970.89', '2026-07-03 20:26:24', '2026-07-04 02:26:24+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45097', 'proveedor', '24396', NULL, '21cdeab1fb80617f96edb8fc474822ece3867777d1183c0e8a0f93cdcf93b138', '8', NULL, 'activo', 'indeterminado', 'diesel', '80.920', '2184.76', '2026-07-03 20:15:39', '2026-07-04 02:15:39+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45098', 'proveedor', '24397', NULL, '72af595c1cba715b7c7b8a6da610356f488e5293a10d3498e5ab37db41cf0bfa', '40', NULL, 'activo', 'motor', 'diesel', '367.410', '9990.00', '2026-07-03 18:28:46', '2026-07-04 00:28:46+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45099', 'proveedor', '24398', NULL, '7ed181647005328b7fbf9c975c5990492e697adcc6c379f8b92b4fe969a320d0', '40', NULL, 'activo', 'motor', 'diesel', '132.590', '3605.01', '2026-07-03 18:23:53', '2026-07-04 00:23:53+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45100', 'proveedor', '24399', NULL, '5ceb3e13fbf4d84fcc81d93da78096d007dee580f8fd3b5177a4533310219d2d', NULL, '24', 'activo', 'termo', 'diesel', '115.000', '3103.85', '2026-07-03 12:39:00', '2026-07-03 18:39:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45101', 'proveedor', '24400', NULL, 'ec3beb06e257fd8c2acfddedfc2d6d8db18732235e3c860bd14bf2b4ec65e187', '1', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-03 12:32:00', '2026-07-03 18:32:00+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45102', 'proveedor', '24401', NULL, 'f8a0ada9f27237e0001a4bc790e91b98b84658d644eafdf023e4b42ba8361094', NULL, '45', 'activo', 'termo', 'diesel', '87.030', '2349.81', '2026-07-03 04:48:29', '2026-07-03 10:48:29+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45103', 'proveedor', '24402', NULL, '9a4114471b8530ff32f7207408ad163239040cedfa768e8bc96c31993bc3f7ae', '25', NULL, 'activo', 'motor', 'diesel', '264.000', '7128.00', '2026-07-03 04:37:24', '2026-07-03 10:37:24+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45104', 'proveedor', '24403', NULL, '7c56d6c74ecd86c6c9b164a92128372f453fa590d9acc04b2477d2bfb1fc0d26', '25', NULL, 'activo', 'motor', 'diesel', '366.000', '9882.00', '2026-07-03 04:22:20', '2026-07-03 10:22:20+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45105', 'proveedor', '24404', NULL, '1c478cf451f569ffcb94ef1d446ff14722544e19dd4fd7b69b975e612742b787', NULL, '48', 'activo', 'termo', 'diesel', '140.000', '4086.60', '2026-07-02 23:42:00', '2026-07-03 05:42:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45106', 'proveedor', '24405', NULL, 'dc16a216e1129cfbfe328afb7f3404177d2371be50a1c39e9fa46d08f8983eab', '45', NULL, 'activo', 'motor', 'diesel', '57.000', '1663.83', '2026-07-02 23:25:00', '2026-07-03 05:25:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45107', 'proveedor', '24406', NULL, 'f5f5e783e40050b9e4699bff11201c42c8eb590e819aeed488df2340ee0bdde5', '45', NULL, 'activo', 'motor', 'diesel', '342.550', '9999.00', '2026-07-02 23:19:00', '2026-07-03 05:19:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45110', 'proveedor', '24409', NULL, '240d58e1ffe8e6ad226c3e6b77dcc3e8cc09c06b4c8f1979392956efe846ad80', '8', NULL, 'activo', 'indeterminado', 'diesel', '21.120', '570.24', '2026-07-02 20:01:52', '2026-07-03 02:01:52+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45111', 'proveedor', '24410', NULL, 'c959ef90bbb823b39016ff23a8e8a19cb8a672ec931ea5b7a42d2965dccdcae8', '8', NULL, 'activo', 'indeterminado', 'diesel', '96.270', '2599.29', '2026-07-02 19:52:37', '2026-07-03 01:52:37+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45112', 'proveedor', '24411', NULL, '3db94069a8c0129926e3fc71d07663bef47759fa7152c36d40c7e3dbacf27ee8', '34', NULL, 'activo', 'motor', 'diesel', '630.000', '17010.00', '2026-07-02 16:22:00', '2026-07-02 22:22:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45113', 'proveedor', '24412', NULL, 'e90fb842bc3376b0567bccff4e7c9f34b7433fd3c47c25cb27632541dff7ebcc', '52', NULL, 'activo', 'indeterminado', 'diesel', '54.290', '1465.80', '2026-07-02 13:18:00', '2026-07-02 19:18:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45114', 'proveedor', '24413', NULL, '8e3196c1d1e0fd760df3d57c3845a3371a3927aac428f98cfdc539ffd44c5fc9', '52', NULL, 'activo', 'indeterminado', 'diesel', '102.610', '2770.34', '2026-07-02 13:13:00', '2026-07-02 19:13:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45115', 'proveedor', '24414', NULL, '06fcbfc484b90ce0b1ce29fda5ed8c9f05dbd021cecc62581e8921fb12567fe3', NULL, NULL, 'cuarentena', NULL, 'diesel', '172.570', '4743.95', '2026-07-02 09:45:00', '2026-07-02 15:45:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45116', 'proveedor', '24415', NULL, '999147e1f9c5df7e97ccfba0fdc70c039400ce0a75540a38364310aea2998357', '28', NULL, 'activo', 'indeterminado', 'diesel', '59.570', '1608.31', '2026-07-02 09:05:00', '2026-07-02 15:05:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45117', 'proveedor', '24416', NULL, '9226341964ec568749f1378486e614bf88ba91a118362be170b8a380a60e1fe5', '28', NULL, 'activo', 'indeterminado', 'diesel', '212.310', '5732.32', '2026-07-02 08:55:00', '2026-07-02 14:55:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45118', 'proveedor', '24417', NULL, 'cfb4902bb47777077ad8ed000200753538153792b15782a1d71e746e03951416', '46', NULL, 'activo', 'motor', 'diesel', '500.000', '13745.00', '2026-07-02 08:31:00', '2026-07-02 14:31:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45119', 'proveedor', '24418', NULL, '942ecd660126d41209c1889b7029ce7dbfe456f6301e84cf735acaec741f6186', NULL, '52', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-02 07:09:00', '2026-07-02 13:09:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45120', 'proveedor', '24419', NULL, 'e9003dcdbea03b96982cb07af7d5858179bd918f8175856fd9d68fb0f2d79389', '3', NULL, 'activo', 'motor', 'diesel', '60.000', '1620.00', '2026-07-02 07:04:00', '2026-07-02 13:04:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45121', 'proveedor', '24420', NULL, '804723915cc3b56c014c11ef8ab9f7a9e9a5ca471b2a4c64457f0ef9b6718ffe', '3', NULL, 'activo', 'motor', 'diesel', '370.000', '9990.00', '2026-07-02 07:01:00', '2026-07-02 13:01:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45122', 'proveedor', '24421', NULL, '5c359cc980120d54f435508842734cccb09ccbc8896ecb3fcc5fab388970a93d', '1', NULL, 'activo', 'motor', 'diesel', '580.000', '15596.20', '2026-07-02 05:42:00', '2026-07-02 11:42:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45123', 'proveedor', '24422', NULL, 'bf4c20d86899c963aa203bf54b8215ef8c3705c64dbfd3eb47bcdd6d20209094', '43', NULL, 'activo', 'motor', 'diesel', '480.000', '13195.20', '2026-07-01 21:29:00', '2026-07-02 03:29:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45124', 'proveedor', '24423', NULL, 'ca808e3f50f2207ae10f459fc7ee1eb5220633dbed529fb896573f352e04f2e3', '18', NULL, 'activo', 'indeterminado', 'diesel', '40.000', '1080.00', '2026-07-01 19:31:09', '2026-07-02 01:31:09+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45125', 'proveedor', '24424', NULL, '1e62f2c1a5d1e025fe2d36db57864c26a082414dd168b50cf6084943eff4b755', '18', NULL, 'activo', 'indeterminado', 'diesel', '60.220', '1625.94', '2026-07-01 19:28:03', '2026-07-02 01:28:03+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45126', 'proveedor', '24425', NULL, 'c8549d677afb91ada4f15a817399120568bcc77ec6fc0f147be0108947c6ea48', NULL, '7', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-01 17:18:59', '2026-07-01 23:18:59+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45127', 'proveedor', '24426', NULL, '18590150d10b6b7dc46ebc840e7648c832ea3f3968cede533277fdc5c124a4fb', '37', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-01 17:14:12', '2026-07-01 23:14:12+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45129', 'proveedor', '24428', NULL, '751de84acdf21a36dfaffc8002d4c6e31ed0379119b9a1c2bb979bf354ae214b', '46', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-01 16:10:00', '2026-07-01 22:10:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45130', 'proveedor', '24429', NULL, 'a3171493f259ea600bd760e0b9524d3ef271e3f03e5b80cbbc97f77eb7c001d4', '30', NULL, 'activo', 'motor', 'diesel', '250.000', '6872.50', '2026-07-01 15:19:00', '2026-07-01 21:19:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45131', 'proveedor', '24430', NULL, 'd93c66f5c45b238d8d391093a7b0e34856b7490ffca178c52d0b7410398addc2', '44', NULL, 'activo', 'indeterminado', 'diesel', '23.510', '634.74', '2026-07-01 10:24:47', '2026-07-01 16:24:47+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45132', 'proveedor', '24431', NULL, '252a122f11502f871bb464009d3a2d98d561d9aedacd7d66c4785855a88721e0', '44', NULL, 'activo', 'indeterminado', 'diesel', '85.040', '2296.05', '2026-07-01 10:20:45', '2026-07-01 16:20:45+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45133', 'proveedor', '24432', NULL, '244fea8688eccdafa1ae663d6797fe045aa834b210628894bcafc91fb56a5642', '21', NULL, 'activo', 'indeterminado', 'diesel', '68.150', '1839.94', '2026-07-01 09:30:00', '2026-07-01 15:30:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45134', 'proveedor', '24433', NULL, 'c5cbacf2ddae1f564a44af7aab2b08c91c9397e9d9a334abf8bedd9f20fbc8d7', '21', NULL, 'activo', 'indeterminado', 'diesel', '98.080', '2648.13', '2026-07-01 09:24:00', '2026-07-01 15:24:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45135', 'proveedor', '24434', NULL, '87f4d494b4e22974bfc4baa31200dad7b8396d8ec3ed782af915a564106566df', '3', NULL, 'activo', 'motor', 'diesel', '300.000', '8247.00', '2026-07-01 04:37:00', '2026-07-01 10:37:00+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45136', 'proveedor', '24435', NULL, 'cb7036ad5ec5492804ab0ff8a0e11341bce4fc9fce63d8ff8b19c6181f435e5a', '55', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-01 02:51:18', '2026-07-01 08:51:18+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45137', 'proveedor', '24436', NULL, '76002d3cbdf5071ca8c0364c32e3f8a4d8255c10c309060cba6cbd6a48b380cf', NULL, '3', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-01 02:55:56', '2026-07-01 08:55:56+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45138', 'proveedor', '24437', NULL, '26b35827a4baeba6efbb92857eaf3533f1d9daf5966c1cd537d3a026efa9eba3', NULL, '23', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-01 06:27:18', '2026-07-01 12:27:18+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45139', 'proveedor', '24438', NULL, '8c09066886ee23e725418381c13efdee3f76a78e6cacaf1f461685281899eccd', '41', NULL, 'activo', 'motor', 'diesel', '200.000', '5400.00', '2026-07-01 06:37:32', '2026-07-01 12:37:32+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45140', 'proveedor', '24439', NULL, 'a4723ea873d9186157d0ec266ec0465edd8f323fdcd63b507f9c2fdf5b17ac48', '1', NULL, 'activo', 'motor', 'diesel', '280.000', '7534.80', '2026-07-01 12:28:45', '2026-07-01 18:28:45+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45141', 'proveedor', '24440', NULL, 'e4ab796ca6c57f4184627f4e429df5d2f7d5f74a7207d120cf95c0fa8c12345a', NULL, '24', 'activo', 'termo', 'diesel', '130.000', '3498.30', '2026-07-01 12:36:39', '2026-07-01 18:36:39+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45142', 'proveedor', '24441', NULL, '056dff0e069b2879766deb5921df683f93bc1457e2b913400430e0310b64cd02', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-01 12:42:50', '2026-07-01 18:42:50+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45143', 'proveedor', '24442', NULL, '19ce7a9d764235285421be8f728d485b16d80e129ed1a6ba29f6ff3e19f7427d', NULL, '2', 'activo', 'termo', 'diesel', '170.000', '4590.00', '2026-07-01 16:30:35', '2026-07-01 22:30:35+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45144', 'proveedor', '24443', NULL, 'fb6c685ffabfe7ff7dd6480b53ef69c37f41898b5f3645566bc8ddd9be4eeff4', '34', NULL, 'activo', 'motor', 'diesel', '180.000', '4860.00', '2026-07-01 16:39:42', '2026-07-01 22:39:42+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45145', 'proveedor', '24444', NULL, 'c49cc0e70cdc9f1387c9f7231ca9a5a6c02bc8245ee291ef58f3828568ec0701', '12', NULL, 'activo', 'indeterminado', 'diesel', '60.697', '1638.81', '2026-07-01 16:40:48', '2026-07-01 22:40:48+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45146', 'proveedor', '24445', NULL, '2064bb8a5ff508a47737b4eaabc86eb5befa3826fa256e442b3e2bf7c7e57b34', '12', NULL, 'activo', 'indeterminado', 'diesel', '61.910', '1671.58', '2026-07-01 16:47:11', '2026-07-01 22:47:11+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45147', 'proveedor', '24446', NULL, '758ef86f8d618f4ecb4a8124a17d13ea8cb17a498c770fcc8e7b7029296ad473', '45', NULL, 'activo', 'motor', 'diesel', '280.000', '7697.20', '2026-07-01 17:53:21', '2026-07-01 23:53:21+00', '2026-07-01', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45148', 'proveedor', '24447', NULL, '1f8946ad03e822f5d39132c47f34340d2286db1826403a737b4324cfced83043', '30', NULL, 'activo', 'motor', 'diesel', '243.680', '6747.50', '2026-07-02 05:33:14', '2026-07-02 11:33:14+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45149', 'proveedor', '24556', NULL, 'e87f54f1cb70907da0ad3effabaf78e63de8f2587ee6dd7482a5fb9e1a85c799', '12', NULL, 'activo', 'indeterminado', 'diesel', '39.937', '1078.30', '2026-07-13 12:53:24', '2026-07-13 18:53:24+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45150', 'proveedor', '24448', NULL, '72563252f622b4a0e65e15e8c6b8ae1cee3ff213a1f45b5dfdb57c9c8ad6e31c', NULL, '36', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-02 07:05:52', '2026-07-02 13:05:52+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45151', 'proveedor', '24449', NULL, 'fa58ae99f8b39483419a4f07fa491e286bf53f37dfdf169566c3986b19a50022', '56', NULL, 'activo', 'motor', 'diesel', '146.208', '4048.50', '2026-07-02 12:32:27', '2026-07-02 18:32:27+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45152', 'proveedor', '24450', NULL, 'b9aece7d2131ee16de33d8bb49c55503102cf1d7167e4b1242ed0b1dc2346ea3', '40', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-02 13:19:04', '2026-07-02 19:19:04+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45153', 'proveedor', '24451', NULL, '42562c6d24520f38460f3906860af6790c7b85fb566dff9837c083d90d2cddab', NULL, '39', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-02 16:11:23', '2026-07-02 22:11:23+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45154', 'proveedor', '24452', NULL, 'e7220d098c9920f57a91e7fa7b5032dfdc52b4dc120d4af63e9c0361a97fa820', NULL, '26', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-02 16:30:13', '2026-07-02 22:30:13+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45155', 'proveedor', '24453', NULL, '650186523fd8ec664df5f66856ffeb1792adbcd7334c85d21d88aece74c12345', NULL, '42', 'activo', 'termo', 'diesel', '143.700', '3878.41', '2026-07-02 16:38:00', '2026-07-02 22:38:00+00', '2026-07-02', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45156', 'proveedor', '24454', NULL, 'ef4aa00748eff11ca3e31d96709c56826c38d23ec9a53784f895e69d18009270', '55', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-03 01:56:58', '2026-07-03 07:56:58+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45157', 'proveedor', '24455', NULL, 'a018ebb465c212118a0c2a69199c2ea3af4e6e09dfedb4c77083eeb75a0339cd', '3', NULL, 'activo', 'motor', 'diesel', '100.000', '2700.00', '2026-07-03 08:33:50', '2026-07-03 14:33:50+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45158', 'proveedor', '24456', NULL, '058a372511d0dc430eb61219e73ac5c71e6c201e38246ed77258241701144cf6', NULL, '52', 'activo', 'termo', 'diesel', '90.000', '2430.00', '2026-07-03 08:43:28', '2026-07-03 14:43:28+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45159', 'proveedor', '24457', NULL, '1475d35beb3b3cd3c44fb29ae3067937d68eac516dadf3cc8b76c42a78d9e50f', NULL, '3', 'activo', 'termo', 'diesel', '146.639', '3959.25', '2026-07-03 12:35:26', '2026-07-03 18:35:26+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45160', 'proveedor', '24458', NULL, '76f7b1772add48a51976afa54970307e9f2ab059e10bd44883705fb046e4ba19', '12', NULL, 'activo', 'indeterminado', 'diesel', '73.490', '1984.22', '2026-07-03 14:37:07', '2026-07-03 20:37:07+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45161', 'proveedor', '24459', NULL, 'f4ab3d52fce6ad051c65a5c975781fdc7b722661b26a9c40618f7fcf47349126', '12', NULL, 'activo', 'indeterminado', 'diesel', '53.129', '1434.49', '2026-07-03 14:42:41', '2026-07-03 20:42:41+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45163', 'proveedor', '24461', NULL, '52c80be7baeacf77320c737e9ae30eb5890f3c7b967a5a1835435bae23cb5973', NULL, '31', 'activo', 'termo', 'diesel', '130.000', '3508.70', '2026-07-03 17:40:36', '2026-07-03 23:40:36+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45164', 'proveedor', '24462', NULL, 'b946e86a0f2f615d659de5ce10c7bea907fa331d2b98e0c924ae87edf8549ed2', NULL, '2', 'activo', 'termo', 'diesel', '100.000', '2699.00', '2026-07-03 22:03:15', '2026-07-04 04:03:15+00', '2026-07-03', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45165', 'proveedor', '24463', NULL, '47de15d92cf0e143c8c1d400a46e81eaa744be8d830a465afa40b20f64b1c001', '45', NULL, 'activo', 'motor', 'diesel', '230.000', '6210.00', '2026-07-04 00:14:00', '2026-07-04 06:14:00+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45166', 'proveedor', '24464', NULL, '1ea48214de22b23618349a62458f68eede98dac589f36fb7218db7048d734883', NULL, NULL, 'fuera_flota', NULL, 'gasolina', '63.457', '1484.25', '2026-07-04 08:57:24', '2026-07-04 14:57:24+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45167', 'proveedor', '24465', NULL, 'a117594bdd94893511a95ff8e973abc7c049d2f5c92ab2cb792397c4714c0d99', NULL, '36', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-04 10:55:30', '2026-07-04 16:55:30+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45168', 'proveedor', '24466', NULL, 'e01d534f7de61ac5c123a2b41ad86a8a49d3254290ed8c73bf7d07a547a24e9e', NULL, '39', 'activo', 'termo', 'diesel', '140.000', '3780.00', '2026-07-04 12:32:20', '2026-07-04 18:32:20+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45169', 'proveedor', '24467', NULL, 'e928a3464f60722a8ce943b7e4127b4c3210c9a0f1137ecd63f3be572c644fea', '29', NULL, 'activo', 'indeterminado', 'diesel', '33.874', '914.25', '2026-07-04 14:12:47', '2026-07-04 20:12:47+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45170', 'proveedor', '24468', NULL, 'c7cb0f667fdb370092e9b748079d3f47f577dcab7e90fa6843798d5148bfe5eb', '29', NULL, 'activo', 'indeterminado', 'diesel', '122.992', '3319.55', '2026-07-04 14:21:40', '2026-07-04 20:21:40+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45171', 'proveedor', '24469', NULL, '3ccd4559c9eaef6370975c05b6cb762360d5e2a8cce9c7fc11a4191535ecd841', '3', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-04 16:01:13', '2026-07-04 22:01:13+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45172', 'proveedor', '24470', NULL, '66a5c13e76f9db569896d9cb48e1de54d3a72a61bb6e5642945e6ea6d1be18fa', '56', NULL, 'activo', 'motor', 'diesel', '100.000', '2700.00', '2026-07-04 19:35:30', '2026-07-05 01:35:30+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45173', 'proveedor', '24471', NULL, '285ab1b60fa9ddf630946b41ab0500465f5fff668fb62ab19be29fb01f0836f5', NULL, '7', 'activo', 'termo', 'diesel', '135.000', '3645.00', '2026-07-04 19:45:22', '2026-07-05 01:45:22+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45174', 'proveedor', '24472', NULL, 'cc96cfef3b8a52bc2a745a40e8a412deb4e9aac72a7768b6ec033ef608577c35', '38', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-04 23:06:34', '2026-07-05 05:06:34+00', '2026-07-04', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45175', 'proveedor', '24473', NULL, 'd231c1352549dd0868a2fb16172db5f11a3998bc46f17a2024efc26dbe248e37', '13', NULL, 'activo', 'motor', 'diesel', '380.000', '10256.20', '2026-07-05 06:38:29', '2026-07-05 12:38:29+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45176', 'proveedor', '24474', NULL, 'c4586255c5b83fc926a2dd1a476b1f115355fc42ce4d0fcf9b4d022d97d8df07', NULL, '8', 'activo', 'termo', 'diesel', '120.000', '3238.80', '2026-07-05 06:43:36', '2026-07-05 12:43:36+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45177', 'proveedor', '24475', NULL, 'd21d9ac3f49f9879bf28ca00d87df32ff4befd1acbe80e45d73b3644cae1d9f3', '25', NULL, 'activo', 'motor', 'diesel', '380.000', '10218.20', '2026-07-05 06:48:17', '2026-07-05 12:48:17+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45178', 'proveedor', '24476', NULL, 'f269c9330259a39ec0d292f7c7972343014fe9f371f0a899fb192426b66c07b9', NULL, '45', 'activo', 'termo', 'diesel', '130.000', '3495.70', '2026-07-05 06:59:05', '2026-07-05 12:59:05+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45179', 'proveedor', '24477', NULL, '5b1cade16e30391c2681b08d3a7b28e900ede74caacd5e90ab702edcc7f515bf', '42', NULL, 'activo', 'indeterminado', 'diesel', '106.430', '2873.62', '2026-07-05 09:21:51', '2026-07-05 15:21:51+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45180', 'proveedor', '24478', NULL, '660d8a4df701a8b82753614bb4a6087574bec6270f73398f1ae431a6b208f3b4', '41', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-05 09:27:03', '2026-07-05 15:27:03+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45181', 'proveedor', '24479', NULL, '70424c682486fca7b8b756f18cb5327d45a7c613db63eb4156dd4431b1804693', NULL, '23', 'activo', 'termo', 'diesel', '138.000', '3726.00', '2026-07-05 09:33:34', '2026-07-05 15:33:34+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45182', 'proveedor', '24480', NULL, '4b67bdb06e545ae36b613b1d97825c82838f94d3a97220e41dbf7ed0b058b47e', NULL, '15', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-05 09:44:02', '2026-07-05 15:44:02+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45183', 'proveedor', '24481', NULL, '10ae7b8cfa05021ebcf28ac4975cf70565cde73ce35178c7b25576ba266c0eab', '3', NULL, 'activo', 'motor', 'diesel', '280.000', '7557.20', '2026-07-05 14:21:58', '2026-07-05 20:21:58+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45184', 'proveedor', '24482', NULL, '6f5a0ab14581dc39218f41a4343f2e297d66722a7223f392b693b86b9e530e62', '1', NULL, 'activo', 'motor', 'diesel', '350.000', '9446.50', '2026-07-05 14:33:23', '2026-07-05 20:33:23+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45185', 'proveedor', '24483', NULL, '60b3df821a6a741208783812a44492bedb7d2d4d32ad9605c83f3a748a15c08c', '13', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-05 17:12:17', '2026-07-05 23:12:17+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45186', 'proveedor', '24484', NULL, '5261ad4c61fca3272d6db4687d12fb7205475b9eb5f84f137d6aadf540ccafd0', '42', NULL, 'activo', 'indeterminado', 'diesel', '87.363', '2358.80', '2026-07-05 17:12:36', '2026-07-05 23:12:36+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45187', 'proveedor', '24485', NULL, '2d3c881fbe40467e8dd1ba5e001d0ad2db92bc3509132c95f46cdba8d9fbef87', '42', NULL, 'activo', 'indeterminado', 'diesel', '14.546', '392.74', '2026-07-05 17:20:06', '2026-07-05 23:20:06+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45188', 'proveedor', '24486', NULL, 'a59c8f3b7ded37e7d604b9e434d6b3f8420df35310e75c50460a0961e7ba1ba3', '41', NULL, 'activo', 'motor', 'diesel', '450.000', '12370.50', '2026-07-05 20:07:52', '2026-07-06 02:07:52+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45189', 'proveedor', '24487', NULL, '0b6a5f78d59a52ef306b71a934b103677f2a6f5e73b671ccf88cc7b57083e55f', '38', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-05 21:05:49', '2026-07-06 03:05:49+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45190', 'proveedor', '24488', NULL, '111ede952f5e8453376a01e01fdb92660278b1414a63fb00bd7a58c0a126ddb4', '46', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-05 23:04:19', '2026-07-06 05:04:19+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45191', 'proveedor', '24489', NULL, 'b365372a8d66f6919af7674f83351b356a5b79e94172d528da63254b79ac339d', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2700.00', '2026-07-05 23:12:31', '2026-07-06 05:12:31+00', '2026-07-05', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45192', 'proveedor', '24490', NULL, '3ec1682fc9055e08fef8fb0766fa6a238f2ded5e05fa48d351dc487b68eb82b0', NULL, '26', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-06 05:23:19', '2026-07-06 11:23:19+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45193', 'proveedor', '24491', NULL, 'a0248205fe0e739b707e3b64655ae7f94fe8f7eda90031ee748f36b39c6023f9', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2700.00', '2026-07-06 05:45:17', '2026-07-06 11:45:17+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45194', 'proveedor', '24492', NULL, '19821f555377a38960d097ffefcb74ef2c598d37d1b6adedc71e2dc1c7c556e0', '12', NULL, 'activo', 'indeterminado', 'diesel', '37.607', '1015.38', '2026-07-06 06:03:58', '2026-07-06 12:03:58+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45195', 'proveedor', '24493', NULL, '9df15263ed61d5b686624692d8cefd56ac352d720d490f570d91705ba0f056df', '12', NULL, 'activo', 'indeterminado', 'diesel', '107.247', '2895.66', '2026-07-06 06:11:55', '2026-07-06 12:11:55+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45196', 'proveedor', '24494', NULL, 'f9ca3b68963db3bad0614c7eb5f348f618bf9e63bfeb2bf38c6b1952528f96e3', '34', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-06 16:19:14', '2026-07-06 22:19:14+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45197', 'proveedor', '24495', NULL, '35ece22626ad0b5d748228715046cde6a886d30ace58ce86fad5a6c576aaef60', '1', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-06 22:27:29', '2026-07-07 04:27:29+00', '2026-07-06', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45198', 'proveedor', '24496', NULL, '63e89ef1d4c238cb4548200fc84533415e9337eed04e422e64a6da672ec0915a', NULL, '3', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-07 01:20:54', '2026-07-07 07:20:54+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45199', 'proveedor', '24513', NULL, 'd2e5aefa57aefe393ef781a7b64f03434507e4b7256dc62ffbdbdbef0752af40', NULL, '29', 'activo', 'termo', 'diesel', '170.000', '4590.00', '2026-07-08 17:11:04', '2026-07-08 23:11:04+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45200', 'proveedor', '24497', NULL, '9c7c9e8047962f799706adf0ac28c1480b9689efeb5c473886bc59f45c407cc4', '56', NULL, 'activo', 'motor', 'diesel', '262.137', '7077.70', '2026-07-07 02:35:17', '2026-07-07 08:35:17+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45201', 'proveedor', '24498', NULL, '041918c4d7eaefcb6969fc31dd0487bbd64d2a8289fa17d71f1774bf14493453', NULL, '7', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-07 02:46:33', '2026-07-07 08:46:33+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45202', 'proveedor', '24499', NULL, '3dc370fd56d4741636c193e782a0929f19642d4fd3c482083163e184b7a5aa6e', NULL, '36', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-07 07:51:54', '2026-07-07 13:51:54+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45203', 'proveedor', '24500', NULL, '030631337d509d698261805565e86dc28f9533e9dc30647b7de93e8e818dbff8', '40', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-07 09:11:58', '2026-07-07 15:11:58+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45204', 'proveedor', '24501', NULL, 'e9d16c73d6a54c58f4b2ae7173d661f8f38db2fbeeee41f8b6cba7cebced5b8a', '43', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-07 18:17:25', '2026-07-08 00:17:25+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45205', 'proveedor', '24502', NULL, '0532d81fd70cb39be3f5f7db570347f389dd184ba7dab3077fba0d7364bfe6d6', '12', NULL, 'activo', 'indeterminado', 'diesel', '72.656', '1961.71', '2026-07-07 18:47:13', '2026-07-08 00:47:13+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45206', 'proveedor', '24503', NULL, '25fd0cbbce53ea6cb31c2379acb13e4061a695350bacfd80f8dd0982538e3fdd', '12', NULL, 'activo', 'indeterminado', 'diesel', '82.522', '2228.08', '2026-07-07 18:51:34', '2026-07-08 00:51:34+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45207', 'proveedor', '24504', NULL, '2e63535770702e8cd64df724d7a22fe7ab71a5f91bd34adcde711063fdd23d00', NULL, '42', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-07 22:45:31', '2026-07-08 04:45:31+00', '2026-07-07', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45208', 'proveedor', '24505', NULL, 'a47a7446bc23c0af34ab7d422d8f1b232f9fa53c1517565270953f3d13ad97c6', NULL, NULL, 'cuarentena', NULL, 'diesel', '135.000', '3645.00', '2026-07-08 04:40:26', '2026-07-08 10:40:26+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45209', 'proveedor', '24506', NULL, '0f83036c0281eaf6ff83b0fa921850795b9d520ebf92e655561eb0908d22e819', '56', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-08 09:23:01', '2026-07-08 15:23:01+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45210', 'proveedor', '24507', NULL, '3b5c485319c454e9539570fd5fe42bf40d420072c8797d9dcb375196419acc09', '42', NULL, 'activo', 'indeterminado', 'diesel', '24.817', '670.06', '2026-07-08 10:30:18', '2026-07-08 16:30:18+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45211', 'proveedor', '24508', NULL, '5657d1b9ca80b08b0b2a50d874d6cec363c1b7e04d1af2cf595dadad8f1e8aed', '42', NULL, 'activo', 'indeterminado', 'diesel', '78.618', '2122.69', '2026-07-08 10:46:19', '2026-07-08 16:46:19+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45212', 'proveedor', '24509', NULL, '5695e7b31990ebd73f21ea85c2bde8bd0e630017b8e7a607e190357099491aa0', NULL, '2', 'activo', 'termo', 'diesel', '135.000', '3643.65', '2026-07-08 11:32:40', '2026-07-08 17:32:40+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45213', 'proveedor', '24510', NULL, 'b49bef7e3d880bcc326d25a149371fbe0e1758afdd21e964add8a87dbf3e8cfe', '1', NULL, 'activo', 'motor', 'diesel', '100.000', '2700.00', '2026-07-08 12:49:23', '2026-07-08 18:49:23+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45214', 'proveedor', '24511', NULL, 'b27dd4ac3fedf1228bf81011199238af3c0117b9ad36093737ffc38d5bc95a56', NULL, '24', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-08 12:55:56', '2026-07-08 18:55:56+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45215', 'proveedor', '24512', NULL, '1fbd18f6ce3f813eaa7c9c26ee7a9278bb7ccfb98aeb6aa25809f31cc04cb867', '38', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-08 16:47:53', '2026-07-08 22:47:53+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45216', 'proveedor', '24514', NULL, '8d91af59f0e93225c6af568879bd0b85ae761bb0d072fe12360cf3f1c26af57c', NULL, '45', 'activo', 'termo', 'diesel', '100.000', '2749.00', '2026-07-08 20:41:27', '2026-07-09 02:41:27+00', '2026-07-08', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45217', 'proveedor', '24515', NULL, 'aba0ab16317c14c683b9ce78b37b4cc9065a16501a8c48141d7e6d746965eb75', '43', NULL, 'activo', 'motor', 'diesel', '250.000', '6872.50', '2026-07-09 00:05:29', '2026-07-09 06:05:29+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00');
INSERT INTO public.asientos_consumo (id, origen, carga_id, orden_id, origen_huella, unidad_id, remolque_id, atribucion, destino, combustible, litros, importe, momento_local, momento_ref, fecha_operacion, vigente, evento_id, contable, motivo, proyectado_en) VALUES
('45218', 'proveedor', '24516', NULL, '20ccf0d5794418f98a3ca18dcf2ee388264672595045ccc1757c2a4c406be5c2', NULL, '39', 'activo', 'termo', 'diesel', '130.000', '3573.70', '2026-07-09 00:12:04', '2026-07-09 06:12:04+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45219', 'proveedor', '24517', NULL, '3e5c7ddaf291b6ed3741347b4ed49cfe544545f4cc35f379e5c4ff9d0ba8cbe2', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4050.00', '2026-07-09 01:16:06', '2026-07-09 07:16:06+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45220', 'proveedor', '24518', NULL, '34e213ff95780eac505a12b79dcce8a80d4ef3ef79a80c5f40806ca3445348e4', '56', NULL, 'activo', 'motor', 'diesel', '290.000', '7830.00', '2026-07-09 02:41:55', '2026-07-09 08:41:55+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45221', 'proveedor', '24519', NULL, 'ae6437619c5fee30ab508853d749c1c404ffada4126bddb12c4fe3224e803c04', NULL, '31', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-09 08:14:02', '2026-07-09 14:14:02+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45223', 'proveedor', '24521', NULL, 'fc4b8da5cbf60ceb24d5560aa995f21698e344ed53c0739bdfbc2d7c9c546999', '12', NULL, 'activo', 'indeterminado', 'diesel', '36.215', '977.81', '2026-07-09 11:38:47', '2026-07-09 17:38:47+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45224', 'proveedor', '24522', NULL, '38d881609aef2546f0a573be1d9a96e948fa0c56a3bc66a2bf39840f30e92085', '12', NULL, 'activo', 'indeterminado', 'diesel', '46.944', '1267.49', '2026-07-09 11:43:56', '2026-07-09 17:43:56+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45225', 'proveedor', '24523', NULL, '9f302e245c8cb80673d4f93351a5e8e990e89c1250a6d1bc067100814424b2eb', NULL, '7', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-09 12:49:13', '2026-07-09 18:49:13+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45226', 'proveedor', '24524', NULL, '526dc93dac6cbf386abedf05e45f6cf7af5641128122e06a6d5ac03f7b13907e', NULL, '36', 'activo', 'termo', 'diesel', '152.848', '4126.90', '2026-07-09 15:37:13', '2026-07-09 21:37:13+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45227', 'proveedor', '24525', NULL, '53ea3e21e2ac12d50b14c6f9157d2ead3eccd39c36927f824b818687e0337da3', '34', NULL, 'activo', 'motor', 'diesel', '280.000', '7697.20', '2026-07-09 18:53:43', '2026-07-10 00:53:43+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45228', 'proveedor', '24526', NULL, '4685384dd5a17a7d06590c17e14b7b014711ec7a7917ea8203acdff776cd838f', '43', NULL, 'activo', 'motor', 'diesel', '190.000', '5128.10', '2026-07-09 19:50:26', '2026-07-10 01:50:26+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45229', 'proveedor', '24527', NULL, 'a109e64ed1d65211d5173d892a56a5dd3f09f81f5bb884453b3e47d6921fd580', '46', NULL, 'activo', 'motor', 'diesel', '300.000', '8097.00', '2026-07-09 22:24:43', '2026-07-10 04:24:43+00', '2026-07-09', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45230', 'proveedor', '24528', NULL, 'a9b1289f6bc8a8bf70028edbdb70f02e3bbee8d33835e2f711f0e1f456f608c1', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8079.00', '2026-07-10 07:29:47', '2026-07-10 13:29:47+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45231', 'proveedor', '24529', NULL, '762ac7cce7a32038b5e87e05756d0e958f6b3fd13d0db78721974f263bae6c26', '38', NULL, 'activo', 'motor', 'diesel', '290.000', '7972.10', '2026-07-10 18:24:47', '2026-07-11 00:24:47+00', '2026-07-10', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45232', 'proveedor', '24532', NULL, '356dff99c0b367be7001c70730d388245293ec85909c8349ccddf4711202810d', NULL, '36', 'activo', 'termo', 'diesel', '110.770', '3045.07', '2026-07-11 09:19:19', '2026-07-11 15:19:19+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45233', 'proveedor', '24533', NULL, '7385569b7063fabddf8179fb3d29ef518590df1472ca39fec05c5ef9efcafd64', '12', NULL, 'activo', 'indeterminado', 'diesel', '60.857', '1643.14', '2026-07-11 11:19:09', '2026-07-11 17:19:09+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45234', 'proveedor', '24534', NULL, '68724d8093d60014013c5cdd11b44f15d715520dcee23c1180f82db9605c1cff', NULL, '8', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-11 11:23:23', '2026-07-11 17:23:23+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45235', 'proveedor', '24535', NULL, 'b2ea5b1ae286f65e0541a1b82266f85810b77af207d72f7033e8afe551031892', '12', NULL, 'activo', 'indeterminado', 'diesel', '75.089', '2027.41', '2026-07-11 11:25:08', '2026-07-11 17:25:08+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45236', 'proveedor', '24536', NULL, 'a5aa2a298ca96f8c966a16e7ec76a1d33568afcffc6a5e30263a588e1ed82eb9', NULL, '4', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-11 11:31:04', '2026-07-11 17:31:04+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45237', 'proveedor', '24537', NULL, '40dbcfc5e582e02488c7741158a8c94a2810962e56ef88b5d4beb465ec0666d4', '5', NULL, 'activo', 'motor', 'diesel', '450.000', '12150.00', '2026-07-11 15:29:29', '2026-07-11 21:29:29+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45238', 'proveedor', '24538', NULL, 'dcb5c88b8018382fb763459d59cdcf4f2be20adffad44faf24001e2d375c1f8c', '38', NULL, 'activo', 'motor', 'diesel', '250.000', '6697.50', '2026-07-11 17:05:55', '2026-07-11 23:05:55+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45239', 'proveedor', '24539', NULL, '8cb5f76582d8a2ae1ce6207c37e5c803502374050332ded41cf213dbf11dc93f', NULL, '29', 'activo', 'termo', 'diesel', '150.000', '4018.50', '2026-07-11 17:15:21', '2026-07-11 23:15:21+00', '2026-07-11', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45240', 'proveedor', '24540', NULL, 'eff536b9dde73b4d8548c1b3bb2645ba55dd082feb1a1c664f76b654120e4beb', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-12 10:06:15', '2026-07-12 16:06:15+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45241', 'proveedor', '24541', NULL, '8c7a2c42d019b8357296751fa69e25c36ffd6a1b2d1e45b80c5c6c3ede82b723', '34', NULL, 'activo', 'motor', 'diesel', '250.000', '6750.00', '2026-07-12 11:18:09', '2026-07-12 17:18:09+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45242', 'proveedor', '24542', NULL, '8d1be535abe05f76b345b0e7b60310ecee25eb05f5ad1f4bb843712b42abe2b2', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4050.00', '2026-07-12 11:19:46', '2026-07-12 17:19:46+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45243', 'proveedor', '24543', NULL, 'beca456c96e0c661ae9764a9499a95f637d038014994b6c68315698e9c9c682f', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4050.00', '2026-07-12 14:11:07', '2026-07-12 20:11:07+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45244', 'proveedor', '24544', NULL, 'd37d2de624fd8278c376d0d42e98f3f2b2959c7689aaea8e10a7d77940caa0a1', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4050.00', '2026-07-12 14:21:23', '2026-07-12 20:21:23+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45245', 'proveedor', '24545', NULL, '0b252b5eb2f2d190da587bd95fc3fb918627930aae5dcad7c7418a3ce27c85d1', '13', NULL, 'activo', 'motor', 'diesel', '350.000', '9446.50', '2026-07-12 18:52:32', '2026-07-13 00:52:32+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45246', 'proveedor', '24546', NULL, '161a223b8e9172c23b3898571ff937774916364a300783a081be364dbb5f55b7', NULL, '30', 'activo', 'termo', 'diesel', '130.000', '3508.70', '2026-07-12 18:59:12', '2026-07-13 00:59:12+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45247', 'proveedor', '24547', NULL, '4c616a1536df014844c819ae7ac1e6d033d117ef02d0ae907c7a68431da6dca1', '5', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-12 21:03:37', '2026-07-13 03:03:37+00', '2026-07-12', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45248', 'proveedor', '24548', NULL, 'c484c8aa117eb3915d958cc03aa395e13414ea8efb8b8edfe4d3c0cae9c0d0e8', '45', NULL, 'activo', 'motor', 'diesel', '130.000', '3508.70', '2026-07-13 06:14:56', '2026-07-13 12:14:56+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45249', 'proveedor', '24549', NULL, '7c5ac57b55eb729224f2082d54aa3e14f2494cc12e3ece9064426ccf4db612df', NULL, '47', 'activo', 'termo', 'diesel', '80.070', '2201.12', '2026-07-13 06:16:18', '2026-07-13 12:16:18+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45250', 'proveedor', '24550', NULL, 'bb6b60a648a9ba8e5d33becad3958d890c17b237d85880063e51baa7d78f5597', '41', NULL, 'activo', 'motor', 'diesel', '150.000', '4036.50', '2026-07-13 06:44:06', '2026-07-13 12:44:06+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45251', 'proveedor', '24551', NULL, 'dfb975256a75c89a4239b7af2fec951cdacdb2c96b6c773aad9a2eed239fb85a', '1', NULL, 'activo', 'motor', 'diesel', '230.000', '6210.00', '2026-07-13 11:14:08', '2026-07-13 17:14:08+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45252', 'proveedor', '24552', NULL, 'e2a5140d06f21fe82674f6bd13f8fcb104e0f0f15e9beb4ea030478d2f09b61d', '46', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-13 11:46:18', '2026-07-13 17:46:18+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45253', 'proveedor', '24553', NULL, '6b83bc4e144fd56cbd2574f9fc6f8d8e7ad8a2e39827a75e1ce4a95ef919ec12', '53', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-13 12:41:04', '2026-07-13 18:41:04+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45254', 'proveedor', '24554', NULL, '65795cc77644d0b2ba0c72c182a8bf3d809129d8a59fdb21720addcdd1059ac4', '12', NULL, 'activo', 'indeterminado', 'diesel', '201.023', '5427.62', '2026-07-13 12:44:30', '2026-07-13 18:44:30+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45255', 'proveedor', '24555', NULL, 'deeddc93d79889b281fe634dabe3514812f45986ebe514041941a99dbc783e02', NULL, '2', 'activo', 'termo', 'diesel', '135.000', '3645.00', '2026-07-13 12:48:43', '2026-07-13 18:48:43+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45256', 'proveedor', '24557', NULL, 'e1c6e4e74d946e56d67d678b8c7df7e029b22dcb3d4d49c535c65be41a2d7717', '46', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-13 18:32:15', '2026-07-14 00:32:15+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45257', 'proveedor', '24558', NULL, 'f26dac2d327e1c7f30787e99c6a56a325c9590452ef82f0ea55601bfb836b5f5', '36', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-13 19:34:22', '2026-07-14 01:34:22+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45258', 'proveedor', '24559', NULL, '1035e8ea0195778b8c7b007218fbddc92895fb6307e7be7f7ea888082faa3c81', NULL, NULL, 'cuarentena', NULL, 'diesel', '140.000', '3780.00', '2026-07-13 19:45:24', '2026-07-14 01:45:24+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45259', 'proveedor', '24560', NULL, 'b11cd6b0499422f824a5051f1aeac9b60c9f391266175ef7eb67ee2fdd741e7f', '38', NULL, 'activo', 'motor', 'diesel', '150.000', '4108.50', '2026-07-13 20:18:20', '2026-07-14 02:18:20+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45260', 'proveedor', '24561', NULL, '5737a4bb7c00a8c2d69af8a480dff5511482bd14314163fe5779bfdfb7cc57aa', NULL, '29', 'activo', 'termo', 'diesel', '150.000', '4108.50', '2026-07-13 20:23:31', '2026-07-14 02:23:31+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45261', 'proveedor', '24562', NULL, '7a2e717b130a88e9747ec734f9fd3ab9ccb7494ceec49ef4da5ef5c3af58faf0', '40', NULL, 'activo', 'motor', 'diesel', '250.000', '6697.50', '2026-07-13 21:24:39', '2026-07-14 03:24:39+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45262', 'proveedor', '24563', NULL, 'e606ffb67c4699fc2968bb9cddf1ffd6f25918a47f90b17e3ce00e665aed399e', '40', NULL, 'activo', 'motor', 'diesel', '90.000', '2411.10', '2026-07-13 21:42:40', '2026-07-14 03:42:40+00', '2026-07-13', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45263', 'proveedor', '24564', NULL, '3fc08617da4829f9eeb6f0473cc1e2e0aadaf903f19ca0e0b53365bed1332848', NULL, '7', 'activo', 'termo', 'diesel', '140.000', '3848.60', '2026-07-14 08:49:57', '2026-07-14 14:49:57+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45264', 'proveedor', '24565', NULL, '1b35303edbab18f5cf5c575dc2a7928ab7f84669f4f0530e9f432eb7e04669f2', '53', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-14 12:33:06', '2026-07-14 18:33:06+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45265', 'proveedor', '24566', NULL, 'cb57fbc089e0a2a40f4a4bbf9a7783628123e074c9a99afbba5434caf1d92736', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2700.00', '2026-07-14 17:13:43', '2026-07-14 23:13:43+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45266', 'proveedor', '24567', NULL, 'f5aecb4d93d5651de82409a4c8f3a7f0d1db3243d960c13c14c5fc1cb0747ea1', '56', NULL, 'activo', 'motor', 'diesel', '230.000', '6207.70', '2026-07-14 18:31:49', '2026-07-15 00:31:49+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45267', 'proveedor', '24568', NULL, 'a74c806ff2ab3cf6fd1826def11656edf5b8c027f9239b4c3433e0bc73f1d27b', '36', NULL, 'activo', 'motor', 'diesel', '250.000', '6747.50', '2026-07-14 20:20:58', '2026-07-15 02:20:58+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45268', 'proveedor', '24569', NULL, 'c1f403480c7da09f4a9653e154dea7b9596d495db30ae35458473ece20ee4cee', NULL, NULL, 'cuarentena', NULL, 'diesel', '50.000', '1349.50', '2026-07-14 20:23:47', '2026-07-15 02:23:47+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45269', 'proveedor', '24570', NULL, 'c216bec5d43869090ef7fe62ca132520d6de19b75c13b8955b8643feb05fece6', '1', NULL, 'activo', 'motor', 'diesel', '150.000', '4050.00', '2026-07-14 20:24:13', '2026-07-15 02:24:13+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45270', 'proveedor', '24571', NULL, '4e98106eca55202cc1b68164058821cda6e005f914411bffb42d913ff5ac8c1a', NULL, '24', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-14 20:26:54', '2026-07-15 02:26:54+00', '2026-07-14', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45271', 'proveedor', '24572', NULL, '6f3d3ca99635dc1e93de8fe992c435262749abe82e63d58061f0ca0ea49c7590', '56', NULL, 'activo', 'motor', 'diesel', '250.000', '6747.50', '2026-07-15 05:48:17', '2026-07-15 11:48:17+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45272', 'proveedor', '24573', NULL, '9ba3f51e4b8284b61d24ae47d80df2aee1550d978c4e54a43f89174917524483', NULL, '44', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-15 06:25:04', '2026-07-15 12:25:04+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45273', 'proveedor', '24574', NULL, '2613ad0dc3f201f7e80e8399bf0dfbcb0b7dbd43d8d24f48b4e10cb9309c37d8', '38', NULL, 'activo', 'motor', 'diesel', '300.000', '8037.00', '2026-07-15 07:37:16', '2026-07-15 13:37:16+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45274', 'proveedor', '24575', NULL, '98eb30cfe6cabf0f363aeb0c01b35ecf465b5298e9eb651ff6986b0a1a993f69', '3', NULL, 'activo', 'motor', 'diesel', '150.000', '4050.00', '2026-07-15 16:16:52', '2026-07-15 22:16:52+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45275', 'proveedor', '24576', NULL, '417c5ac145170cd8402cc2cd406f0f4e6ff89c5957decfdad1563d30bd3647d5', NULL, '52', 'activo', 'termo', 'diesel', '63.925', '1725.98', '2026-07-15 16:22:30', '2026-07-15 22:22:30+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45276', 'proveedor', '24577', NULL, '01c9cb152e8b54346308f89b3014bef0437ff8e685d9dda9883385aae9ab603c', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-15 17:02:41', '2026-07-15 23:02:41+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45277', 'proveedor', '24578', NULL, '02bdd179ccaa9583c6111982f680e06f37e57db6651028b65202f9b772a6fcc8', '37', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-15 17:07:48', '2026-07-15 23:07:48+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45278', 'proveedor', '24579', NULL, '75cfd4a5533431298b01d2b96650adf773a12676e70b8b6e7ef08ff20f281267', '12', NULL, 'activo', 'indeterminado', 'diesel', '102.575', '2769.52', '2026-07-15 17:43:59', '2026-07-15 23:43:59+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45279', 'proveedor', '24580', NULL, 'f639a4d38c199b02f17cedd42a70945eb1fe3c1f9a9d0d6d37622a661ce3f0d6', '12', NULL, 'activo', 'indeterminado', 'diesel', '91.838', '2479.62', '2026-07-15 17:49:57', '2026-07-15 23:49:57+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45280', 'proveedor', '24581', NULL, '0207311fd03fc8481f6b37f5b1a389defbb72a90f7f006d0a104592d1b6b7070', '1', NULL, 'activo', 'motor', 'diesel', '350.000', '9446.50', '2026-07-15 21:10:02', '2026-07-16 03:10:02+00', '2026-07-15', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45281', 'proveedor', '24582', NULL, '6db362713c0c309aaa75118b4fe71bf2b975bd8bef8e72e46a234406317c94d5', NULL, '36', 'activo', 'termo', 'diesel', '150.000', '4123.50', '2026-07-16 07:44:10', '2026-07-16 13:44:10+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45282', 'proveedor', '24583', NULL, '6e9a6b505a93a9246e72f76802902af1665c520789438f5a6026fc6fe8e80d60', '13', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-16 07:50:44', '2026-07-16 13:50:44+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45283', 'proveedor', '24584', NULL, '407c3997b98ac54d64732332620cec9b6dc54d5d4b16f1e7aa52e939c79fbcdf', NULL, '30', 'activo', 'termo', 'diesel', '110.202', '2975.45', '2026-07-16 07:55:58', '2026-07-16 13:55:58+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45284', 'proveedor', '24585', NULL, '3aad046306a63cfceb0b9014ceea9964227549f0e8f24bc2348ae2968f9a0aeb', '1', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-16 10:10:35', '2026-07-16 16:10:35+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45285', 'proveedor', '24586', NULL, '1e7797cd89a51912bf862009a2efcc20b42bf339032612fbbc0057a6de9b9318', '38', NULL, 'activo', 'motor', 'diesel', '280.000', '7697.20', '2026-07-16 19:43:37', '2026-07-17 01:43:37+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45286', 'proveedor', '24587', NULL, '3890a292b841673f4e6ca6ab4a3a83b79ae6360ab17867f6699785286a08c7af', '46', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-16 19:43:53', '2026-07-17 01:43:53+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45287', 'proveedor', '24588', NULL, '1be2c84d11b486623c592d8e1262bf6409a49c88053ff56049b795db9bec951f', NULL, '44', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-16 19:52:42', '2026-07-17 01:52:42+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45288', 'proveedor', '24589', NULL, '303e714b0cfccc120c384cdc7a5ece321b154bf82bff2b2ac29ad5007d85bfd0', NULL, '29', 'activo', 'termo', 'diesel', '100.000', '2749.00', '2026-07-16 20:07:30', '2026-07-17 02:07:30+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45289', 'proveedor', '24590', NULL, '3a45aefa4041e0f5e70b6a5f7a23487bbc01360c14dc6080f22244d348fa9675', '3', NULL, 'activo', 'motor', 'diesel', '350.000', '9796.50', '2026-07-16 20:13:27', '2026-07-17 02:13:27+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45290', 'proveedor', '24591', NULL, '61e1e4182a8536b97238e634e2324372065e9ec07e603d234c88f9ef75d889e6', NULL, NULL, 'cuarentena', NULL, 'diesel', '137.132', '3701.19', '2026-07-16 20:18:01', '2026-07-17 02:18:01+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45291', 'proveedor', '24592', NULL, '3053f9956942c6dd8dd986a62b87f2905482f39ff6e17fc0995b13194f109549', NULL, '52', 'activo', 'termo', 'diesel', '50.000', '1399.50', '2026-07-16 20:19:22', '2026-07-17 02:19:22+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45292', 'proveedor', '24593', NULL, '2edd0bb07796779ce32e5671d4b0e7685fae6319357512d56ee52c1052b84b7f', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4048.50', '2026-07-16 20:31:26', '2026-07-17 02:31:26+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45294', 'proveedor', '24595', NULL, 'dd0bddc7ddf6dd47c4b8bed4a3457efef45c55691668aeccb987c82ff8aca71f', NULL, '47', 'activo', 'termo', 'diesel', '100.000', '2699.00', '2026-07-16 20:52:51', '2026-07-17 02:52:51+00', '2026-07-16', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45295', 'proveedor', '24596', NULL, '26e20126400efd71ca3928e7994a323a984a5a604d33c2743d41522b85c8b6eb', '13', NULL, 'activo', 'motor', 'diesel', '180.000', '5074.20', '2026-07-17 05:56:13', '2026-07-17 11:56:13+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45296', 'proveedor', '24597', NULL, 'b74c1b6d01fdae6fa9a31dd808be052d572c10d7bec8797321e6731925eb3088', NULL, '30', 'activo', 'termo', 'diesel', '100.000', '2819.00', '2026-07-17 06:03:58', '2026-07-17 12:03:58+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45297', 'proveedor', '24598', NULL, '3df6cbcd6c5e700083feeb6633509c5f5181cf039f6e268d60628882a58a692f', '37', NULL, 'activo', 'motor', 'diesel', '108.000', '2916.00', '2026-07-17 10:37:15', '2026-07-17 16:37:15+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45298', 'proveedor', '24599', NULL, '7c253dc0088eaac93beba7abfcc9cbbfe22863c224a8954fbf45536e471c6240', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-17 10:52:44', '2026-07-17 16:52:44+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45300', 'proveedor', '24601', NULL, 'ce3360e66ffa87dc21bb6a64c3a668a45d5d973740de874d4acdf0f118f7c779', '1', NULL, 'activo', 'motor', 'diesel', '199.716', '5392.33', '2026-07-17 18:11:59', '2026-07-18 00:11:59+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45301', 'proveedor', '24602', NULL, 'c0ff242585e80373f88ab465a317bc7a97dd2b7e563678fa224cc9d850e57b61', '41', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-17 22:23:39', '2026-07-18 04:23:39+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45302', 'proveedor', '24603', NULL, 'f169eed3e1a43c41a02c354ec737081b5d91c40b788ccc5806187ccd03304de7', '53', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-17 22:34:05', '2026-07-18 04:34:05+00', '2026-07-17', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45303', 'proveedor', '24604', NULL, '68b9c1b77049b1f38c2ef6ee76cba3dc34910a3d1a133b7b15bc2f40353a6654', '36', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-18 00:20:51', '2026-07-18 06:20:51+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45304', 'proveedor', '24605', NULL, 'ed59647712a475f9e40e0bf1069edf86eedfd03e482fcbbe710fc8c37a590184', NULL, '2', 'activo', 'termo', 'diesel', '120.000', '3240.00', '2026-07-18 11:15:26', '2026-07-18 17:15:26+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45305', 'proveedor', '24606', NULL, 'eae17669fbc098054dfad7b2c1c083239eb921e11578e7e577004c7115605504', '34', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-18 12:56:28', '2026-07-18 18:56:28+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45306', 'proveedor', '24607', NULL, 'd62a60583a6965ad9d30deaad484267d22e5189f9cac1d080cda41b35d77ebbe', NULL, '46', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-18 13:13:59', '2026-07-18 19:13:59+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45307', 'proveedor', '24608', NULL, '0035557446f386c55d46582d7b0126508876a32fca4d4aa1d795d27ecf2a36fc', '41', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-18 13:25:02', '2026-07-18 19:25:02+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45308', 'proveedor', '24609', NULL, '956859fc43e49e0830abd4ffd6336f2f9d27511261f2a14086dfb3501f03f1d6', NULL, '36', 'activo', 'termo', 'diesel', '120.000', '3240.00', '2026-07-18 13:56:10', '2026-07-18 19:56:10+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45309', 'proveedor', '24610', NULL, '419b800f5d15dc38b96c347682e7218ff4583e14b6a04f3a2fc8161c11ae148a', NULL, '3', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-18 18:12:49', '2026-07-19 00:12:49+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45310', 'proveedor', '24611', NULL, '0f3a2099846f1d161aa22e17b3d3c4e40c4959f45e55776e220a41d4bcf7a845', '13', NULL, 'activo', 'motor', 'diesel', '380.000', '10260.00', '2026-07-18 20:35:33', '2026-07-19 02:35:33+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45311', 'proveedor', '24612', NULL, 'd4263a59070abd60a16c017f0b88fef1a803a578850296891acf12a39a23752f', NULL, '30', 'activo', 'termo', 'diesel', '51.460', '1389.42', '2026-07-18 20:41:16', '2026-07-19 02:41:16+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45312', 'proveedor', '24613', NULL, '36bca03fc5d444d06ecaf2f8c9677d5ff18454c4e789c368d8c24ebfb013c65e', '53', NULL, 'activo', 'motor', 'diesel', '350.000', '9621.50', '2026-07-18 20:42:16', '2026-07-19 02:42:16+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45313', 'proveedor', '24614', NULL, '4b1aab1dd84a53c9717650a5deb5ef77b3bcbb2471435a6aca791fa9c80ad9dd', NULL, NULL, 'cuarentena', NULL, 'diesel', '135.000', '3645.00', '2026-07-18 23:29:44', '2026-07-19 05:29:44+00', '2026-07-18', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45315', 'proveedor', '24616', NULL, 'cd67e1043241898e3f01d471e6771a2a23994ffc4c532b50bbdff7628fdf7e30', NULL, '47', 'activo', 'termo', 'diesel', '50.000', '1350.00', '2026-07-19 06:23:02', '2026-07-19 12:23:02+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45316', 'proveedor', '24617', NULL, 'ce2632f4a7c5722f4dbacb3b75d381659416d1e3f3cb9fde15ddc1ba0f02dd11', NULL, '44', 'activo', 'termo', 'diesel', '135.000', '3711.15', '2026-07-19 08:23:23', '2026-07-19 14:23:23+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45317', 'proveedor', '24618', NULL, '1521c56404cd7e5dc66b47a4193ab4447d95f22ae6f53b82febe1efcc46b7363', NULL, '36', 'activo', 'termo', 'diesel', '120.000', '3240.00', '2026-07-19 08:31:05', '2026-07-19 14:31:05+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45318', 'proveedor', '24619', NULL, 'e4efb3a2ff7fc6e17b121037843f0fbbbc85d89773261b2cf2763d731d5e4a8d', '45', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-19 15:56:11', '2026-07-19 21:56:11+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45319', 'proveedor', '24620', NULL, 'b16cd17076a77d7369d418530af0703f8d8b2e5e5db1527906a438f12be59cc0', NULL, '4', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-19 16:20:10', '2026-07-19 22:20:10+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45320', 'proveedor', '24621', NULL, 'bf030d67162f48abd21b555695d71e9259e203f66301a2b3a2e184b6c453083d', '53', NULL, 'activo', 'motor', 'diesel', '120.000', '3240.00', '2026-07-19 18:41:29', '2026-07-20 00:41:29+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45321', 'proveedor', '24622', NULL, '2073eac1c43790317d7608cc676001ca2b546fae5309147efd07b523644a06e3', NULL, '2', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-19 18:42:57', '2026-07-20 00:42:57+00', '2026-07-19', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45323', 'proveedor', '24624', NULL, '894f126ef4fd0d5b89e9876b88a79f266d832c0a0ab4144a527981fcad412b0b', '13', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-20 06:46:56', '2026-07-20 12:46:56+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45324', 'proveedor', '24625', NULL, 'd658714f9e742a55aba21e9f127216f074f149c033768fb3c79bbc6b7bc20133', '37', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-20 07:16:19', '2026-07-20 13:16:19+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45325', 'proveedor', '24626', NULL, 'f30471208e52e236f0461991c28e8dbc6e5ea062611de0b0cc728b6dd87b875c', '3', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-20 07:19:51', '2026-07-20 13:19:51+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45326', 'proveedor', '24627', NULL, '02b82b9cc84c763880ae9a5a7992318888081fa4aff2ed8fec4ae22b96e9e932', '53', NULL, 'activo', 'motor', 'diesel', '250.000', '6897.50', '2026-07-20 07:41:45', '2026-07-20 13:41:45+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45327', 'proveedor', '24628', NULL, '09a768378945ed9d4029c47eb5dd94f80f4f00266accdc2dbd3fcf821988abc3', NULL, '52', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-20 09:29:35', '2026-07-20 15:29:35+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45328', 'proveedor', '24629', NULL, '35f8a5d0b6765ecdbc0227fcbb698b7f34b5641ed27d3666585551d69e538e3e', '34', NULL, 'activo', 'motor', 'diesel', '330.000', '8880.30', '2026-07-20 09:54:33', '2026-07-20 15:54:33+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45329', 'proveedor', '24630', NULL, 'd42154e9462dc0c57e779c84a2e9aca90db44d2072869c9c40638f67e06921b7', NULL, NULL, 'fuera_flota', NULL, 'gasolina', '67.888', '1587.89', '2026-07-20 10:22:18', '2026-07-20 16:22:18+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45330', 'proveedor', '24631', NULL, 'd454487188192924a8ff03d1784a8c2fd2523c6a9b9dad5dad9e682c854bfb59', '12', NULL, 'activo', 'indeterminado', 'diesel', '146.902', '3966.35', '2026-07-20 12:42:53', '2026-07-20 18:42:53+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45331', 'proveedor', '24632', NULL, '90e00e286e13b413a3c8e2f934ec6ea4782986e60f81829f170f36e78dc2f14f', NULL, NULL, 'cuarentena', NULL, 'diesel', '122.875', '3317.63', '2026-07-20 12:45:38', '2026-07-20 18:45:38+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45332', 'proveedor', '24633', NULL, 'f42791ecff827b9be5c2bff8ed995b9b2d4d58aeb51ae70d4f9c6b2c185c1b1a', NULL, '8', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-20 12:46:33', '2026-07-20 18:46:33+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45333', 'proveedor', '24634', NULL, '522d5bdccabfb965965d772b736ce6e7e4cc6a81cd13dbfd5c44863f1e89f526', NULL, '17', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-20 12:48:19', '2026-07-20 18:48:19+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45334', 'proveedor', '24635', NULL, '4aa79ca3a8c0bf0b5a78de13b58de7e15879eb69a451d772a4f8337581998aba', '12', NULL, 'activo', 'indeterminado', 'diesel', '52.468', '1416.64', '2026-07-20 12:50:18', '2026-07-20 18:50:18+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45335', 'proveedor', '24636', NULL, 'dc771257ad5e34cd9c496c056e220dda43a431f95972e5853b151453e215f512', '46', NULL, 'activo', 'motor', 'diesel', '300.000', '8097.00', '2026-07-20 13:41:44', '2026-07-20 19:41:44+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45336', 'proveedor', '24637', NULL, 'f0132cbce11d9802f914ffde1eda328efdeeb20d88bc0032eca1cdf29f8f6955', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4048.50', '2026-07-20 13:48:23', '2026-07-20 19:48:23+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45337', 'proveedor', '24638', NULL, '30b61c47d0a4e2e2eba00d24ffe5b4b71cb2be5acba74ee150100eb1113ca644', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-20 13:54:00', '2026-07-20 19:54:00+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45338', 'proveedor', '24639', NULL, '283b4c3f5e72b48015311409c333cfaad698cea8dd3503d954750a6da4a8ef44', NULL, '4', 'activo', 'termo', 'diesel', '69.787', '1883.55', '2026-07-20 13:54:56', '2026-07-20 19:54:56+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45339', 'proveedor', '24640', NULL, 'd9c5afac681facc28f735c4cd3783c2d5dafd07eb5748dadd862f0fcc52c3bbd', '30', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-20 19:46:06', '2026-07-21 01:46:06+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45340', 'proveedor', '24641', NULL, 'f8353322859fd3cac59946b13309778f8bf5b986bd0d68953ac8e1a775957ef6', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4050.00', '2026-07-20 19:57:09', '2026-07-21 01:57:09+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45341', 'proveedor', '24642', NULL, '3525533cac5482e8d7e35f1c8212760f17f441e07423f0663ace90849cef6fe5', '45', NULL, 'activo', 'motor', 'diesel', '400.000', '10996.00', '2026-07-20 20:29:57', '2026-07-21 02:29:57+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45342', 'proveedor', '24643', NULL, '578f261db346f776fc7a7b9ee7d22a4d7407dd69911598ffdcdb59ea6012b8b2', '38', NULL, 'activo', 'motor', 'diesel', '380.000', '10446.20', '2026-07-20 20:35:08', '2026-07-21 02:35:08+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45343', 'proveedor', '24644', NULL, 'e3bdc7e0d690244868bbae24b7124eba13eb917a2030473c49835c8f461d52ff', NULL, '29', 'activo', 'termo', 'diesel', '135.000', '3711.15', '2026-07-20 20:46:14', '2026-07-21 02:46:14+00', '2026-07-20', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45344', 'proveedor', '24645', NULL, '14ff1d66dd77dda4b172ed621ea60316bca5a671ac210f65d437607716607249', '46', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-21 07:03:45', '2026-07-21 13:03:45+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45345', 'proveedor', '24646', NULL, '858262497a1d043ee7607c22c700bf79fd6a82c23dd576f9ba665c590219672f', '53', NULL, 'activo', 'motor', 'diesel', '230.000', '6207.70', '2026-07-21 12:07:04', '2026-07-21 18:07:04+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45346', 'proveedor', '24647', NULL, '41bfcf655d0a7b1185a76a77cbdf91cc0f4f580b60d31cf22f90baa13ddfed20', '34', NULL, 'activo', 'motor', 'diesel', '100.000', '2799.00', '2026-07-21 13:07:58', '2026-07-21 19:07:58+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45347', 'proveedor', '24648', NULL, '37c02d4cdbc4e858353dcb054867bfc333849754715228c07532edefb94f3021', NULL, '46', 'activo', 'termo', 'diesel', '135.000', '3778.65', '2026-07-21 13:13:35', '2026-07-21 19:13:35+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45348', 'proveedor', '24649', NULL, '43e97f6b121db140001ecbcae9de9647545ef0069e78a8d77b436486f4942665', '45', NULL, 'activo', 'motor', 'diesel', '120.000', '3240.00', '2026-07-21 14:06:55', '2026-07-21 20:06:55+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45349', 'proveedor', '24650', NULL, 'ba06fb9b1ff02e1442f5afd97c11adb2a34d87a7a5837fd581fdb42e755448dc', '36', NULL, 'activo', 'motor', 'diesel', '255.000', '6885.00', '2026-07-21 14:47:48', '2026-07-21 20:47:48+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45350', 'proveedor', '24651', NULL, 'd5bedb91146428c7173b3c98ef7cdd51d8d9824d8961f01f685f388a4eb8ecc1', NULL, NULL, 'cuarentena', NULL, 'diesel', '130.000', '3510.00', '2026-07-21 20:44:08', '2026-07-22 02:44:08+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45351', 'proveedor', '24652', NULL, '2cf0ecc36842b7667275ef16eae7a81237e9179c5bba9899d2d8322c3930f5a5', NULL, '4', 'activo', 'termo', 'diesel', '134.420', '3629.34', '2026-07-21 20:54:02', '2026-07-22 02:54:02+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45352', 'proveedor', '24653', NULL, '082acc24fab03025dc1e823ce4834f9361c249e283fa87ca6c009491454e384f', '38', NULL, 'activo', 'motor', 'diesel', '120.000', '3240.00', '2026-07-21 22:24:11', '2026-07-22 04:24:11+00', '2026-07-21', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45353', 'proveedor', '24654', NULL, 'f7099acd697c216f36790f47dd38b562ce933eba8f87cfefe2ca736708bd0ba6', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2700.00', '2026-07-22 06:29:15', '2026-07-22 12:29:15+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45354', 'proveedor', '24655', NULL, '2a58f21528cdf9ccefc66f99039ee5e55c8ed04a05c7135eeea2f1c35e1f1cba', '41', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-22 06:49:19', '2026-07-22 12:49:19+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45355', 'proveedor', '24656', NULL, 'b5e57caa133b628033b7fddf2bbb6c82664adc0843cf47226c6ae0ad406cf710', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3240.00', '2026-07-22 14:04:57', '2026-07-22 20:04:57+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45356', 'proveedor', '24657', NULL, '96acb148114898861902fb56bb783e7718ed5cffef93e56399d88188e8ff8abb', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-22 14:14:07', '2026-07-22 20:14:07+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45358', 'proveedor', '24659', NULL, '69eba74889bb9c115c2dccdcd44481e17aee023f705247d279b88874d6474cb7', NULL, '42', 'activo', 'termo', 'diesel', '135.000', '3645.00', '2026-07-22 17:54:46', '2026-07-22 23:54:46+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45359', 'proveedor', '24660', NULL, 'ef08c1c8da6143660fa4d6b4ee78852e67a48b439eb225919bf7f0aeb1a96194', '37', NULL, 'activo', 'motor', 'diesel', '323.927', '8742.78', '2026-07-22 18:38:50', '2026-07-23 00:38:50+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45360', 'proveedor', '24661', NULL, 'f9fc98536f646a094df0f06a708526bd038416a2ed0326c1fb708eed459f979f', NULL, '2', 'activo', 'termo', 'diesel', '130.000', '3508.70', '2026-07-22 18:50:31', '2026-07-23 00:50:31+00', '2026-07-22', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45361', 'proveedor', '24662', NULL, 'bbcb9fec57a818e7050f1c342c790e4d437f13e3f2353bb5485b87e7dfe5ac31', '30', NULL, 'activo', 'motor', 'diesel', '150.000', '4048.50', '2026-07-23 02:40:49', '2026-07-23 08:40:49+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45362', 'proveedor', '24663', NULL, 'd4869cc61f98d35514cecc1f53b1fba2a41cb3fcdf8677ff65b5433d9c78b3ac', NULL, '47', 'activo', 'termo', 'diesel', '135.000', '3643.65', '2026-07-23 09:28:47', '2026-07-23 15:28:47+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45364', 'proveedor', '24665', NULL, 'a561ce50fdc63a1072b5c7f622e68b8eb731516b83003335d40e5e65122c96b0', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-23 13:18:16', '2026-07-23 19:18:16+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45365', 'proveedor', '24666', NULL, 'a3d3c114cd88e0e7092d9c400247fb52811d23b94aa75d968b29e47cbeb33e0d', '13', NULL, 'activo', 'motor', 'diesel', '350.000', '9446.50', '2026-07-23 14:03:40', '2026-07-23 20:03:40+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45366', 'proveedor', '24667', NULL, '1fc6fde264f9cdea8bfc3929c25883fc1db8d02e0c39495d64e7e8154ff98a90', NULL, '30', 'activo', 'termo', 'diesel', '50.000', '1349.50', '2026-07-23 14:08:13', '2026-07-23 20:08:13+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45367', 'proveedor', '24668', NULL, 'a8c47bb132f6c43ed3b0d439ab5ede099f15041b453e6537b960919084197802', '37', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-23 19:53:35', '2026-07-24 01:53:35+00', '2026-07-23', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45368', 'proveedor', '24669', NULL, '38096c2f36131b4d83e1fe573bec7fda20fba47aed9fd9ac25a423bdcc47390f', '55', NULL, 'activo', 'motor', 'diesel', '380.000', '10256.20', '2026-07-24 07:08:57', '2026-07-24 13:08:57+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45369', 'proveedor', '24670', NULL, '420570f8cd5ec13f3ddf184b1f67d3a2d5c85caee4b6b0355a5268a033d1674e', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3238.80', '2026-07-24 07:19:23', '2026-07-24 13:19:23+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45370', 'proveedor', '24671', NULL, 'f5d2e00ccddc7cbc742baf3beee058a81add33119f42b91f45634c4817ec2d12', '38', NULL, 'activo', 'motor', 'diesel', '380.000', '10408.20', '2026-07-24 07:24:55', '2026-07-24 13:24:55+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45371', 'proveedor', '24672', NULL, '98e7ee43efe66c5e25483260a0cde55ba43c0f73a3a557b0795d367139282a34', NULL, '29', 'activo', 'termo', 'diesel', '150.000', '4108.50', '2026-07-24 07:33:22', '2026-07-24 13:33:22+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45372', 'proveedor', '24673', NULL, '8ab1bc4eb1cc799351bab4d5447caace890249856b63894fbf6fb9e3886b7dfb', '34', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-24 07:37:06', '2026-07-24 13:37:06+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45373', 'proveedor', '24674', NULL, 'cf630b0d3387b7a3484dd65149538988757493f2a44e0e3cb87911c95c16ce6c', NULL, '36', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-24 10:04:59', '2026-07-24 16:04:59+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45374', 'proveedor', '24675', NULL, '4305b224fdbf62ac7f3681bb019e69e5ee786da3e4104ee9c14d22ae09d39f36', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3240.00', '2026-07-24 10:31:36', '2026-07-24 16:31:36+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45375', 'proveedor', '24676', NULL, 'bdc69a83dcce50caf7855c45d23d0a20ee9f0256e7694031cb856f9f4d54fbaa', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-24 10:50:04', '2026-07-24 16:50:04+00', '2026-07-24', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45376', 'proveedor', '24677', NULL, 'fd1b7a654c179316bc96e43d54f1858567d63d89b5ee808da785e6cb294fe1ca', '45', NULL, 'activo', 'motor', 'diesel', '400.000', '10796.00', '2026-07-25 06:51:55', '2026-07-25 12:51:55+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45377', 'proveedor', '24678', NULL, 'cf6ce4851593acdea9772d8fc33dc6efc08de95773dba89731c380bce3121b6f', NULL, '15', 'activo', 'termo', 'diesel', '135.000', '3643.65', '2026-07-25 07:08:27', '2026-07-25 13:08:27+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45378', 'proveedor', '24679', NULL, '2962f86f4c1be80ea39dad3dde5d9500d55deb4e1835fe30edfb86c04d52871c', NULL, '8', 'activo', 'termo', 'diesel', '135.000', '3643.65', '2026-07-25 07:10:52', '2026-07-25 13:10:52+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45380', 'proveedor', '24681', NULL, 'e287339726e97a9dc866215dc0a6704ae3ca63ff582257a6c547c5b4dd4ab4dc', '13', NULL, 'activo', 'motor', 'diesel', '300.000', '8247.00', '2026-07-25 10:08:16', '2026-07-25 16:08:16+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45381', 'proveedor', '24682', NULL, '7fe86f7126f96172ad1b27ff928cce344623640c9393f84d4984031d8a565357', NULL, '30', 'activo', 'termo', 'diesel', '45.630', '1254.37', '2026-07-25 10:17:41', '2026-07-25 16:17:41+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45382', 'proveedor', '24683', NULL, '981179c170ad602739a21e469d4584c8945a64d26a4e4bb98ebd9b936f9a0525', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2700.00', '2026-07-25 15:02:23', '2026-07-25 21:02:23+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45383', 'proveedor', '24684', NULL, '1dd7aae253cd4bf4d1822b46c75c9964882b83f3081eaf4f62e75adf397c4618', '34', NULL, 'activo', 'motor', 'diesel', '350.000', '9621.50', '2026-07-25 17:35:44', '2026-07-25 23:35:44+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45384', 'proveedor', '24685', NULL, '6d4704c0dda4ff59383374eda89eec499cd46ecd5dd0a2f35a9a0251650d5850', '45', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-25 18:53:22', '2026-07-26 00:53:22+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45385', 'proveedor', '24686', NULL, '1ac911bd9cab906828b39cdae7f619d56ad04505425cae08be93d173da6a635c', '46', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-25 18:55:21', '2026-07-26 00:55:21+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45386', 'proveedor', '24687', NULL, 'b2357fd786a294105844a953fb8eb023e190f23ca1c3564a359fd8f7fe5ec024', NULL, NULL, 'cuarentena', NULL, 'diesel', '90.000', '2430.00', '2026-07-25 19:05:21', '2026-07-26 01:05:21+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45387', 'proveedor', '24688', NULL, 'b1586394e4adc569428a313c3774d9c845ab29014924a8d8053c29a3e94300e4', NULL, '4', 'activo', 'termo', 'diesel', '51.788', '1398.28', '2026-07-25 19:11:56', '2026-07-26 01:11:56+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45388', 'proveedor', '24689', NULL, 'dee6ad27fc66687dc4bc7db4a8aa2e8b26ef305986dc5dd882b9e1d39373a349', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2699.00', '2026-07-25 20:26:53', '2026-07-26 02:26:53+00', '2026-07-25', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45389', 'proveedor', '24690', NULL, 'e26051dfb57cf769fa2ad266c0ef0368cc2364cad99df166312fdf2b0f325ac6', '41', NULL, 'activo', 'motor', 'diesel', '380.000', '10260.00', '2026-07-26 03:03:55', '2026-07-26 09:03:55+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45390', 'proveedor', '24691', NULL, '0c567aa08779468be053adeb48a2db6a528614b7b06f814cf0ebb47f12897e1f', NULL, NULL, 'cuarentena', NULL, 'diesel', '140.000', '3780.00', '2026-07-26 07:12:59', '2026-07-26 13:12:59+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45391', 'proveedor', '24692', NULL, 'ac5edb372de3d62d84967a21d17ef3966c904040fc75f03d7e5015d7a49e065d', NULL, NULL, 'cuarentena', NULL, 'diesel', '140.000', '3780.00', '2026-07-26 07:21:16', '2026-07-26 13:21:16+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45392', 'proveedor', '24693', NULL, '42cfc9f1b92fc3b8b4ef35627595c3d33a493c9893673f9c263f8f34a0a7a77c', NULL, '15', 'activo', 'termo', 'diesel', '48.000', '1296.00', '2026-07-26 10:51:54', '2026-07-26 16:51:54+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45393', 'proveedor', '24694', NULL, '0b70349b33296d2a816139ef78f6e851ee281782df6b87a48622ca4260ecae9a', NULL, '29', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-26 14:43:25', '2026-07-26 20:43:25+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45394', 'proveedor', '24695', NULL, '0b98b645561ef10f51e42514a6a0a44180d8f3dd7b016db2d982dd3b26520984', '38', NULL, 'activo', 'motor', 'diesel', '150.000', '4050.00', '2026-07-26 14:49:22', '2026-07-26 20:49:22+00', '2026-07-26', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45395', 'proveedor', '24696', NULL, 'c21ea825030b109caf9facb4df7ea43da4d87f27f1a1ab92e31858028135d518', '25', NULL, 'activo', 'motor', 'diesel', '285.000', '7695.00', '2026-07-27 01:25:41', '2026-07-27 07:25:41+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45396', 'proveedor', '24697', NULL, '4bb5b44a87d961adbd8206bbe42fff0c10c79bd4c480e57a307f5ea95281b0e1', NULL, '45', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-27 01:32:25', '2026-07-27 07:32:25+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45398', 'proveedor', '24699', NULL, '2b96eb64c133ba7b99602146ef45699841d622e63a2f2f1a982f654e778e04e5', NULL, '47', 'activo', 'termo', 'diesel', '90.000', '2474.10', '2026-07-27 03:31:59', '2026-07-27 09:31:59+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45399', 'proveedor', '24700', NULL, 'b77d706875dd0254b524ca527c8fdcd3c47f3e59399fa959a923f55969da80c5', '5', NULL, 'activo', 'motor', 'diesel', '180.000', '4858.20', '2026-07-27 05:00:24', '2026-07-27 11:00:24+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45400', 'proveedor', '24701', NULL, 'd4577da0d24ff0f3132f62220570438d0572da3e24e3fe991a5663054abde60f', '13', NULL, 'activo', 'motor', 'diesel', '400.000', '10796.00', '2026-07-27 06:05:45', '2026-07-27 12:05:45+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45401', 'proveedor', '24702', NULL, '5a56377605ba221d4c1aa90d0e40f62f139d01294e6ccaeccfd82c89d124bad1', NULL, '17', 'activo', 'termo', 'diesel', '100.000', '2699.00', '2026-07-27 06:12:40', '2026-07-27 12:12:40+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45402', 'proveedor', '24703', NULL, '6844c554366a2e3c22aae83e5ada55e5fcf47febcf96e0f86a855ea0d0c702c8', '46', NULL, 'activo', 'motor', 'diesel', '450.000', '12150.00', '2026-07-27 10:30:51', '2026-07-27 16:30:51+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45403', 'proveedor', '24704', NULL, 'f3bda6aefc0f93d3ce8c646e4d587ad65b97387a34bd7a9ffe6a918defdf46ae', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3240.00', '2026-07-27 10:41:33', '2026-07-27 16:41:33+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45404', 'proveedor', '24705', NULL, 'f395ece2b63c552e235bc86950941400250510242cf72c297b494b01b63cf35a', NULL, '4', 'activo', 'termo', 'diesel', '111.422', '3008.39', '2026-07-27 10:53:33', '2026-07-27 16:53:33+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45405', 'proveedor', '24706', NULL, '54ed37a21e117c0dbdad06d91b28b03af2c1ffb56638f34ee11bde441432ca76', '37', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-27 13:23:35', '2026-07-27 19:23:35+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45406', 'proveedor', '24707', NULL, '6a717282bcbd8bbf99ea99a5680933609151ba6c92f6a1c104ece6cf0c33bae7', NULL, '15', 'activo', 'termo', 'diesel', '140.000', '3780.00', '2026-07-27 16:32:00', '2026-07-27 22:32:00+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45407', 'proveedor', '24708', NULL, '14ac183b908606d442f9ce7c4898c2fa53198e053f7461b036064a519795a551', NULL, '8', 'activo', 'termo', 'diesel', '140.000', '3780.00', '2026-07-27 16:39:26', '2026-07-27 22:39:26+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45408', 'proveedor', '24709', NULL, '640665b506b05370e1266b266dcfeadd75250c2e936edb442620b9ca556f3f20', '38', NULL, 'activo', 'motor', 'diesel', '350.000', '9450.00', '2026-07-27 18:55:15', '2026-07-28 00:55:15+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45409', 'proveedor', '24710', NULL, 'c73b5e7a7a7456296b9253d8ac7b003fbef2690cae60ed64fd91fe09d23d3efe', '41', NULL, 'activo', 'motor', 'diesel', '120.000', '3238.80', '2026-07-27 20:30:27', '2026-07-28 02:30:27+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45410', 'proveedor', '24711', NULL, '095ea60f878c26ad31382d35da313a5cf4a3f3f22d9ec439e024607f90c7e827', '3', NULL, 'activo', 'motor', 'diesel', '280.000', '7560.00', '2026-07-27 21:35:16', '2026-07-28 03:35:16+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45411', 'proveedor', '24712', NULL, 'f95ec2dcb28a50da70b5a33bd690506f2e7fd0b2ea2deb53c25bcc240595f5aa', NULL, '44', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-27 21:51:14', '2026-07-28 03:51:14+00', '2026-07-27', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45412', 'proveedor', '24713', NULL, '8fbf1bf073a7ccf297a084eefd91eeb64a78fac7c6bfc6de29b485282f527cd4', '55', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-28 05:49:28', '2026-07-28 11:49:28+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45413', 'proveedor', '24714', NULL, '9a7ec36f9728c781e00d0cb435ff04caa023aafdbfbf09ceff7f5319e0cc89ff', NULL, NULL, 'cuarentena', NULL, 'diesel', '147.909', '3993.54', '2026-07-28 06:03:56', '2026-07-28 12:03:56+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45414', 'proveedor', '24715', NULL, 'e15800c68827940853941b97e6ad855e60c8c92b7288f5d8582fa7a95e4a237f', NULL, NULL, 'cuarentena', NULL, 'diesel', '150.000', '4048.50', '2026-07-28 07:09:26', '2026-07-28 13:09:26+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45415', 'proveedor', '24716', NULL, '03ee80bc72bc83a92ab2691e57753b831ac3090952d53ecd2845313851a966f2', NULL, NULL, 'cuarentena', NULL, 'diesel', '100.000', '2699.00', '2026-07-28 07:17:25', '2026-07-28 13:17:25+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45416', 'proveedor', '24717', NULL, '3e8ecc63ba656add24fcd4938cb62103c6e8c6c8dc0e7bada11a38e3bc750328', NULL, '47', 'activo', 'termo', 'diesel', '100.000', '2699.00', '2026-07-28 10:29:33', '2026-07-28 16:29:33+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45417', 'proveedor', '24718', NULL, 'f8aaad32abc963b66a046aac0679054e7beaeaad293828a729df42c94eed6e50', '13', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-28 11:10:51', '2026-07-28 17:10:51+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45418', 'proveedor', '24719', NULL, 'c102371b8e61842238172d206c7ddd50c645053207e1882ee78fad40f1d647a5', '46', NULL, 'activo', 'motor', 'diesel', '150.000', '4050.00', '2026-07-28 12:55:52', '2026-07-28 18:55:52+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45419', 'proveedor', '24720', NULL, 'a644a93b81959b137d2e99fccea0d7e4ff7e98b46bf403aae271645ebfa31f53', '37', NULL, 'activo', 'motor', 'diesel', '70.000', '1890.00', '2026-07-28 16:24:25', '2026-07-28 22:24:25+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45420', 'proveedor', '24721', NULL, '58e3f391e5ecbdb860bb7639e35ce99f3cfc69c5473c198fcf635fe2b706e432', NULL, '2', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-28 16:30:47', '2026-07-28 22:30:47+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45421', 'proveedor', '24722', NULL, 'fe4613b76194c55f6e32eb09ddcf13b282dfad7cc0c8ca28c6ae9f9b35f55887', '45', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-28 20:25:49', '2026-07-29 02:25:49+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45422', 'proveedor', '24723', NULL, '93470ef8f2779210f9dee149db8e7cd878d62df404683fe0782adcde6460d933', '37', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-28 20:48:30', '2026-07-29 02:48:30+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45423', 'proveedor', '24724', NULL, 'cd71dbe807ea9092269b9d50940f76451d4265d662e32e56a389ba2cc2541860', '38', NULL, 'activo', 'motor', 'diesel', '200.000', '5400.00', '2026-07-28 22:04:21', '2026-07-29 04:04:21+00', '2026-07-28', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45424', 'proveedor', '24725', NULL, '4c429d4d2de741f806939e2426305b430a86213363d561c7fb0da5e7b142cb19', NULL, '29', 'activo', 'termo', 'diesel', '150.000', '4108.50', '2026-07-29 11:07:22', '2026-07-29 17:07:22+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45425', 'proveedor', '24726', NULL, '715ffa6bddeb7f9fe1e503b4d19aa1b5ac3c0654c79fa4c5bf711890152ec31e', NULL, NULL, 'cuarentena', NULL, 'diesel', '120.000', '3240.00', '2026-07-29 12:28:44', '2026-07-29 18:28:44+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45426', 'proveedor', '24727', NULL, '0245aec88e9ec8cf655a6dfe4aa5c309a060c90e3430653319a29d108ce2907e', '37', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-29 12:47:28', '2026-07-29 18:47:28+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00');
INSERT INTO public.asientos_consumo (id, origen, carga_id, orden_id, origen_huella, unidad_id, remolque_id, atribucion, destino, combustible, litros, importe, momento_local, momento_ref, fecha_operacion, vigente, evento_id, contable, motivo, proyectado_en) VALUES
('45427', 'proveedor', '24728', NULL, '247635630dd57998a3d24e632ad710ca7fa22c5c47cfdff3631f6515c51f3f45', NULL, NULL, 'fuera_flota', NULL, 'gasolina', '57.000', '1338.93', '2026-07-29 14:02:04', '2026-07-29 20:02:04+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45429', 'proveedor', '24730', NULL, 'f0566130e477d8333bab8e009ad57ceac5b35edc29d8a6beb5266830e7e201c0', NULL, '47', 'activo', 'termo', 'diesel', '90.175', '2421.19', '2026-07-29 16:19:48', '2026-07-29 22:19:48+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45430', 'proveedor', '24731', NULL, 'b5ab550076b29ee2e003ce2eb093dbffc189bfb74dba5c46f72e76c9d839a65c', '45', NULL, 'activo', 'motor', 'diesel', '450.000', '12370.50', '2026-07-29 23:22:09', '2026-07-30 05:22:09+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45431', 'proveedor', '24732', NULL, 'cf94abd1f5777b6b43d65f97db0a89e46c0feeed500b4056fd980c171231d919', NULL, '8', 'activo', 'termo', 'diesel', '140.000', '3848.60', '2026-07-29 23:34:08', '2026-07-30 05:34:08+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45432', 'proveedor', '24733', NULL, 'ca888ebf437e8835212787d90a81663fc4feb9c1b41567a54d2e411376f2ab89', NULL, '15', 'activo', 'termo', 'diesel', '140.000', '3848.60', '2026-07-29 23:46:50', '2026-07-30 05:46:50+00', '2026-07-29', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45433', 'proveedor', '24734', NULL, '7ecf3e35fad9fe09fa654304b30166b25a60fcbc68f4569d34c655b4cba67fd3', NULL, '44', 'activo', 'termo', 'diesel', '100.000', '2700.00', '2026-07-30 00:37:47', '2026-07-30 06:37:47+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45434', 'proveedor', '24735', NULL, 'bf3578c943c3fddd34db4f7190f86f3bf67fc95200ca40a8a66b3d9ae4c0f8da', NULL, NULL, 'cuarentena', NULL, 'diesel', '95.275', '2571.48', '2026-07-30 05:38:17', '2026-07-30 11:38:17+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45435', 'proveedor', '24736', NULL, '722479340c282d9ea4fef0e7be7ff265f045e44c4688be7cc5b0afcb58b9f0da', '36', NULL, 'activo', 'motor', 'diesel', '300.000', '8100.00', '2026-07-30 13:48:54', '2026-07-30 19:48:54+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45436', 'proveedor', '24737', NULL, 'd8f795420ba7103cb6c8a704116f4c570781d50a0e1722debdf38bc46b8a267e', '40', NULL, 'activo', 'motor', 'diesel', '380.000', '10256.20', '2026-07-30 17:41:57', '2026-07-30 23:41:57+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45437', 'proveedor', '24738', NULL, '5358c17b84d20f8a151159a9efea2df91e0d56a6798d0f3e16fb14c5761a30b1', NULL, '39', 'activo', 'termo', 'diesel', '140.000', '3778.60', '2026-07-30 17:51:29', '2026-07-30 23:51:29+00', '2026-07-30', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45438', 'proveedor', '24739', NULL, 'd72ac86adc23def6e4b40c10d42cfea981476edd8eab2a5b309ba8b5bba95b95', NULL, '17', 'activo', 'termo', 'diesel', '130.000', '3510.00', '2026-07-31 00:19:27', '2026-07-31 06:19:27+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45439', 'proveedor', '24740', NULL, '8081a96d49c2fd23369979f9b1e49402e915be9bd450be9d360cdf8be23e0aa8', NULL, '16', 'activo', 'termo', 'diesel', '150.000', '4050.00', '2026-07-31 00:27:43', '2026-07-31 06:27:43+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45440', 'proveedor', '24741', NULL, 'f40b4f6f297a1eaf722cf5e8d23838185fa7cde5cfc4247b562baf88c3481b45', '38', NULL, 'activo', 'motor', 'diesel', '400.000', '10800.00', '2026-07-31 02:01:20', '2026-07-31 08:01:20+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45442', 'proveedor', '24743', NULL, '394abad52a027fe2bf32a8746988aea048dd22af15f0dca91471dd204b87e2e5', NULL, NULL, 'fuera_flota', NULL, 'gasolina', '38.785', '930.84', '2026-07-31 06:29:14', '2026-07-31 12:29:14+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45443', 'proveedor', '24744', NULL, '52e6ffd67a45f26a3de4ca62db11f84cf9fd16d5c53638e284e21dfd23da17fe', '55', NULL, 'activo', 'motor', 'diesel', '290.000', '7827.10', '2026-07-31 11:06:49', '2026-07-31 17:06:49+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45444', 'proveedor', '24745', NULL, '3107ca046cc83d1eb6923a04c151f29dc8f5f12c168468687f56413e96eeaace', '34', NULL, 'activo', 'motor', 'diesel', '150.000', '4153.50', '2026-07-31 15:06:58', '2026-07-31 21:06:58+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45445', 'proveedor', '24746', NULL, 'a318d6991d1e27e1d1871336e0d344026710175aaa695b0ff17831934ce25966', NULL, '42', 'activo', 'termo', 'diesel', '150.000', '4153.50', '2026-07-31 15:13:27', '2026-07-31 21:13:27+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00'),
('45446', 'proveedor', '24747', NULL, 'a78590c5c4a9fbfae4e2c158f762edd1bf2551ecc291739d97bccbcd1c9c80f2', NULL, '29', 'activo', 'termo', 'diesel', '150.000', '4048.50', '2026-07-31 22:36:45', '2026-08-01 04:36:45+00', '2026-07-31', 't', NULL, 't', NULL, '2026-09-02 01:06:52.303734+00');
