from flask import Flask, jsonify, request, Response
from flask_cors import CORS
import json, os, threading, time, hmac, hashlib, requests as req
from urllib.parse import urlencode
from datetime import datetime

app  = Flask(__name__)
CORS(app, origins="*")

API_KEY    = os.environ.get("API_KEY","").strip()
API_SECRET = os.environ.get("API_SECRET","").strip()
BASE_URL   = "https://api.pionex.com"

# ── Estado global del bot ──────────────────────────────
estado = {
    "capital": 61.0, "ganancia_total": 0.0, "ciclo": 1,
    "ciclos_ganados": 0, "ciclos_perdidos": 0,
    "racha_ganados": 0, "racha_perdidos": 0,
    "bot_direction": None, "bot_symbol": None,
    "pnl_pct": 0.0, "pnl_usdt": 0.0,
    "precios": {}, "scores": {},
    "ultimo_evento": "Iniciando...",
    "corriendo": False, "pausado": False,
    "modo_horario": "NORMAL",
    "resumen_dia": {"ganancia": 0.0, "ciclos": 0, "inicio": str(datetime.utcnow().date())},
    "tp_activo": 0.5, "rondas": 0,
    "api_ok": bool(API_KEY and API_SECRET)
}
comando = {"cmd": None}
lock = threading.Lock()

PARES        = ["ETH_USDT","BTC_USDT","SOL_USDT","BNB_USDT"]
CAPITAL_BASE = 61.0
LEVERAGE     = 3
GRIDS        = 50
TAKE_PROFIT  = 0.5
STOP_LOSS    = -1.5
CHECK_EVERY  = 20

# ── API Pionex ─────────────────────────────────────────
def sign(method, path, params):
    params["timestamp"] = str(int(time.time()*1000))
    sp  = urlencode(sorted(params.items()))
    msg = f"{method}{path}?{sp}" if method=="GET" else f"{method}{path}{sp}"
    params["signature"] = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return params

def hdrs():
    return {"PIONEX-KEY": API_KEY, "Content-Type": "application/json"}

def get_prices():
    try:
        r = req.get(f"{BASE_URL}/api/v1/market/tickers", timeout=8)
        p = {}
        for t in r.json().get("data",{}).get("tickers",[]):
            if t["symbol"] in PARES:
                p[t["symbol"]] = float(t["close"])
        return p
    except: return {}

def get_candles(symbol, interval="1h", limit=60):
    try:
        r = req.get(f"{BASE_URL}/api/v1/market/klines",
                    params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=8)
        return r.json().get("data",{}).get("klines",[])
    except: return []

def ema(prices, p):
    k=2/(p+1); e=prices[0]
    for x in prices: e=x*k+e*(1-k)
    return round(e,4)

def rsi(prices, p=14):
    g,l=[],[]
    for i in range(1,len(prices)):
        d=prices[i]-prices[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[-p:])/p; al=sum(l[-p:])/p
    return round(100-(100/(1+ag/al)),2) if al else 100

def macd(prices): return round(ema(prices,12)-ema(prices,26),4)

def atr(candles,p=14):
    r=[float(c[2])-float(c[3]) for c in candles[-p:]]
    return sum(r)/len(r) if r else 0

def analizar(symbol):
    c1h=get_candles(symbol,"1h",60); c15m=get_candles(symbol,"15m",60)
    if not c1h or not c15m: return {"direction":"ESPERAR","score_long":0,"score_short":0,"symbol":symbol,"precio":0,"upper":0,"lower":0,"vol":0}
    cl1h=[float(c[4]) for c in c1h]; cl15m=[float(c[4]) for c in c15m]; price=cl15m[-1]
    e9=ema(cl1h,9); e21=ema(cl1h,21); e55=ema(cl1h,55)
    r1h=rsi(cl1h); r15m=rsi(cl15m); m1h=macd(cl1h); m15m=macd(cl15m)
    vol=round((atr(c15m)/price)*100,3) if price else 0
    SL=SS=0
    if e9>e21>e55: SL+=25
    elif e9<e21<e55: SS+=25
    elif e9>e21: SL+=12
    else: SS+=12
    if m1h>0: SL+=10
    else: SS+=10
    if 45<=r1h<=65: SL+=10
    elif r1h>65: SS+=12
    elif r1h<30: SL+=12
    if ema(cl15m,9)>ema(cl15m,21): SL+=15
    else: SS+=15
    if m15m>0: SL+=8
    else: SS+=8
    if vol>=0.3: SL+=5; SS+=5
    UMBRAL=55
    if SL>=SS and SL>=UMBRAL: direction="LONG"
    elif SS>SL and SS>=UMBRAL: direction="SHORT"
    else: direction="ESPERAR"
    mult=max(2.0,min(vol*2.5,6.0))
    upper=round(price*(1+mult/100),2); lower=round(price*(1-mult/100),2)
    return {"direction":direction,"score_long":SL,"score_short":SS,"symbol":symbol,
            "precio":price,"upper":upper,"lower":lower,"vol":vol,"rsi_1h":r1h}

def get_active_bot():
    try:
        path="/api/v1/grid/openOrders"
        r=req.get(BASE_URL+path,params=sign("GET",path,{}),headers=hdrs(),timeout=8)
        orders=r.json().get("data",{}).get("orders",[])
        eth=[o for o in orders if o.get("symbol") in PARES]
        return eth[0] if eth else None
    except: return None

def close_bot(bot_id):
    try:
        path="/api/v1/grid/closeOrder"; body={"orderId":bot_id}
        r=req.delete(BASE_URL+path,params=sign("DELETE",path,body.copy()),
                     data=json.dumps(body),headers=hdrs(),timeout=8)
        return r.json().get("result",False)
    except: return False

def open_bot(capital, direction, symbol, upper, lower):
    try:
        path="/api/v1/grid/createOrder"
        body={"symbol":symbol,"upperPrice":str(upper),"lowerPrice":str(lower),
              "gridNum":GRIDS,"amount":str(round(capital,2)),"leverage":LEVERAGE,"direction":direction}
        r=req.post(BASE_URL+path,params=sign("POST",path,body.copy()),
                   data=json.dumps(body),headers=hdrs(),timeout=8)
        return r.json().get("data",{}).get("orderId")
    except: return None

def get_pnl(bot):
    try:
        inv=float(bot.get("investment",CAPITAL_BASE)); pnl=float(bot.get("totalPnl",0))
        return pnl,(pnl/inv*100) if inv>0 else 0
    except: return 0,0

# ── CICLO PRINCIPAL ────────────────────────────────────
def bot_loop():
    capital=CAPITAL_BASE; ciclo=1; ganancia_total=0.0
    bot_direction=None; bot_symbol=None; esperando=False
    ciclos_ganados=ciclos_perdidos=racha_g=racha_p=0

    with lock: estado["corriendo"]=True; estado["ultimo_evento"]="🚀 Bot iniciado"

    while True:
        with lock:
            if not estado["corriendo"]: break
            if estado["pausado"]:
                estado["ultimo_evento"]="⏸ Bot pausado"
                time.sleep(CHECK_EVERY); continue
            cmd=comando.get("cmd")
            if cmd: comando["cmd"]=None

        if cmd=="stop":
            with lock: estado["corriendo"]=False; estado["ultimo_evento"]="⛔ Bot detenido"
            break
        if cmd=="pause":
            with lock: estado["pausado"]=True; continue
        if cmd=="resume":
            with lock: estado["pausado"]=False; continue

        try:
            precios=get_prices()
            with lock: estado["precios"]=precios

            bot=get_active_bot()

            if bot:
                esperando=False
                pnl_usdt,pnl_pct=get_pnl(bot)
                bot_id=bot.get("orderId")
                rondas=bot.get("filledCount",0)
                with lock:
                    estado.update({"pnl_usdt":pnl_usdt,"pnl_pct":pnl_pct,
                                   "bot_direction":bot_direction,"bot_symbol":bot_symbol,
                                   "rondas":rondas,"capital":capital,
                                   "ciclo":ciclo,"ciclos_ganados":ciclos_ganados,
                                   "ciclos_perdidos":ciclos_perdidos,
                                   "racha_ganados":racha_g,"racha_perdidos":racha_p,
                                   "ultimo_evento":f"{bot_direction} {bot_symbol} PnL:{pnl_pct:+.3f}%"})

                tp=estado["tp_activo"]
                if pnl_pct>=tp:
                    if close_bot(bot_id):
                        ganancia_total+=pnl_usdt; ciclos_ganados+=1; racha_g+=1; racha_p=0
                        if True: capital=round(CAPITAL_BASE+ganancia_total,2)
                        with lock:
                            estado.update({"capital":capital,"ganancia_total":ganancia_total,
                                           "ciclos_ganados":ciclos_ganados,"bot_direction":None,
                                           "racha_ganados":racha_g,"racha_perdidos":0,
                                           "ultimo_evento":f"🎯 TP ciclo #{ciclo} +{pnl_pct:.3f}%"})
                            estado["resumen_dia"]["ganancia"]+=pnl_usdt
                            estado["resumen_dia"]["ciclos"]+=1
                        bot_direction=bot_symbol=None; ciclo+=1; time.sleep(3)
                    continue

                if pnl_pct<=STOP_LOSS:
                    if close_bot(bot_id):
                        ganancia_total+=pnl_usdt; ciclos_perdidos+=1; racha_p+=1; racha_g=0
                        capital=round(CAPITAL_BASE+ganancia_total,2)
                        with lock:
                            estado.update({"capital":capital,"ganancia_total":ganancia_total,
                                           "ciclos_perdidos":ciclos_perdidos,"bot_direction":None,
                                           "racha_perdidos":racha_p,"racha_ganados":0,
                                           "ultimo_evento":f"🛑 SL ciclo #{ciclo} {pnl_pct:.3f}%"})
                        bot_direction=bot_symbol=None; ciclo+=1
                        time.sleep(180 if racha_p<2 else 300)
                    continue
            else:
                with lock: estado["bot_direction"]=None; estado["pnl_pct"]=0; estado["pnl_usdt"]=0

                resultados=[]
                for par in PARES:
                    res=analizar(par)
                    resultados.append(res)

                with lock:
                    estado["scores"]={r["symbol"]:r["score_long"] for r in resultados}

                accionables=[r for r in resultados if r["direction"] in ["LONG","SHORT"]]
                if accionables:
                    mejor=sorted(accionables,key=lambda x:x["score_long"]+x["score_short"],reverse=True)[0]
                    direction=mejor["direction"]; symbol=mejor["symbol"]
                    bot_direction=direction; bot_symbol=symbol; esperando=False
                    vol=mejor["vol"]
                    tp=1.0 if vol>2 else 0.75 if vol>1.5 else 0.5
                    with lock:
                        estado["tp_activo"]=tp
                        estado["ultimo_evento"]=f"🚀 Abriendo {direction} {symbol.split('_')[0]}"
                    new_id=open_bot(capital,direction,symbol,mejor["upper"],mejor["lower"])
                    if not new_id:
                        with lock: estado["ultimo_evento"]="⚠️ Error abriendo bot, reintentando..."
                        time.sleep(60); continue
                else:
                    if not esperando:
                        with lock: estado["ultimo_evento"]="⏳ Buscando mejor oportunidad..."
                        esperando=True

            time.sleep(CHECK_EVERY)

        except Exception as e:
            with lock: estado["ultimo_evento"]=f"⚠️ Error: {str(e)[:50]}"
            time.sleep(30)

bot_thread = None

# ── FLASK ROUTES ───────────────────────────────────────
@app.route("/")
def index():
    with open("dashboard.html") as f: content=f.read()
    content=content.replace(
        "SERVER_URL = LS.get('serverUrl') || 'http://localhost:5000';","SERVER_URL = '';"
    ).replace(
        "SERVER_URL = LS.get('serverUrl') || '';","SERVER_URL = '';"
    )
    return Response(content, mimetype='text/html')

@app.route("/api/status")
def status():
    with lock: d=dict(estado)
    d["bot_running"]=bot_thread is not None and bot_thread.is_alive()
    return jsonify(d)

@app.route("/api/pause",  methods=["POST"])
def pause():
    with lock: estado["pausado"]=True; estado["ultimo_evento"]="⏸ Pausado"
    return jsonify({"ok":True,"msg":"⏸ Bot pausado"})

@app.route("/api/resume", methods=["POST"])
def resume():
    with lock: estado["pausado"]=False; estado["ultimo_evento"]="▶️ Reanudado"
    return jsonify({"ok":True,"msg":"▶️ Bot reanudado"})

@app.route("/api/stop", methods=["POST"])
def stop():
    with lock: estado["corriendo"]=False; estado["ultimo_evento"]="⛔ Detenido"
    return jsonify({"ok":True,"msg":"⛔ Bot detenido"})

@app.route("/api/start", methods=["POST", "GET"])
def start():
    global bot_thread
    if not API_KEY or not API_SECRET:
        return jsonify({"ok":False,"msg":"⚠️ Configura API_KEY y API_SECRET en Railway Variables"})
    if bot_thread and bot_thread.is_alive():
        return jsonify({"ok":False,"msg":"⚠️ El bot ya está corriendo"})
    with lock: estado["corriendo"]=True; estado["pausado"]=False
    bot_thread=threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    return jsonify({"ok":True,"msg":"🚀 Bot iniciado!"})

@app.route("/api/log")
def log():
    try:
        with open("ciclo_pro.log") as f: return jsonify({"lines":f.readlines()[-50:]})
    except: return jsonify({"lines":[]})

# Auto-start bot on import (for gunicorn)
if API_KEY and API_SECRET:
    import atexit
    _t = threading.Thread(target=bot_loop, daemon=True)
    _t.start()
    print("🚀 Bot auto-iniciado")

if __name__=="__main__":
    print(f"API KEY: {'✅' if API_KEY else '❌'}")
    print(f"API SECRET: {'✅' if API_SECRET else '❌'}")
    if API_KEY and API_SECRET:
        bot_thread=threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()
        print("🚀 Bot iniciado automáticamente")
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, debug=False)
