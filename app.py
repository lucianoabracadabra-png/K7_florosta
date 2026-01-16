import os
import time
import random
import threading
from flask import Flask, render_template, url_for # Importante: render_template
from flask_socketio import SocketIO, emit
from yt_dlp import YoutubeDL

# ==========================================
# 1. CONFIGURAÇÃO
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aurora-secret-key')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='gevent')

# ==========================================
# 2. ESTADO GLOBAL
# ==========================================
room_state = {
    'playlist': [], 'current_video_index': 0, 'is_playing': False,        
    'anchor_time': 0, 'server_start_time': 0, 'auto_dj_enabled': True     
}

# ==========================================
# 3. ROTAS (Agora muito mais simples)
# ==========================================
@app.route('/')
def index():
    # O Flask procura automaticamente por 'index.html' dentro da pasta 'templates/'
    return render_template('index.html')

# ==========================================
# 4. LÓGICA DE BACKEND (HELPER FUNCTIONS)
# ==========================================
def extract_info_smart(url):
    try:
        ydl_opts = {'quiet': True, 'extract_flat': True, 'noplaylist': False, 'playlistend': 20}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            detected_items = []
            if 'entries' in info:
                for entry in info['entries']:
                    if entry.get('id') and entry.get('title'):
                        detected_items.append({'id': entry['id'], 'title': entry['title'], 'thumbnail': f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"})
            else:
                detected_items.append({'id': info['id'], 'title': info['title'], 'thumbnail': f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"})
            return detected_items
    except Exception as e:
        print(f"Erro: {e}")
        return None

def find_recommendation(last_video_title):
    try:
        ydl_opts = {'quiet': True, 'default_search': 'ytsearch', 'noplaylist': True, 'extract_flat': True}
        query = f"{last_video_title} related music"
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
            if 'entries' in info and len(info['entries']) > 0:
                rec = random.choice(info['entries'])
                return {'id': rec['id'], 'title': f"📻 Auto: {rec['title']}", 'thumbnail': f"https://i.ytimg.com/vi/{rec['id']}/hqdefault.jpg"}
        return None
    except: return None

def get_broadcast_packet():
    s = room_state.copy()
    s['server_now'] = time.time()
    return s

def heartbeat_loop():
    while True:
        socketio.sleep(10)
        socketio.emit('heartbeat', get_broadcast_packet())

socketio.start_background_task(heartbeat_loop)

# ==========================================
# 5. EVENTOS SOCKET.IO
# ==========================================
@socketio.on('add_video')
def handle_add(url):
    emit('notification', "SCANNING TAPE...", broadcast=True)
    items = extract_info_smart(url)
    if items:
        count = len(items)
        room_state['playlist'].extend(items)
        if len(room_state['playlist']) == count:
            room_state['current_video_index'] = 0
            room_state['is_playing'] = True
            room_state['anchor_time'] = 0
            room_state['server_start_time'] = time.time()
        emit('update_state', get_broadcast_packet(), broadcast=True)
        emit('notification', f"📚 {count} FITAS" if count > 1 else f"FITA: {items[0]['title'][:20]}...", broadcast=True)
    else: emit('notification', "❌ ERRO", broadcast=True)

@socketio.on('control_action')
def handle_control(d):
    room_state['is_playing'] = (d['action'] == 'play')
    room_state['anchor_time'] = d['time']
    room_state['server_start_time'] = time.time()
    emit('update_state', get_broadcast_packet(), broadcast=True)

@socketio.on('seek_event')
def handle_seek(d):
    room_state['anchor_time'] = d['time']
    room_state['server_start_time'] = time.time()
    emit('update_state', get_broadcast_packet(), broadcast=True)

@socketio.on('next_video')
def handle_next():
    curr = room_state['current_video_index']
    has_next = False
    if curr + 1 < len(room_state['playlist']):
        room_state['current_video_index'] += 1
        has_next = True
    elif room_state['auto_dj_enabled'] and len(room_state['playlist']) > 0:
        last = room_state['playlist'][-1]
        rec = find_recommendation(last['title'])
        if rec:
            room_state['playlist'].append(rec)
            room_state['current_video_index'] += 1
            has_next = True
            emit('notification', "AUTO-DJ LOADING...", broadcast=True)
    if has_next:
        room_state['is_playing'] = True
        room_state['anchor_time'] = 0
        room_state['server_start_time'] = time.time()
        emit('update_state', get_broadcast_packet(), broadcast=True)

@socketio.on('master_sync_force')
def handle_master_force(data):
    room_state['anchor_time'] = data['time']
    room_state['is_playing'] = data['is_playing']
    room_state['server_start_time'] = time.time()
    emit('update_state', get_broadcast_packet(), broadcast=True)
    emit('notification', "⚠️ MASTER OVERRIDE!", broadcast=True)

@socketio.on('shuffle')
def handle_shuffle():
    idx = room_state['current_video_index']
    if len(room_state['playlist']) > idx + 1:
        future = room_state['playlist'][idx+1:]
        random.shuffle(future)
        room_state['playlist'] = room_state['playlist'][:idx+1] + future
        emit('update_state', get_broadcast_packet(), broadcast=True)

@socketio.on('remove')
def handle_remove(i):
    if i > room_state['current_video_index']:
        room_state['playlist'].pop(i)
        emit('update_state', get_broadcast_packet(), broadcast=True)

@socketio.on('toggle_autodj')
def handle_tdj(v): room_state['auto_dj_enabled'] = v; emit('update_state', get_broadcast_packet(), broadcast=True)
@socketio.on('request_sync')
def handle_req_sync(): emit('update_state', get_broadcast_packet())
@socketio.on('video_ended')
def handle_ended(): handle_next()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)