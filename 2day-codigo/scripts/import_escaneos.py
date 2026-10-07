"""Importa los PDF de escaneo del motor (Cummins PowerSpec / Detroit DDEC) a la BD.

Uso:  python scripts/import_escaneos.py "ruta\\a\\la\\carpeta"
      (sin argumento usa la carpeta 'Escaneo de unidades PDF' de la raíz del proyecto)

Es idempotente por DOS puertas, y hacen falta las dos:
  1. el NOMBRE del archivo (`EscaneoMotor.archivo` es unique): se salta lo ya importado.
  2. el ODÓMETRO DE CIERRE de la unidad, que es lo que el nombre no cubre. El mismo PDF
     guardado con dos nombres distintos pasaba la puerta 1 sin problema: así entraron los
     períodos de T203 y T228, idénticos campo por campo y con el nombre del segundo de
     T228 mintiendo (dice 12.06 y su período cierra el 01.06). La puerta 2 los rechaza.

POR QUÉ EL ODÓMETRO IDENTIFICA EL PERÍODO. `odometro_total` es el contador de por vida del
motor, estrictamente creciente dentro de una unidad: un escaneo con km > 0 no puede dejarlo
donde estaba. Dos lecturas de la misma unidad que cierran en el mismo kilómetro son la misma
lectura. Sobre los 43 escaneos de la base la regla atrapa los 2 casos conocidos y ni uno más.
Es la MISMA regla con la que `app/rendimiento.py::_serie()` se defiende al LEER; aquí cierra
la puerta antes, para que la base deje de aceptarlos.

En los Cummins el período de INICIO no viene en el PDF; se infiere como el fin del
escaneo ANTERIOR de esa misma unidad (los reportes son consecutivos: el Trip se reinicia
en cada extracción, verificado contra los acumulados del motor).
"""
import sys
from pathlib import Path

# La consola de Windows es cp1252 y revienta con los acentos que este script imprime
# ('Períodos', 'odómetro') en cuanto la salida se redirige a un archivo.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
# `llave_odometro` se re-exporta a propósito: vive en app/escaneo.py porque ahora la
# usan DOS puertas (este script y la carga desde la pantalla de Combustible), y quien
# hace `import_escaneos.llave_odometro(...)` —verificar_escaneo_duplicados.py y sus 7
# comprobaciones— sigue apuntando a la única definición que queda.
from app.escaneo import leer_pdf, llave_odometro  # noqa: F401
from app.models import EscaneoMotor, TipoUnidad, Unidad

RAIZ_DEFECTO = Path(__file__).resolve().parent.parent.parent / "Escaneo de unidades PDF"


def main(carpeta: Path, session=None) -> dict:
    """Importa la carpeta y devuelve el recuento de lo que hizo.

    `session` existe para que `scripts/verificar_escaneo_duplicados.py` pueda correr ESTA
    importación —la de verdad, sin trucos— dentro de una transacción de ensayo y deshacerla:
    la base es la viva del cliente y no se muta para probar. Sin argumento se comporta igual
    que siempre: abre su propia sesión y la cierra.
    """
    pdfs = sorted(p for p in carpeta.rglob("*.pdf") if "__MACOSX" not in str(p))
    if not pdfs:
        print(f"No se encontraron PDF en {carpeta}")
        return {"nuevos": 0, "saltados": 0, "repetidos": [], "fallidos": 0, "total": None}
    print(f"Encontrados {len(pdfs)} PDF en {carpeta}\n")

    propia = session is None
    s = SessionLocal() if propia else session
    nuevos = saltados = fallidos = 0
    sin_unidad = []
    repetidos = []
    try:
        ya = {a for (a,) in s.execute(select(EscaneoMotor.archivo))}
        # Puerta 2: el odómetro de cierre de lo que YA está en la base. El diccionario se va
        # ampliando con lo insertado en esta misma corrida, así que dos copias del mismo PDF
        # que lleguen JUNTAS —ninguna de las dos en la base todavía— tampoco pasan.
        odo_vistos: dict[tuple[int, float], str] = {}
        for uid, odo, km, arch in s.execute(
                select(EscaneoMotor.unidad_id, EscaneoMotor.odometro_total,
                       EscaneoMotor.km, EscaneoMotor.archivo)):
            k = llave_odometro(uid, odo, km)
            if k is not None:
                odo_vistos.setdefault(k, arch)
        registros = []
        for p in pdfs:
            if p.name in ya:
                saltados += 1
                continue
            try:
                registros.append(leer_pdf(p))
            except Exception as e:
                fallidos += 1
                print(f"  ! {p.name}: {e}")

        for d in registros:
            u = s.execute(select(Unidad).where(Unidad.clave == d["unidad"])).scalar_one_or_none()
            if u is None:
                # La unidad del escaneo debe existir en el catálogo; si no, se registra
                # (es una unidad real de la flota que aún no estaba dada de alta).
                tipo = TipoUnidad.TRACTO if d["unidad"].startswith("T") else TipoUnidad.CAMION
                u = Unidad(clave=d["unidad"], tipo=tipo)
                s.add(u)
                s.flush()
                sin_unidad.append(d["unidad"])
            k = llave_odometro(u.id, d.get("odometro_total"), d.get("km"))
            if k is not None and k in odo_vistos:
                repetidos.append((d["archivo"], d["unidad"], k[1], odo_vistos[k]))
                continue
            s.add(EscaneoMotor(
                unidad_id=u.id, archivo=d["archivo"], formato=d["formato"],
                motor=d.get("motor"), serie_motor=d.get("serie_motor"),
                periodo_inicio=d.get("periodo_inicio"), periodo_fin=d["periodo_fin"],
                odometro_total=d.get("odometro_total"), lts_total=d.get("lts_total"),
                lts_ralenti_total=d.get("lts_ralenti_total"),
                km=d["km"], litros=d["litros"], rendimiento=d.get("rendimiento"),
                tiempo=d.get("tiempo"), lts_ralenti=d.get("lts_ralenti"),
                pct_ralenti=d.get("pct_ralenti"), tiempo_ralenti=d.get("tiempo_ralenti"),
                vel_max=d.get("vel_max"), vel_prom=d.get("vel_prom"),
                rpm_prom=d.get("rpm_prom"), rpm_max=d.get("rpm_max"),
                carga_prom=d.get("carga_prom"), km_crucero=d.get("km_crucero"),
                km_top_gear=d.get("km_top_gear"), frenadas=d.get("frenadas"),
                paradas_panico=d.get("paradas_panico"),
            ))
            if k is not None:
                odo_vistos[k] = d["archivo"]
            nuevos += 1
        s.commit()

        # Inferir el inicio del período en los que no lo traen (Cummins), encadenando
        # con el fin del escaneo anterior de la MISMA unidad.
        inferidos = 0
        for u_id, in s.execute(select(EscaneoMotor.unidad_id).distinct()):
            esc = s.execute(
                select(EscaneoMotor).where(EscaneoMotor.unidad_id == u_id)
                .order_by(EscaneoMotor.periodo_fin, EscaneoMotor.id)
            ).scalars().all()
            for prev, act in zip(esc, esc[1:]):
                if act.periodo_inicio is None:
                    act.periodo_inicio = prev.periodo_fin
                    inferidos += 1
        s.commit()

        print(f"\nIMPORTADOS: {nuevos}   ya estaban: {saltados}   "
              f"repetidos: {len(repetidos)}   fallidos: {fallidos}")
        if repetidos:
            # Se dice completo y con nombre y apellido: cuál llegó, contra cuál chocó y en
            # qué kilómetro. Un contador a secas obligaría a abrir la base para entenderlo.
            print(f"\nREPETIDOS ({len(repetidos)}): NO se insertaron. Ya hay un escaneo de esa "
                  f"unidad que cierra en el MISMO odómetro,")
            print("o sea el mismo período guardado con otro nombre de archivo:")
            for arch, uni, odo, ya_esta in repetidos:
                print(f"  - «{arch}» ({uni}, cierra en {odo:,.2f} km)")
                print(f"      ya estaba en la base como «{ya_esta}»")
            print("  El nombre del archivo NO es prueba de nada: en T228 el nombre decía 12.06 "
                  "y el período cerraba el 01.06.")
            print("  Si crees que alguno NO es repetición, compáralo campo por campo antes de "
                  "forzarlo: el odómetro es")
            print("  un contador de por vida, y un período con kilómetros no puede cerrar "
                  "donde cerró el anterior.")
        print(f"Períodos de inicio inferidos (encadenando): {inferidos}")
        if sin_unidad:
            print(f"Unidades creadas por venir en un escaneo: {', '.join(sorted(set(sin_unidad)))}")

        total = s.scalar(select(__import__('sqlalchemy').func.count()).select_from(EscaneoMotor))
        print(f"Total de escaneos en la BD: {total}")
        return {"nuevos": nuevos, "saltados": saltados, "repetidos": repetidos,
                "fallidos": fallidos, "inferidos": inferidos, "total": total}
    finally:
        if propia:
            s.close()


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ_DEFECTO
    main(carpeta)
