"""
PLAYBOOK — Analyst Live  |  app.py
Servidor Flask que expone el analizador táctico como interfaz web en tiempo real.
"""
import sys, os, json, time, threading, queue, logging
from pathlib import Path
from datetime import datetime

# ── Ruta al analizador existente (escritorio, dos niveles arriba de este archivo) ──
DESKTOP = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(DESKTOP))

# ── Flask (se instala automáticamente si falta) ────────────────────────────────
try:
    from flask import Flask, Response, render_template, jsonify, request
except ImportError:
    import subprocess
    print("Instalando Flask…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "-q"])
    from flask import Flask, Response, render_template, jsonify, request

# ── Importar funciones del analizador ─────────────────────────────────────────
ANALYZER_OK   = False
_import_error = ""
try:
    from sofascore_stats import (
        espn_live_events, espn_parse_event,
        apif_find_fixture, _fetch_full,
        analisis_tactico, analisis_arbitral,
        _alerta_dominio, _proxima_min, guardar_snapshot,
        reporte_ht, reporte_ft, _es_estado_final,
        INTERVALO_ACTUALIZACION,
    )
    ANALYZER_OK = True
except Exception as exc:
    _import_error = str(exc)
    logging.warning(f"[Playbook] No se pudo importar sofascore_stats: {exc}")

# ── Aplicación Flask ───────────────────────────────────────────────────────────
app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ── Estado global del monitor ──────────────────────────────────────────────────
_lock   = threading.Lock()
_state  = {
    "running":  False,
    "info":     None,
    "stats":    None,
    "lecturas": None,
    "eventos":  [],
    "arb":      {},
    "alerta":   "",
    "proxima":  "",
    "num_act":  0,
    "error":    None,
    "report":   None,
    "ft_done":  False,
}
_subscribers:  list[queue.Queue] = []   # cola SSE por cliente
_evento_cache: dict              = {}   # event_id → raw ESPN event dict


# ── SSE helpers ───────────────────────────────────────────────────────────────
def _push(event_type: str, payload: dict) -> None:
    """Encola un mensaje SSE para todos los clientes conectados."""
    msg  = json.dumps({"type": event_type, "data": payload})
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead.append(q)
    for q in dead:
        try: _subscribers.remove(q)
        except ValueError: pass


# ── Hilo monitor ──────────────────────────────────────────────────────────────
def _monitor(event_id: str, league: str) -> None:
    """
    Hilo de fondo: consulta ESPN + API-Football cada INTERVALO_ACTUALIZACION
    segundos y emite actualizaciones SSE a todos los clientes conectados.
    """
    global _state

    evento = _evento_cache.get(event_id)
    if evento is None:
        _push("error", {"mensaje": "Evento no encontrado. Recarga la lista de partidos."})
        with _lock: _state["running"] = False
        return

    # Buscar fixture en API-Football (opcional)
    parsed       = espn_parse_event(evento)
    apif_fixture = None
    try:
        apif_fixture = apif_find_fixture(parsed["home_name"], parsed["away_name"])
    except Exception:
        pass
    apif_on = apif_fixture is not None

    history     = []
    ht_done     = False
    ft_done     = False
    stats_ht    = None
    prev_period = None
    num_act     = 0

    while True:
        with _lock:
            if not _state["running"]:
                break

        # ── Fetch principal ────────────────────────────────────────────────
        try:
            stats, info, eventos = _fetch_full(
                event_id, league, evento, apif_fixture, apif_on
            )
        except Exception as exc:
            with _lock: _state["error"] = str(exc)
            _push("error", {"mensaje": str(exc)})
            time.sleep(30)
            continue

        # ── Análisis táctico ───────────────────────────────────────────────
        lecturas = {}
        try:
            lecturas = analisis_tactico(info, stats, history)
            guardar_snapshot(history, info, stats)
        except Exception:
            pass

        num_act += 1
        if prev_period is None:
            prev_period = info.get("period", 1)

        # ── Detectar Medio Tiempo ──────────────────────────────────────────
        report  = None
        periodo = info.get("period", 1)
        es_ht   = info.get("is_ht", False) or (periodo >= 2 and prev_period < 2)

        if not ht_done and es_ht:
            ht_done  = True
            stats_ht = {"home": dict(stats["home"]), "away": dict(stats["away"])}
            ts = datetime.now().strftime("%d/%m/%Y %H:%M")
            try:
                texto = reporte_ht(info, stats, lecturas, history, ts, list(eventos))
            except Exception:
                texto = "INFORME DE MEDIO TIEMPO\n(ver estadísticas en el panel)"
            report = {"tipo": "HT", "texto": texto}

        # ── Detectar Partido Finalizado ────────────────────────────────────
        if not ft_done and _es_estado_final(info):
            ft_done = True
            ts = datetime.now().strftime("%d/%m/%Y %H:%M")
            try:
                texto = reporte_ft(info, stats, lecturas, stats_ht, history, ts, eventos)
            except Exception:
                texto = "INFORME FINAL\n(ver estadísticas en el panel)"
            report = {"tipo": "FT", "texto": texto}

        prev_period = periodo

        # ── Módulo arbitral ────────────────────────────────────────────────
        arb = {}
        try:
            arb = analisis_arbitral(info, stats, eventos) if eventos else {}
        except Exception:
            pass

        # ── Alerta de dominio y próxima actualización ──────────────────────
        alerta  = ""
        proxima = ""
        try:
            alerta  = _alerta_dominio(info, stats) or ""
            proxima = _proxima_min(info.get("match_time", ""))
        except Exception:
            pass

        # ── Actualizar estado global ───────────────────────────────────────
        with _lock:
            _state.update({
                "info":     info,
                "stats":    stats,
                "lecturas": lecturas,
                "eventos":  eventos,
                "arb":      arb,
                "alerta":   alerta,
                "proxima":  proxima,
                "num_act":  num_act,
                "error":    None,
                "report":   report,
                "ft_done":  ft_done,
            })

        # ── Emitir a clientes SSE ──────────────────────────────────────────
        _push("update", {
            "info":     info,
            "stats":    stats,
            "lecturas": lecturas,
            "eventos":  eventos,
            "arb":      arb,
            "alerta":   alerta,
            "proxima":  proxima,
            "num_act":  num_act,
            "report":   report,
        })

        if ft_done:
            _push("ended", {"mensaje": "Partido finalizado"})
            with _lock: _state["running"] = False
            break

        # ── Esperar hasta el próximo ciclo (chequeos cada 30s) ─────────────
        interval = INTERVALO_ACTUALIZACION if ANALYZER_OK else 180
        elapsed  = 0
        while elapsed < interval:
            time.sleep(30)
            elapsed += 30
            with _lock:
                still = _state["running"]
            if not still:
                break


# ── Rutas Flask ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", analyzer_ok=ANALYZER_OK,
                           import_error=_import_error)


@app.route("/api/matches")
def api_matches():
    """Devuelve la lista de partidos en vivo desde ESPN."""
    if not ANALYZER_OK:
        return jsonify({"error": _import_error, "matches": []})
    try:
        raw_events = espn_live_events()
    except Exception as exc:
        return jsonify({"error": str(exc), "matches": []})
    if not raw_events:
        return jsonify({"matches": [], "mensaje": "No hay partidos en este momento."})

    matches = []
    for ev in raw_events:
        try:
            info = espn_parse_event(ev)
            _evento_cache[info["event_id"]] = ev
            matches.append({
                "event_id":   info["event_id"],
                "league":     info["league"],
                "tournament": info["tournament"],
                "home_name":  info["home_name"],
                "away_name":  info["away_name"],
                "home_score": info["home_score"],
                "away_score": info["away_score"],
                "match_time": info["match_time"],
                "state":      info["state"],
                "is_ht":      info["is_ht"],
            })
        except Exception:
            continue
    return jsonify({"matches": matches})


@app.route("/api/start", methods=["POST"])
def api_start():
    """Inicia el monitoreo del partido seleccionado."""
    body     = request.get_json(force=True)
    event_id = str(body.get("event_id", ""))
    league   = body.get("league", "all")

    with _lock:
        if _state["running"]:
            _state["running"] = False
            time.sleep(1)
        _state.update({
            "running":  True,
            "info":     None,  "stats":    None,
            "lecturas": None,  "eventos":  [],
            "arb":      {},    "alerta":   "",
            "proxima":  "",    "num_act":  0,
            "error":    None,  "report":   None,
            "ft_done":  False,
        })

    t = threading.Thread(target=_monitor, args=(event_id, league), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with _lock:
        _state["running"] = False
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_state)


@app.route("/stream")
def stream():
    """
    Endpoint SSE (Server-Sent Events).
    Mantiene la conexión abierta y envía eventos en tiempo real.
    """
    q = queue.Queue(maxsize=50)
    _subscribers.append(q)

    def generate():
        # Enviar snapshot inmediato al conectar
        with _lock:
            snap = dict(_state)
        if snap.get("info"):
            yield f"data: {json.dumps({'type': 'update', 'data': snap})}\n\n"

        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"   # heartbeat — mantiene la conexión viva
        except GeneratorExit:
            pass
        finally:
            try: _subscribers.remove(q)
            except ValueError: pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Punto de entrada ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    sep = "═" * 52
    print(f"\n{sep}")
    print("  PLAYBOOK — Analyst Live")
    print(f"  Analizador: {'OK ✓' if ANALYZER_OK else 'ERROR — ' + _import_error[:40]}")
    print(f"  Servidor local: http://localhost:5000")
    print(f"{sep}\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
