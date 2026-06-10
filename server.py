from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
import json, os, subprocess, sys, threading

app = Flask(__name__)
CORS(app)

STATE_FILE   = "bot_state.json"
CONTROL_FILE = "bot_control.json"
KEYS_FILE    = "bot_keys.json"
CONFIG_FILE  = "bot_config.json"

# ── Auto-cargar keys desde variables de entorno Railway ──
def init_keys_from_env():
    key    = os.environ.get("API_KEY","").strip()
    secret = os.environ.get("API_SECRET","").strip()
    if key and secret:
        write_json(KEYS_FILE, {"key": key, "secret": secret})
        print(f"✅ Keys cargadas desde variables de entorno")

def read_json(f):
    try:
        if os.path.exists(f):
            with open(f) as fp: return json.load(fp)
    except: pass
    return {}

def write_json(f, data):
    with open(f, "w") as fp: json.dump(data, fp)

def write_control(cmd):
    write_json(CONTROL_FILE, {"cmd": cmd})

# ── Arrancar bot en background ──
bot_process = None

def start_bot_process():
    global bot_process
    try:
        if bot_process and bot_process.poll() is None:
            return False
        bot_process = subprocess.Popen(
            [sys.executable, "ciclo_pro.py"],
            stdout=open("ciclo_pro.log","a"),
            stderr=subprocess.STDOUT
        )
        print(f"✅ Bot iniciado PID: {bot_process.pid}")
        return True
    except Exception as e:
        print(f"Error iniciando bot: {e}")
        return False

@app.route("/")
def index():
    return send_file("dashboard.html")

@app.route("/api/status")
def status():
    d = read_json(STATE_FILE) or {"error": "Bot no iniciado"}
    d["bot_running"] = bot_process is not None and bot_process.poll() is None
    return jsonify(d)

@app.route("/api/keys", methods=["POST"])
def set_keys():
    data = request.json or {}
    key    = data.get("key","").strip()
    secret = data.get("secret","").strip()
    if not key or not secret:
        return jsonify({"ok": False, "msg": "Keys inválidas"})
    write_json(KEYS_FILE, {"key": key, "secret": secret})
    write_control("reload_keys")
    return jsonify({"ok": True, "msg": "✅ Keys guardadas"})

@app.route("/api/config", methods=["POST"])
def set_config():
    write_json(CONFIG_FILE, request.json or {})
    write_control("reload_config")
    return jsonify({"ok": True, "msg": "✅ Configuración actualizada"})

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
    keys = read_json(KEYS_FILE)
    if not keys.get("key"):
        return jsonify({"ok":False,"msg":"⚠️ Primero guarda tus API Keys"})
    ok = start_bot_process()
    return jsonify({"ok":ok,"msg":"🚀 Bot iniciado!" if ok else "⚠️ Error al iniciar"})

@app.route("/api/log")
def log():
    try:
        with open("ciclo_pro.log") as f:
            return jsonify({"lines": f.readlines()[-50:]})
    except:
        return jsonify({"lines":[]})

if __name__ == "__main__":
    init_keys_from_env()
    # Auto-iniciar bot si hay keys
    keys = read_json(KEYS_FILE)
    if keys.get("key"):
        threading.Thread(target=start_bot_process, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"="*50)
    print(f"  PIONEX SERVER — Puerto {port}")
    print(f"="*50)
    app.run(host="0.0.0.0", port=port, debug=False)
