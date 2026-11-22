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

# --- HTML UI ---
HTML_UI = r"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Denon EQ Mischpult</title>
<style>
:root{
  --bg:#0b0b0b;
  --panel:#121213;
  --text:#e7e7e8;
  --accent:#ff9f1c;
  --accent-soft:rgba(255,159,28,0.14);
  --accent-border:rgba(255,159,28,0.6);
  --muted:#2a2a2c;
  --fader:#9a9a9a;
  --track:#000;
  --track-h:340px;
}
html,body{
  height:100%;
  margin:0;
  background:var(--bg);
  color:var(--text);
  font-family:Inter,Arial,sans-serif;
}
.wrap{
  max-width:1200px;
  margin:20px auto;
  padding:28px 20px 24px;
  background:linear-gradient(180deg,var(--panel),#0c0c0c);
  border-radius:12px;
  box-shadow:0 10px 40px rgba(0,0,0,0.6);
}
h1{
  margin:0 0 22px;
  font-weight:600;
  font-size:20px;
  text-align:center;
}
.controls{
  display:flex;
  gap:26px;
  justify-content:center;
  align-items:flex-end;
  padding:12px 10px 0;
}
.band{
  width:88px;
  display:flex;
  flex-direction:column;
  align-items:center;
  position:relative;
  user-select:none;
}
.band .label{
  font-size:14px;
  margin-bottom:16px;
  color:#cfd8df;
}
.band .value{
  margin-top:34px;       /* +20px mehr Abstand unter der Skala */
  font-size:13px;
  color:var(--accent);
}

/* Wrapper für Fader + Skala */
.track-wrap{
  position:relative;
  width:90px;
  height:var(--track-h);
  display:flex;
  align-items:center;
}

/* Fader-Spalte links: Track + Slider + Linien */
.fader{
  position:relative;
  width:28px;
  height:100%;
}

/* schwarze Spur */
.track{
  position:absolute;
  width:6px;
  height:100%;
  left:50%;
  transform:translateX(-50%);
  background:var(--track);
  border-radius:3px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,0.02);
  z-index:1;
}

/* horizontale Linien */
.zero-line,
.value-line{
  position:absolute;
  left:2px;
  right:2px;
  height:1px;
  background:rgba(255,255,255,0.25);
  z-index:2;
  pointer-events:none;
}
.value-line{
  background:rgba(255,255,255,0.6);
}

/* Slider exakt auf der Spur (Hitbox = Spur) */
.fader input[type=range]{
  -webkit-appearance:none;
  appearance:none;
  position:absolute;
  top:50%;
  left:50%;
  width:var(--track-h);
  height:40px;
  transform:translate(-50%, -50%) rotate(-90deg);
  background:transparent;
  outline:none;
  cursor:pointer;
  z-index:3;
}
.fader input[type=range]::-webkit-slider-runnable-track{
  width:100%;
  height:6px;
  background:transparent;
}
.fader input[type=range]::-moz-range-track{
  width:100%;
  height:6px;
  background:transparent;
}
.fader input[type=range]::-webkit-slider-thumb{
  -webkit-appearance:none;
  width:34px;
  height:14px;
  border-radius:3px;
  background:var(--fader);
  box-shadow:0 2px 4px rgba(0,0,0,0.6);
  border:1px solid rgba(0,0,0,0.6);
}
.fader input[type=range]::-moz-range-thumb{
  width:34px;
  height:14px;
  border-radius:3px;
  background:var(--fader);
  border:1px solid rgba(0,0,0,0.6);
}

/* Skala rechts daneben */
.scale{
  position:absolute;
  right:0;
  top:0;
  height:100%;
  width:56px;
  font-size:11px;
  color:#cfd8df;
  user-select:none;
  z-index:4;
}
.scale-row{
  position:absolute;
  left:0;
  display:flex;
  align-items:center;
  gap:6px;
  transform:translateY(-50%);
}
.scale-tick{
  width:14px;
  height:1px;
  background:#cfd8df;
  flex-shrink:0;
}
.scale-row span{
  white-space:nowrap;
  line-height:1.1;
}

.status{
  margin-top:22px;
  text-align:center;
  color:#b9c4cc;
  font-size:13px;
}

/* --- PRESET-BEREICH --- */
.presets{
  margin-top:26px;
  padding:14px 16px 16px;
  border-radius:10px;
  background:linear-gradient(135deg,rgba(255,255,255,0.02),rgba(0,0,0,0.5));
  border:1px solid rgba(255,255,255,0.06);
}
.presets-title{
  font-size:13px;
  letter-spacing:0.03em;
  text-transform:uppercase;
  color:#cfd8df;
  opacity:0.9;
  margin-bottom:10px;
}
.preset-row{
  display:flex;
  gap:10px;
  align-items:center;
  margin-bottom:8px;
  flex-wrap:wrap;
}
.preset-row:last-child{
  margin-bottom:0;
}
.preset-input{
  flex:1;
  min-width:180px;
  padding:6px 10px;
  border-radius:6px;
  border:1px solid rgba(255,255,255,0.08);
  background:rgba(0,0,0,0.35);
  color:var(--text);
  font-size:13px;
}
.preset-select{
  flex:1;
  min-width:200px;
  padding:6px 10px;
  border-radius:6px;
  border:1px solid rgba(255,255,255,0.08);
  background:rgba(0,0,0,0.35);
  color:var(--text);
  font-size:13px;
}
.preset-input::placeholder{
  color:#858585;
}
.preset-btn{
  padding:6px 12px;
  border-radius:6px;
  border:1px solid var(--accent-border);
  background:var(--accent-soft);
  color:var(--accent);
  font-size:12px;
  font-weight:500;
  cursor:pointer;
  transition:background 0.15s, transform 0.05s, box-shadow 0.15s, border-color 0.15s;
  white-space:nowrap;
}
.preset-btn.secondary{
  border-color:rgba(255,255,255,0.25);
  background:rgba(255,255,255,0.04);
  color:#dde3ea;
}
.preset-btn.danger{
  border-color:rgba(255,80,80,0.7);
  background:rgba(255,80,80,0.08);
  color:#ff8080;
}
.preset-btn:hover{
  box-shadow:0 0 0 1px rgba(255,255,255,0.06);
}
.preset-btn:active{
  transform:translateY(1px);
}
.preset-message{
  margin-top:6px;
  font-size:12px;
  color:#9aa4ad;
}

@media (max-width:920px){
  .controls{gap:14px;flex-wrap:wrap}
  .band{width:70px}
  .presets{margin-top:20px}
}
</style>
</head>
<body>
<div class="wrap">
  <h1>Denon AVR-S750H — EQ Mischpult</h1>
  <div class="controls" id="controls"></div>
  <div class="status" id="status">Bereit. Änderungen werden an den Receiver gesendet.</div>

  <!-- Preset-Bereich -->
  <div class="presets">
    <div class="presets-title">Presets</div>
    <div class="preset-row">
      <input id="presetName" class="preset-input" type="text" placeholder="Neuen Preset-Namen eingeben…">
      <button id="btnSavePreset" class="preset-btn">Preset speichern</button>
    </div>
    <div class="preset-row">
      <select id="presetList" class="preset-select">
        <option value="">Gespeicherte Presets auswählen…</option>
      </select>
      <button id="btnLoadPreset" class="preset-btn secondary">Preset laden</button>
      <button id="btnDeletePreset" class="preset-btn danger">Preset löschen</button>
    </div>
    <div id="presetMessage" class="preset-message"></div>
  </div>
</div>

<script>
const BRIDGE = "/set_eq";
const DEBOUNCE_MS = 160;
const MIN_DB = -20;
const MAX_DB = 6;

const bands = [
  {key:"63", label:"63 Hz"},{key:"125", label:"125 Hz"},{key:"250", label:"250 Hz"},
  {key:"500", label:"500 Hz"},{key:"1k", label:"1 kHz"},{key:"2k", label:"2 kHz"},
  {key:"4k", label:"4 kHz"},{key:"8k", label:"8 kHz"},{key:"16k", label:"16 kHz"}
];
const controls = document.getElementById('controls');
const statusEl = document.getElementById('status');
const deb = {};
let currentValues = {"63":0,"125":0,"250":0,"500":0,"1k":0,"2k":0,"4k":0,"8k":0,"16k":0};
let lastSentSnapshot = null;

/* Presets liegen auf dem Server. Die letzte Faderstellung bleibt im Browser. */
const PRESET_STORAGE_KEY = "denonEqPresetsV1";
const LAST_VALUES_STORAGE_KEY = "denonEqLastValuesV1";
let presets = {};

function valueToTopPct(v){
  return (MAX_DB - v) / (MAX_DB - MIN_DB) * 100;
}

function buildUI(){
  bands.forEach(b=>{
    const el=document.createElement('div');el.className='band';
    const lbl=document.createElement('div');lbl.className='label';lbl.textContent=b.label;

    const wrap=document.createElement('div');wrap.className='track-wrap';

    const fader=document.createElement('div');fader.className='fader';

    const track=document.createElement('div');track.className='track';
    fader.appendChild(track);

    const zero=document.createElement('div');zero.className='zero-line';
    fader.appendChild(zero);

    const vline=document.createElement('div');vline.className='value-line';
    fader.appendChild(vline);

    const slider=document.createElement('input');slider.type='range';
    slider.min=MIN_DB;slider.max=MAX_DB;slider.step=0.5;slider.value=0;
    slider.dataset.band=b.key;
    slider.addEventListener('input',onSlide);
    slider.addEventListener('change',onSlideFinal);
    fader.appendChild(slider);

    wrap.appendChild(fader);

    const scale=document.createElement('div');scale.className='scale';
    for(let s=MAX_DB; s>=MIN_DB; s-=2){
      const row=document.createElement('div');
      row.className='scale-row';

      const topPct = valueToTopPct(s);
      row.style.top = topPct + '%';

      const tick=document.createElement('div');
      tick.className='scale-tick';

      const label=document.createElement('span');
      label.textContent=s+' dB';

      row.appendChild(tick);
      row.appendChild(label);
      scale.appendChild(row);
    }
    wrap.appendChild(scale);

    const val=document.createElement('div');val.className='value';val.textContent='0.0 dB';
    el.appendChild(lbl);
    el.appendChild(wrap);
    el.appendChild(val);
    controls.appendChild(el);

    const zeroTop = valueToTopPct(0);
    zero.style.top = zeroTop + '%';
    vline.style.top = zeroTop + '%';
  });
}

function updateValueLine(slider, v){
  const fader = slider.parentElement;
  const vline = fader.querySelector('.value-line');
  if(!vline) return;
  const topPct = valueToTopPct(v);
  vline.style.top = topPct + '%';
}

function onSlide(e){
  const band=e.target.dataset.band;
  let v=Number(e.target.value);
  v = Math.max(MIN_DB, Math.min(MAX_DB, v));
  currentValues[band]=v;

  const valEl = e.target.parentElement.parentElement.nextSibling;
  valEl.textContent=v.toFixed(1)+' dB';

  updateValueLine(e.target, v);

  clearTimeout(deb[band]);
  deb[band]=setTimeout(()=>sendAll(),DEBOUNCE_MS);
}

function onSlideFinal(e){
  clearTimeout(deb[e.target.dataset.band]);
  sendAll();
}

function sendAll(){
  const snapshot=JSON.stringify(currentValues);
  if(snapshot===lastSentSnapshot) return;
  lastSentSnapshot=snapshot;
  statusEl.textContent='Sende EQ an Receiver…'; statusEl.classList.remove('err');
  fetch(BRIDGE,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:snapshot
  })
  .then(async r=>{
    if(!r.ok){
      const t=await r.text();
      statusEl.textContent=`Fehler: HTTP ${r.status} — ${t}`;
      statusEl.classList.add('err');
      return;
    }
    const j=await r.json();
    console.log('Receiver response',j);
    try{
      localStorage.setItem(LAST_VALUES_STORAGE_KEY, JSON.stringify(currentValues));
    }catch(e){
      console.warn('Konnte letzte Werte nicht speichern:', e);
    }
    statusEl.textContent='Gesendet.';
    setTimeout(()=>statusEl.textContent='Bereit. Änderungen werden an den Receiver gesendet.',900);
  })
  .catch(err=>{
    statusEl.textContent='Netzwerkfehler: '+err.message;
    statusEl.classList.add('err');
    console.error(err);
  });
}

/* --- Presets --- */

function loadPresetsFromServer(){
  return fetch('/presets').then(async r=>{
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    presets = (j && j.presets && typeof j.presets === 'object') ? j.presets : {};
  });
}

function browserPresets(){
  try{
    const raw = localStorage.getItem(PRESET_STORAGE_KEY);
    if(!raw) return {};
    const parsed = JSON.parse(raw);
    return (parsed && typeof parsed === 'object') ? parsed : {};
  }catch(e){
    return {};
  }
}

function migrateBrowserPresets(){
  const local = browserPresets();
  const names = Object.keys(local);
  if(!names.length || Object.keys(presets).length) return Promise.resolve();
  return Promise.all(names.map(name => fetch('/presets/' + encodeURIComponent(name), {
    method:'PUT',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(local[name])
  }).then(r=>{
    if(!r.ok) throw new Error('HTTP ' + r.status);
  }))).then(()=>{
    presets = local;
    localStorage.removeItem(PRESET_STORAGE_KEY);
  });
}

function refreshPresetList(){
  const select = document.getElementById('presetList');
  if(!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">Gespeicherte Presets auswählen…</option>';
  Object.keys(presets).sort().forEach(name=>{
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  if(current && presets[current]) select.value = current;
}

function setPresetMessage(msg){
  const el = document.getElementById('presetMessage');
  if(!el) return;
  el.textContent = msg || '';
}

function initPresetsUI(){
  const nameInput = document.getElementById('presetName');
  const btnSave = document.getElementById('btnSavePreset');
  const btnLoad = document.getElementById('btnLoadPreset');
  const btnDelete = document.getElementById('btnDeletePreset');
  const select = document.getElementById('presetList');

  loadPresetsFromServer()
    .then(()=>migrateBrowserPresets().catch(()=>{}))
    .then(()=>refreshPresetList())
    .catch(()=>setPresetMessage('Presets konnten nicht geladen werden.'));

  if(btnSave){
    btnSave.addEventListener('click', ()=>{
      const name = (nameInput.value || '').trim();
      if(!name){
        setPresetMessage('Bitte einen Namen für das Preset eingeben.');
        return;
      }
      fetch('/presets/' + encodeURIComponent(name), {
        method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(currentValues)
      }).then(async r=>{
        if(!r.ok) throw new Error('HTTP ' + r.status);
        presets[name] = {...currentValues};
        refreshPresetList();
        if(select) select.value = name;
        setPresetMessage(`Preset „${name}“ gespeichert.`);
      }).catch(()=>setPresetMessage('Preset konnte nicht gespeichert werden.'));
    });
  }

  if(btnLoad){
    btnLoad.addEventListener('click', ()=>{
      const name = select.value;
      if(!name || !presets[name]){
        setPresetMessage('Bitte zuerst ein Preset aus der Liste wählen.');
        return;
      }
      applyPreset(presets[name]);
      setPresetMessage(`Preset „${name}“ geladen und an den Receiver gesendet.`);
    });
  }

  if(btnDelete){
    btnDelete.addEventListener('click', ()=>{
      const name = select.value;
      if(!name || !presets[name]){
        setPresetMessage('Kein gültiges Preset zum Löschen ausgewählt.');
        return;
      }
      fetch('/presets/' + encodeURIComponent(name), {method:'DELETE'}).then(async r=>{
        if(!r.ok) throw new Error('HTTP ' + r.status);
        delete presets[name];
        refreshPresetList();
        setPresetMessage(`Preset „${name}“ gelöscht.`);
      }).catch(()=>setPresetMessage('Preset konnte nicht gelöscht werden.'));
    });
  }
}

function showValues(values){
  Object.keys(currentValues).forEach(k=>{
    if(typeof values[k] === 'number'){
      currentValues[k] = values[k];
    }
  });
  syncUIFromCurrentValues();
}

function applyPreset(values){
  showValues(values);
  lastSentSnapshot = null;
  sendAll();
}

function loadReceiverValues(){
  statusEl.textContent = 'Lese Pegel vom Receiver…';
  statusEl.classList.remove('err');
  fetch('/eq').then(async r=>{
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if(!data || !data.bands) throw new Error('keine Pegel');
    showValues(data.bands);
    lastSentSnapshot = JSON.stringify(currentValues);
    statusEl.textContent = 'Bereit. Änderungen werden an den Receiver gesendet.';
  }).catch(()=>{
    loadLastValuesFromStorage();
    syncUIFromCurrentValues();
    lastSentSnapshot = JSON.stringify(currentValues);
    statusEl.textContent = 'Pegel vom Receiver nicht lesbar.';
    statusEl.classList.add('err');
  });
}

function loadLastValuesFromStorage(){
  try{
    const raw = localStorage.getItem(LAST_VALUES_STORAGE_KEY);
    if(!raw) return;
    const parsed = JSON.parse(raw);
    if(parsed && typeof parsed === 'object'){
      Object.keys(currentValues).forEach(k=>{
        if(typeof parsed[k] === 'number'){
          currentValues[k] = parsed[k];
        }
      });
    }
  }catch(e){
    console.warn('Konnte letzte Werte nicht laden:', e);
  }
}

function syncUIFromCurrentValues(){
  bands.forEach(b=>{
    const v = currentValues[b.key];
    const slider = document.querySelector(`input[type="range"][data-band="${b.key}"]`);
    if(!slider) return;
    slider.value = v;
    const valEl = slider.parentElement.parentElement.nextSibling;
    if(valEl) valEl.textContent = v.toFixed(1)+' dB';
    updateValueLine(slider, v);
  });
}

/* Start */
buildUI();
syncUIFromCurrentValues();
loadReceiverValues();
initPresetsUI();
</script>
</body>
</html>
"""

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


def _read_presets() -> dict:
    if not PRESET_PATH.is_file():
        return {}
    try:
        data = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_presets(presets: dict) -> None:
    PRESET_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRESET_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(PRESET_PATH)


@app.route("/", methods=["GET"])
def index():
    return Response(HTML_UI, mimetype="text/html")


@app.route("/presets", methods=["GET"])
def list_presets():
    with _preset_lock:
        return jsonify({"presets": _read_presets()})


@app.route("/presets/<path:name>", methods=["PUT"])
def save_preset(name):
    cleaned_name = _clean_preset_name(name)
    cleaned_bands = _clean_bands(request.get_json(silent=True))
    if cleaned_name is None or cleaned_bands is None:
        return jsonify({"error": "invalid preset"}), 400
    with _preset_lock:
        presets = _read_presets()
        presets[cleaned_name] = cleaned_bands
        _write_presets(presets)
    return jsonify({"ok": True, "name": cleaned_name})


@app.route("/presets/<path:name>", methods=["DELETE"])
def delete_preset(name):
    cleaned_name = _clean_preset_name(name)
    if cleaned_name is None:
        return jsonify({"error": "invalid name"}), 400
    with _preset_lock:
        presets = _read_presets()
        presets.pop(cleaned_name, None)
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
