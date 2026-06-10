import hmac, hashlib, time, requests, json, logging, threading
from urllib.parse import urlencode
from datetime import datetime

import os

CONTROL_FILE = "bot_control.json"

def leer_control():
    """Lee comandos enviados desde el dashboard."""
    try:
        if os.path.exists(CONTROL_FILE):
            with open(CONTROL_FILE) as f:
                data = json.load(f)
            os.remove(CONTROL_FILE)
            return data.get("cmd")
    except:
        pass
    return None

def guardar_estado_bot():
    """Escribe el estado actual para que el dashboard lo lea."""
    try:
        datos = {}
        for k, v in estado.items():
            if k == "corriendo": continue
            if hasattr(v, 'isoformat'): datos[k] = str(v)
            elif isinstance(v, dict):
                datos[k] = {kk: str(vv) if hasattr(vv,'isoformat') else vv for kk,vv in v.items()}
            else: datos[k] = v
        with open("bot_state.json", "w") as f:
            json.dump(datos, f)
    except:
        pass


# ═══════════════════════════════════════════════════════
#  CONFIGURACIÓN PRINCIPAL
# ═══════════════════════════════════════════════════════
API_KEY    = ""
API_SECRET = ""

# Pares a escanear — elige el mejor en cada ciclo
PARES = ["ETH_USDT", "BTC_USDT", "SOL_USDT", "BNB_USDT"]

CAPITAL_BASE  = 61.0   # Capital inicial total
LEVERAGE      = 3
GRIDS         = 50
TAKE_PROFIT   = 0.5    # % base de TP
STOP_LOSS     = -1.5   # % máximo de pérdida
CHECK_EVERY   = 20     # segundos
REINVEST      = True

# Gestión dinámica de capital
MAX_CAPITAL_PCT   = 0.90   # máximo 90% del capital en un bot
MIN_CAPITAL_PCT   = 0.30   # mínimo 30% en entradas de baja confianza
BOOST_WINS        = 5      # tras N ciclos ganados seguidos → sube capital 10%
REDUCE_LOSSES     = 2      # tras N ciclos perdidos seguidos → baja capital 20%

# Horario óptimo Colombia (UTC-5): 14:00-22:00 = 19:00-03:00 UTC
HORA_ACTIVA_INICIO = 19   # UTC
HORA_ACTIVA_FIN    = 3    # UTC
SCORE_MINIMO_NORMAL   = 55
SCORE_MINIMO_FUERA_HR = 70  # exige más confianza fuera del horario prime

TELEGRAM_TOKEN   = "8888154374:AAH9sHvZPExP0tNn6bDsA54tDwVWeg--Gdc"
TELEGRAM_CHAT_ID = "6876653066"

BASE_URL = "https://api.pionex.com"

# ═══════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ciclo_pro.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger()

# ═══════════════════════════════════════════════════════
#  ESTADO GLOBAL
# ═══════════════════════════════════════════════════════
estado = {
    "capital": CAPITAL_BASE, "ganancia_total": 0.0,
    "ciclo": 1, "ciclos_ganados": 0, "ciclos_perdidos": 0,
    "racha_ganados": 0, "racha_perdidos": 0,
    "bot_direction": None, "bot_symbol": None,
    "pnl_pct": 0.0, "pnl_usdt": 0.0,
    "precios": {}, "scores": {},
    "ultimo_evento": "Iniciando...",
    "corriendo": True, "pausado": False, "modo_horario": "NORMAL",
    "resumen_dia": {"ganancia": 0.0, "ciclos": 0, "inicio": datetime.utcnow().date()}
}

# ═══════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════
def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram: {e}")

def get_tg_updates(offset=0):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 5}, timeout=10
        )
        return r.json().get("result", [])
    except:
        return []

def status_msg():
    e = estado
    scores_txt = " | ".join([f"{p.split('_')[0]}:{s}" for p, s in e["scores"].items()]) if e["scores"] else "Analizando..."
    precios_txt = " | ".join([f"{p.split('_')[0]}:${v:.0f}" for p, v in e["precios"].items()]) if e["precios"] else "..."
    bot_txt = (
        f"🔄 {e['bot_direction']} {e['bot_symbol']}\n"
        f"PnL: {e['pnl_pct']:+.3f}% (${e['pnl_usdt']:+.4f})"
    ) if e["bot_direction"] else "⏳ Escaneando mercado..."

    hoy = e["resumen_dia"]
    return (
        f"📈 <b>CICLO PRO BOT — STATUS</b>\n"
        f"{'─'*32}\n"
        f"💰 Capital: ${e['capital']:.2f} USDT\n"
        f"📊 Ganancia total: ${e['ganancia_total']:.4f}\n"
        f"🔢 Ciclo: #{e['ciclo']} | ✅{e['ciclos_ganados']} ❌{e['ciclos_perdidos']}\n"
        f"🔥 Racha: +{e['racha_ganados']} | -{e['racha_perdidos']}\n"
        f"⏰ Modo: {e['modo_horario']}\n"
        f"{'─'*32}\n"
        f"💹 Precios: {precios_txt}\n"
        f"🧠 Scores: {scores_txt}\n"
        f"{'─'*32}\n"
        f"{bot_txt}\n"
        f"{'─'*32}\n"
        f"📅 Hoy: ${hoy['ganancia']:.4f} en {hoy['ciclos']} ciclos\n"
        f"🕐 {e['ultimo_evento']}"
    )

def telegram_listener():
    log.info("Telegram listener ON")
    last_id = 0
    while estado["corriendo"]:
        try:
            for upd in get_tg_updates(offset=last_id + 1):
                last_id = upd["update_id"]
                msg     = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip().lower()
                if chat_id != TELEGRAM_CHAT_ID:
                    continue
                if text in ["/status", "/s"]:
                    tg(status_msg())
                elif text in ["/stop", "/parar"]:
                    tg("⛔ Deteniendo bot al finalizar ciclo actual...")
                    estado["corriendo"] = False
                elif text in ["/start", "/help", "/ayuda"]:
                    tg(
                        "🤖 <b>COMANDOS</b>\n\n"
                        "/status — Estado completo\n"
                        "/s — Atajo status\n"
                        "/stop — Detener bot\n"
                        "/ayuda — Esta ayuda"
                    )
            time.sleep(3)
        except Exception as e:
            log.error(f"Listener: {e}")
            time.sleep(10)

# ═══════════════════════════════════════════════════════
#  API BASE
# ═══════════════════════════════════════════════════════
def sign(method, path, params):
    params["timestamp"] = str(int(time.time() * 1000))
    sp  = urlencode(sorted(params.items()))
    msg = f"{method}{path}?{sp}" if method == "GET" else f"{method}{path}{sp}"
    params["signature"] = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return params

def hdrs():
    return {"PIONEX-KEY": API_KEY, "Content-Type": "application/json"}

# ═══════════════════════════════════════════════════════
#  INDICADORES
# ═══════════════════════════════════════════════════════
def get_candles(symbol, interval="15m", limit=120):
    try:
        r = requests.get(f"{BASE_URL}/api/v1/market/klines",
                         params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
        return r.json().get("data", {}).get("klines", [])
    except:
        return []

def get_all_prices():
    try:
        r = requests.get(f"{BASE_URL}/api/v1/market/tickers", timeout=10)
        prices = {}
        for t in r.json().get("data", {}).get("tickers", []):
            if t["symbol"] in PARES:
                prices[t["symbol"]] = float(t["close"])
        return prices
    except:
        return {}

def ema(prices, p):
    k, e = 2/(p+1), prices[0]
    for x in prices: e = x*k + e*(1-k)
    return round(e, 6)

def rsi(prices, p=14):
    g, l = [], []
    for i in range(1, len(prices)):
        d = prices[i]-prices[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag = sum(g[-p:])/p; al = sum(l[-p:])/p
    return round(100-(100/(1+ag/al)), 2) if al else 100

def macd(prices):
    return round(ema(prices,12)-ema(prices,26), 6)

def atr(candles, p=14):
    r = [float(c[2])-float(c[3]) for c in candles[-p:]]
    return sum(r)/len(r) if r else 0

def adx(candles, p=14):
    try:
        H=[float(c[2]) for c in candles]; L=[float(c[3]) for c in candles]; C=[float(c[4]) for c in candles]
        trl,pdml,ndml=[],[],[]
        for i in range(1,len(candles)):
            tr=max(H[i]-L[i],abs(H[i]-C[i-1]),abs(L[i]-C[i-1]))
            pdm=max(H[i]-H[i-1],0); ndm=max(L[i-1]-L[i],0)
            if pdm>ndm: ndm=0
            elif ndm>pdm: pdm=0
            else: pdm=ndm=0
            trl.append(tr); pdml.append(pdm); ndml.append(ndm)
        at=sum(trl[-p:])/p
        pdi=(sum(pdml[-p:])/at)*100 if at else 0
        ndi=(sum(ndml[-p:])/at)*100 if at else 0
        dx=abs(pdi-ndi)/(pdi+ndi)*100 if (pdi+ndi)>0 else 0
        return round(dx,2), round(pdi,2), round(ndi,2)
    except:
        return 0,0,0

def bollinger(prices, p=20, std_mult=2):
    """Retorna si precio está cerca de banda inferior (rebote) o superior (venta)."""
    try:
        mn = sum(prices[-p:])/p
        sd = (sum((x-mn)**2 for x in prices[-p:])/p)**0.5
        upper = mn + std_mult*sd
        lower = mn - std_mult*sd
        price = prices[-1]
        pos = (price - lower)/(upper - lower) if (upper-lower) > 0 else 0.5
        return round(pos, 3), round(lower, 4), round(upper, 4)
    except:
        return 0.5, 0, 0

def detectar_soporte_resistencia(candles, ventana=20):
    """Detecta si precio está cerca de soporte (compra) o resistencia (venta)."""
    try:
        highs  = [float(c[2]) for c in candles[-ventana:]]
        lows   = [float(c[3]) for c in candles[-ventana:]]
        closes = [float(c[4]) for c in candles]
        price  = closes[-1]
        soporte    = min(lows)
        resistencia= max(highs)
        rango      = resistencia - soporte
        if rango == 0: return "NEUTRAL", 0.5
        pos = (price - soporte) / rango
        if pos < 0.25:   return "SOPORTE", pos
        elif pos > 0.75: return "RESISTENCIA", pos
        else:            return "MEDIO", pos
    except:
        return "NEUTRAL", 0.5

# ═══════════════════════════════════════════════════════
#  MOTOR DE DECISIÓN MULTI-PAR
# ═══════════════════════════════════════════════════════
def analizar_par(symbol):
    c1h  = get_candles(symbol, "1h",  100)
    c15m = get_candles(symbol, "15m", 100)
    c5m  = get_candles(symbol, "5m",  60)

    if not c1h or not c15m:
        return {"direction":"ESPERAR","score_long":0,"score_short":0,
                "score":0,"symbol":symbol,"precio":0,"upper":0,"lower":0,
                "vol":0,"razon":"Sin datos"}

    cl1h  = [float(c[4]) for c in c1h]
    cl15m = [float(c[4]) for c in c15m]
    cl5m  = [float(c[4]) for c in c5m] if c5m else cl15m
    price = cl15m[-1]

    # Indicadores
    e9_1h,  e21_1h,  e55_1h  = ema(cl1h,9),  ema(cl1h,21),  ema(cl1h,55)
    e9_15m, e21_15m           = ema(cl15m,9), ema(cl15m,21)
    e9_5m,  e21_5m            = ema(cl5m,9),  ema(cl5m,21)
    r1h  = rsi(cl1h); r15m = rsi(cl15m); r5m = rsi(cl5m)
    m1h  = macd(cl1h); m15m = macd(cl15m)
    adx_v, pdi, ndi = adx(c1h)
    vol_pct = round((atr(c15m)/price)*100, 3)
    bb_pos, bb_low, bb_high = bollinger(cl15m)
    sr_zona, sr_pos = detectar_soporte_resistencia(c15m)

    # Anti-whipsaw: confirmar con 2 velas consecutivas
    ultimas3_15m = [float(c[4]) for c in c15m[-3:]]
    tendencia_confirmada_up   = ultimas3_15m[-1] > ultimas3_15m[-2] > ultimas3_15m[-3]
    tendencia_confirmada_down = ultimas3_15m[-1] < ultimas3_15m[-2] < ultimas3_15m[-3]

    SL = SS = 0

    # ── 1H EMAs (peso 25) ──
    if e9_1h>e21_1h>e55_1h:   SL+=25
    elif e9_1h<e21_1h<e55_1h: SS+=25
    elif e9_1h>e21_1h:        SL+=12
    else:                      SS+=12

    # ── MACD 1H (peso 10) ──
    if m1h>0: SL+=10
    else:     SS+=10

    # ── ADX (peso 15) ──
    if adx_v>25:
        if pdi>ndi: SL+=15
        else:       SS+=15
    else:
        SL+=5; SS+=5

    # ── RSI 1H (peso 10) ──
    if 45<=r1h<=65:   SL+=10
    elif r1h>70:      SS+=12
    elif r1h<30:      SL+=12
    elif r1h>65:      SS+=8
    else:             SL+=5; SS+=5

    # ── 15M EMAs (peso 12) ──
    if e9_15m>e21_15m: SL+=12
    else:              SS+=12

    # ── MACD 15M (peso 8) ──
    if m15m>0: SL+=8
    else:      SS+=8

    # ── 5M confirmación (peso 10) ──
    if e9_5m>e21_5m and r5m<65: SL+=10
    elif e9_5m<e21_5m and r5m>35: SS+=10

    # ── Bollinger Bands (peso 8) ──
    if bb_pos < 0.2:   SL+=8   # precio cerca banda inferior → rebote
    elif bb_pos > 0.8: SS+=8   # precio cerca banda superior → caída

    # ── Soporte/Resistencia (peso 8) ──
    if sr_zona == "SOPORTE":     SL+=8
    elif sr_zona == "RESISTENCIA": SS+=8

    # ── Anti-whipsaw confirmación (peso 7) ──
    if tendencia_confirmada_up:   SL+=7
    elif tendencia_confirmada_down: SS+=7

    # ── Volatilidad (peso 5) ──
    if vol_pct>=0.3: SL+=5; SS+=5

    # Rango óptimo adaptativo
    mult  = max(2.0, min(vol_pct*2.5, 7.0))
    upper = round(price*(1+mult/100), 2)
    lower = round(price*(1-mult/100), 2)

    UMBRAL = SCORE_MINIMO_FUERA_HR if not en_horario_prime() else SCORE_MINIMO_NORMAL

    if SL>=SS and SL>=UMBRAL:   direction="LONG";    score=SL; emoji="📈"
    elif SS>SL and SS>=UMBRAL:  direction="SHORT";   score=SS; emoji="📉"
    else:                        direction="ESPERAR"; score=max(SL,SS); emoji="⏳"

    return {
        "direction": direction, "score_long": SL, "score_short": SS,
        "score": score, "symbol": symbol, "precio": price,
        "upper": upper, "lower": lower, "vol": vol_pct,
        "rsi_1h": r1h, "rsi_15m": r15m, "adx": adx_v,
        "bb_pos": bb_pos, "sr_zona": sr_zona,
        "razon": f"{emoji} {symbol.split('_')[0]} {direction} L:{SL} S:{SS}"
    }

def escanear_mejor_par():
    """Analiza todos los pares y elige el de mayor score."""
    resultados = []
    precios    = {}
    scores     = {}

    for par in PARES:
        try:
            res = analizar_par(par)
            resultados.append(res)
            precios[par] = res["precio"]
            scores[par]  = res["score"]
            log.info(f"  {res['razon']} | Vol:{res['vol']}%")
        except Exception as e:
            log.error(f"Error analizando {par}: {e}")

    estado["precios"] = precios
    estado["scores"]  = scores

    # Filtrar solo los que tienen dirección accionable
    accionables = [r for r in resultados if r["direction"] in ["LONG","SHORT"]]
    if not accionables:
        return resultados[0] if resultados else None

    # Ordenar por score descendente
    accionables.sort(key=lambda x: x["score"], reverse=True)
    return accionables[0]

# ═══════════════════════════════════════════════════════
#  HORARIO Y CAPITAL DINÁMICO
# ═══════════════════════════════════════════════════════
def en_horario_prime():
    hora_utc = datetime.utcnow().hour
    if HORA_ACTIVA_INICIO > HORA_ACTIVA_FIN:
        return hora_utc >= HORA_ACTIVA_INICIO or hora_utc < HORA_ACTIVA_FIN
    return HORA_ACTIVA_INICIO <= hora_utc < HORA_ACTIVA_FIN

def calcular_capital_dinamico(capital_disponible, score):
    """Ajusta capital según score de confianza y rachas."""
    pct = 0.6  # base 60%
    if score >= 80:   pct = 0.85
    elif score >= 70: pct = 0.75
    elif score >= 60: pct = 0.65

    # Boost por racha ganadora
    if estado["racha_ganados"] >= BOOST_WINS:
        pct = min(pct * 1.10, MAX_CAPITAL_PCT)
        log.info(f"🔥 Racha ganadora {estado['racha_ganados']} — capital +10%")

    # Reducción por racha perdedora
    if estado["racha_perdidos"] >= REDUCE_LOSSES:
        pct = max(pct * 0.80, MIN_CAPITAL_PCT)
        log.warning(f"⚠️ Racha perdedora {estado['racha_perdidos']} — capital -20%")

    return round(capital_disponible * pct, 2)

def calcular_tp_dinamico(vol_pct, score):
    """TP más alto cuando hay más volatilidad y confianza — trailing effect."""
    tp = TAKE_PROFIT
    if vol_pct > 2.0 and score >= 75: tp = 1.0   # alta vol + alta confianza
    elif vol_pct > 1.5 and score >= 65: tp = 0.75
    elif vol_pct < 0.5: tp = 0.35                  # baja vol → TP más fácil
    return tp

# ═══════════════════════════════════════════════════════
#  GESTIÓN DE BOTS
# ═══════════════════════════════════════════════════════
def get_active_bot():
    try:
        path = "/api/v1/grid/openOrders"
        r = requests.get(BASE_URL+path, params=sign("GET",path,{}), headers=hdrs(), timeout=10)
        orders = r.json().get("data",{}).get("orders",[])
        eth = [o for o in orders if o.get("symbol") in PARES]
        return eth[0] if eth else None
    except Exception as e:
        log.error(f"get_active_bot: {e}"); return None

def close_bot(bot_id):
    try:
        path = "/api/v1/grid/closeOrder"
        body = {"orderId": bot_id}
        r = requests.delete(BASE_URL+path, params=sign("DELETE",path,body.copy()),
                            data=json.dumps(body), headers=hdrs(), timeout=10)
        return r.json().get("result", False)
    except Exception as e:
        log.error(f"close_bot: {e}"); return False

def open_bot(capital, direction, symbol, upper, lower):
    try:
        path = "/api/v1/grid/createOrder"
        body = {
            "symbol": symbol, "upperPrice": str(upper), "lowerPrice": str(lower),
            "gridNum": GRIDS, "amount": str(capital),
            "leverage": LEVERAGE, "direction": direction
        }
        r = requests.post(BASE_URL+path, params=sign("POST",path,body.copy()),
                          data=json.dumps(body), headers=hdrs(), timeout=10)
        result = r.json()
        bot_id = result.get("data",{}).get("orderId")
        if bot_id:
            log.info(f"✅ {direction} {symbol} | {bot_id} | ${lower}–${upper} | ${capital}")
        else:
            log.error(f"Error API: {result}")
        return bot_id
    except Exception as e:
        log.error(f"open_bot: {e}"); return None

def get_pnl(bot):
    try:
        inv = float(bot.get("investment", CAPITAL_BASE))
        pnl = float(bot.get("totalPnl", 0))
        return pnl, (pnl/inv*100) if inv>0 else 0
    except:
        return 0, 0

# ═══════════════════════════════════════════════════════
#  RESUMEN DIARIO
# ═══════════════════════════════════════════════════════
def verificar_resumen_diario():
    hoy = datetime.utcnow().date()
    if estado["resumen_dia"]["inicio"] != hoy:
        ayer = estado["resumen_dia"]
        tg(
            f"📅 <b>RESUMEN DEL DÍA</b>\n"
            f"Fecha: {ayer['inicio']}\n"
            f"Ciclos completados: {ayer['ciclos']}\n"
            f"Ganancia del día: ${ayer['ganancia']:.4f} USDT\n"
            f"Capital actual: ${estado['capital']:.2f}"
        )
        estado["resumen_dia"] = {"ganancia": 0.0, "ciclos": 0, "inicio": hoy}

# ═══════════════════════════════════════════════════════
#  CICLO PRINCIPAL
# ═══════════════════════════════════════════════════════
def run():
    estado["capital"] = CAPITAL_BASE

    tg(
        f"🚀 <b>CICLO PRO BOT ULTIMATE — INICIADO</b>\n"
        f"Pares: {', '.join([p.split('_')[0] for p in PARES])}\n"
        f"Capital: ${CAPITAL_BASE} | TP: dinámico | SL: {STOP_LOSS}%\n"
        f"Leverage: {LEVERAGE}x | Grids: {GRIDS}\n\n"
        f"Comandos: /status /stop /ayuda"
    )
    log.info("CICLO PRO BOT ULTIMATE — INICIADO")

    threading.Thread(target=telegram_listener, daemon=True).start()

    capital         = CAPITAL_BASE
    ciclo           = 1
    ganancia_total  = 0.0
    bot_direction   = None
    bot_symbol      = None
    esperando       = False
    ciclos_ganados  = 0
    ciclos_perdidos = 0
    racha_g = racha_p = 0
    tp_activo = TAKE_PROFIT

    while estado["corriendo"]:
        try:
            verificar_resumen_diario()
            guardar_estado_bot()

            # Leer comandos del dashboard
            cmd = leer_control()
            if cmd == "pause" and not estado["pausado"]:
                estado["pausado"] = True
                estado["ultimo_evento"] = "⏸ Bot pausado desde dashboard"
                log.info("⏸ PAUSADO desde dashboard")
                tg("⏸ <b>Bot pausado</b> desde el dashboard")
            elif cmd == "resume" and estado["pausado"]:
                estado["pausado"] = False
                estado["ultimo_evento"] = "▶️ Bot reanudado"
                log.info("▶️ REANUDADO desde dashboard")
                tg("▶️ <b>Bot reanudado</b> desde el dashboard")
            elif cmd == "stop":
                estado["corriendo"] = False
                log.info("⛔ DETENIDO desde dashboard")
                break

            # Si está pausado, esperar
            if estado["pausado"]:
                time.sleep(CHECK_EVERY)
                continue

            bot = get_active_bot()
            hora_prime = en_horario_prime()
            estado["modo_horario"] = "PRIME ⭐" if hora_prime else "NORMAL"

            # ── BOT ACTIVO ───────────────────────────────
            if bot:
                esperando = False
                pnl_usdt, pnl_pct = get_pnl(bot)
                bot_id  = bot.get("orderId")
                rondas  = bot.get("filledCount", 0)

                estado.update({
                    "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
                    "bot_direction": bot_direction, "bot_symbol": bot_symbol,
                    "ultimo_evento": f"{bot_direction} {bot_symbol} PnL:{pnl_pct:+.3f}%"
                })

                log.info(f"⏱  [{bot_direction} {bot_symbol}] #{ciclo} | PnL:{pnl_pct:+.3f}% | Rondas:{rondas} | TP:{tp_activo}%")

                # TAKE PROFIT
                if pnl_pct >= tp_activo:
                    if close_bot(bot_id):
                        ganancia_total += pnl_usdt
                        ciclos_ganados += 1
                        racha_g += 1; racha_p = 0
                        if REINVEST:
                            capital = round(CAPITAL_BASE + ganancia_total, 2)

                        estado.update({
                            "capital": capital, "ganancia_total": ganancia_total,
                            "ciclos_ganados": ciclos_ganados, "bot_direction": None,
                            "racha_ganados": racha_g, "racha_perdidos": 0,
                            "ultimo_evento": f"✅ TP #{ciclo} +{pnl_pct:.3f}%"
                        })
                        estado["resumen_dia"]["ganancia"] += pnl_usdt
                        estado["resumen_dia"]["ciclos"]   += 1

                        tg(
                            f"🎯 <b>TAKE PROFIT!</b>\n"
                            f"Par: {bot_symbol} | {bot_direction}\n"
                            f"Ganancia: +{pnl_pct:.3f}% (+${pnl_usdt:.4f})\n"
                            f"Capital: ${capital:.2f} | Total: ${ganancia_total:.4f}\n"
                            f"✅ Ganados: {ciclos_ganados} | 🔥 Racha: {racha_g}"
                        )
                        bot_direction = bot_symbol = None
                        ciclo += 1; estado["ciclo"] = ciclo
                        time.sleep(3)
                    continue

                # STOP LOSS
                if pnl_pct <= STOP_LOSS:
                    if close_bot(bot_id):
                        ganancia_total  += pnl_usdt
                        ciclos_perdidos += 1
                        racha_p += 1; racha_g = 0
                        capital = round(CAPITAL_BASE + ganancia_total, 2)

                        estado.update({
                            "capital": capital, "ganancia_total": ganancia_total,
                            "ciclos_perdidos": ciclos_perdidos, "bot_direction": None,
                            "racha_perdidos": racha_p, "racha_ganados": 0,
                            "ultimo_evento": f"🛑 SL #{ciclo} {pnl_pct:.3f}%"
                        })
                        estado["resumen_dia"]["ganancia"] += pnl_usdt
                        estado["resumen_dia"]["ciclos"]   += 1

                        pausa = 300 if racha_p >= 2 else 180
                        tg(
                            f"🛑 <b>STOP LOSS</b>\n"
                            f"Par: {bot_symbol} | {bot_direction}\n"
                            f"Pérdida: {pnl_pct:.3f}% (${pnl_usdt:.4f})\n"
                            f"Capital: ${capital:.2f}\n"
                            f"❌ Perdidos: {ciclos_perdidos} | Racha: {racha_p}\n"
                            f"⏳ Pausa {pausa//60} min..."
                        )
                        bot_direction = bot_symbol = None
                        ciclo += 1; estado["ciclo"] = ciclo
                        time.sleep(pausa)
                    continue

            # ── SIN BOT — ESCANEAR ───────────────────────
            else:
                estado["bot_direction"] = None
                estado["pnl_usdt"]      = 0
                estado["pnl_pct"]       = 0

                log.info(f"🔍 Escaneando {len(PARES)} pares... | Horario: {'PRIME⭐' if hora_prime else 'NORMAL'}")
                mejor = escanear_mejor_par()

                if mejor and mejor["direction"] in ["LONG","SHORT"]:
                    direction     = mejor["direction"]
                    symbol        = mejor["symbol"]
                    bot_direction = direction
                    bot_symbol    = symbol
                    esperando     = False

                    capital_usar = calcular_capital_dinamico(capital, mejor["score"])
                    tp_activo    = calcular_tp_dinamico(mejor["vol"], mejor["score"])

                    estado["ultimo_evento"] = f"🚀 {direction} {symbol} Score:{mejor['score']}"

                    tg(
                        f"🚀 <b>ABRIENDO {direction} — {symbol.split('_')[0]}</b>\n"
                        f"Score: L:{mejor['score_long']} S:{mejor['score_short']}\n"
                        f"Precio: ${mejor['precio']:.2f} | Rango: ${mejor['lower']}–${mejor['upper']}\n"
                        f"RSI 1H: {mejor['rsi_1h']} | Vol: {mejor['vol']}% | ADX: {mejor['adx']}\n"
                        f"BB: {mejor['bb_pos']:.2f} | S/R: {mejor['sr_zona']}\n"
                        f"Capital: ${capital_usar} | TP: {tp_activo}% | Ciclo #{ciclo}\n"
                        f"Modo: {'PRIME⭐' if hora_prime else 'NORMAL'}"
                    )

                    new_id = open_bot(capital_usar, direction, symbol, mejor["upper"], mejor["lower"])
                    if not new_id:
                        tg("⚠️ Error abriendo bot. Reintentando en 60s...")
                        time.sleep(60)
                        continue
                else:
                    if not esperando:
                        scores_str = " | ".join([f"{p.split('_')[0]}:{s}" for p,s in estado["scores"].items()])
                        tg(
                            f"⏳ <b>ESPERANDO OPORTUNIDAD</b>\n"
                            f"Scores: {scores_str}\n"
                            f"Mínimo requerido: {SCORE_MINIMO_FUERA_HR if not hora_prime else SCORE_MINIMO_NORMAL}\n"
                            f"Modo: {'PRIME⭐' if hora_prime else 'NORMAL'}"
                        )
                        estado["ultimo_evento"] = "⏳ Esperando condiciones óptimas"
                        esperando = True

            time.sleep(CHECK_EVERY)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Error: {e}")
            tg(f"⚠️ Error: {e}")
            time.sleep(30)

    estado["corriendo"] = False
    tg(
        f"⛔ <b>BOT DETENIDO</b>\n"
        f"Ciclos: {ciclo-1} | ✅{ciclos_ganados} | ❌{ciclos_perdidos}\n"
        f"Ganancia total: ${ganancia_total:.4f} USDT\n"
        f"Capital final: ${capital:.2f}"
    )

if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        print("═"*55)
        print("  CICLO PRO BOT ULTIMATE")
        print("═"*55)
        API_KEY    = input("API Key: ").strip()
        API_SECRET = input("API Secret: ").strip()
    run()

# ── GUARDAR ESTADO PARA DASHBOARD ──────────────────
import json as _json

def guardar_estado():
    try:
        datos = {k: v for k, v in estado.items() if k != "corriendo"}
        # Serializar fecha
        if "resumen_dia" in datos and hasattr(datos["resumen_dia"].get("inicio"), "isoformat"):
            datos["resumen_dia"]["inicio"] = str(datos["resumen_dia"]["inicio"])
        with open("bot_state.json", "w") as f:
            _json.dump(datos, f)
    except:
        pass

# Llamar guardar_estado() después de cada update en run()
