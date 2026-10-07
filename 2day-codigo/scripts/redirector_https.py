"""Escucha en HTTP y manda a HTTPS. Existe por una razón de teléfono, no de arquitectura.

Al teclear "192.168.1.66:8000" en el móvil, Chrome asume `http://`. Si ahí hay un servidor
TLS, el navegador recibe basura y dice `ERR_EMPTY_RESPONSE`, que no le dice a nadie que
faltaba escribir `https://`. Este redirector ocupa el puerto que la gente teclea y contesta
lo único útil: un 301 a la dirección buena.

Redirige al MISMO nombre con el que llamaron —la IP si entraron por la IP, `localhost` si
entraron por localhost—, porque el certificado sólo vale para los nombres que lleva dentro
y saltar de uno a otro dispararía un aviso de seguridad evitable.

Uso:
    python -m scripts.redirector_https [puerto_http] [puerto_https]
"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PUERTO_HTTP = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
PUERTO_HTTPS = int(sys.argv[2]) if len(sys.argv) > 2 else 8443


class ADondeDeVerdad(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _redirigir(self):
        # El Host llega como "192.168.1.66:8000"; se conserva el nombre y se cambia el puerto.
        host = (self.headers.get("Host") or "").split(":")[0] or "localhost"
        destino = f"https://{host}:{PUERTO_HTTPS}{self.path}"
        cuerpo = (f'<meta http-equiv="refresh" content="0;url={destino}">'
                  f'<p>Esta dirección va por HTTPS: <a href="{destino}">{destino}</a>').encode()
        self.send_response(301)
        self.send_header("Location", destino)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    do_GET = do_HEAD = do_POST = _redirigir

    def log_message(self, *a):
        pass          # sin ruido: esto no es un servidor, es un cartel


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PUERTO_HTTP), ADondeDeVerdad)
    print(f"Redirector: http://0.0.0.0:{PUERTO_HTTP}  ->  https://<mismo host>:{PUERTO_HTTPS}")
    srv.serve_forever()
