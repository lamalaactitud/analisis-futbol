#!/usr/bin/env python3
"""
Football Live Analysis System v3
Fuentes: ESPN (principal) + API-Football (opcional)
Análisis táctico dinámico + reportes HT/FT inmediatos + evolución estadística.
"""

import re
import requests
import time
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# ════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN — edita solo esta sección
# ════════════════════════════════════════════════════════════════

INTERVALO_ACTUALIZACION = 300   # segundos entre actualizaciones completas
INTERVALO_MONITOR       = 30    # segundos entre chequeos de estado

API_FOOTBALL_KEY = "ad862569421ecfb58354dce03c811791"

# ════════════════════════════════════════════════════════════════
#  CONSTANTES
# ════════════════════════════════════════════════════════════════

ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports/soccer"
APIF_BASE    = "https://v3.football.api-sports.io"
HEADERS_ESPN = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

STATS_MAP = {
    "possessionPct":  "Posesión (%)",
    "shotsOnTarget":  "Tiros al arco",
    "totalShots":     "Tiros totales",
    "wonCorners":     "Córners",
    "foulsCommitted": "Faltas",
    "yellowCards":    "T. Amarillas",
    "redCards":       "T. Rojas",
    "saves":          "Atajadas",
    "passesAccurate": "Pases completados",
    "passesTotal":    "Pases totales",
    "passesPct":      "Precisión pases (%)",
    "offsides":       "Offsides",
    "xG":             "xG (goles esperados)",
}

APIF_KEY_MAP = {
    "Ball Possession":  "possessionPct",
    "Shots on Goal":    "shotsOnTarget",
    "Total Shots":      "totalShots",
    "Corner Kicks":     "wonCorners",
    "Fouls":            "foulsCommitted",
    "Yellow Cards":     "yellowCards",
    "Red Cards":        "redCards",
    "Goalkeeper Saves": "saves",
    "Total passes":     "passesTotal",
    "Passes accurate":  "passesAccurate",
    "Passes %":         "passesPct",
    "Offsides":         "offsides",
    "Expected Goals":   "xG",
    "expected_goals":   "xG",
}

ESTADOS_FINALES = {
    "STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_FINAL_AET",
    "STATUS_FINAL_PEN", "STATUS_ABANDONED", "STATUS_POSTPONED",
}

# ════════════════════════════════════════════════════════════════
#  UTILIDADES
# ════════════════════════════════════════════════════════════════

def _n(val):
    try:
        return float(str(val).replace("%", "").strip())
    except (TypeError, ValueError):
        return None

def _sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def _wrap(texto, ancho=68, indent="  "):
    palabras = texto.split()
    lineas, linea = [], indent
    for p in palabras:
        if len(linea) + len(p) + 1 > ancho:
            lineas.append(linea.rstrip())
            linea = indent + p + " "
        else:
            linea += p + " "
    if linea.strip():
        lineas.append(linea.rstrip())
    return "\n".join(lineas)

def _extraer_minuto(match_time):
    m = re.search(r'(\d+)', str(match_time))
    return int(m.group(1)) if m else None

def _header_tiempo(info):
    hora       = datetime.now().strftime("%H:%M")
    match_time = info.get("match_time", "") if isinstance(info, dict) else info
    period     = info.get("period", 1)      if isinstance(info, dict) else 1
    is_ht      = info.get("is_ht", False)   if isinstance(info, dict) else False
    state      = info.get("state", "")      if isinstance(info, dict) else ""

    if state == "post" or match_time == "FT":
        return f"[{hora}  |  PARTIDO FINALIZADO]"
    if is_ht or match_time == "HT":
        return f"[{hora}  |  MEDIO TIEMPO]"
    if match_time in ("AET",):
        return f"[{hora}  |  TIEMPO EXTRA]"
    if match_time in ("AP", "Penalties"):
        return f"[{hora}  |  PENALES]"

    minuto = _extraer_minuto(match_time)
    min_str = f" - Min. {minuto}'" if minuto else ""
    if period == 1:
        return f"[{hora}  |  1er TIEMPO{min_str}]"
    elif period == 2:
        return f"[{hora}  |  2do TIEMPO{min_str}]"
    elif period == 3:
        return f"[{hora}  |  TIEMPO EXTRA{min_str}]"
    elif period >= 4:
        return f"[{hora}  |  PENALES]"
    return f"[{hora}  |  {match_time}]"

def _es_estado_final(info):
    state       = info.get("state", "")
    status_name = info.get("status_name", "").upper()
    match_time  = info.get("match_time", "")
    return (state == "post" or
            status_name in ESTADOS_FINALES or
            match_time in ("FT", "AET", "AP"))

# ════════════════════════════════════════════════════════════════
#  HISTORIAL DE EVOLUCIÓN
# ════════════════════════════════════════════════════════════════

def guardar_snapshot(history, info, stats):
    history.append({
        "ts":         datetime.now().strftime("%H:%M"),
        "match_time": info.get("match_time", ""),
        "minuto":     _extraer_minuto(info.get("match_time", "")),
        "score_h":    info.get("home_score", "0"),
        "score_a":    info.get("away_score", "0"),
        "stats":      {"home": dict(stats.get("home", {})),
                       "away": dict(stats.get("away", {}))},
    })

def tabla_evolucion(history, h, a):
    if len(history) < 2:
        return ""
    claves = [
        ("possessionPct", "Posesión (%)  "),
        ("totalShots",    "Tiros totales "),
        ("shotsOnTarget", "Al arco       "),
        ("wonCorners",    "Córners       "),
    ]
    lineas = []
    for clave, label in claves:
        puntos = []
        for snap in history:
            vh = _n(snap["stats"]["home"].get(clave))
            va = _n(snap["stats"]["away"].get(clave))
            tag = f"min.{snap['minuto']}" if snap["minuto"] else snap["ts"]
            if vh is not None and va is not None:
                es_pct = "pct" in clave.lower()
                fmt = f"{vh:.0f}{'%' if es_pct else ''}-{va:.0f}{'%' if es_pct else ''}"
                puntos.append(f"{tag}({fmt})")
        if len(puntos) >= 2:
            lineas.append(f"  {label}: " + " → ".join(puntos))
    return "\n".join(lineas)

def _interpretar_evolucion(history, h, a):
    if len(history) < 2:
        return ""
    first, last = history[0], history[-1]
    partes = []

    pos_h_ini = _n(first["stats"]["home"].get("possessionPct"))
    pos_h_fin = _n(last["stats"]["home"].get("possessionPct"))
    if pos_h_ini is not None and pos_h_fin is not None:
        diff = pos_h_fin - pos_h_ini
        if diff >= 8:
            partes.append(
                f"{h} fue ganando el control progresivamente: de {pos_h_ini:.0f}% a "
                f"{pos_h_fin:.0f}% de posesión. La propuesta local se impuso con el tiempo."
            )
        elif diff <= -8:
            partes.append(
                f"{h} cedió el dominio con el correr del partido ({pos_h_ini:.0f}% → "
                f"{pos_h_fin:.0f}%). {a} tomó el control en la segunda fase del juego."
            )

    sht_h_ini = _n(first["stats"]["home"].get("totalShots"))
    sht_h_fin = _n(last["stats"]["home"].get("totalShots"))
    sht_a_ini = _n(first["stats"]["away"].get("totalShots"))
    sht_a_fin = _n(last["stats"]["away"].get("totalShots"))
    if all(v is not None for v in [sht_h_ini, sht_h_fin, sht_a_ini, sht_a_fin]):
        ritmo_h = sht_h_fin - sht_h_ini
        ritmo_a = sht_a_fin - sht_a_ini
        if ritmo_h > ritmo_a + 3:
            partes.append(
                f"{h} fue el equipo más insistente en la búsqueda del arco, "
                f"acumulando {sht_h_fin:.0f} remates en total."
            )
        elif ritmo_a > ritmo_h + 3:
            partes.append(
                f"{a} incrementó su presión ofensiva a lo largo del partido, "
                f"terminando con {sht_a_fin:.0f} remates totales."
            )
    return " ".join(partes)

# ════════════════════════════════════════════════════════════════
#  FUENTE ESPN
# ════════════════════════════════════════════════════════════════

def _espn_all_events():
    try:
        r = requests.get(f"{ESPN_BASE}/all/scoreboard", headers=HEADERS_ESPN, timeout=15)
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception:
        return []

def espn_live_events():
    try:
        todos = _espn_all_events()
        vivos = [e for e in todos
                 if e.get("competitions", [{}])[0].get("status", {})
                    .get("type", {}).get("state") == "in"]
        return vivos or todos
    except Exception:
        return None

def espn_summary(event_id, league="all"):
    try:
        r = requests.get(f"{ESPN_BASE}/{league}/summary?event={event_id}",
                         headers=HEADERS_ESPN, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

def espn_parse_event(ev):
    comp  = ev.get("competitions", [{}])[0]
    comps = comp.get("competitors", [{}, {}])
    home  = next((c for c in comps if c.get("homeAway") == "home"), comps[0] if comps else {})
    away  = next((c for c in comps if c.get("homeAway") == "away"), comps[1] if len(comps) > 1 else {})

    status      = comp.get("status", {})
    state       = status.get("type", {}).get("state", "")
    clock       = status.get("displayClock", "")
    detail      = status.get("type", {}).get("shortDetail", "")
    period      = status.get("period", 1)
    status_name = status.get("type", {}).get("name", "")
    is_ht       = (status_name.upper() in ("STATUS_HALFTIME", "STATUS_HALF_TIME") or
                   detail.upper() in ("HT", "HALF TIME", "HALFTIME"))

    if state == "post":   match_time = "FT"
    elif state == "pre":  match_time = detail or "Por jugar"
    elif is_ht:           match_time = "HT"
    else:                 match_time = clock or detail or "En vivo"

    return {
        "home_name":   home.get("team", {}).get("displayName", "Local"),
        "away_name":   away.get("team", {}).get("displayName", "Visitante"),
        "home_score":  home.get("score", "0"),
        "away_score":  away.get("score", "0"),
        "match_time":  match_time,
        "state":       state,
        "is_ht":       is_ht,
        "period":      period,
        "status_name": status_name,
        "tournament":  ev.get("name", "") or ev.get("season", {}).get("slug", ""),
        "event_id":    ev.get("id", ""),
        "league":      ev.get("league", {}).get("slug", "all"),
    }

def espn_parse_stats(summary):
    stats = {"home": {}, "away": {}}
    for team in summary.get("boxscore", {}).get("teams", []):
        side = team.get("homeAway", "home")
        for s in team.get("statistics", []):
            stats[side][s.get("name", "")] = s.get("displayValue", s.get("value"))
    return stats

# ════════════════════════════════════════════════════════════════
#  FUENTE API-FOOTBALL
# ════════════════════════════════════════════════════════════════

def _apif_headers():
    return {"x-apisports-key": API_FOOTBALL_KEY, "Accept": "application/json"}

def apif_diagnostico():
    """Prueba la clave y el plan de API-Football. Retorna string con resultado."""
    if not API_FOOTBALL_KEY:
        return "API-Football: clave vacía — desactivado."
    try:
        r = requests.get(f"{APIF_BASE}/status", headers=_apif_headers(), timeout=10)
        data = r.json()
        errors = data.get("errors", {})
        if r.status_code == 401 or errors:
            return "API-Football: CLAVE INVÁLIDA — revisa en dashboard.api-football.com"
        if r.status_code != 200:
            return f"API-Football: error HTTP {r.status_code}"
        response = data.get("response", {})
        if not isinstance(response, dict):
            return "API-Football OK — conectado"
        sub    = response.get("subscription", {})
        plan   = sub.get("plan", "desconocido") if isinstance(sub, dict) else str(sub)
        reqs   = response.get("requests", {})
        usado  = reqs.get("current", "?") if isinstance(reqs, dict) else "?"
        limite = reqs.get("limit_day", "?") if isinstance(reqs, dict) else "?"
        return f"API-Football OK — Plan: {plan} | Requests hoy: {usado}/{limite}"
    except Exception as e:
        return f"API-Football: sin conexión — {e}"

def apif_find_fixture(home_name, away_name):
    if not API_FOOTBALL_KEY:
        return None
    try:
        r = requests.get(f"{APIF_BASE}/fixtures", params={"live": "all"},
                         headers=_apif_headers(), timeout=15)
        data = r.json()
        if r.status_code != 200 or data.get("errors"):
            print(f"  [API-Football] Error en fixtures: {data.get('errors', r.status_code)}")
            return None
        fixtures = data.get("response", [])
        best_score, best = 0, None
        for fx in fixtures:
            t = fx.get("teams", {})
            h_n = t.get("home", {}).get("name", "")
            a_n = t.get("away", {}).get("name", "")
            sc = max((_sim(home_name, h_n) + _sim(away_name, a_n)) / 2,
                     (_sim(home_name, a_n) + _sim(away_name, h_n)) / 2)
            if sc > best_score:
                best_score, best = sc, fx
        if best_score < 0.50:
            print(f"  [API-Football] Partido no encontrado (mejor coincidencia: {best_score:.0%})")
            return None
        return best
    except Exception as e:
        print(f"  [API-Football] Excepción en búsqueda: {e}")
        return None

def apif_get_stats(fixture_id):
    if not API_FOOTBALL_KEY or not fixture_id:
        return {"home": {}, "away": {}}
    try:
        r = requests.get(f"{APIF_BASE}/fixtures/statistics",
                         params={"fixture": fixture_id},
                         headers=_apif_headers(), timeout=15)
        data = r.json()
        if r.status_code != 200 or data.get("errors"):
            return {"home": {}, "away": {}}
        response = data.get("response", [])
        out = {"home": {}, "away": {}}
        for i, team_data in enumerate(response[:2]):
            side = "home" if i == 0 else "away"
            for stat in team_data.get("statistics", []):
                key = APIF_KEY_MAP.get(stat.get("type", ""))
                if key and stat.get("value") is not None:
                    out[side][key] = stat["value"]
        return out
    except Exception:
        return {"home": {}, "away": {}}

def merge_stats(espn, apif):
    merged = {"home": dict(espn.get("home", {})), "away": dict(espn.get("away", {}))}
    for side in ("home", "away"):
        for k, v in apif.get(side, {}).items():
            if v is not None:
                merged[side][k] = v
    return merged

# ════════════════════════════════════════════════════════════════
#  MOTOR DE ANÁLISIS TÁCTICO
# ════════════════════════════════════════════════════════════════

def analisis_tactico(info, stats, history=None):
    h, a    = info["home_name"], info["away_name"]
    hs, as_ = stats["home"], stats["away"]

    pos_h  = _n(hs.get("possessionPct"));   pos_a  = _n(as_.get("possessionPct"))
    sht_h  = _n(hs.get("totalShots"));      sht_a  = _n(as_.get("totalShots"))
    sot_h  = _n(hs.get("shotsOnTarget"));   sot_a  = _n(as_.get("shotsOnTarget"))
    cor_h  = _n(hs.get("wonCorners"));      cor_a  = _n(as_.get("wonCorners"))
    foul_h = _n(hs.get("foulsCommitted"));  foul_a = _n(as_.get("foulsCommitted"))
    yc_h   = _n(hs.get("yellowCards"));     yc_a   = _n(as_.get("yellowCards"))
    rc_h   = _n(hs.get("redCards"));        rc_a   = _n(as_.get("redCards"))
    sav_h  = _n(hs.get("saves"));           sav_a  = _n(as_.get("saves"))
    ptot_h = _n(hs.get("passesTotal"));     ptot_a = _n(as_.get("passesTotal"))
    pok_h  = _n(hs.get("passesAccurate"));  pok_a  = _n(as_.get("passesAccurate"))
    xg_h   = _n(hs.get("xG"));             xg_a   = _n(as_.get("xG"))
    off_h  = _n(hs.get("offsides"));        off_a  = _n(as_.get("offsides"))
    gh     = _n(info["home_score"]);         ga     = _n(info["away_score"])

    lecturas = {}

    # ── 1. CONTROL TERRITORIAL ─────────────────────────────────────────────────
    t = []
    if pos_h is not None and pos_a is not None:
        dif  = abs(pos_h - pos_a)
        dom  = h if pos_h >= pos_a else a
        sub  = a if pos_h >= pos_a else h
        myor = max(pos_h, pos_a); mnor = min(pos_h, pos_a)
        if dif < 5:
            t.append(f"La pelota se reparte de manera casi equitativa ({pos_h:.0f}%–{pos_a:.0f}%). "
                     "El mediocampo es tierra de nadie; las disputas en zona media deciden quién "
                     "toma la iniciativa.")
        elif dif < 15:
            t.append(f"{dom} maneja más el balón ({myor:.0f}% frente al {mnor:.0f}% de {sub}). "
                     f"{sub} cede el centro del campo y se organiza para salir en transición rápida.")
        else:
            t.append(f"{dom} ejerce un dominio territorial claro con el {myor:.0f}% de posesión. "
                     f"La propuesta es controlar los tiempos y decidir cuándo atacar. "
                     f"{sub} apuesta al bloque bajo y al contragolpe.")

    if ptot_h and pok_h and ptot_h > 0:
        pct = (pok_h / ptot_h) * 100
        if pct >= 88:
            t.append(f"{h} mueve el balón con notable precisión: {pok_h:.0f}/{ptot_h:.0f} pases "
                     f"completados ({pct:.0f}%). Circulación posicional que busca atraer al rival "
                     "para generar superioridades en zonas liberadas.")
        elif pct < 74 and ptot_h >= 80:
            t.append(f"{h} tropieza en la salida: {100-pct:.0f}% de sus pases no encuentran "
                     "destino. La presión rival en el inicio de jugada le genera errores constantes.")

    if cor_h is not None and cor_a is not None:
        tot_cor = cor_h + cor_a
        if tot_cor >= 6:
            dom_c = h if cor_h > cor_a else a
            t.append(f"{tot_cor:.0f} saques de esquina revelan un partido muy disputado en los flancos. "
                     f"{dom_c} es quien más insiste con centros al área ({max(cor_h, cor_a):.0f} córners).")

    lecturas["territorial"] = " ".join(t) if t else \
        "Datos territoriales aún insuficientes. Se completará en próximas actualizaciones."

    # ── 2. EFICACIA OFENSIVA ────────────────────────────────────────────────────
    o = []
    if sht_h is not None and sht_a is not None:
        if sht_h > sht_a + 5:
            o.append(f"{h} lleva la carga atacante con {sht_h:.0f} remates frente a "
                     f"{sht_a:.0f} de {a}. Superioridad clara en la generación de peligro.")
        elif sht_a > sht_h + 5:
            o.append(f"{a} domina en llegadas ({sht_a:.0f} vs {sht_h:.0f}). "
                     f"{h} está siendo superado en la fase de creación.")
        elif sht_h >= 4 and sht_a >= 4:
            o.append(f"El partido se juega abierto: {sht_h:.0f} remates de {h} y "
                     f"{sht_a:.0f} de {a}. Ambas porterías bajo amenaza.")

    for nombre, sht, sot in [(h, sht_h, sot_h), (a, sht_a, sot_a)]:
        if sht and sot is not None and sht > 0:
            ratio = sot / sht
            if ratio >= 0.60 and sot >= 3:
                o.append(f"{nombre} es clínico en el disparo: {sot:.0f} de {sht:.0f} "
                         f"remates van al arco ({ratio*100:.0f}%). Calidad por encima de cantidad.")
            elif ratio < 0.30 and sht >= 6:
                o.append(f"{nombre} genera pero no afina: solo {sot:.0f} de {sht:.0f} "
                         "tiros entre los tres palos.")

    if xg_h is not None and xg_a is not None:
        o.append(f"Los goles esperados sitúan a {h} en {xg_h:.2f} xG y a {a} en {xg_a:.2f} xG.")
        if gh is not None and gh > xg_h + 0.5:
            o.append(f"{h} está sobrerendiendo respecto a la calidad de sus ocasiones.")
        elif gh is not None and gh < xg_h - 0.5:
            o.append(f"{h} está siendo ineficaz: el marcador le debe más goles.")
        if ga is not None and ga > xg_a + 0.5:
            o.append(f"{a} convierte por encima del valor de sus oportunidades.")

    if cor_h is not None and gh is not None and cor_h >= 3 and gh >= 1:
        o.append(f"La amenaza en pelota parada de {h} ({cor_h:.0f} córners) puede ser "
                 "un factor determinante en la creación de peligro real.")
    if cor_a is not None and ga is not None and cor_a >= 3 and ga >= 1:
        o.append(f"{a} explota los saques de esquina ({cor_a:.0f}) como vía de llegada al arco.")

    lecturas["ofensiva"] = " ".join(o) if o else \
        "Estadísticas ofensivas en construcción. Se actualizará con más datos."

    # ── 3. SOLIDEZ DEFENSIVA ────────────────────────────────────────────────────
    d = []
    for nombre_gk, sav, equipo_rival in [(h, sav_h, a), (a, sav_a, h)]:
        if sav is not None:
            if sav >= 5:
                d.append(f"El portero de {nombre_gk} es la figura del partido con {sav:.0f} "
                         "atajadas. Su actuación está cambiando el resultado.")
            elif sav >= 3:
                d.append(f"El arquero de {nombre_gk} tuvo una tarde activa: "
                         f"{sav:.0f} intervenciones bajo palos.")
            elif sav == 0:
                d.append(f"El portero de {nombre_gk} no fue exigido (0 atajadas), "
                         f"señal de solidez defensiva o de escasa profundidad de {equipo_rival}.")

    if foul_h is not None and foul_a is not None:
        tot_f = foul_h + foul_a
        if foul_h > foul_a + 6:
            d.append(f"{h} recurre al foul como herramienta defensiva ({foul_h:.0f} faltas). "
                     "No logra robar limpio y necesita interrumpir el juego para reorganizarse.")
        elif foul_a > foul_h + 6:
            d.append(f"{a} comete {foul_a:.0f} faltas, evidenciando dificultad para contener "
                     f"a {h} sin recurrir al contacto.")
        if tot_f >= 28:
            d.append(f"Partido físicamente muy intenso: {tot_f:.0f} faltas en total. "
                     "El árbitro debe mantener el control.")

    if yc_h is not None and yc_a is not None:
        tot_am = (yc_h or 0) + (yc_a or 0)
        if tot_am >= 5:
            d.append(f"Alta tensión disciplinaria: {tot_am:.0f} tarjetas amarillas "
                     f"({yc_h:.0f} para {h}, {yc_a:.0f} para {a}). El partido está al filo.")
        elif (yc_h or 0) >= 3:
            d.append(f"{h} acumula {yc_h:.0f} amonestaciones; la próxima puede cambiar el partido.")
        elif (yc_a or 0) >= 3:
            d.append(f"{a} tiene {yc_a:.0f} amarillas y juega al límite.")

    if rc_h and rc_h >= 1:
        d.append(f"INFERIORIDAD NUMÉRICA: {h} juega con un hombre menos.")
    if rc_a and rc_a >= 1:
        d.append(f"INFERIORIDAD NUMÉRICA: {a} está con un jugador menos.")

    lecturas["defensiva"] = " ".join(d) if d else \
        "El partido transcurre sin grandes alarmas defensivas hasta el momento."

    # ── 4. TRANSICIONES, RITMO Y DIMENSIONES ALTERNATIVAS ──────────────────────
    tr = []
    if pos_h is not None and sht_h is not None and sht_a is not None:
        if pos_h < 44 and sht_h >= (sht_a or 0):
            tr.append(f"{h} construye su juego en las transiciones: con solo el {pos_h:.0f}% "
                      "del balón iguala o supera en remates al rival. Equipo que vive en el "
                      "contraataque, explotando los espacios que deja el oponente al avanzar.")
        if pos_a is not None and pos_a < 44 and sht_a >= (sht_h or 0):
            tr.append(f"{a} es un equipo de transiciones puras: el {pos_a:.0f}% de posesión "
                      "contrasta con su capacidad de generar ocasiones.")
        if pos_h > 58 and sht_h and sht_h >= 8:
            tr.append(f"{h} impone un ritmo muy alto: dominio territorial y alto volumen de "
                      "remates. Presiona alto y convierte cada recuperación en ocasión.")

    if off_h is not None and off_h >= 4:
        tr.append(f"{h} cae {off_h:.0f} veces en offside; busca insistentemente la espalda "
                  "a la defensa rival pero sin la sincronía necesaria.")
    if off_a is not None and off_a >= 4:
        tr.append(f"{a} queda {off_a:.0f} veces adelantado; la línea defensiva local los atrapa.")

    if foul_h is not None and foul_a is not None:
        tot_f = (foul_h or 0) + (foul_a or 0)
        if tot_f <= 10 and sht_h and sht_a and sht_h + sht_a >= 10:
            tr.append("El partido fluye con pocas interrupciones y bastante llegada al arco. "
                      "Encuentro de ritmo alto que premia a los equipos con buen estado físico.")

    # Dimensiones alternativas cuando los datos básicos no generan texto
    if not tr:
        if cor_h is not None and cor_a is not None and (cor_h + cor_a) >= 4:
            dom_c = h if cor_h >= cor_a else a
            resto = a if cor_h >= cor_a else h
            tr.append(f"El partido se decide mucho en pelota parada: "
                      f"{cor_h + cor_a:.0f} córners en total. "
                      f"{dom_c} lleva la ventaja ({max(cor_h, cor_a):.0f} vs {min(cor_h, cor_a):.0f}) "
                      f"e impone su juego aéreo sobre {resto}.")

        if foul_a is not None and ptot_h is not None and ptot_h >= 100 and foul_a >= 10:
            tr.append(f"{a} ejerce presión alta sobre {h}: {foul_a:.0f} faltas cometidas "
                      "sugieren una línea defensiva adelantada que busca recuperar en campo rival.")
        elif foul_h is not None and ptot_a is not None and ptot_a >= 100 and foul_h >= 10:
            tr.append(f"{h} presiona alto sobre {a}: {foul_h:.0f} interrupciones denotan "
                      "una propuesta de presión intensa en campo contrario.")

        if ptot_h is not None and ptot_a is not None and sht_h is not None and sht_a is not None:
            ratio_pases = ptot_h / max(ptot_a, 1)
            if ratio_pases >= 1.5 and sht_h >= sht_a:
                tr.append(f"{h} domina en todos los sectores: más circulación "
                          f"({ptot_h:.0f} vs {ptot_a:.0f} pases) y más llegada al arco "
                          f"({sht_h:.0f} vs {sht_a:.0f} remates). Propuesta integral.")
            elif ratio_pases <= 0.67 and sht_a >= sht_h:
                tr.append(f"{a} controla construcción y proyección ofensiva: "
                          f"{ptot_a:.0f} pases y {sht_a:.0f} remates marcan su superioridad.")

        if history and len(history) >= 2:
            foul_h_early = _n(history[0]["stats"]["home"].get("foulsCommitted"))
            foul_a_early = _n(history[0]["stats"]["away"].get("foulsCommitted"))
            if (foul_h is not None and foul_h_early is not None and
                    foul_a is not None and foul_a_early is not None):
                incr_total = (foul_h - foul_h_early) + (foul_a - foul_a_early)
                if incr_total >= 8:
                    tr.append("El nivel de faltas se incrementó significativamente en el tramo "
                              "final, señal de desgaste físico y mayor tensión táctica.")

    lecturas["transiciones"] = " ".join(tr) if tr else ""

    return lecturas

# ════════════════════════════════════════════════════════════════
#  ALERTA DE DOMINIO
# ════════════════════════════════════════════════════════════════

def _alerta_dominio(info, stats):
    """Genera 'DOMINA [EQUIPO] — razones' o 'EQUILIBRIO — datos'."""
    h, a    = info["home_name"], info["away_name"]
    hs, as_ = stats["home"], stats["away"]

    pos_h = _n(hs.get("possessionPct")); pos_a = _n(as_.get("possessionPct"))
    sot_h = _n(hs.get("shotsOnTarget")); sot_a = _n(as_.get("shotsOnTarget"))
    sht_h = _n(hs.get("totalShots"));   sht_a = _n(as_.get("totalShots"))
    cor_h = _n(hs.get("wonCorners"));   cor_a = _n(as_.get("wonCorners"))

    pts_h, pts_a = 0, 0
    raz_h, raz_a = [], []

    if pos_h is not None and pos_a is not None:
        if pos_h > pos_a + 5:
            pts_h += 2; raz_h.append(f"posesión {pos_h:.0f}%")
        elif pos_a > pos_h + 5:
            pts_a += 2; raz_a.append(f"posesión {pos_a:.0f}%")

    if sot_h is not None and sot_a is not None:
        if sot_h > sot_a + 1:
            pts_h += 3; raz_h.append(f"más tiros al arco ({sot_h:.0f} vs {sot_a:.0f})")
        elif sot_a > sot_h + 1:
            pts_a += 3; raz_a.append(f"más tiros al arco ({sot_a:.0f} vs {sot_h:.0f})")
    elif sht_h is not None and sht_a is not None:
        if sht_h > sht_a + 3:
            pts_h += 2; raz_h.append(f"más remates ({sht_h:.0f} vs {sht_a:.0f})")
        elif sht_a > sht_h + 3:
            pts_a += 2; raz_a.append(f"más remates ({sht_a:.0f} vs {sht_h:.0f})")

    if cor_h is not None and cor_a is not None:
        if cor_h > cor_a + 1:
            pts_h += 1; raz_h.append(f"más córners ({cor_h:.0f} vs {cor_a:.0f})")
        elif cor_a > cor_h + 1:
            pts_a += 1; raz_a.append(f"más córners ({cor_a:.0f} vs {cor_h:.0f})")

    if pts_h > pts_a and raz_h:
        return f"DOMINA {h.upper()} — " + ", ".join(raz_h)
    if pts_a > pts_h and raz_a:
        return f"DOMINA {a.upper()} — " + ", ".join(raz_a)
    if pts_h > 0 or pts_a > 0:
        datos = []
        if pos_h is not None and pos_a is not None:
            datos.append(f"posesión {pos_h:.0f}%–{pos_a:.0f}%")
        if sot_h is not None and sot_a is not None:
            datos.append(f"tiros al arco {sot_h:.0f}–{sot_a:.0f}")
        return "EQUILIBRIO — " + ", ".join(datos) if datos else "EQUILIBRIO"
    return ""  # Sin suficientes datos aún

# ════════════════════════════════════════════════════════════════
#  ANÁLISIS DE PORTEROS
# ════════════════════════════════════════════════════════════════

def analisis_porteros(info, stats):
    h, a    = info["home_name"], info["away_name"]
    hs, as_ = stats["home"], stats["away"]
    partes  = []

    for nombre, sav, ptot, pok in [
        (h, _n(hs.get("saves")), _n(hs.get("passesTotal")), _n(hs.get("passesAccurate"))),
        (a, _n(as_.get("saves")), _n(as_.get("passesTotal")), _n(as_.get("passesAccurate"))),
    ]:
        if sav is not None:
            if sav >= 5:
                partes.append(f"El portero de {nombre} fue figura con {sav:.0f} atajadas. "
                              "Determinante para el resultado final.")
            elif sav >= 3:
                partes.append(f"El arquero de {nombre} tuvo una tarde activa: {sav:.0f} paradas.")
            elif sav <= 1:
                partes.append(f"El portero de {nombre} prácticamente no fue exigido "
                              f"({sav:.0f} atajadas).")
        if ptot and pok and ptot > 20:
            pct = (pok / ptot) * 100
            partes.append(
                f"En distribución con el pie, el guardameta de {nombre} completó "
                f"{pok:.0f}/{ptot:.0f} pases ({pct:.0f}%). "
                + ("Salida limpia de balón, contribuye al inicio de jugada desde atrás."
                   if pct >= 75 else
                   "Dificultades para iniciar con claridad desde el área propia.")
            )
    return " ".join(partes)

# ════════════════════════════════════════════════════════════════
#  GRÁFICOS DE EVOLUCIÓN (matplotlib)
# ════════════════════════════════════════════════════════════════

def generar_graficos(history, info, ts):
    """
    Genera un PNG con 3 subgráficos de evolución del partido.
    Retorna la ruta del archivo guardado, o None si falla.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        try:
            import subprocess
            print("  Instalando matplotlib...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "matplotlib"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except Exception as e:
            print(f"  No se pudo instalar matplotlib: {e}")
            return None

    h, a   = info["home_name"], info["away_name"]
    snaps  = [s for s in history if s["minuto"] is not None]
    if len(snaps) < 2:
        return None

    minutos = [s["minuto"] for s in snaps]
    C_H  = "#1565C0"   # azul oscuro local
    C_A  = "#C62828"   # rojo oscuro visitante
    C_H2 = "#90CAF9"   # azul claro (tiros al arco local)
    C_A2 = "#EF9A9A"   # rojo claro (tiros al arco visitante)

    fig, axes = plt.subplots(3, 1, figsize=(13, 17), facecolor="#FAFAFA")
    fig.suptitle(f"{h}  vs  {a}\n{info.get('tournament', '')}  ·  {ts}",
                 fontsize=13, fontweight="bold", y=0.99, color="#212121")

    def _vals(key, side):
        return [_n(s["stats"][side].get(key)) for s in snaps]

    # ── GRÁFICO 1: POSESIÓN ──────────────────────────────────────
    ax1 = axes[0]
    ph = _vals("possessionPct", "home")
    pa = _vals("possessionPct", "away")
    valid1 = [(m, vh, va) for m, vh, va in zip(minutos, ph, pa)
              if vh is not None and va is not None]
    if valid1:
        mins1, ph_v, pa_v = zip(*valid1)
        ax1.fill_between(mins1, ph_v, alpha=0.30, color=C_H)
        ax1.fill_between(mins1, pa_v, alpha=0.30, color=C_A)
        ax1.plot(mins1, ph_v, color=C_H, lw=2.5, marker="o", ms=5, label=h)
        ax1.plot(mins1, pa_v, color=C_A, lw=2.5, marker="o", ms=5, label=a)
        ax1.axhline(50, color="gray", ls="--", alpha=0.4, lw=1)
        ax1.set_ylim(0, 100)
        ax1.set_ylabel("Posesión (%)", fontsize=10)
        ax1.set_title("EVOLUCIÓN DE POSESIÓN", fontweight="bold", fontsize=11, pad=8)
        ax1.set_xticks(mins1)
        ax1.set_xticklabels([f"Min.{m}'" for m in mins1], fontsize=8)
        ax1.legend(loc="upper right", fontsize=9)
        ax1.grid(True, alpha=0.25)
        # Interpretación
        diff = ph_v[-1] - ph_v[0]
        if diff > 6:
            it1 = f"{h} fue creciendo en la posesión: {ph_v[0]:.0f}% → {ph_v[-1]:.0f}% al final."
        elif diff < -6:
            it1 = f"{a} tomó el control progresivamente: {pa_v[0]:.0f}% → {pa_v[-1]:.0f}%."
        elif abs(ph_v[-1] - pa_v[-1]) <= 5:
            it1 = f"Posesión equilibrada de principio a fin ({ph_v[-1]:.0f}% – {pa_v[-1]:.0f}%)."
        else:
            dom = h if ph_v[-1] > pa_v[-1] else a
            it1 = f"{dom} mantuvo la pelota ({max(ph_v[-1], pa_v[-1]):.0f}%) a lo largo del partido."
        ax1.text(0.5, -0.13, it1, transform=ax1.transAxes,
                 ha="center", fontsize=9, style="italic", color="#424242")

    # ── GRÁFICO 2: TIROS TOTALES VS AL ARCO ─────────────────────
    ax2 = axes[1]
    sht_h = _vals("totalShots",    "home")
    sht_a = _vals("totalShots",    "away")
    sot_h = _vals("shotsOnTarget", "home")
    sot_a = _vals("shotsOnTarget", "away")
    valid2 = [(m, sh, sa, oh, oa)
              for m, sh, sa, oh, oa in zip(minutos, sht_h, sht_a, sot_h, sot_a)
              if any(v is not None for v in [sh, sa, oh, oa])]
    if valid2:
        mins2, sh_v, sa_v, oh_v, oa_v = zip(*valid2)
        x2 = list(range(len(mins2)))
        w  = 0.2
        def safe(lst): return [v if v is not None else 0 for v in lst]
        ax2.bar([i - 1.5*w for i in x2], safe(sh_v), w, color=C_H,
                label=f"{h} - Tiros totales", alpha=0.88)
        ax2.bar([i - 0.5*w for i in x2], safe(oh_v), w, color=C_H2,
                label=f"{h} - Al arco", alpha=0.88)
        ax2.bar([i + 0.5*w for i in x2], safe(sa_v), w, color=C_A,
                label=f"{a} - Tiros totales", alpha=0.88)
        ax2.bar([i + 1.5*w for i in x2], safe(oa_v), w, color=C_A2,
                label=f"{a} - Al arco", alpha=0.88)
        ax2.set_xticks(x2)
        ax2.set_xticklabels([f"Min.{m}'" for m in mins2], fontsize=8)
        ax2.set_ylabel("Cantidad acumulada", fontsize=10)
        ax2.set_title("TIROS TOTALES VS AL ARCO", fontweight="bold", fontsize=11, pad=8)
        ax2.legend(loc="upper left", fontsize=8, ncol=2)
        ax2.grid(True, alpha=0.25, axis="y")
        # Interpretación
        lsh = sh_v[-1] or 0; lsa = sa_v[-1] or 0
        loh = oh_v[-1] or 0; loa = oa_v[-1] or 0
        ef_h = (loh / lsh * 100) if lsh > 0 else 0
        ef_a = (loa / lsa * 100) if lsa > 0 else 0
        if ef_h > ef_a + 15:
            it2 = f"{h} más eficaz: {ef_h:.0f}% de sus tiros al arco vs {ef_a:.0f}% de {a}."
        elif ef_a > ef_h + 15:
            it2 = f"{a} más eficaz: {ef_a:.0f}% de sus tiros al arco vs {ef_h:.0f}% de {h}."
        else:
            it2 = f"Eficacia similar en el disparo — {h}: {ef_h:.0f}%  |  {a}: {ef_a:.0f}% al arco."
        ax2.text(0.5, -0.13, it2, transform=ax2.transAxes,
                 ha="center", fontsize=9, style="italic", color="#424242")

    # ── GRÁFICO 3: CÓRNERS Y FALTAS ──────────────────────────────
    ax3 = axes[2]
    cor_h  = _vals("wonCorners",    "home")
    cor_a  = _vals("wonCorners",    "away")
    foul_h = _vals("foulsCommitted","home")
    foul_a = _vals("foulsCommitted","away")
    valid3 = [(m, ch, ca, fh, fa)
              for m, ch, ca, fh, fa in zip(minutos, cor_h, cor_a, foul_h, foul_a)
              if any(v is not None for v in [ch, ca, fh, fa])]
    if valid3:
        mins3, ch_v, ca_v, fh_v, fa_v = zip(*valid3)
        def sp(lst): return [v if v is not None else 0 for v in lst]
        ax3.plot(mins3, sp(ch_v),  color=C_H, lw=2, marker="s", ms=5, ls="-",
                 label=f"{h} - Córners")
        ax3.plot(mins3, sp(ca_v),  color=C_A, lw=2, marker="s", ms=5, ls="-",
                 label=f"{a} - Córners")
        ax3.plot(mins3, sp(fh_v),  color=C_H, lw=2, marker="^", ms=5, ls="--",
                 label=f"{h} - Faltas")
        ax3.plot(mins3, sp(fa_v),  color=C_A, lw=2, marker="^", ms=5, ls="--",
                 label=f"{a} - Faltas")
        ax3.set_xticks(mins3)
        ax3.set_xticklabels([f"Min.{m}'" for m in mins3], fontsize=8)
        ax3.set_ylabel("Cantidad acumulada", fontsize=10)
        ax3.set_title("CÓRNERS Y FALTAS ACUMULADAS", fontweight="bold", fontsize=11, pad=8)
        ax3.legend(loc="upper left", fontsize=8, ncol=2)
        ax3.grid(True, alpha=0.25)
        # Interpretación
        lch = ch_v[-1] or 0; lca = ca_v[-1] or 0
        lfh = fh_v[-1] or 0; lfa = fa_v[-1] or 0
        partes3 = []
        if lch + lca > 0:
            dom_c = h if lch >= lca else a
            partes3.append(f"{dom_c} dominó en córners ({max(lch,lca):.0f} vs {min(lch,lca):.0f})")
        if lfh + lfa > 0:
            mas_f = h if lfh >= lfa else a
            partes3.append(f"{mas_f} acumuló más faltas ({max(lfh,lfa):.0f} vs {min(lfh,lfa):.0f})")
        it3 = ". ".join(partes3) + "." if partes3 else "Sin datos suficientes de córners y faltas."
        ax3.text(0.5, -0.13, it3, transform=ax3.transAxes,
                 ha="center", fontsize=9, style="italic", color="#424242")

    # ── GUARDAR ──────────────────────────────────────────────────
    fecha_str = datetime.now().strftime("%Y%m%d_%H%M")
    h_safe    = re.sub(r"[^\w]", "_", h)[:15]
    a_safe    = re.sub(r"[^\w]", "_", a)[:15]
    desktop   = os.path.join(os.path.expanduser("~"), "Desktop")
    nombre    = f"evolucion_{h_safe}_vs_{a_safe}_{fecha_str}.png"
    ruta      = os.path.join(desktop, nombre)
    try:
        plt.tight_layout(rect=[0, 0, 1, 0.97])
        plt.savefig(ruta, dpi=150, bbox_inches="tight", facecolor="#FAFAFA")
        plt.close()
        return ruta
    except Exception as e:
        plt.close()
        print(f"  Error al guardar gráfico: {e}")
        return None

# ════════════════════════════════════════════════════════════════
#  GENERADORES DE REPORTES
# ════════════════════════════════════════════════════════════════

SEP  = "─" * 62
SEP2 = "═" * 62

# Estadísticas mínimas que siempre se muestran aunque sean "—"
BASE_STATS = {"possessionPct", "totalShots", "shotsOnTarget", "wonCorners", "foulsCommitted"}

def _estadisticas_tabla(stats, h, a):
    lines = [f"  {'':26}  {h[:14]:>14}  {a[:14]:<14}", "  " + "─" * 56]
    for key, display in STATS_MAP.items():
        hv = stats["home"].get(key)
        av = stats["away"].get(key)
        if hv is not None or av is not None or key in BASE_STATS:
            lines.append(f"  {display:<26}  {str(hv or '—'):>14}  {str(av or '—'):<14}")
    return "\n".join(lines)

def reporte_ht(info, stats, lecturas, history, ts):
    h, a = info["home_name"], info["away_name"]
    marcador = f"{h} {info['home_score']} – {info['away_score']} {a}"

    lines = [
        "", SEP2,
        "  REPORTE DE PRIMERA MITAD",
        f"  {marcador}",
        f"  {info['tournament']}",
        f"  {ts}",
        SEP2, "",
    ]

    gh, ga = _n(info["home_score"]), _n(info["away_score"])
    if gh is not None and ga is not None:
        if gh > ga:
            res = f"{h} gana la primera mitad {gh:.0f}–{ga:.0f}."
        elif ga > gh:
            res = f"{a} se va al descanso ganando {ga:.0f}–{gh:.0f}."
        else:
            res = f"Empate {gh:.0f}–{ga:.0f} al término de la primera mitad."
        lines += ["RESUMEN EJECUTIVO", SEP, _wrap(res), ""]

    secciones = [
        ("CONTROL TERRITORIAL",  "territorial"),
        ("EFICACIA OFENSIVA",    "ofensiva"),
        ("SOLIDEZ DEFENSIVA",    "defensiva"),
        ("RITMO Y TRANSICIONES", "transiciones"),
    ]
    for titulo, clave in secciones:
        texto = lecturas.get(clave, "")
        if texto:
            lines += [titulo, SEP, _wrap(texto), ""]

    evo_tabla = tabla_evolucion(history, h, a)
    if evo_tabla:
        lines += ["EVOLUCIÓN DE ESTADÍSTICAS CLAVE", SEP, evo_tabla, ""]
        evo_interp = _interpretar_evolucion(history, h, a)
        if evo_interp:
            lines += ["LECTURA DE LA EVOLUCIÓN", SEP, _wrap(evo_interp), ""]

    lines += ["ESTADÍSTICAS DEL PRIMER TIEMPO", SEP,
              _estadisticas_tabla(stats, h, a), ""]

    lines += ["PROYECCIÓN PARA EL SEGUNDO TIEMPO", SEP]
    if gh is not None and ga is not None:
        if gh < ga:
            proy = (f"{h} deberá abrirse en el complemento para buscar el empate, "
                    f"generando espacios para el contragolpe de {a}.")
        elif gh > ga:
            proy = (f"Con el marcador a favor, {h} puede administrar el resultado. "
                    f"{a} tendrá que arriesgar y dejar espacios.")
        else:
            proy = ("Con el empate al descanso el segundo tiempo puede ser más abierto. "
                    "Quien marque primero obligará al rival a modificar su propuesta táctica.")
        lines.append(_wrap(proy))
    lines += ["", SEP2, ""]
    return "\n".join(lines)


def reporte_ft(info, stats, lecturas, stats_ht, history, ts):
    h, a   = info["home_name"], info["away_name"]
    marcador = f"{h} {info['home_score']} – {info['away_score']} {a}"
    gh, ga = _n(info["home_score"]), _n(info["away_score"])

    lines = [
        "", SEP2,
        "  ANÁLISIS POST-PARTIDO — USO EN CRÓNICA Y ANÁLISIS",
        f"  {marcador}",
        f"  {info['tournament']}",
        f"  {ts}",
        SEP2, "",
    ]

    # 1. QUÉ DEFINIÓ EL PARTIDO
    lines += ["1. QUÉ DEFINIÓ EL PARTIDO", SEP]
    pos_h = _n(stats["home"].get("possessionPct"))
    pos_a = _n(stats["away"].get("possessionPct"))
    sht_h = _n(stats["home"].get("totalShots"))
    sht_a = _n(stats["away"].get("totalShots"))
    sot_h = _n(stats["home"].get("shotsOnTarget"))
    sot_a = _n(stats["away"].get("shotsOnTarget"))
    xg_h  = _n(stats["home"].get("xG"))
    xg_a  = _n(stats["away"].get("xG"))

    definicion = []
    if gh is not None and ga is not None:
        if gh > ga:
            ganador, perdedor, gf, gc = h, a, gh, ga
        elif ga > gh:
            ganador, perdedor, gf, gc = a, h, ga, gh
        else:
            ganador = perdedor = None
            gf = gc = gh

        if ganador:
            if xg_h is not None and xg_a is not None:
                xg_gan = xg_h if ganador == h else xg_a
                xg_per = xg_a if ganador == h else xg_h
                if xg_gan > xg_per + 0.3:
                    definicion.append(
                        f"{ganador} mereció ganar según los números: {xg_gan:.2f} xG contra "
                        f"{xg_per:.2f} de {perdedor}. La victoria refleja el dominio real en "
                        "la generación de ocasiones."
                    )
                elif xg_per > xg_gan + 0.3:
                    definicion.append(
                        f"Victoria de {ganador} contra el marcador esperado: {perdedor} generó "
                        f"más peligro ({xg_per:.2f} xG vs {xg_gan:.2f}) pero no lo convirtió. "
                        "La eficacia o la actuación del portero ganador cambiaron el partido."
                    )
                else:
                    definicion.append(
                        f"Los xG estuvieron parejos ({xg_h:.2f} – {xg_a:.2f}). "
                        f"{ganador} encontró la diferencia en la definición, no en la creación."
                    )
            elif sht_h is not None and sht_a is not None:
                sht_gan = sht_h if ganador == h else sht_a
                sht_per = sht_a if ganador == h else sht_h
                if sht_gan > sht_per + 4:
                    definicion.append(
                        f"{ganador} fue superior en la generación de peligro ({sht_gan:.0f} remates) "
                        "y esa presión constante terminó siendo decisiva."
                    )
                else:
                    definicion.append(
                        f"{ganador} se impuso {gf:.0f}–{gc:.0f} en un partido equilibrado. "
                        "La diferencia estuvo en los detalles."
                    )
        else:
            definicion.append(
                f"Empate {gh:.0f}–{ga:.0f}. Ninguno encontró la superioridad suficiente "
                "para sentenciar. El resultado es justo para ambos."
            )
    lines.append(_wrap(" ".join(definicion)) if definicion else "  Sin datos suficientes.")
    lines.append("")

    # 2. QUIÉN PROPUSO MÁS VS QUIÉN RESOLVIÓ MEJOR
    lines += ["2. QUIÉN PROPUSO MÁS Y QUIÉN RESOLVIÓ MEJOR", SEP]
    propuesta = []
    if pos_h is not None and pos_a is not None:
        if pos_h > pos_a + 5:
            if gh is not None and ga is not None and ga >= gh:
                propuesta.append(
                    f"{h} propuso el juego con el {pos_h:.0f}% de la pelota, "
                    f"pero fue {a} quien resolvió mejor: menor posesión y mayor efectividad."
                )
            else:
                propuesta.append(
                    f"{h} llevó la iniciativa del partido con {pos_h:.0f}% de posesión, "
                    "imponiendo su propuesta táctica."
                )
        elif pos_a > pos_h + 5:
            if gh is not None and ga is not None and gh >= ga:
                propuesta.append(
                    f"{a} dominó el balón ({pos_a:.0f}%) pero {h} resultó más efectivo, "
                    "confirmando que la posesión no siempre se traduce en resultado."
                )
            else:
                propuesta.append(
                    f"{a} llevó la iniciativa del juego con {pos_a:.0f}% de posesión."
                )
        else:
            propuesta.append(
                f"La posesión estuvo muy repartida ({pos_h:.0f}%–{pos_a:.0f}%). "
                "Ninguno impuso un claro dominio territorial."
            )
        if sot_h is not None and sot_a is not None:
            if sot_h > sot_a + 2:
                propuesta.append(f"{h} fue más preciso en sus disparos: {sot_h:.0f} tiros al arco.")
            elif sot_a > sot_h + 2:
                propuesta.append(f"{a} apuntó mejor: {sot_a:.0f} disparos entre los tres palos.")
    lines.append(_wrap(" ".join(propuesta)) if propuesta else "  Datos insuficientes.")
    lines.append("")

    # 3. MOMENTOS DE INFLEXIÓN
    lines += ["3. MOMENTOS DE INFLEXIÓN", SEP]
    inflexion = []
    if stats_ht:
        pos_ht_h = _n(stats_ht["home"].get("possessionPct"))
        pos_ft_h = _n(stats["home"].get("possessionPct"))
        if pos_ht_h is not None and pos_ft_h is not None and abs(pos_ft_h - pos_ht_h) >= 7:
            dir_t = "aumentó" if pos_ft_h > pos_ht_h else "redujo"
            equipo_cambio = h if pos_ft_h > pos_ht_h else a
            inflexion.append(
                f"El entretiempo cambió el partido: {equipo_cambio} {dir_t} notablemente "
                f"su control territorial ({pos_ht_h:.0f}% → {pos_ft_h:.0f}%), "
                "señal de un ajuste táctico claro en el descanso."
            )

        sht_ht_h = _n(stats_ht["home"].get("totalShots"))
        sht_ft_h = _n(stats["home"].get("totalShots"))
        sht_ht_a = _n(stats_ht["away"].get("totalShots"))
        sht_ft_a = _n(stats["away"].get("totalShots"))
        if all(v is not None for v in [sht_ht_h, sht_ft_h, sht_ht_a, sht_ft_a]):
            incr_h = sht_ft_h - sht_ht_h
            incr_a = sht_ft_a - sht_ht_a
            if incr_h > incr_a + 4:
                inflexion.append(
                    f"{h} fue mucho más intenso en el segundo tiempo: "
                    f"{incr_h:.0f} remates adicionales frente a {incr_a:.0f} de {a}."
                )
            elif incr_a > incr_h + 4:
                inflexion.append(
                    f"{a} intensificó su presión en la segunda mitad: "
                    f"{incr_a:.0f} remates adicionales frente a {incr_h:.0f} de {h}."
                )

    if not inflexion:
        inflexion.append("El partido mantuvo una línea táctica coherente de principio a fin, "
                         "sin cambios bruscos en el dominio del juego.")
    lines.append(_wrap(" ".join(inflexion)))
    lines.append("")

    # 4. GRÁFICOS DE EVOLUCIÓN
    lines += ["4. GRÁFICOS DE EVOLUCIÓN", SEP]
    ruta_png = generar_graficos(history, info, ts)
    if ruta_png:
        lines += [
            f"  Gráficos guardados en:",
            f"  {ruta_png}",
            "",
            "  Contiene: evolución de posesión · tiros totales vs al arco · córners y faltas",
        ]
    else:
        evo_tabla = tabla_evolucion(history, h, a)
        if evo_tabla:
            lines.append(evo_tabla)
            evo_interp = _interpretar_evolucion(history, h, a)
            if evo_interp:
                lines += ["", _wrap(evo_interp)]
    lines.append("")

    # 5. ANÁLISIS DE PORTEROS
    gk = analisis_porteros(info, stats)
    if gk:
        lines += ["5. ANÁLISIS DE PORTEROS", SEP, _wrap(gk), ""]

    # 6. CUADRO ESTADÍSTICO FINAL
    lines += ["6. CUADRO ESTADÍSTICO FINAL", SEP,
              _estadisticas_tabla(stats, h, a), ""]

    fuentes = "ESPN API" + (" + API-Football" if API_FOOTBALL_KEY else "")
    lines += [SEP, f"  Fuente de datos: {fuentes}", SEP2, ""]
    return "\n".join(lines)

# ════════════════════════════════════════════════════════════════
#  VISUALIZACIÓN
# ════════════════════════════════════════════════════════════════

def mostrar_rich(info, stats, lecturas, num_act, apif_on):
    console = Console()
    console.clear()
    fuentes  = "ESPN" + (" + API-Football" if apif_on else "")
    header_t = _header_tiempo(info)

    console.print(Panel(
        f"[bold cyan]{info['tournament']}[/bold cyan]  [dim]· {fuentes}[/dim]",
        style="dim"))

    console.print(Panel(
        f"[bold white]{info['home_name']}[/bold white]  "
        f"[bold yellow]{info['home_score']} – {info['away_score']}[/bold yellow]  "
        f"[bold white]{info['away_name']}[/bold white]  "
        f"[bold green]{header_t}[/bold green]",
        title="PARTIDO EN VIVO", box=box.DOUBLE_EDGE, style="bold"))

    table = Table(show_header=True, header_style="bold magenta",
                  box=box.ROUNDED, title="[bold]Estadísticas[/bold]",
                  title_style="bold white", expand=True)
    table.add_column(info["home_name"], justify="right", style="cyan", no_wrap=True)
    table.add_column("Estadística", justify="center", style="bold white")
    table.add_column(info["away_name"], justify="left", style="red", no_wrap=True)

    filas = 0
    for key, display in STATS_MAP.items():
        hv = stats["home"].get(key)
        av = stats["away"].get(key)
        if hv is not None or av is not None:
            hs_s, as_s = str(hv or "—"), str(av or "—")
            hn, an = _n(hv), _n(av)
            if hn is not None and an is not None:
                if hn > an: hs_s = f"[bold]{hs_s}[/bold]"
                elif an > hn: as_s = f"[bold]{as_s}[/bold]"
            table.add_row(hs_s, display, as_s)
            filas += 1
    if filas == 0:
        table.add_row("—", "[dim]Sin datos aún[/dim]", "—")
    console.print(table)

    # Alerta de dominio
    alerta = _alerta_dominio(info, stats)
    if alerta:
        color_alerta = "red" if "DOMINA" in alerta else "yellow"
        console.print(f"\n[bold {color_alerta}]◆ {alerta}[/bold {color_alerta}]")

    colores = {"territorial": "yellow", "ofensiva": "green",
               "defensiva": "red", "transiciones": "cyan"}
    titulos = {"territorial": "CONTROL TERRITORIAL",
               "ofensiva":    "EFICACIA OFENSIVA",
               "defensiva":   "SOLIDEZ DEFENSIVA",
               "transiciones":"TRANSICIONES Y RITMO"}

    for clave, color in colores.items():
        texto = lecturas.get(clave, "")
        if texto:
            console.print(f"\n[bold {color}]▶ {titulos[clave]}[/bold {color}]")
            console.print(f"  [white]{texto}[/white]")

    console.print(f"\n[dim]Actualizado: {datetime.now().strftime('%H:%M:%S')}  ·  "
                  f"#{num_act}  ·  Próxima en {INTERVALO_ACTUALIZACION//60} min  ·  "
                  "Ctrl+C para salir[/dim]")


def limpiar():
    os.system("cls" if os.name == "nt" else "clear")


def mostrar_plano(info, stats, lecturas, num_act, apif_on):
    limpiar()
    ancho    = 70
    fuentes  = "ESPN" + (" + API-Football" if apif_on else "")
    header_t = _header_tiempo(info)
    print(SEP2)
    print(f"  {info['tournament'][:60]}")
    print(f"  Fuentes: {fuentes}")
    print(SEP2)
    marcador = (f"{info['home_name']}  {info['home_score']} – "
                f"{info['away_score']}  {info['away_name']}  {header_t}")
    print(marcador[:ancho].center(ancho))
    print(SEP)
    print(f"  {'LOCAL':^20}  {'ESTADÍSTICA':^22}  {'VISITANTE':^12}")
    print(SEP)
    for key, display in STATS_MAP.items():
        hv = stats["home"].get(key)
        av = stats["away"].get(key)
        if hv is not None or av is not None:
            print(f"  {str(hv or '—'):^20}  {display:^22}  {str(av or '—'):^12}")

    # Alerta de dominio
    alerta = _alerta_dominio(info, stats)
    if alerta:
        print(f"\n{SEP}\n  ◆ {alerta}\n{SEP}")

    titulos = [("CONTROL TERRITORIAL",  "territorial"),
               ("EFICACIA OFENSIVA",    "ofensiva"),
               ("SOLIDEZ DEFENSIVA",    "defensiva"),
               ("TRANSICIONES Y RITMO", "transiciones")]
    for titulo, clave in titulos:
        texto = lecturas.get(clave, "")
        if texto:
            print(f"\n{SEP}\n  {titulo}\n{SEP}")
            print(_wrap(texto))

    print(f"\n{SEP}")
    print(f"  {datetime.now().strftime('%H:%M:%S')}  |  Update #{num_act}  |  Ctrl+C = salir")
    print(SEP2)


def seleccionar_partido(eventos):
    info_list = [espn_parse_event(ev) for ev in eventos]

    if RICH_AVAILABLE:
        console = Console()
        console.clear()
        table = Table(title="Partidos disponibles", box=box.ROUNDED, show_lines=True)
        table.add_column("#", style="bold yellow", width=4)
        table.add_column("Local", style="cyan")
        table.add_column("Marc.", style="bold white", justify="center")
        table.add_column("Visitante", style="red")
        table.add_column("Estado", justify="center")
        table.add_column("Liga", style="dim")
    else:
        limpiar()
        print("=" * 88)
        print("  PARTIDOS DISPONIBLES".center(88))
        print("=" * 88)
        print(f"  {'#':>3}  {'LOCAL':<24} {'MARC':>6}  {'VISITANTE':<24}  {'EST':>8}  LIGA")
        print("-" * 88)

    for i, inf in enumerate(info_list, 1):
        marc   = f"{inf['home_score']}-{inf['away_score']}"
        estado = inf["match_time"]
        liga   = inf["tournament"][:28]
        if RICH_AVAILABLE:
            color = "green" if inf["state"] == "in" else "dim"
            table.add_row(str(i), inf["home_name"], marc, inf["away_name"],
                          f"[{color}]{estado}[/{color}]", liga)
        else:
            vivo = " <<" if inf["state"] == "in" else ""
            print(f"  {i:>3}  {inf['home_name']:<24} {marc:>6}  "
                  f"{inf['away_name']:<24}  {estado:>8}  {liga}{vivo}")

    if RICH_AVAILABLE:
        console.print(table)
        console.print("\n[dim]Verde = en vivo ahora.[/dim]")
    else:
        print("  << = En vivo ahora")

    print()
    while True:
        try:
            elic = input(f"  Escribe el número del partido (1-{len(info_list)}): ").strip()
            idx  = int(elic) - 1
            if 0 <= idx < len(info_list):
                return eventos[idx], info_list[idx]
            print(f"  Número entre 1 y {len(info_list)}.")
        except ValueError:
            print("  Solo el número, por favor.")
        except KeyboardInterrupt:
            print("\n  Saliendo..."); sys.exit(0)

# ════════════════════════════════════════════════════════════════
#  MONITOR DE ESTADO (chequeo cada 30 segundos)
# ════════════════════════════════════════════════════════════════

def _chequeo_rapido(event_id):
    try:
        todos = _espn_all_events()
        ev = next((e for e in todos if str(e.get("id", "")) == str(event_id)), None)
        if ev:
            return espn_parse_event(ev)
    except Exception:
        pass
    return None

def _esperar_con_monitor(segundos, event_id, prev_period, ht_done):
    """
    Reemplaza time.sleep(segundos). Chequea el estado del partido
    cada INTERVALO_MONITOR segundos. Retorna ('normal'|'ht'|'ft', info_rapida).
    """
    transcurrido = 0
    while transcurrido < segundos:
        dormir = min(INTERVALO_MONITOR, segundos - transcurrido)
        time.sleep(dormir)
        transcurrido += dormir

        info_q = _chequeo_rapido(event_id)
        if info_q is None:
            continue

        if _es_estado_final(info_q):
            return 'ft', info_q

        periodo_q = info_q.get("period", 1)
        is_ht_q   = info_q.get("is_ht", False)
        if not ht_done and (is_ht_q or (periodo_q >= 2 and prev_period < 2)):
            return 'ht', info_q

    return 'normal', None

# ════════════════════════════════════════════════════════════════
#  BUCLE PRINCIPAL
# ════════════════════════════════════════════════════════════════

def _fetch_full(event_id, league, evento, apif_fixture, apif_on):
    sum_espn = espn_summary(event_id, league)
    st_espn  = espn_parse_stats(sum_espn)
    st_apif  = {"home": {}, "away": {}}
    if apif_on and apif_fixture:
        fid     = apif_fixture.get("fixture", {}).get("id")
        st_apif = apif_get_stats(fid)
    stats = merge_stats(st_espn, st_apif)

    todos    = _espn_all_events()
    ev_act   = next((e for e in todos if str(e.get("id", "")) == str(event_id)), evento)
    info_act = espn_parse_event(ev_act)
    return stats, info_act

def _emitir(texto):
    if RICH_AVAILABLE:
        Console().clear()
        Console().print(texto)
    else:
        limpiar()
        print(texto)

def _mensaje_cierre(info_act):
    return (
        f"\n{SEP2}\n"
        f"  PARTIDO FINALIZADO — Análisis completado\n"
        f"  {info_act['home_name']} {info_act['home_score']} – "
        f"{info_act['away_score']} {info_act['away_name']}\n"
        f"  {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"{SEP2}\n"
    )

def main():
    if RICH_AVAILABLE:
        Console().print("\n[bold green]Football Live Analysis v3[/bold green] — cargando...\n")
    else:
        print("\nFootball Live Analysis v3 — cargando...\n")

    eventos = espn_live_events()
    if eventos is None:
        print("ERROR: Sin conexión. Verifica tu internet."); sys.exit(1)
    if not eventos:
        print("No hay partidos disponibles en este momento."); sys.exit(0)

    evento, info = seleccionar_partido(eventos)
    league, event_id = info["league"], info["event_id"]

    apif_fixture = None
    if API_FOOTBALL_KEY:
        diag = apif_diagnostico()
        if RICH_AVAILABLE:
            color = "green" if "OK" in diag else "yellow"
            Console().print(f"[{color}]{diag}[/{color}]")
        else:
            print(f"  {diag}")

        if "OK" in diag:
            print("  Buscando partido en API-Football...")
            apif_fixture = apif_find_fixture(info["home_name"], info["away_name"])
            if RICH_AVAILABLE:
                msg = ("[green]  API-Football: partido encontrado.[/green]"
                       if apif_fixture else
                       "[yellow]  API-Football: partido no encontrado. Usando solo ESPN.[/yellow]")
                Console().print(msg + "\n")
            else:
                print("  API-Football:", "encontrado." if apif_fixture else "no encontrado. Solo ESPN.")

    apif_on  = apif_fixture is not None
    num_act  = 0
    ht_done  = False
    stats_ht = None
    history  = []

    time.sleep(1)

    # Primer fetch para inicializar el estado real del partido
    stats, info_act = _fetch_full(event_id, league, evento, apif_fixture, apif_on)
    prev_period = info_act.get("period", 1)  # Bug 1: partir del período real, no de 1 hardcodeado

    while True:
        num_act += 1
        if num_act > 1:
            stats, info_act = _fetch_full(event_id, league, evento, apif_fixture, apif_on)
        lecturas = analisis_tactico(info_act, stats, history)
        guardar_snapshot(history, info_act, stats)

        if RICH_AVAILABLE:
            mostrar_rich(info_act, stats, lecturas, num_act, apif_on)
        else:
            mostrar_plano(info_act, stats, lecturas, num_act, apif_on)

        # Verificar HT en el ciclo principal
        periodo_actual = info_act.get("period", 1)
        es_ht = info_act.get("is_ht", False) or (periodo_actual >= 2 and prev_period < 2)

        if not ht_done and es_ht:
            stats_ht = {"home": dict(stats["home"]), "away": dict(stats["away"])}
            ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt = reporte_ht(info_act, stats, lecturas, history, ts)
            _emitir(rpt)
            ht_done = True

        prev_period = periodo_actual

        # Verificar FT en el ciclo principal
        if _es_estado_final(info_act):
            ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt = reporte_ft(info_act, stats, lecturas, stats_ht, history, ts)
            _emitir(rpt)
            print(_mensaje_cierre(info_act))
            break

        # Espera con monitoreo cada 30 segundos
        try:
            motivo, info_q = _esperar_con_monitor(
                INTERVALO_ACTUALIZACION, event_id, prev_period, ht_done
            )
        except KeyboardInterrupt:
            print("\n\n  Detenido. ¡Hasta luego!")
            break

        if motivo == 'ht' and not ht_done:
            stats, info_act = _fetch_full(event_id, league, evento, apif_fixture, apif_on)
            lecturas = analisis_tactico(info_act, stats, history)
            guardar_snapshot(history, info_act, stats)
            stats_ht = {"home": dict(stats["home"]), "away": dict(stats["away"])}
            ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt = reporte_ht(info_act, stats, lecturas, history, ts)
            _emitir(rpt)
            ht_done     = True
            prev_period = info_act.get("period", 1)

        elif motivo == 'ft':
            stats, info_act = _fetch_full(event_id, league, evento, apif_fixture, apif_on)
            lecturas = analisis_tactico(info_act, stats, history)
            guardar_snapshot(history, info_act, stats)
            ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt = reporte_ft(info_act, stats, lecturas, stats_ht, history, ts)
            _emitir(rpt)
            print(_mensaje_cierre(info_act))
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Detenido. ¡Hasta luego!")
