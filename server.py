from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
import json, os, subprocess, sys, threading, time

app = Flask(__name__)
CORS(app)

STATE_FILE   = "bot_state.json"
CONTROL_FILE = "bot_control.json"

# ── Leer keys desde variables de entorno ──
API_KEY    = os.environ.get("API_KEY", "").strip()
API_SECRET = os.environ.get("API_SECRET", "").strip()

bot_process = None

def read_json(f):
    try:
        if os.path.exists(f):
            with open(f) as fp: return json.load(fp)
    except: pass
    return {}

def write_json(f, data):
    try:
        with open(f, "w") as fp: json.dump(data, fp)
    except: pass

def write_control(cmd):
    write_json(CONTROL_FILE, {"cmd": cmd})

def start_bot():
    global bot_process
    try:
        if bot_process and bot_process.poll() is None:
            return True
        env = os.environ.copy()
        env["API_KEY"]    = API_KEY
        env["API_SECRET"] = API_SECRET
        bot_process = subprocess.Popen(
            [sys.executable, "ciclo_pro.py"],
            env=env,
            stdout=open("ciclo_pro.log", "a"),
            stderr=subprocess.STDOUT
        )
        print(f"✅ Bot iniciado PID: {bot_process.pid}")
        return True
    except Exception as e:
        print(f"Error iniciando bot: {e}")
        return False

def monitor_bot():
    """Reinicia el bot si se cae."""
    while True:
        time.sleep(30)
        global bot_process
        if bot_process and bot_process.poll() is not None:
            print("⚠️ Bot caído, reiniciando...")
            start_bot()

@app.route("/")
def index():
    return send_file("dashboard.html")

@app.route("/api/status")
def status():
    d = read_json(STATE_FILE) or {}
    d["bot_running"] = bot_process is not None and bot_process.poll() is None
    d["api_configured"] = bool(API_KEY and API_SECRET)
    if not d.get("ultimo_evento"):
        if not d["api_configured"]:
            d["ultimo_evento"] = "⚠️ API Keys no configuradas en Railway"
        elif not d["bot_running"]:
            d["ultimo_evento"] = "⏳ Bot iniciando..."
    return jsonify(d)

@app.route("/api/pause",  methods=["POST"])
def pause():  write_control("pause");  return jsonify({"ok":True,"msg":"⏸ Bot pausado"})

@app.route("/api/resume", methods=["POST"])
def resume(): write_control("resume"); return jsonify({"ok":True,"msg":"▶️ Bot reanudado"})

@app.route("/api/stop",   methods=["POST"])
def stop():
    write_control("stop")
    return jsonify({"ok":True,"msg":"⛔ Bot detenido"})

@app.route("/api/start",  methods=["POST"])
def start():
    if not API_KEY or not API_SECRET:
        return jsonify({"ok":False,"msg":"⚠️ Configura API_KEY y API_SECRET en Railway Variables"})
    ok = start_bot()
    return jsonify({"ok":ok,"msg":"🚀 Bot iniciado!" if ok else "⚠️ Error al iniciar"})

@app.route("/api/log")
def log():
    try:
        with open("ciclo_pro.log") as f:
            return jsonify({"lines": f.readlines()[-50:]})
    except:
        return jsonify({"lines":[]})

if __name__ == "__main__":
    print("="*50)
    print(f"  API KEY configurada: {'✅' if API_KEY else '❌'}")
    print(f"  API SECRET configurada: {'✅' if API_SECRET else '❌'}")
    print("="*50)

    if API_KEY and API_SECRET:
        print("🚀 Arrancando bot automáticamente...")
        threading.Thread(target=start_bot, daemon=True).start()
        threading.Thread(target=monitor_bot, daemon=True).start()
    else:
        print("⚠️ Sin API Keys — configura variables en Railway")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

