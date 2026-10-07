"""Lista los grupos de WhatsApp de la instancia para obtener el JID del grupo de la flota.

Uso (con la instancia ya vinculada por QR):
    python -m scripts.list_groups

Copia el JID del grupo de la operación a GROUP_JID en backend/.env.
"""

import httpx

from app.config import settings

HEADERS = {"apikey": settings.evolution_apikey}


def main() -> None:
    url = f"{settings.evolution_url}/group/fetchAllGroups/{settings.evolution_instance}"
    with httpx.Client(timeout=60) as client:
        r = client.get(url, headers=HEADERS, params={"getParticipants": "false"})
        r.raise_for_status()
        grupos = r.json()

    if not grupos:
        print("No se encontraron grupos. ¿Ya está vinculado el número y es miembro del grupo?")
        return

    print(f"{'JID':<40} Nombre")
    print("-" * 70)
    for g in grupos:
        print(f"{g.get('id', ''):<40} {g.get('subject', '')}")
    print()
    print("Copia el JID del grupo de la flota a GROUP_JID en backend/.env y reinicia el backend.")


if __name__ == "__main__":
    main()
