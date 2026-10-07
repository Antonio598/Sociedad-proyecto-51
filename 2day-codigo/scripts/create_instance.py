"""Crea la instancia de WhatsApp en Evolution API y configura el webhook.

Uso (desde la carpeta backend/, con el venv activado y docker compose corriendo):
    python -m scripts.create_instance

Después de ejecutarlo, abre http://localhost:8080/manager (usa la API key
como credencial), entra a la instancia y escanea el QR con el número dedicado.
"""

import sys

import httpx

from app.config import settings

WEBHOOK_URL = "http://host.docker.internal:8000/webhook"
EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE", "QRCODE_UPDATED"]

HEADERS = {"apikey": settings.evolution_apikey, "Content-Type": "application/json"}


def instancia_existe(client: httpx.Client) -> bool:
    r = client.get(f"{settings.evolution_url}/instance/fetchInstances", headers=HEADERS)
    r.raise_for_status()
    instancias = r.json()
    return any(
        (i.get("name") or i.get("instance", {}).get("instanceName")) == settings.evolution_instance
        for i in instancias
    )


def crear_instancia(client: httpx.Client) -> None:
    body = {
        "instanceName": settings.evolution_instance,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
    }
    r = client.post(f"{settings.evolution_url}/instance/create", headers=HEADERS, json=body)
    if r.status_code not in (200, 201):
        print(f"Error al crear la instancia ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"Instancia '{settings.evolution_instance}' creada.")


def configurar_webhook(client: httpx.Client) -> None:
    # Formato del schema de Evolution API v2.3.7: las claves dentro de "webhook"
    # son "byEvents" y "base64" (no "webhookByEvents"/"webhookBase64" — esas se
    # ignoran en silencio y las fotos nunca llegarían en el payload).
    body = {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_URL,
            "byEvents": False,
            "base64": True,
            "events": EVENTS,
        }
    }
    url = f"{settings.evolution_url}/webhook/set/{settings.evolution_instance}"
    r = client.post(url, headers=HEADERS, json=body)
    if r.status_code not in (200, 201):
        print(f"Error al configurar el webhook ({r.status_code}): {r.text}")
        sys.exit(1)

    # Verificar que la configuración quedó guardada de verdad
    r = client.get(
        f"{settings.evolution_url}/webhook/find/{settings.evolution_instance}",
        headers=HEADERS,
    )
    r.raise_for_status()
    guardado = r.json() or {}
    base64_activo = guardado.get("webhookBase64", guardado.get("base64", False))
    if not base64_activo:
        print("ERROR: el webhook se guardó pero base64 quedó desactivado.")
        print(f"Respuesta de /webhook/find: {guardado}")
        sys.exit(1)
    print(f"Webhook configurado y verificado -> {WEBHOOK_URL}")
    print(f"  base64: activado | eventos: {', '.join(EVENTS)}")


def main() -> None:
    with httpx.Client(timeout=30) as client:
        try:
            existe = instancia_existe(client)
        except httpx.ConnectError:
            print(f"No se pudo conectar a Evolution API en {settings.evolution_url}.")
            print("¿Está corriendo docker compose? Prueba: docker compose up -d")
            sys.exit(1)

        if existe:
            print(f"La instancia '{settings.evolution_instance}' ya existe, solo actualizo el webhook.")
        else:
            crear_instancia(client)
        configurar_webhook(client)
        print()
        print("Siguiente paso: abre http://localhost:8080/manager y escanea el QR")
        print("con el número de WhatsApp dedicado (WhatsApp > Dispositivos vinculados).")


if __name__ == "__main__":
    main()
