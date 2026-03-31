#!/usr/bin/env python3
"""
Football Live Analysis System v2
Fuentes: ESPN (principal) + API-Football (opcional)
Análisis táctico en 4 lecturas + reportes automáticos HT/FT para periodistas.
"""

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

INTERVALO_ACTUALIZACION = 300  # 5 minutos

# Obtén tu clave gratuita en https://dashboard.api-football.com/register
# Deja vacío ("") para usar solo ESPN
API_FOOTBALL_KEY = "Lamalaactitud1986."

# ════════════════════════════════════════════════════════════════
#  CONSTANTES
# ════════════════════════════════════════════════════════════════

ESPN_BASE      = "https://site.api.espn.com/apis/site/v2/sports/soccer"
APIF_BASE      = "https://v3.football.api-sports.io"
HEADERS_ESPN   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

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

# ════════════════════════════════════════════════════════════════
#  UTILIDADES
# ════════════════════════════════════════════════════════════════

def _n(val):
    """Convierte a float de forma segura."""
    try:
        return float(str(val).replace("%", "").strip())
    except (TypeError, ValueError):
        return None

def _sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def _wrap(texto, ancho=68, indent="  "):
    """Ajusta texto a ancho de línea."""
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

# ════════════════════════════════════════════════════════════════
#  FUENTE ESPN
# ════════════════════════════════════════════════════════════════

def espn_live_events():
    try:
        r = requests.get(f"{ESPN_BASE}/all/scoreboard", headers=HEADERS_ESPN, timeout=15)
        r.raise_for_status()
        data = r.json()
        vivos = [e for e in data.get("events", [])
                 if e.get("competitions", [{}])[0].get("status", {})
                    .get("type", {}).get("state") == "in"]
        return vivos or data.get("events", [])
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
    is_ht       = ("HALF" in status_name.upper() or
                   detail.upper() in ("HT", "HALF TIME", "HALFTIME"))

    if state == "post":       match_time = "FT"
    elif state == "pre":      match_time = detail or "Por jugar"
    elif is_ht:               match_time = "HT"
    else:                     match_time = clock or detail or "En vivo"

    return {
        "home_name":  home.get("team", {}).get("displayName", "Local"),
        "away_name":  away.get("team", {}).get("displayName", "Visitante"),
        "home_score": home.get("score", "0"),
        "away_score": away.get("score", "0"),
        "match_time": match_time,
        "state":      state,
        "is_ht":      is_ht,
        "period":     period,
        "tournament": ev.get("name", "") or ev.get("season", {}).get("slug", ""),
        "event_id":   ev.get("id", ""),
        "league":     ev.get("league", {}).get("slug", "all"),
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

def apif_find_fixture(home_name, away_name):
    if not API_FOOTBALL_KEY:
        return None
    try:
        r = requests.get(f"{APIF_BASE}/fixtures", params={"live": "all"},
                         headers=_apif_headers(), timeout=15)
        r.raise_for_status()
        fixtures = r.json().get("response", [])
        best_score, best = 0, None
        for fx in fixtures:
            t = fx.get("teams", {})
            h, a = t.get("home", {}).get("name", ""), t.get("away", {}).get("name", "")
            sc = max((_sim(home_name, h) + _sim(away_name, a)) / 2,
                     (_sim(home_name, a) + _sim(away_name, h)) / 2)
            if sc > best_score:
                best_score, best = sc, fx
        return best if best_score >= 0.50 else None
    except Exception:
        return None

def apif_get_stats(fixture_id):
    if not API_FOOTBALL_KEY or not fixture_id:
        return {"home": {}, "away": {}}
    try:
        r = requests.get(f"{APIF_BASE}/fixtures/statistics",
                         params={"fixture": fixture_id},
                         headers=_apif_headers(), timeout=15)
        r.raise_for_status()
        response = r.json().get("response", [])
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
#  MOTOR DE ANÁLISIS TÁCTICO — 4 lecturas independientes
# ════════════════════════════════════════════════════════════════

def analisis_tactico(info, stats):
    h, a   = info["home_name"], info["away_name"]
    hs, as_ = stats["home"], stats["away"]

    pos_h  = _n(hs.get("possessionPct"));  pos_a  = _n(as_.get("possessionPct"))
    sht_h  = _n(hs.get("totalShots"));     sht_a  = _n(as_.get("totalShots"))
    sot_h  = _n(hs.get("shotsOnTarget"));  sot_a  = _n(as_.get("shotsOnTarget"))
    cor_h  = _n(hs.get("wonCorners"));     cor_a  = _n(as_.get("wonCorners"))
    foul_h = _n(hs.get("foulsCommitted")); foul_a = _n(as_.get("foulsCommitted"))
    yc_h   = _n(hs.get("yellowCards"));    yc_a   = _n(as_.get("yellowCards"))
    rc_h   = _n(hs.get("redCards"));       rc_a   = _n(as_.get("redCards"))
    sav_h  = _n(hs.get("saves"));          sav_a  = _n(as_.get("saves"))
    ptot_h = _n(hs.get("passesTotal"))
    pok_h  = _n(hs.get("passesAccurate"))
    xg_h   = _n(hs.get("xG"));            xg_a   = _n(as_.get("xG"))
    off_h  = _n(hs.get("offsides"));       off_a  = _n(as_.get("offsides"))
    gh     = _n(info["home_score"]);        ga     = _n(info["away_score"])

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
                     "toma la iniciativa de un lado al otro.")
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
            t.append(f"{tot_cor:.0f} saques de esquina en total revelan un partido muy disputado "
                     f"en los flancos. {dom_c} es quien más insiste con centros al área "
                     f"({max(cor_h,cor_a):.0f} córners).")

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
                         f"tiros entre los tres palos. Presencia ofensiva sin la puntería necesaria.")

    if xg_h is not None and xg_a is not None:
        o.append(f"Los goles esperados sitúan a {h} en {xg_h:.2f} xG y a {a} en {xg_a:.2f} xG.")
        if gh is not None and gh > xg_h + 0.5:
            o.append(f"{h} está sobrerendiendo respecto a la calidad de sus ocasiones.")
        elif gh is not None and gh < xg_h - 0.5:
            o.append(f"{h} está siendo ineficaz: el marcador le debe más goles de los que refleja.")
        if ga is not None and ga > xg_a + 0.5:
            o.append(f"{a} convierte por encima del valor de sus oportunidades.")

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
            d.append(f"El partido es físicamente muy intenso: {tot_f:.0f} faltas en total. "
                     "El árbitro debe mantener el control para que la dureza no condicione el juego.")

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
        d.append(f"INFERIORIDAD NUMÉRICA: {h} juega con un hombre menos. "
                 "Su esquema defensivo se reconfigura sobre la marcha.")
    if rc_a and rc_a >= 1:
        d.append(f"INFERIORIDAD NUMÉRICA: {a} está con un jugador menos. "
                 "El bloque bajo se vuelve la única táctica viable para el visitante.")

    lecturas["defensiva"] = " ".join(d) if d else \
        "El partido transcurre sin grandes alarmas defensivas hasta el momento."

    # ── 4. TRANSICIONES Y RITMO ─────────────────────────────────────────────────
    tr = []
    if pos_h is not None and sht_h is not None and sht_a is not None:
        if pos_h < 44 and sht_h >= (sht_a or 0):
            tr.append(f"{h} construye su juego en las transiciones: con solo el {pos_h:.0f}% "
                      "del balón iguala o supera en remates al rival. Equipo que vive en el "
                      "contraataque, explotando los espacios que deja el oponente al avanzar.")
        if pos_a is not None and pos_a < 44 and sht_a >= (sht_h or 0):
            tr.append(f"{a} es un equipo de transiciones puras: el {pos_a:.0f}% de posesión "
                      "contrasta con su capacidad de generar ocasiones. El bloque bajo y la "
                      "velocidad en profundidad son su principal arma.")
        if pos_h > 58 and sht_h and sht_h >= 8:
            tr.append(f"{h} impone un ritmo muy alto: dominio territorial y alto volumen de "
                      "remates. El equipo presiona alto y convierte cada recuperación en ocasión.")

    if off_h is not None and off_h >= 4:
        tr.append(f"{h} cae {off_h:.0f} veces en offside; busca insistentemente la espalda a "
                  "la defensa rival pero sin la sincronía necesaria en las carreras.")
    if off_a is not None and off_a >= 4:
        tr.append(f"{a} queda {off_a:.0f} veces adelantado; la línea defensiva local los atrapa.")

    if foul_h is not None and foul_a is not None:
        tot_f = (foul_h or 0) + (foul_a or 0)
        if tot_f <= 10 and sht_h and sht_a and sht_h + sht_a >= 10:
            tr.append("El partido fluye con pocas interrupciones y bastante llegada al arco. "
                      "Encuentro de ritmo alto que premia a los equipos con buen estado físico.")

    lecturas["transiciones"] = " ".join(tr) if tr else \
        "El ritmo aún no permite definir con claridad el modelo de transiciones de cada equipo."

    return lecturas

# ════════════════════════════════════════════════════════════════
#  ANÁLISIS DE PORTEROS (para reporte FT)
# ════════════════════════════════════════════════════════════════

def analisis_porteros(info, stats):
    h, a   = info["home_name"], info["away_name"]
    hs, as_ = stats["home"], stats["away"]
    partes = []

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
#  GENERADORES DE REPORTES
# ════════════════════════════════════════════════════════════════

SEP  = "─" * 62
SEP2 = "═" * 62

def _estadisticas_tabla(stats, h, a):
    lines = [f"  {'':26}  {h[:14]:>14}  {a[:14]:<14}", "  " + "─" * 56]
    for key, display in STATS_MAP.items():
        hv = stats["home"].get(key)
        av = stats["away"].get(key)
        if hv is not None or av is not None:
            lines.append(f"  {display:<26}  {str(hv or '—'):>14}  {str(av or '—'):<14}")
    return "\n".join(lines)

def reporte_ht(info, stats, lecturas, ts):
    h, a = info["home_name"], info["away_name"]
    marcador = f"{h} {info['home_score']} – {info['away_score']} {a}"

    secciones = [
        ("CONTROL TERRITORIAL",   lecturas.get("territorial", "Sin datos.")),
        ("EFICACIA OFENSIVA",     lecturas.get("ofensiva",    "Sin datos.")),
        ("SOLIDEZ DEFENSIVA",     lecturas.get("defensiva",   "Sin datos.")),
        ("RITMO Y TRANSICIONES",  lecturas.get("transiciones","Sin datos.")),
    ]

    lines = [
        "", SEP2,
        "  REPORTE DE PRIMERA MITAD",
        f"  {marcador}",
        f"  {info['tournament']}",
        f"  {ts}",
        SEP2, "",
    ]

    # Resumen ejecutivo
    gh, ga = _n(info["home_score"]), _n(info["away_score"])
    if gh is not None and ga is not None:
        if gh > ga:
            res = f"{h} gana la primera mitad {gh:.0f}–{ga:.0f}."
        elif ga > gh:
            res = f"{a} se va al descanso ganando {ga:.0f}–{gh:.0f}."
        else:
            res = f"Empate {gh:.0f}–{ga:.0f} al término de la primera mitad."
        lines += ["RESUMEN EJECUTIVO", SEP, _wrap(res), ""]

    for titulo, texto in secciones:
        if texto and "insuficientes" not in texto and "construcción" not in texto:
            lines += [titulo, SEP, _wrap(texto), ""]

    lines += ["ESTADÍSTICAS DEL PRIMER TIEMPO", SEP,
              _estadisticas_tabla(stats, h, a), ""]

    # Proyección
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


def reporte_ft(info, stats, lecturas, stats_ht, ts):
    h, a = info["home_name"], info["away_name"]
    marcador = f"{h} {info['home_score']} – {info['away_score']} {a}"

    lines = [
        "", SEP2,
        "  ANÁLISIS POST-PARTIDO — USO EN CRÓNICA Y ANÁLISIS",
        f"  {marcador}",
        f"  {info['tournament']}",
        f"  {ts}",
        SEP2, "",
    ]

    # 1. Lectura global
    gh, ga = _n(info["home_score"]), _n(info["away_score"])
    if gh is not None and ga is not None:
        if gh > ga:
            global_txt = (f"{h} venció {gh:.0f}–{ga:.0f} a {a} en un partido donde "
                          "las estadísticas confirman el dominio del ganador.")
        elif ga > gh:
            global_txt = (f"{a} se llevó los tres puntos {ga:.0f}–{gh:.0f}. "
                          "El visitante logró imponer su propuesta sobre {h}.")
        else:
            global_txt = (f"Empate {gh:.0f}–{ga:.0f} en un encuentro donde ninguno "
                          "logró la superioridad necesaria para sentenciar.")
        lines += ["1. LECTURA GLOBAL DEL PARTIDO", SEP, _wrap(global_txt), ""]

    # 2–5. Lecturas tácticas
    for titulo, clave in [
        ("2. DOMINIO TERRITORIAL",      "territorial"),
        ("3. PROPUESTA OFENSIVA",       "ofensiva"),
        ("4. COMPORTAMIENTO DEFENSIVO", "defensiva"),
        ("5. MODELO DE TRANSICIONES",   "transiciones"),
    ]:
        texto = lecturas.get(clave, "")
        if texto:
            lines += [titulo, SEP, _wrap(texto), ""]

    # 6. Evolución HT → FT
    if stats_ht:
        evo = []
        pos_ht = _n(stats_ht["home"].get("possessionPct"))
        pos_ft = _n(stats["home"].get("possessionPct"))
        if pos_ht is not None and pos_ft is not None and abs(pos_ft - pos_ht) >= 5:
            dir_t = "aumentó" if pos_ft > pos_ht else "redujo"
            evo.append(f"{h} {dir_t} su posesión del primer al segundo tiempo "
                       f"({pos_ht:.0f}% → {pos_ft:.0f}%), señal de un ajuste táctico en el entretiempo.")

        sht_ht_h = _n(stats_ht["home"].get("totalShots"))
        sht_ft_h = _n(stats["home"].get("totalShots"))
        sht_ht_a = _n(stats_ht["away"].get("totalShots"))
        sht_ft_a = _n(stats["away"].get("totalShots"))
        if sht_ht_h is not None and sht_ft_h is not None:
            if sht_ft_h > sht_ht_h + 3:
                evo.append(f"{h} fue más agresivo en el segundo tiempo, "
                           "incrementando significativamente su llegada al arco.")
        if sht_ht_a is not None and sht_ft_a is not None:
            if sht_ft_a > sht_ht_a + 3:
                evo.append(f"{a} intensificó su presión ofensiva tras el descanso.")

        if evo:
            lines += ["6. EVOLUCIÓN: 1ª VS 2ª MITAD", SEP, _wrap(" ".join(evo)), ""]

    # 7. Porteros
    gk = analisis_porteros(info, stats)
    if gk:
        lines += ["7. ANÁLISIS DE PORTEROS", SEP, _wrap(gk), ""]

    # 8. Estadísticas finales
    lines += ["8. CUADRO ESTADÍSTICO FINAL", SEP,
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
    fuentes = "ESPN" + (" + API-Football" if apif_on else "")

    console.print(Panel(
        f"[bold cyan]{info['tournament']}[/bold cyan]  [dim]· {fuentes}[/dim]",
        style="dim"))

    console.print(Panel(
        f"[bold white]{info['home_name']}[/bold white]  "
        f"[bold yellow]{info['home_score']} – {info['away_score']}[/bold yellow]  "
        f"[bold white]{info['away_name']}[/bold white]"
        f"  [bold green][{info['match_time']}][/bold green]",
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
            hs, as_ = str(hv or "—"), str(av or "—")
            hn, an = _n(hv), _n(av)
            if hn is not None and an is not None:
                if hn > an: hs = f"[bold]{hs}[/bold]"
                elif an > hn: as_ = f"[bold]{as_}[/bold]"
            table.add_row(hs, display, as_)
            filas += 1
    if filas == 0:
        table.add_row("—", "[dim]Sin datos aún[/dim]", "—")
    console.print(table)

    colores = {"territorial": "yellow", "ofensiva": "green",
               "defensiva": "red", "transiciones": "cyan"}
    titulos = {"territorial": "CONTROL TERRITORIAL",
               "ofensiva":    "EFICACIA OFENSIVA",
               "defensiva":   "SOLIDEZ DEFENSIVA",
               "transiciones":"TRANSICIONES Y RITMO"}

    for clave, color in colores.items():
        texto = lecturas.get(clave, "")
        if texto and "insuficientes" not in texto and "construcción" not in texto:
            console.print(f"\n[bold {color}]▶ {titulos[clave]}[/bold {color}]")
            console.print(f"  [white]{texto}[/white]")

    ahora = datetime.now().strftime("%H:%M:%S")
    console.print(f"\n[dim]Actualizado: {ahora}  ·  "
                  f"#{num_act}  ·  Próxima en {INTERVALO_ACTUALIZACION//60} min  ·  "
                  "Ctrl+C para salir[/dim]")


def limpiar():
    os.system("cls" if os.name == "nt" else "clear")


def mostrar_plano(info, stats, lecturas, num_act, apif_on):
    limpiar()
    ancho = 70
    fuentes = "ESPN" + (" + API-Football" if apif_on else "")
    print(SEP2)
    print(f"  {info['tournament'][:60]}")
    print(f"  Fuentes: {fuentes}")
    print(SEP2)
    marcador = (f"{info['home_name']}  {info['home_score']} – "
                f"{info['away_score']}  {info['away_name']}  [{info['match_time']}]")
    print(marcador[:ancho].center(ancho))
    print(SEP)
    print(f"  {'LOCAL':^20}  {'ESTADÍSTICA':^22}  {'VISITANTE':^12}")
    print(SEP)
    for key, display in STATS_MAP.items():
        hv = stats["home"].get(key)
        av = stats["away"].get(key)
        if hv is not None or av is not None:
            print(f"  {str(hv or '—'):^20}  {display:^22}  {str(av or '—'):^12}")

    titulos = [("CONTROL TERRITORIAL",  "territorial"),
               ("EFICACIA OFENSIVA",    "ofensiva"),
               ("SOLIDEZ DEFENSIVA",    "defensiva"),
               ("TRANSICIONES Y RITMO", "transiciones")]
    for titulo, clave in titulos:
        texto = lecturas.get(clave, "")
        if texto and "insuficientes" not in texto:
            print(f"\n{SEP}\n  {titulo}\n{SEP}")
            print(_wrap(texto))

    print(f"\n{SEP}")
    ahora = datetime.now().strftime("%H:%M:%S")
    print(f"  {ahora}  |  Update #{num_act}  |  Ctrl+C = salir")
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

    for i, info in enumerate(info_list, 1):
        marc   = f"{info['home_score']}-{info['away_score']}"
        estado = info["match_time"]
        liga   = info["tournament"][:28]
        if RICH_AVAILABLE:
            color = "green" if info["state"] == "in" else "dim"
            table.add_row(str(i), info["home_name"], marc, info["away_name"],
                          f"[{color}]{estado}[/{color}]", liga)
        else:
            vivo = " <<" if info["state"] == "in" else ""
            print(f"  {i:>3}  {info['home_name']:<24} {marc:>6}  "
                  f"{info['away_name']:<24}  {estado:>8}  {liga}{vivo}")

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
#  BUCLE PRINCIPAL
# ════════════════════════════════════════════════════════════════

def main():
    if RICH_AVAILABLE:
        Console().print("\n[bold green]Football Live Analysis v2[/bold green] — cargando...\n")
    else:
        print("\nFootball Live Analysis v2 — cargando...\n")

    eventos = espn_live_events()
    if eventos is None:
        print("ERROR: Sin conexión. Verifica tu internet."); sys.exit(1)
    if not eventos:
        print("No hay partidos disponibles en este momento."); sys.exit(0)

    evento, info = seleccionar_partido(eventos)
    league, event_id = info["league"], info["event_id"]

    # Buscar en API-Football
    apif_fixture = None
    if API_FOOTBALL_KEY:
        print("Buscando en API-Football...")
        apif_fixture = apif_find_fixture(info["home_name"], info["away_name"])
        if RICH_AVAILABLE:
            msg = ("[green]API-Football: partido encontrado.[/green]"
                   if apif_fixture else
                   "[yellow]API-Football: no encontrado. Usando solo ESPN.[/yellow]")
            Console().print(msg + "\n")
        else:
            print("API-Football:", "encontrado.\n" if apif_fixture else "no encontrado. Solo ESPN.\n")

    apif_on     = apif_fixture is not None
    num_act     = 0
    ht_done     = False
    stats_ht    = None
    prev_period = 1

    time.sleep(1)

    while True:
        num_act += 1

        # Obtener datos
        sum_espn  = espn_summary(event_id, league)
        st_espn   = espn_parse_stats(sum_espn)
        st_apif   = {"home": {}, "away": {}}
        if apif_on and apif_fixture:
            fid     = apif_fixture.get("fixture", {}).get("id")
            st_apif = apif_get_stats(fid)
        stats = merge_stats(st_espn, st_apif)

        # Info actualizada
        evs_act  = espn_live_events() or [evento]
        ev_act   = next((e for e in evs_act if e.get("id") == event_id), evento)
        info_act = espn_parse_event(ev_act)

        lecturas = analisis_tactico(info_act, stats)

        if RICH_AVAILABLE:
            mostrar_rich(info_act, stats, lecturas, num_act, apif_on)
        else:
            mostrar_plano(info_act, stats, lecturas, num_act, apif_on)

        # ── Reporte HT ────────────────────────────────────────────────────────
        periodo_actual = info_act.get("period", 1)
        es_ht = info_act.get("is_ht", False) or (periodo_actual >= 2 and prev_period < 2)

        if not ht_done and es_ht:
            stats_ht = {"home": dict(stats["home"]), "away": dict(stats["away"])}
            ts       = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt      = reporte_ht(info_act, stats, lecturas, ts)
            if RICH_AVAILABLE:
                Console().clear()
                Console().print(rpt)
            else:
                limpiar()
                print(rpt)
            ht_done = True

        prev_period = periodo_actual

        # ── Reporte FT ────────────────────────────────────────────────────────
        if info_act["state"] == "post":
            ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
            rpt = reporte_ft(info_act, stats, lecturas, stats_ht, ts)
            if RICH_AVAILABLE:
                Console().clear()
                Console().print(rpt)
            else:
                limpiar()
                print(rpt)
            break

        try:
            time.sleep(INTERVALO_ACTUALIZACION)
        except KeyboardInterrupt:
            print("\n\n  Detenido. ¡Hasta luego!")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Detenido. ¡Hasta luego!")
