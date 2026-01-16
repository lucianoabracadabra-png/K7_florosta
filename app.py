import os
import time
import random
import requests
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit
from yt_dlp import YoutubeDL

# ==========================================
# 1. CONFIGURAÇÃO DO SERVIDOR
# ==========================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aurora-secret-key')

# async_mode='gevent' é essencial para performance em produção
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='gevent')

# ==========================================
# 2. ESTADO GLOBAL (BANCO DE DADOS EM MEMÓRIA)
# ==========================================

room_state = {
    'playlist': [],             
    'current_video_index': 0,   
    'is_playing': False,        
    
    # Sistema de Ancoragem de Tempo
    'anchor_time': 0,           # Posição do vídeo no momento da ação
    'server_start_time': 0,     # Timestamp do servidor no momento da ação
    
    'auto_dj_enabled': True     
}

# ==========================================
# 3. FRONTEND (HTML/CSS/JS - AURORA DESIGN)
# ==========================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aurora K7 Sync</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&family=Monoton&display=swap" rel="stylesheet">
    <style>
        :root {
            --glass-bg: rgba(255, 255, 255, 0.05);
            --glass-border: rgba(255, 255, 255, 0.1);
            --glass-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            --primary-glow: #00d4ff;
            --tape-plastic: #1a1a1a;
            --tape-label: #eee;
        }

        body {
            font-family: 'Inter', sans-serif;
            margin: 0;
            padding: 20px;
            color: white;
            min-height: 100vh;
            background: linear-gradient(-45deg, #0f0c29, #302b63, #24243e, #4a1c40);
            background-size: 400% 400%;
            animation: aurora 15s ease infinite;
            overflow-x: hidden;
        }

        @keyframes aurora {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .container {
            max-width: 900px;
            margin: 0 auto;
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 20px;
            padding: 30px;
            box-shadow: var(--glass-shadow);
        }

        h1 {
            font-family: 'Monoton', cursive;
            text-align: center;
            font-size: 2.5rem;
            margin-top: 0;
            background: linear-gradient(to right, #00d4ff, #ff00cc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
        }

        /* --- PLAYER RECORDER WINDOW --- */
        #player-deck {
            background: #000;
            border-radius: 15px;
            padding: 10px;
            box-shadow: inset 0 0 20px rgba(0,0,0,0.8);
            border: 2px solid #333;
            margin-bottom: 25px;
            position: relative;
        }
        
        #status-light {
            width: 10px; height: 10px; border-radius: 50%;
            background: #333; position: absolute; top: 15px; right: 15px;
            z-index: 10; box-shadow: 0 0 5px #000; transition: 0.3s;
        }
        #status-light.playing { background: #0f0; box-shadow: 0 0 10px #0f0; }
        #status-light.paused { background: #ff0; box-shadow: 0 0 10px #ff0; }

        #player-wrapper {
            position: relative; padding-bottom: 56.25%; height: 0;
            border-radius: 8px; overflow: hidden;
            opacity: 0.8; transition: opacity 0.5s;
        }
        #player-wrapper:hover { opacity: 1; }
        #player { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }

        /* --- INPUTS & CONTROLS --- */
        .input-group { display: flex; gap: 10px; margin-bottom: 25px; }
        input[type="text"] {
            flex: 1; padding: 15px; border-radius: 50px; border: 1px solid rgba(255,255,255,0.2);
            background: rgba(0,0,0,0.3); color: white; outline: none;
            font-family: 'Inter', sans-serif; transition: 0.3s;
        }
        input[type="text"]:focus { border-color: var(--primary-glow); box-shadow: 0 0 15px rgba(0, 212, 255, 0.3); }

        .btn {
            padding: 12px 25px; border-radius: 50px; border: 1px solid rgba(255,255,255,0.1);
            cursor: pointer; font-weight: bold; background: rgba(255,255,255,0.1);
            color: white; backdrop-filter: blur(5px); transition: 0.3s; text-transform: uppercase; letter-spacing: 1px;
            font-size: 0.8rem;
        }
        .btn:hover { background: rgba(255,255,255,0.2); transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.3); }
        .btn-primary { background: linear-gradient(45deg, #00d4ff, #0051ff); border: none; }
        .btn-danger { background: linear-gradient(45deg, #ff0055, #ff00cc); border: none; }
        
        .controls { display: flex; justify-content: center; gap: 15px; flex-wrap: wrap; margin-bottom: 30px; }

        /* --- FITA K7 (LIST ITEM) --- */
        #playlist { 
            list-style: none; padding: 0; display: flex; flex-direction: column; gap: 15px; 
            max-height: 500px; overflow-y: auto; padding-right: 5px;
        }
        
        #playlist::-webkit-scrollbar { width: 6px; }
        #playlist::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); }
        #playlist::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 10px; }

        .k7-tape {
            background: var(--tape-plastic);
            border-radius: 10px;
            padding: 10px;
            position: relative;
            box-shadow: 0 4px 10px rgba(0,0,0,0.5);
            transition: transform 0.3s, border-color 0.3s;
            border: 2px solid #333;
            display: flex;
            align-items: center;
            overflow: hidden;
        }

        .k7-tape::before {
            content: ''; position: absolute; top:0; left:0; right:0; bottom:0;
            background: repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.02) 2px, rgba(255,255,255,0.02) 4px);
            pointer-events: none;
        }

        .k7-tape:hover { transform: scale(1.02); border-color: #555; }
        
        .k7-tape.active {
            border-color: var(--primary-glow);
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.4);
            background: #222;
        }

        .k7-label-area {
            width: 120px; height: 70px;
            background: #ccc;
            border-radius: 4px;
            overflow: hidden;
            position: relative;
            margin-right: 15px;
            flex-shrink: 0;
            border: 4px solid #fff;
        }
        .k7-label-img { width: 100%; height: 100%; object-fit: cover; filter: sepia(30%); }

        .k7-info { flex: 1; z-index: 2; }
        .k7-title { font-weight: bold; font-size: 0.95rem; margin-bottom: 4px; display: block; }
        .k7-status { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 1px; }

        .k7-reels { display: flex; gap: 15px; margin-right: 15px; }
        .reel-svg { width: 30px; height: 30px; fill: none; stroke: #555; stroke-width: 3; }
        .active .reel-svg { stroke: var(--primary-glow); }
        
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .active.playing .reel-svg { animation: spin 2s linear infinite; }

        .btn-remove {
            background: transparent; border: none; color: #555; cursor: pointer; font-size: 1.2rem;
            transition: 0.2s; z-index: 5;
        }
        .btn-remove:hover { color: #ff0055; transform: scale(1.2); }

        .sync-area { 
            display: flex; justify-content: space-between; margin-top: 20px; 
            background: rgba(0,0,0,0.2); padding: 15px; border-radius: 15px;
        }
        
        #overlay { position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(0,0,0,0.9); z-index:99; display:flex; justify-content:center; align-items:center; flex-direction:column; backdrop-filter: blur(20px); cursor: pointer;}
        #toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); border: 1px solid var(--primary-glow); color: var(--primary-glow); padding: 12px 30px; border-radius: 30px; opacity: 0; transition: 0.3s; pointer-events: none; font-weight: bold; box-shadow: 0 0 20px rgba(0,212,255,0.2);}
    </style>
</head>
<body>
    <div id="overlay" onclick="startSession()">
        <h1 style="font-size: 4rem; margin-bottom:0;">PLAY</h1>
        <p style="color: #ccc; letter-spacing: 2px;">CLICK TO START SESSION</p>
    </div>
    
    <div id="toast">SYSTEM READY</div>

    <div class="container">
        <h1>AURORA SYNC <span style="font-size: 0.5em; vertical-align: super; opacity: 0.7;">v8</span></h1>

        <div id="player-deck">
            <div id="status-light"></div>
            <div id="player-wrapper"><div id="player"></div></div>
        </div>

        <div class="input-group">
            <input type="text" id="linkInput" placeholder="Cole o link do YouTube..." onkeypress="if(event.key==='Enter') addLink()">
            <button class="btn btn-primary" onclick="addLink()">INSERIR FITA</button>
        </div>

        <div class="controls">
            <button class="btn" onclick="sendControl('playpause')">⏯ Play/Pause</button>
            <button class="btn" onclick="sendControl('next')">⏭ Eject / Next</button>
            <button class="btn" onclick="sendControl('shuffle')">🔀 Shuffle</button>
            <label class="btn" style="border-color: var(--primary-glow); color: var(--primary-glow);">
                <input type="checkbox" id="autoDjCheck" onchange="toggleDj()" checked> &nbsp; AUTO-DJ
            </label>
        </div>

        <ul id="playlist">
            </ul>

        <div class="sync-area">
            <button class="btn" onclick="requestSync()" style="font-size: 0.7rem;">⬇️ RESYNC (PUXAR)</button>
            <div style="color: #666; font-size: 0.8rem; align-self: center;">SERVER HEARTBEAT: 10s</div>
            <button class="btn btn-danger" onclick="masterSync()" style="font-size: 0.7rem;">⬆️ MASTER FORCE</button>
        </div>
    </div>

    <svg style="display: none;">
        <symbol id="gear-icon" viewBox="0 0 24 24">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm0 10c-2.21 0-4-1.79-4-4s1.79-4 4-4 4 1.79 4 4-1.79 4-4 4z"/>
            <path d="M12 6c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-2.69-6-6-6zm0 10c-2.21 0-4-1.79-4-4s1.79-4 4-4 4 1.79 4 4-1.79 4-4 4z" fill="currentColor"/>
            <circle cx="12" cy="12" r="2" fill="currentColor"/>
            <rect x="11" y="2" width="2" height="4" fill="currentColor"/>
            <rect x="11" y="18" width="2" height="4" fill="currentColor"/>
            <rect x="2" y="11" width="4" height="2" fill="currentColor"/>
            <rect x="18" y="11" width="4" height="2" fill="currentColor"/>
        </symbol>
    </svg>

    <script>
        const socket = io(); 
        var player, isReady = false;
        var lastCalculatedTime = 0; 
        var ignoreUpdates = false; 

        var tag = document.createElement('script'); tag.src = "https://www.youtube.com/iframe_api";
        document.head.appendChild(tag);

        function onYouTubeIframeAPIReady() {
            player = new YT.Player('player', {
                height: '100%', width: '100%',
                playerVars: { 'autoplay': 0, 'controls': 0, 'rel': 0, 'modestbranding': 1 }, 
                events: { 'onReady': onReady, 'onStateChange': onStateChange }
            });
        }
        
        function onReady() { 
            isReady = true; socket.emit('request_sync');
            setInterval(() => {
                if(!isReady || ignoreUpdates) return;
                try {
                    let t = player.getCurrentTime();
                    if(Math.abs(t - lastCalculatedTime) > 4 && player.getPlayerState() !== 3) { 
                        socket.emit('seek_event', {time: t}); lastCalculatedTime = t; 
                    }
                } catch(e){}
            }, 1000);
        }

        function onStateChange(e) { if(e.data === 0) socket.emit('video_ended'); }
        function startSession() { document.getElementById('overlay').style.display = 'none'; if(isReady) player.unMute(); }
        function showToast(msg) { let t = document.getElementById('toast'); t.innerText = msg; t.style.opacity = 1; setTimeout(() => t.style.opacity = 0, 3000); }

        socket.on('heartbeat', processState);
        socket.on('update_state', processState);
        socket.on('notification', (msg) => showToast(msg));

        function processState(state) {
            ignoreUpdates = true;
            renderPlaylist(state);
            document.getElementById('autoDjCheck').checked = state.auto_dj_enabled;

            let light = document.getElementById('status-light');
            light.className = state.is_playing ? 'playing' : 'paused';

            if(isReady) {
                let vid = state.playlist[state.current_video_index];
                if(vid) {
                    let url = player.getVideoUrl();
                    if(!url.includes(vid.id)) player.loadVideoById(vid.id);
                }

                let pState = player.getPlayerState();
                if(state.is_playing) {
                    if(pState !== 1 && pState !== 3) player.playVideo();
                } else {
                    if(pState === 1) player.pauseVideo();
                }

                let targetTime = state.anchor_time;
                if(state.is_playing) {
                    let timePassedOnServer = state.server_now - state.server_start_time;
                    targetTime = state.anchor_time + timePassedOnServer;
                }
                lastCalculatedTime = targetTime;

                let diff = Math.abs(player.getCurrentTime() - targetTime);
                if(diff > 2.5) player.seekTo(targetTime, true);
            }
            setTimeout(() => ignoreUpdates = false, 1500);
        }

        function addLink() {
            let url = document.getElementById('linkInput').value;
            if(url) { showToast("SCANNING TAPE..."); socket.emit('add_video', url); document.getElementById('linkInput').value = ''; }
        }

        function sendControl(type) {
            if(!isReady) return;
            if(type === 'playpause') {
                let action = player.getPlayerState() === 1 ? 'pause' : 'play';
                socket.emit('control_action', {action: action, time: player.getCurrentTime()});
            } else if (type === 'next') socket.emit('next_video');
            else if (type === 'shuffle') socket.emit('shuffle');
        }
        
        function requestSync() { showToast("RESYNCING..."); socket.emit('request_sync'); }
        function masterSync() {
            if(!isReady) return; showToast("OVERRIDING SYSTEM...");
            let data = { time: player.getCurrentTime(), is_playing: player.getPlayerState() === 1 };
            socket.emit('master_sync_force', data);
        }
        function toggleDj() { socket.emit('toggle_autodj', document.getElementById('autoDjCheck').checked); }
        
        function renderPlaylist(state) {
            let list = document.getElementById('playlist');
            list.innerHTML = '';
            state.playlist.forEach((v, i) => {
                let li = document.createElement('li');
                let isActive = (i === state.current_video_index);
                let isPlaying = isActive && state.is_playing;
                li.className = `k7-tape ${isActive ? 'active' : ''} ${isPlaying ? 'playing' : ''}`;
                let btn = i > state.current_video_index ? `<button class="btn-remove" onclick="socket.emit('remove', ${i})">✕</button>` : '';
                
                let reelsHtml = `
                    <div class="k7-reels">
                        <svg class="reel-svg"><use href="#gear-icon"></use></svg>
                        <svg class="reel-svg"><use href="#gear-icon"></use></svg>
                    </div>
                `;

                li.innerHTML = `
                    <div class="k7-label-area">
                        <img src="${v.thumbnail || 'https://via.placeholder.com/120x70?text=No+Image'}" class="k7-label-img">
                    </div>
                    ${reelsHtml}
                    <div class="k7-info">
                        <span class="k7-title">${i+1}. ${v.title}</span>
                        <span class="k7-status">${isActive ? (isPlaying ? '▶ PLAYING' : '⏸ PAUSED') : 'QUEUED'}</span>
                    </div>
                    ${btn}
                `;
                list.appendChild(li);
            });
        }
    </script>
</body>
</html>
"""

# ==========================================
# 4. BACKEND LOGIC (HELPER FUNCTIONS)
# ==========================================

def extract_info_smart(url):
    """Extrai informações de Vídeos Únicos, Playlists ou Mixes"""
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True, # Rápido (apenas metadados)
            'noplaylist': False,
            'playlistend': 20,    # Limite de segurança
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            detected_items = []

            # Caso 1: Playlist/Mix
            if 'entries' in info:
                print(f"📂 Playlist detectada: {info.get('title')}")
                for entry in info['entries']:
                    if entry.get('id') and entry.get('title'):
                        detected_items.append({
                            'id': entry['id'],
                            'title': entry['title'],
                            'thumbnail': f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"
                        })
            # Caso 2: Vídeo Único
            else:
                detected_items.append({
                    'id': info['id'],
                    'title': info['title'],
                    'thumbnail': f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                })
            return detected_items
    except Exception as e:
        print(f"Erro na extração: {e}")
        return None

def find_recommendation(last_video_title):
    """Auto-DJ inteligente"""
    try:
        print(f"🤖 AutoDJ gerando mix para: {last_video_title}")
        ydl_opts = {'quiet': True, 'default_search': 'ytsearch', 'noplaylist': True, 'extract_flat': True}
        query = f"{last_video_title} related music"
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
            if 'entries' in info and len(info['entries']) > 0:
                rec = random.choice(info['entries']) # Aleatório entre os top 5
                return {'id': rec['id'], 'title': f"📻 Auto: {rec['title']}", 'thumbnail': f"https://i.ytimg.com/vi/{rec['id']}/hqdefault.jpg"}
        return None
    except: return None

def get_broadcast_packet():
    """Empacota estado com timestamp do servidor para cálculo preciso de latência"""
    s = room_state.copy()
    s['server_now'] = time.time()
    return s

# --- Heartbeat Loop (Metrônomo) ---
def heartbeat_loop():
    while True:
        socketio.sleep(10)
        socketio.emit('heartbeat', get_broadcast_packet())

socketio.start_background_task(heartbeat_loop)

# ==========================================
# 5. ROTAS E EVENTOS SOCKET.IO
# ==========================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('add_video')
def handle_add(url):
    emit('notification', "SCANNING TAPE...", broadcast=True)
    items = extract_info_smart(url)
    
    if items:
        count = len(items)
        room_state['playlist'].extend(items)
        
        # Inicializa se a sala estava vazia
        if len(room_state['playlist']) == count:
            room_state['current_video_index'] = 0
            room_state['is_playing'] = True
            room_state['anchor_time'] = 0
            room_state['server_start_time'] = time.time()
        
        emit('update_state', get_broadcast_packet(), broadcast=True)
        if count > 1: emit('notification', f"📚 {count} FITAS ADICIONADAS", broadcast=True)
        else: emit('notification', f"FITA INSERIDA: {items[0]['title'][:20]}...", broadcast=True)
    else:
        emit('notification', "❌ ERRO NA LEITURA", broadcast=True)

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
            emit('notification', "AUTO-DJ SELECTING NEXT TRACK...", broadcast=True)
    
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
    emit('notification', "⚠️ MASTER OVERRIDE DETECTED!", broadcast=True)

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


# ==========================================
# 6. EXECUÇÃO (Produção)
# ==========================================

if __name__ == '__main__':
    # O Render define a porta na variável de ambiente PORT
    port = int(os.environ.get("PORT", 5000))
    # '0.0.0.0' libera o acesso externo
    socketio.run(app, host='0.0.0.0', port=port)