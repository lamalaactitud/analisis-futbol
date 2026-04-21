"""
PLAYBOOK — Analyst Live  |  iniciar.py
Arranca Flask y (opcionalmente) ngrok para acceso externo.

USO:
  python iniciar.py            — solo red local
  python iniciar.py --ngrok    — activa el túnel ngrok para acceso externo
"""

import sys
import subprocess
import os
import time
import argparse

SEP = "═" * 56

def instalar(paquete):
    print(f"  Instalando {paquete}…")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", paquete, "-q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

# ── Verificar dependencias ────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  PLAYBOOK — Analyst Live  |  Iniciando…")
print(SEP)

print("\n[1/3] Verificando dependencias…")

try:
    import flask
except ImportError:
    instalar("flask")
    import flask
print(f"  Flask {flask.__version__} — OK")

try:
    import requests
except ImportError:
    instalar("requests")
print("  requests — OK")

# ── Argumentos ────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--ngrok", action="store_true",
                    help="Activa el túnel ngrok para acceso externo")
args, _ = parser.parse_known_args()

# ── ngrok (opcional) ──────────────────────────────────────────────────────────
public_url = None

if args.ngrok:
    print("\n[2/3] Configurando ngrok…")
    try:
        from pyngrok import ngrok as _ngrok, conf as _conf
    except ImportError:
        instalar("pyngrok")
        try:
            from pyngrok import ngrok as _ngrok, conf as _conf
        except ImportError:
            print("  ⚠  No se pudo instalar pyngrok.")
            print("     Continúa sin ngrok (solo acceso local).")
            _ngrok = None

    if _ngrok:
        # Si el usuario tiene un authtoken de ngrok, configurarlo aquí:
        # _conf.get_default().auth_token = "TU_TOKEN_AQUI"
        try:
            tunnel    = _ngrok.connect(5000, "http")
            public_url = tunnel.public_url
            print(f"  Túnel activo: {public_url}")
        except Exception as e:
            print(f"  ⚠  ngrok no disponible: {e}")
            print("     Continúa solo con acceso local.")
else:
    print("\n[2/3] ngrok no activado (usa --ngrok para acceso externo)")

# ── Mostrar URLs ──────────────────────────────────────────────────────────────
print(f"\n[3/3] Servidor iniciando…\n")
print(SEP)
print("  PLAYBOOK — Analyst Live  |  LISTO")
print(SEP)
print(f"\n  Acceso local (esta computadora):")
print(f"  → http://localhost:5000")
print(f"  → http://127.0.0.1:5000")

if public_url:
    print(f"\n  Acceso externo (cualquier dispositivo / red):")
    print(f"  → {public_url}")
    print(f"\n  Comparte esa URL con tu equipo.")
    print(f"  El enlace es válido mientras esta ventana esté abierta.")
else:
    print(f"\n  Para acceso externo ejecuta:")
    print(f"  python iniciar.py --ngrok")

print(f"\n  Presiona Ctrl+C para detener el servidor.\n")
print(SEP + "\n")

# ── Arrancar Flask ────────────────────────────────────────────────────────────
# Agregar el directorio actual al path para que Flask encuentre app.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Importar y arrancar la app
from app import app
app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
