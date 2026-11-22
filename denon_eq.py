from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json
import os
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
import requests, urllib.parse, time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# Receiver address comes from the environment. The port default is the Denon HTTPS API.
RECV_HOST = os.environ.get("DENON_HOST", "").strip()
RECV_PORT = int(os.environ.get("DENON_PORT", "10443"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "80"))
RECV_BASE = f"https://{RECV_HOST}:{RECV_PORT}"
PRESET_PATH = Path(os.environ.get("PRESET_PATH", "/var/lib/tx-denon-tools/presets.json"))
BANDS = ("63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
BAND_TAGS = {
    "63": "Eq63Hz",
    "125": "Eq125Hz",
    "250": "Eq250Hz",
    "500": "Eq500Hz",
    "1k": "Eq1kHz",
    "2k": "Eq2kHz",
    "4k": "Eq4kHz",
    "8k": "Eq8kHz",
    "16k": "Eq16kHz",
}
SPEAKER_NAMES = {
    "0": "Front L",
    "1": "Center",
    "2": "Front R",
    "3": "Front Wide R",
    "4": "Surround R",
    "5": "Surround Back R",
    "6": "Surround Back",
    "7": "Surround Back L",
    "8": "Surround L",
    "9": "Front Wide L",
}
PAGE_PATH = Path(__file__).with_name("web") / "index.html"
_preset_lock = threading.Lock()

# Mapping: unsere dB-Werte (-20..+6) direkt auf Denon-Rohwert (-200..+60)
def db_to_val(db: float) -> int:
    db = float(db)
    if db < -20.0:
        db = -20.0
    if db > 6.0:
        db = 6.0
    return int(round(db * 10.0))

def build_graphic_eq_payload(band_values):
    vals = {k: db_to_val(v) for k, v in band_values.items()}
    xml = (
        "<GraphicEQ><AdjustEQ><Channel>0</Channel>"
        f"<Eq63Hz>{vals['63']}</Eq63Hz>"
        f"<Eq125Hz>{vals['125']}</Eq125Hz>"
        f"<Eq250Hz>{vals['250']}</Eq250Hz>"
        f"<Eq500Hz>{vals['500']}</Eq500Hz>"
        f"<Eq1kHz>{vals['1k']}</Eq1kHz>"
        f"<Eq2kHz>{vals['2k']}</Eq2kHz>"
        f"<Eq4kHz>{vals['4k']}</Eq4kHz>"
        f"<Eq8kHz>{vals['8k']}</Eq8kHz>"
        f"<Eq16kHz>{vals['16k']}</Eq16kHz>"
        "</AdjustEQ></GraphicEQ>"
    )
    return xml

def receiver_request(path, params):
    query = dict(params)
    query["_"] = str(int(time.time() * 1000))
    response = requests.get(f"{RECV_BASE}{path}", params=query, timeout=5, verify=False)
    response.raise_for_status()
    return response.text

def read_graphic_eq():
    ts = str(int(time.time() * 1000))
    url = f"{RECV_BASE}/ajax/audio/get_config?type=10&_={ts}"
    response = requests.get(url, timeout=5, verify=False)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    adjust = None
    for node in root.iter("AdjustEQ"):
        if node.find("Eq63Hz") is not None:
            adjust = node
            break
    if adjust is None:
        raise ValueError("graphic EQ response has no band values")
    bands = {}
    for key, tag in BAND_TAGS.items():
        raw = adjust.findtext(tag)
        if raw is None or raw == "":
            raise ValueError(f"missing {tag}")
        bands[key] = int(raw) / 10.0
    return bands

def read_levels():
    root = ET.fromstring(receiver_request("/ajax/speakers/get_config", {"type": 5}))
    channels = []
    for node in root.iter("Speaker"):
        index = node.attrib.get("index")
        raw = (node.text or "").strip()
        if index is None or raw == "" or node.attrib.get("display") == "1":
            continue
        channels.append({
            "id": str(int(index)),
            "name": SPEAKER_NAMES.get(str(int(index)), f"Kanal {int(index)}"),
            "db": int(raw) / 10.0,
        })
    channels.sort(key=lambda channel: int(channel["id"]))
    return channels

def write_levels(values):
    speakers = []
    for key, db in values.items():
        raw = int(round(float(db) * 10))
        speakers.append(f'<Speaker index="{int(key)}">{raw}</Speaker>')
    receiver_request("/ajax/speakers/set_config", {"type": 5, "data": "<List>" + "".join(speakers) + "</List>"})

def read_volume():
    root = ET.fromstring(receiver_request("/ajax/control/get_config", {"type": 1}))
    zone = root if root.tag == "MainZone" else root.find("MainZone")
    if zone is None:
        raise ValueError("main zone missing")
    return {
        "volume": int(zone.findtext("Volume") or "0") / 10.0,
        "mute": (zone.findtext("Mute") or "") == "1",
    }

def write_volume(volume=None, mute=None):
    inner = ""
    if volume is not None:
        inner += f"<Volume>{int(round(float(volume) * 10))}</Volume>"
    if mute is not None:
        inner += f"<Mute>{1 if mute else 2}</Mute>"
    if not inner:
        raise ValueError("nothing to set")
    receiver_request("/ajax/control/set_config", {"type": 1, "data": f"<MainZone>{inner}</MainZone>"})

def send_to_receiver(payload_xml):
    encoded = urllib.parse.quote(payload_xml, safe='')
    ts = str(int(time.time() * 1000))
    url = f"{RECV_BASE}/ajax/audio/set_config?type=10&data={encoded}&_={ts}"
    try:
        r = requests.get(url, timeout=5, verify=False)
        return {"url": url, "status_code": r.status_code, "text": r.text}
    except Exception as e:
        return {"url": url, "error": str(e)}

@app.route("/eq", methods=["GET"])
def get_eq():
    if not RECV_HOST:
        return jsonify({"error": "receiver not configured"}), 503
    try:
        bands = read_graphic_eq()
    except Exception:
        return jsonify({"error": "receiver unreachable"}), 502
    return jsonify({"bands": bands})

@app.route("/levels", methods=["GET"])
def get_levels():
    if not RECV_HOST:
        return jsonify({"error": "receiver not configured"}), 503
    try:
        channels = read_levels()
    except Exception:
        return jsonify({"error": "receiver unreachable"}), 502
    return jsonify({"channels": channels, "min": -12, "max": 12, "step": 0.5})

@app.route("/levels", methods=["POST"])
def post_levels():
    cleaned = _clean_levels(request.get_json(silent=True))
    if cleaned is None:
        return jsonify({"error": "invalid levels"}), 400
    try:
        write_levels(cleaned)
    except Exception:
        return jsonify({"error": "receiver unreachable"}), 502
    return jsonify({"ok": True})

@app.route("/volume", methods=["GET"])
def get_volume():
    if not RECV_HOST:
        return jsonify({"error": "receiver not configured"}), 503
    try:
        state = read_volume()
    except Exception:
        return jsonify({"error": "receiver unreachable"}), 502
    return jsonify(state)

@app.route("/volume", methods=["POST"])
def post_volume():
    data = request.get_json(silent=True) or {}
    volume = data.get("volume")
    mute = data.get("mute")
    if volume is None and mute is None:
        return jsonify({"error": "invalid volume"}), 400
    try:
        if volume is not None:
            volume = float(volume)
            if volume < 0 or volume > 98:
                return jsonify({"error": "invalid volume"}), 400
        if mute is not None:
            mute = bool(mute)
        write_volume(volume=volume, mute=mute)
    except Exception:
        return jsonify({"error": "receiver unreachable"}), 502
    return jsonify({"ok": True})

@app.route("/set_eq", methods=["POST"])
def set_eq():
    data = request.get_json() or {}
    xml = build_graphic_eq_payload(data)
    result = send_to_receiver(xml)
    print("=== SEND TO RECEIVER ===")
    print("URL:", result.get("url"))
    if "error" in result:
        print("Error:", result["error"])
    else:
        print("HTTP", result.get("status_code"))
        print("Response (truncated):", result.get("text","")[:500])
    print("========================")
    return jsonify({"sent_xml": xml, "receiver": result})

def _clean_preset_name(name: str) -> str | None:
    name = (name or "").strip()
    if not name or len(name) > 80:
        return None
    if any(char in name for char in "/\\\x00"):
        return None
    return name


def _clean_bands(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    cleaned = {}
    for key in BANDS:
        if key not in data:
            return None
        try:
            value = float(data[key])
        except (TypeError, ValueError):
            return None
        if value < -20.0 or value > 6.0:
            return None
        cleaned[key] = value
    return cleaned


def _clean_levels(data) -> dict | None:
    if not isinstance(data, dict) or not data:
        return None
    cleaned = {}
    for key, value in data.items():
        if not str(key).isdigit():
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < -12.0 or number > 12.0:
            return None
        cleaned[str(int(key))] = number
    return cleaned


def _read_presets() -> dict:
    empty = {"eq": {}, "levels": {}}
    if not PRESET_PATH.is_file():
        return empty
    try:
        data = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    if "eq" not in data and "levels" not in data:
        return {"eq": data, "levels": {}}
    return {"eq": data.get("eq") or {}, "levels": data.get("levels") or {}}


def _write_presets(presets: dict) -> None:
    PRESET_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRESET_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(PRESET_PATH)


@app.route("/", methods=["GET"])
def index():
    return Response(PAGE_PATH.read_text(encoding="utf-8"), mimetype="text/html")


@app.route("/presets/<kind>", methods=["GET"])
def list_presets(kind):
    if kind not in {"eq", "levels"}:
        return jsonify({"error": "unknown preset kind"}), 404
    with _preset_lock:
        return jsonify({"presets": _read_presets()[kind]})


@app.route("/presets/<kind>/<path:name>", methods=["PUT"])
def save_preset(kind, name):
    if kind not in {"eq", "levels"}:
        return jsonify({"error": "unknown preset kind"}), 404
    cleaned_name = _clean_preset_name(name)
    payload = request.get_json(silent=True)
    cleaned = _clean_bands(payload) if kind == "eq" else _clean_levels(payload)
    if cleaned_name is None or cleaned is None:
        return jsonify({"error": "invalid preset"}), 400
    with _preset_lock:
        presets = _read_presets()
        presets[kind][cleaned_name] = cleaned
        _write_presets(presets)
    return jsonify({"ok": True, "name": cleaned_name})


@app.route("/presets/<kind>/<path:name>", methods=["DELETE"])
def delete_preset(kind, name):
    if kind not in {"eq", "levels"}:
        return jsonify({"error": "unknown preset kind"}), 404
    cleaned_name = _clean_preset_name(name)
    if cleaned_name is None:
        return jsonify({"error": "invalid name"}), 400
    with _preset_lock:
        presets = _read_presets()
        presets[kind].pop(cleaned_name, None)
        _write_presets(presets)
    return jsonify({"ok": True})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "receiver": f"{RECV_HOST}:{RECV_PORT}"})

if __name__ == "__main__":
    if not RECV_HOST:
        raise SystemExit("DENON_HOST is empty. Set the receiver address in /etc/denon-eq.env.")
    print(f"Bridge läuft auf http://{LISTEN_HOST}:{LISTEN_PORT} — Receiver https://{RECV_HOST}:{RECV_PORT}")
    app.run(host=LISTEN_HOST, port=LISTEN_PORT)
