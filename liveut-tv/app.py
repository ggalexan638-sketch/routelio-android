import sys, os, re, json, gzip, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QFileDialog, QListWidget, QLabel, QComboBox, QSpinBox,
    QColorDialog, QSplitter, QMessageBox, QCheckBox, QGroupBox
)

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
FFMPEG = str(BASE / "ffmpeg.exe") if (BASE / "ffmpeg.exe").exists() else "ffmpeg"
VLC_DIR = BASE
if (VLC_DIR / "libvlc.dll").exists():
    os.environ.setdefault("VLC_PLUGIN_PATH", str(VLC_DIR / "plugins"))
    os.add_dll_directory(str(VLC_DIR)) if hasattr(os, "add_dll_directory") else None

APP_VERSION = "1.2"
USER_AGENT = "LiveUT-TV/1.2 VLC/3.0"
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8", ".xspf", ".txt", ".json"}
LANGUAGES = [
    ("Automatisch (60+ Sprachen)", "auto"), ("Deutsch", "de"),
    ("Englisch", "en"), ("Französisch", "fr"), ("Spanisch", "es"),
    ("Italienisch", "it"), ("Portugiesisch", "pt"), ("Türkisch", "tr"),
    ("Arabisch", "ar"), ("Polnisch", "pl"), ("Russisch", "ru"),
    ("Ukrainisch", "uk"), ("Niederländisch", "nl"), ("Griechisch", "el"),
    ("Rumänisch", "ro"), ("Serbisch", "sr"), ("Kroatisch", "hr"),
    ("Bosnisch", "bs"), ("Albanisch", "sq"), ("Bulgarisch", "bg"),
    ("Tschechisch", "cs"), ("Slowakisch", "sk"), ("Ungarisch", "hu"),
    ("Schwedisch", "sv"), ("Norwegisch", "no"), ("Dänisch", "da"),
    ("Finnisch", "fi"), ("Chinesisch", "zh"), ("Japanisch", "ja"),
    ("Koreanisch", "ko"), ("Hindi", "hi"), ("Persisch", "fa"),
    ("Hebräisch", "he"), ("Indonesisch", "id"), ("Vietnamesisch", "vi")
]


def http_bytes(url, timeout=30, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data, response.geturl(), response.headers.get("Content-Type", "")
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Server antwortet nicht: {last}")


def parse_m3u(text):
    result, meta = [], None
    for raw in text.replace("\ufeff", "").splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            meta = {
                "name": line.rsplit(",", 1)[-1].strip() or "Unbenannt",
                "group": attrs.get("group-title", "Andere") or "Andere",
                "logo": attrs.get("tvg-logo", ""), "type": "live"
            }
        elif line and not line.startswith("#"):
            item = meta or {"name": line, "group": "Direkte Streams", "logo": "", "type": "live"}
            result.append({**item, "url": line})
            meta = None
    return result


def parse_xspf(text):
    import xml.etree.ElementTree as ET
    result = []
    root = ET.fromstring(text)
    for track in root.findall(".//{*}track"):
        location = track.findtext("{*}location", "").strip()
        if location:
            result.append({"name": track.findtext("{*}title", location),
                           "group": "XSPF", "logo": "", "type": "live", "url": location})
    return result


def parse_json_playlist(text):
    raw = json.loads(text)
    raw = raw.get("items", raw.get("channels", raw)) if isinstance(raw, dict) else raw
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("stream_url")
        if url:
            result.append({"name": str(item.get("name") or item.get("title") or url),
                           "group": str(item.get("group") or item.get("category") or "JSON"),
                           "logo": str(item.get("logo") or ""), "type": "live", "url": url})
    return result


def decode_playlist(data, source=""):
    text = data.decode("utf-8-sig", "ignore")
    suffix = Path(urlparse(source).path).suffix.lower()
    stripped = text.lstrip()
    if suffix == ".xspf" or stripped.startswith("<?xml"):
        return parse_xspf(text)
    if suffix == ".json" or stripped.startswith("[") or stripped.startswith("{"):
        return parse_json_playlist(text)
    return parse_m3u(text)


def xtream_from_get_url(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if not parsed.path.lower().endswith("get.php") or not query.get("username") or not query.get("password"):
        return None
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base, query["username"][0], query["password"][0]


def load_xtream(url):
    credentials = xtream_from_get_url(url)
    if not credentials:
        return []
    base, username, password = credentials
    auth = urlencode({"username": username, "password": password})
    categories_data, _, _ = http_bytes(f"{base}/player_api.php?{auth}&action=get_live_categories")
    streams_data, _, _ = http_bytes(f"{base}/player_api.php?{auth}&action=get_live_streams", timeout=45)
    categories = {str(x.get("category_id")): x.get("category_name", "Andere") for x in json.loads(categories_data)}
    result = []
    for stream in json.loads(streams_data):
        stream_id = stream.get("stream_id")
        if stream_id is None:
            continue
        extension = stream.get("container_extension") or "ts"
        result.append({"name": stream.get("name") or f"Sender {stream_id}",
                       "group": categories.get(str(stream.get("category_id")), "Andere"),
                       "logo": stream.get("stream_icon") or "", "type": "live",
                       "url": f"{base}/live/{username}/{password}/{stream_id}.{extension}"})
    return result


class Bus(QObject):
    caption = Signal(str)
    status = Signal(str)
    loaded = Signal(object)


class SonioxWorker(threading.Thread):
    def __init__(self, url, key, bus, source_language="auto", target_language="none"):
        super().__init__(daemon=True)
        self.url, self.key, self.bus = url, key, bus
        self.source_language, self.target_language = source_language, target_language
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            from websockets.sync.client import connect
            self.bus.status.emit("Soniox verbindet …")
            config = {
                "model": "stt-rt-v5", "audio_format": "pcm_s16le",
                "sample_rate": 16000, "num_channels": 1,
                "enable_language_identification": True,
                "enable_endpoint_detection": True, "max_endpoint_delay_ms": 700,
                "endpoint_latency_adjustment_level": 0,
                "context": {"general": [{"key": "domain", "value": "live television and IPTV broadcast"}]}
            }
            if self.source_language != "auto":
                config["language_hints"] = [self.source_language]
            if self.target_language != "none":
                config["translation"] = {"type": "one_way", "target_language": self.target_language}
            with connect("wss://stt-rt.soniox.com/transcribe-websocket",
                         additional_headers={"Authorization": f"Bearer {self.key}"},
                         open_timeout=15, close_timeout=5) as ws:
                ws.send(json.dumps(config))
                sender = threading.Thread(target=self._send_audio, args=(ws,), daemon=True)
                sender.start()
                self.bus.status.emit("Soniox Live-UT aktiv")
                final_words, live_words = [], []
                while not self.stop_event.is_set():
                    try:
                        message = json.loads(ws.recv(timeout=3))
                    except TimeoutError:
                        continue
                    if message.get("error_code"):
                        raise RuntimeError(message.get("error_message") or message.get("error_code"))
                    tokens = message.get("tokens", [])
                    if not tokens:
                        continue
                    for token in tokens:
                        status = token.get("translation_status", "none")
                        wanted = status == "translation" if self.target_language != "none" else status in ("none", "original")
                        if not wanted or token.get("text") == "<end>":
                            continue
                        (final_words if token.get("is_final") else live_words).append(token.get("text", ""))
                    text = "".join((final_words + live_words)[-45:]).strip()
                    live_words.clear()
                    if text:
                        self.bus.caption.emit(text)
                    if len(final_words) > 55:
                        final_words = final_words[-25:]
        except Exception as exc:
            if not self.stop_event.is_set(): self.bus.status.emit("Soniox-Fehler: " + str(exc))
        finally:
            self.stop()

    def _send_audio(self, ws):
        try:
            import soundcard as sc
            import numpy as np
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with loopback.recorder(samplerate=16000, channels=1, blocksize=1600) as recorder:
                while not self.stop_event.is_set():
                    audio = recorder.record(numframes=1600)
                    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    ws.send(pcm)
            if not self.stop_event.is_set(): ws.send("")
        except Exception as exc:
            if not self.stop_event.is_set():
                self.bus.status.emit("Systemton kann nicht gelesen werden: " + str(exc))
            self.stop_event.set()


class VLCWidget(QWidget):
    error = Signal(str)

    def __init__(self):
        super().__init__()
        import vlc
        options = ["--network-caching=1800", "--live-caching=1800", "--http-reconnect",
                   "--avcodec-hw=any", "--no-video-title-show", "--quiet"]
        self.vlc = vlc
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()
        self.url = None

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform.startswith("win"):
            self.player.set_hwnd(int(self.winId()))
        elif sys.platform.startswith("linux"):
            self.player.set_xwindow(int(self.winId()))

    def play(self, url):
        self.url = url
        media = self.instance.media_new(url, ":http-user-agent=" + USER_AGENT,
                                        ":network-caching=1800", ":live-caching=1800")
        self.player.set_media(media)
        self.player.play()

    def retry(self):
        if self.url:
            self.player.stop(); time.sleep(0.15); self.play(self.url)

    def state_name(self):
        try: return str(self.player.get_state())
        except Exception: return ""


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"LiveUT TV {APP_VERSION} – Soniox")
        self.resize(1500, 880)
        self.items, self.current, self.soniox_worker = [], None, None
        self.caption_color, self.caption_size = "#ffffff", 38
        self.bus = Bus(); self.bus.caption.connect(self.show_caption)
        self.bus.status.connect(self.set_status); self.bus.loaded.connect(self.finish_load)

        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root)
        top = QHBoxLayout(); self.url = QLineEdit(); self.url.setPlaceholderText("M3U/M3U8, Xtream-get.php oder direkte Stream-Adresse")
        top.addWidget(self.url, 1)
        open_address = QPushButton("Adresse öffnen"); open_address.clicked.connect(self.open_url); top.addWidget(open_address)
        open_file = QPushButton("IPTV-Datei"); open_file.clicked.connect(self.open_file); top.addWidget(open_file)
        outer.addLayout(top)

        split = QSplitter(); left = QWidget(); left_layout = QVBoxLayout(left)
        self.search = QLineEdit(); self.search.setPlaceholderText("Sender oder Kategorie suchen")
        self.search.textChanged.connect(self.filter_items); left_layout.addWidget(self.search)
        self.list = QListWidget(); self.list.itemDoubleClicked.connect(self.play_item); left_layout.addWidget(self.list)
        split.addWidget(left)

        center = QWidget(); center_layout = QVBoxLayout(center)
        self.video = VLCWidget(); center_layout.addWidget(self.video, 1)
        self.overlay = QLabel("", self.video); self.overlay.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.overlay.setWordWrap(True); self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.video.resizeEvent = self.resize_overlay; self.apply_caption_style()
        controls = QHBoxLayout()
        self.soniox_button = QPushButton("Soniox Live-UT EIN"); self.soniox_button.clicked.connect(self.toggle_soniox); controls.addWidget(self.soniox_button)
        self.size = QSpinBox(); self.size.setRange(20, 72); self.size.setValue(38); self.size.valueChanged.connect(self.change_size); controls.addWidget(QLabel("Textgröße")); controls.addWidget(self.size)
        color = QPushButton("Textfarbe"); color.clicked.connect(self.choose_color); controls.addWidget(color)
        full = QPushButton("Vollbild"); full.clicked.connect(self.toggle_fullscreen); controls.addWidget(full)
        center_layout.addLayout(controls)
        self.status = QLabel("Bereit"); self.status.setWordWrap(True); center_layout.addWidget(self.status)
        split.addWidget(center)

        soniox = QGroupBox("Soniox Live-Untertitel"); settings = QVBoxLayout(soniox)
        settings.addWidget(QLabel("API-Key (nur im Windows-Anmeldespeicher):"))
        self.soniox_key = QLineEdit(); self.soniox_key.setEchoMode(QLineEdit.Password); self.soniox_key.setPlaceholderText("Soniox API-Key")
        settings.addWidget(self.soniox_key)
        self.show_key = QCheckBox("Key anzeigen"); self.show_key.toggled.connect(lambda on: self.soniox_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)); settings.addWidget(self.show_key)
        self.remember = QCheckBox("Key sicher speichern"); self.remember.setChecked(True); settings.addWidget(self.remember)
        settings.addWidget(QLabel("Gesprochene Sprache:")); self.source_language = QComboBox()
        for name, code in LANGUAGES: self.source_language.addItem(name, code)
        settings.addWidget(self.source_language)
        settings.addWidget(QLabel("Ausgabe/Übersetzung:")); self.target_language = QComboBox()
        self.target_language.addItem("Originalsprache", "none")
        for name, code in LANGUAGES[1:]: self.target_language.addItem(name, code)
        self.target_language.setCurrentIndex(1); settings.addWidget(self.target_language)
        note = QLabel("Soniox unterstützt 60+ Sprachen. API-Nutzung wird nach Zeit berechnet.")
        note.setWordWrap(True); settings.addWidget(note); settings.addStretch(1)
        split.addWidget(soniox); split.setSizes([330, 900, 280]); outer.addWidget(split)

        self.load_key()
        self.retry_count = 0
        self.watchdog = QTimer(self); self.watchdog.timeout.connect(self.check_stream); self.watchdog.start(4000)

    def resize_overlay(self, event):
        self.overlay.setGeometry(30, max(0, self.video.height() - 160), max(100, self.video.width() - 60), 125)

    def apply_caption_style(self):
        self.overlay.setStyleSheet(f"color:{self.caption_color};background:rgba(0,0,0,210);font-size:{self.caption_size}px;font-weight:600;padding:8px;border-radius:5px")

    def change_size(self, value):
        self.caption_size = value; self.apply_caption_style()

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.caption_color), self)
        if color.isValid(): self.caption_color = color.name(); self.apply_caption_style()

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "IPTV-Datei öffnen", "", "IPTV-Listen (*.m3u *.m3u8 *.xspf *.json *.txt);;Alle Dateien (*)")
        if not path: return
        try: self.finish_load(decode_playlist(Path(path).read_bytes(), path))
        except Exception as exc: QMessageBox.warning(self, "IPTV-Datei", str(exc))

    def open_url(self):
        url = self.url.text().strip()
        if not url: return
        self.status.setText("Adresse wird geprüft …")
        def work():
            try:
                if xtream_from_get_url(url):
                    try:
                        items = load_xtream(url)
                        if items: self.bus.loaded.emit(items); return
                    except Exception:
                        pass
                path = urlparse(url).path.lower()
                looks_playlist = any(path.endswith(ext) for ext in PLAYLIST_EXTENSIONS) or "get.php" in path
                if looks_playlist:
                    data, final_url, _ = http_bytes(url, timeout=45)
                    items = decode_playlist(data, final_url)
                    if items: self.bus.loaded.emit(items); return
                self.bus.loaded.emit([{"name": "Direkter Stream", "group": "Direkte Streams", "logo": "", "type": "live", "url": url}])
            except Exception as exc: self.bus.status.emit("IPTV-Fehler: " + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def finish_load(self, items):
        self.items = items or []; self.filter_items(); self.status.setText(f"{len(self.items)} Programme geladen")
        if len(self.items) == 1 and self.items[0]["group"] == "Direkte Streams": self.play_url(self.items[0])

    def filter_items(self):
        query = self.search.text().lower(); self.list.clear()
        for index, item in enumerate(self.items):
            if query in (item.get("name", "") + " " + item.get("group", "")).lower():
                self.list.addItem(f"{item.get('group','Andere')}  |  {item.get('name','Unbenannt')}")
                self.list.item(self.list.count() - 1).setData(Qt.UserRole, index)

    def play_item(self, list_item):
        self.play_url(self.items[list_item.data(Qt.UserRole)])

    def play_url(self, item):
        self.stop_soniox()
        self.current = item; self.overlay.clear(); self.retry_count = 0
        self.video.play(item["url"]); self.status.setText("Wiedergabe: " + item.get("name", "Stream"))

    def check_stream(self):
        if not self.current: return
        state = self.video.state_name()
        if ("Error" in state or "Ended" in state) and self.retry_count < 3:
            self.retry_count += 1; self.status.setText(f"Stream wird neu verbunden ({self.retry_count}/3) …"); self.video.retry()

    def load_key(self):
        try:
            import keyring
            key = keyring.get_password("LiveUT TV", "Soniox API Key")
            if key: self.soniox_key.setText(key)
        except Exception: pass

    def save_key(self):
        if self.remember.isChecked():
            import keyring
            keyring.set_password("LiveUT TV", "Soniox API Key", self.soniox_key.text().strip())

    def toggle_soniox(self):
        if self.soniox_worker:
            self.stop_soniox(); return
        if not self.current: return QMessageBox.information(self, "Soniox", "Bitte zuerst einen Sender starten.")
        key = self.soniox_key.text().strip()
        if not key: return QMessageBox.information(self, "Soniox", "Bitte deinen Soniox API-Key eingeben.")
        try: self.save_key()
        except Exception as exc: self.status.setText("Key konnte nicht gespeichert werden: " + str(exc))
        self.soniox_worker = SonioxWorker(self.current["url"], key, self.bus,
                                          self.source_language.currentData(), self.target_language.currentData())
        self.soniox_worker.start(); self.soniox_button.setText("Soniox Live-UT AUS")

    def stop_soniox(self):
        if self.soniox_worker: self.soniox_worker.stop(); self.soniox_worker = None
        self.soniox_button.setText("Soniox Live-UT EIN")

    def show_caption(self, text):
        words = text.split()
        if len(words) > 22: words = words[-22:]
        midpoint = min(len(words), max(1, len(words) // 2))
        self.overlay.setText(" ".join(words[:midpoint]) + ("\n" + " ".join(words[midpoint:]) if midpoint < len(words) else ""))

    def set_status(self, text): self.status.setText(text)
    def toggle_fullscreen(self): self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, event):
        self.stop_soniox()
        try: self.video.player.stop()
        except Exception: pass
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("QWidget{background:#0b1628;color:#f4f7fb;font-size:15px} QPushButton,QLineEdit,QComboBox,QSpinBox{padding:9px;background:#162945;border:1px solid #315274;border-radius:6px} QPushButton:hover{background:#18a999} QListWidget{background:#0d1d33}")
    window = Window(); window.show(); sys.exit(app.exec())
