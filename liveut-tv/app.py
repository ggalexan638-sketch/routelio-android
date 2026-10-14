import sys, os, re, json, subprocess, tempfile, threading, uuid, urllib.request, urllib.error
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, Signal, QObject
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLineEdit,QFileDialog,QListWidget,QLabel,QComboBox,QSpinBox,QColorDialog,QSplitter,QMessageBox,QCheckBox,QGroupBox)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).parent)); FFMPEG=str(BASE/'ffmpeg.exe') if (BASE/'ffmpeg.exe').exists() else 'ffmpeg'

def parse_m3u(text):
    out=[]; meta=None
    for line in text.splitlines():
        line=line.strip()
        if line.startswith('#EXTINF:'):
            attrs=dict(re.findall(r'([\w-]+)="([^"]*)"',line)); name=line.rsplit(',',1)[-1].strip() or 'Unbenannt'
            meta={'name':name,'group':attrs.get('group-title','Andere'),'logo':attrs.get('tvg-logo','')}
        elif line and not line.startswith('#') and meta: out.append({**meta,'url':line}); meta=None
    return out

class Bus(QObject): caption=Signal(str); sound=Signal(str); status=Signal(str)

class CaptionWorker(threading.Thread):
    def __init__(self,url,model,bus,sounds): super().__init__(daemon=True); self.url=url; self.model=model; self.bus=bus; self.sounds=sounds; self.stop=False
    def run(self):
        try:
            from faster_whisper import WhisperModel
            self.bus.status.emit('Whisper-Modell wird geladen …')
            wm=WhisperModel(self.model,device='cpu',compute_type='int8')
            while not self.stop:
                tmp=tempfile.NamedTemporaryFile(suffix='.wav',delete=False); tmp.close()
                p=subprocess.run([FFMPEG,'-y','-loglevel','quiet','-i',self.url,'-t','2.4','-vn','-ac','1','-ar','16000',tmp.name],timeout=12)
                if p.returncode: raise RuntimeError('FFmpeg kann den Stream-Ton nicht lesen.')
                seg,_=wm.transcribe(tmp.name,language='de',beam_size=1,vad_filter=True)
                text=' '.join(s.text.strip() for s in seg).strip()
                if text:self.bus.caption.emit(text)
                if self.sounds:self.detect_sound(tmp.name)
                os.unlink(tmp.name)
        except Exception as e:self.bus.status.emit('UT-Fehler: '+str(e))
    def detect_sound(self,filename):
        try:
            import tensorflow as tf, tensorflow_hub as hub, numpy as np
            if not hasattr(self,'yam'): self.yam=hub.load('https://tfhub.dev/google/yamnet/1'); self.names=None
            raw=tf.io.read_file(filename); audio,_=tf.audio.decode_wav(raw,desired_channels=1); scores,_,_=self.yam(tf.squeeze(audio,axis=-1)); idx=int(tf.argmax(tf.reduce_mean(scores,axis=0)))
            labels={0:'Sprache',132:'Musik',137:'Tür schlägt',138:'Tür',317:'Applaus',16:'Lachen',69:'Hund bellt',39:'Schritte'}
            if idx in labels and idx!=0:self.bus.sound.emit('('+labels[idx]+')')
        except Exception: pass

def api_request(url, key, data=None, content_type=None, timeout=30):
    headers={'Authorization':'Bearer '+key}
    if content_type: headers['Content-Type']=content_type
    req=urllib.request.Request(url,data=data,headers=headers,method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as res:return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try: message=json.loads(e.read().decode('utf-8')).get('error',{}).get('message',str(e))
        except Exception: message=str(e)
        raise RuntimeError(f'OpenAI {e.code}: {message}')

def multipart_audio(path, model='gpt-4o-mini-transcribe'):
    boundary='----LiveUT'+uuid.uuid4().hex
    audio=Path(path).read_bytes()
    parts=[]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="liveut.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+audio+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts),'multipart/form-data; boundary='+boundary

def response_text(data):
    if data.get('output_text'): return data['output_text'].strip()
    for item in data.get('output',[]):
        for part in item.get('content',[]):
            if part.get('type')=='output_text': return part.get('text','').strip()
    return ''

class OpenAIWorker(threading.Thread):
    def __init__(self,url,key,bus,translate):
        super().__init__(daemon=True);self.url=url;self.key=key;self.bus=bus;self.translate=translate;self.stop=False
    def run(self):
        self.bus.status.emit('OpenAI Live-UT wird gestartet …')
        while not self.stop:
            path=None
            try:
                tmp=tempfile.NamedTemporaryFile(suffix='.wav',delete=False);path=tmp.name;tmp.close()
                p=subprocess.run([FFMPEG,'-y','-loglevel','error','-i',self.url,'-t','3.5','-vn','-ac','1','-ar','16000',path],capture_output=True,timeout=18)
                if p.returncode: raise RuntimeError('FFmpeg kann den Stream-Ton nicht lesen.')
                body,ctype=multipart_audio(path)
                result=api_request('https://api.openai.com/v1/audio/transcriptions',self.key,body,ctype,45)
                text=result.get('text','').strip()
                if text and self.translate:
                    payload=json.dumps({'model':'gpt-4.1-mini','input':'Übersetze nur diesen gesprochenen Untertitel kurz und natürlich ins Deutsche. Keine Erklärung:\n'+text}).encode('utf-8')
                    text=response_text(api_request('https://api.openai.com/v1/responses',self.key,payload,'application/json',30)) or text
                if text:self.bus.caption.emit(text)
                self.bus.status.emit('OpenAI Live-UT aktiv')
            except Exception as e:
                self.bus.status.emit('OpenAI-UT Fehler: '+str(e));break
            finally:
                if path:
                    try:os.unlink(path)
                    except OSError:pass

class Window(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('LiveUT TV 1.1 – OpenAI'); self.resize(1500,850); self.items=[]; self.worker=None; self.openai_worker=None; self.status=QLabel('Bereit'); self.bus=Bus()
        root=QWidget(); self.setCentralWidget(root); v=QVBoxLayout(root)
        top=QHBoxLayout(); self.url=QLineEdit(); self.url.setPlaceholderText('M3U/M3U8-Adresse eingeben'); top.addWidget(self.url); b=QPushButton('Adresse öffnen');b.clicked.connect(self.open_url);top.addWidget(b); f=QPushButton('M3U-Datei');f.clicked.connect(self.open_file);top.addWidget(f);v.addLayout(top)
        split=QSplitter(); left=QWidget();lv=QVBoxLayout(left);self.search=QLineEdit();self.search.setPlaceholderText('Sender, Land oder Kategorie suchen');self.search.textChanged.connect(self.filter);lv.addWidget(self.search);self.list=QListWidget();self.list.itemDoubleClicked.connect(self.play);lv.addWidget(self.list);split.addWidget(left)
        right=QWidget();rv=QVBoxLayout(right);self.video=QVideoWidget();rv.addWidget(self.video,1);self.overlay=QLabel('',self.video);self.overlay.setAlignment(Qt.AlignCenter);self.overlay.setStyleSheet('color:white;background:rgba(0,0,0,190);font-size:34px;padding:8px');self.overlay.setWordWrap(True);self.overlay.setGeometry(80,600,900,100);self.video.resizeEvent=lambda e:self.overlay.setGeometry(40,max(0,self.video.height()-150),max(100,self.video.width()-80),110)
        controls=QHBoxLayout();self.ut=QPushButton('Lokale UT starten');self.ut.clicked.connect(self.toggle_ut);controls.addWidget(self.ut);self.model=QComboBox();self.model.addItems(['tiny','base','small']);self.model.setCurrentText('base');controls.addWidget(self.model);self.soundbox=QPushButton('YAMNet: EIN');self.soundbox.setCheckable(True);self.soundbox.setChecked(True);controls.addWidget(self.soundbox);size=QSpinBox();size.setRange(22,64);size.setValue(34);size.valueChanged.connect(lambda n:self.overlay.setStyleSheet(f'color:white;background:rgba(0,0,0,190);font-size:{n}px;padding:8px'));controls.addWidget(size);color=QPushButton('UT-Farbe');color.clicked.connect(self.color);controls.addWidget(color);full=QPushButton('Vollbild');full.clicked.connect(self.full);controls.addWidget(full);rv.addLayout(controls);rv.addWidget(self.status);split.addWidget(right)
        api=QGroupBox('OpenAI Live-UT');av=QVBoxLayout(api);hint=QLabel('API-Key nur auf diesem PC eingeben:');hint.setWordWrap(True);av.addWidget(hint);self.api_key=QLineEdit();self.api_key.setEchoMode(QLineEdit.EchoMode.Password);self.api_key.setPlaceholderText('sk-…');av.addWidget(self.api_key);self.show_key=QCheckBox('Key anzeigen');self.show_key.toggled.connect(lambda on:self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password));av.addWidget(self.show_key);self.remember=QCheckBox('Key sicher speichern');self.remember.setChecked(True);av.addWidget(self.remember);test=QPushButton('Key testen');test.clicked.connect(self.test_key);av.addWidget(test);self.translate=QCheckBox('Automatisch auf Deutsch übersetzen');self.translate.setChecked(True);av.addWidget(self.translate);self.openai_btn=QPushButton('OpenAI-UT EIN');self.openai_btn.clicked.connect(self.toggle_openai);av.addWidget(self.openai_btn);self.api_status=QLabel('Noch nicht verbunden');self.api_status.setWordWrap(True);av.addWidget(self.api_status);av.addStretch(1);split.addWidget(api);split.setSizes([320,880,280]);v.addWidget(split)
        self.load_saved_key()
        self.bus.caption.connect(self.caption); self.bus.sound.connect(self.sound); self.bus.status.connect(self.status_message)
        self.player=QMediaPlayer();self.audio=QAudioOutput();self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video);self.current=None
    def load(self,text):self.items=parse_m3u(text);self.filter();self.status.setText(f'{len(self.items)} Programme geladen')
    def open_file(self):
        p,_=QFileDialog.getOpenFileName(self,'M3U öffnen','','M3U (*.m3u *.m3u8);;Alle Dateien (*)')
        if p:self.load(Path(p).read_text('utf-8',errors='ignore'))
    def open_url(self):
        try:
            import urllib.request
            req=urllib.request.Request(self.url.text().strip(),headers={'User-Agent':'VLC/3.0'});self.load(urllib.request.urlopen(req,timeout=30).read().decode('utf-8','ignore'))
        except Exception as e:QMessageBox.warning(self,'Verbindung',str(e))
    def filter(self):
        q=self.search.text().lower();self.list.clear()
        for i,x in enumerate(self.items):
            if q in (x['name']+' '+x['group']).lower():self.list.addItem(f"{x['group']}  |  {x['name']}");self.list.item(self.list.count()-1).setData(Qt.UserRole,i)
    def play(self,item):self.current=self.items[item.data(Qt.UserRole)];self.player.setSource(QUrl(self.current['url']));self.player.play();self.status.setText(self.current['name'])
    def toggle_ut(self):
        if self.worker:self.worker.stop=True;self.worker=None;self.ut.setText('Lokale UT starten');return
        if not self.current:return QMessageBox.information(self,'Live-UT','Bitte zuerst einen Sender starten.')
        self.worker=CaptionWorker(self.current['url'],self.model.currentText(),self.bus,self.soundbox.isChecked());self.worker.start();self.ut.setText('Live-UT stoppen')
    def load_saved_key(self):
        try:
            import keyring
            saved=keyring.get_password('LiveUT TV','OpenAI API Key')
            if saved:self.api_key.setText(saved);self.api_status.setText('Gespeicherter Key gefunden')
        except Exception:pass
    def save_key(self):
        if not self.remember.isChecked():return
        try:
            import keyring
            keyring.set_password('LiveUT TV','OpenAI API Key',self.api_key.text().strip())
        except Exception as e:self.api_status.setText('Key konnte nicht gespeichert werden: '+str(e))
    def test_key(self):
        key=self.api_key.text().strip()
        if not key:return QMessageBox.information(self,'OpenAI','Bitte zuerst deinen API-Key eingeben.')
        self.api_status.setText('Key wird geprüft …')
        remember=self.remember.isChecked()
        def work():
            try:
                api_request('https://api.openai.com/v1/models',key,timeout=15)
                if remember:
                    import keyring
                    keyring.set_password('LiveUT TV','OpenAI API Key',key)
                self.bus.status.emit('OPENAI_KEY_OK')
            except Exception as e:self.bus.status.emit('OPENAI_KEY_ERROR:'+str(e))
        threading.Thread(target=work,daemon=True).start()
    def toggle_openai(self):
        if self.openai_worker:self.openai_worker.stop=True;self.openai_worker=None;self.openai_btn.setText('OpenAI-UT EIN');self.api_status.setText('OpenAI Live-UT ausgeschaltet');return
        if not self.current:return QMessageBox.information(self,'OpenAI Live-UT','Bitte zuerst einen Sender starten.')
        key=self.api_key.text().strip()
        if not key:return QMessageBox.information(self,'OpenAI Live-UT','Bitte API-Key eingeben und testen.')
        if self.worker:self.toggle_ut()
        self.save_key();self.openai_worker=OpenAIWorker(self.current['url'],key,self.bus,self.translate.isChecked());self.openai_worker.start();self.openai_btn.setText('OpenAI-UT AUS');self.api_status.setText('OpenAI Live-UT startet …')
    def caption(self,t):self.overlay.setText(t)
    def sound(self,t):self.overlay.setText((self.overlay.text()+'\n'+t).strip())
    def color(self):
        c=QColorDialog.getColor();
        if c.isValid():self.overlay.setStyleSheet(f'color:{c.name()};background:rgba(0,0,0,190);font-size:34px;padding:8px')
    def full(self):self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self,event):
        if self.worker:self.worker.stop=True
        if self.openai_worker:self.openai_worker.stop=True
        event.accept()

    def status_message(self,text):
        if text=='OPENAI_KEY_OK':self.api_status.setText('✓ API-Key funktioniert')
        elif text.startswith('OPENAI_KEY_ERROR:'):self.api_status.setText('✗ '+text.split(':',1)[1])
        else:
            self.status.setText(text)
            if text.startswith('OpenAI'):self.api_status.setText(text)

app=QApplication(sys.argv);app.setStyleSheet('QWidget{background:#0b1628;color:#f4f7fb;font-size:15px} QPushButton,QLineEdit,QComboBox,QSpinBox{padding:9px;background:#162945;border:1px solid #315274;border-radius:6px} QPushButton:hover{background:#18a999} QListWidget{background:#0d1d33}');w=Window();w.show();sys.exit(app.exec())
