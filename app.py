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

# --- Interface Frontend (CYBERJUNGLE K7 EDITION 🌿🦾) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CYBERJUNGLE SYNC</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700;900&family=Rajdhani:wght@500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --jungle-green: #00ff41;
            --toxic-yellow: #f0f000;
            --poison-purple: #bc13fe;
            --deep-swamp: #021207;
            --mech-grey: #1a1f1c;
            
            --glass-bg: rgba(2, 20, 10, 0.6);
            --glass-border: rgba(0, 255, 65, 0.3);
            --neon-shadow: 0 0 15px rgba(0, 255, 65, 0.2);
        }

        body {
            font-family: 'Rajdhani', sans-serif;
            margin: 0;
            padding: 20px;
            color: #e0ffe0;
            min-height: 100vh;
            /* Fundo Animado de Floresta Digital */
            background: linear-gradient(135deg, #051a0d, #000000, #0a2e1d, #1f0f2e);
            background-size: 400% 400%;
            animation: bio-pulse 15s ease infinite;
            overflow-x: hidden;
        }

        @keyframes bio-pulse {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        /* Scanlines Overlay (Efeito de monitor antigo) */
        body::after {
            content: "";
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: repeating-linear-gradient(0deg, rgba(0,0,0,0.1), rgba(0,0,0,0.1) 1px, transparent 1px, transparent 2px);
            pointer-events: none; z-index: 0;
        }

        .container {
            position: relative; z-index: 1;
            max-width: 900px;
            margin: 0 auto;
            background: var(--glass-bg);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid var(--glass-border);
            border-radius: 4px; /* Cantos mais retos estilo militar */
            padding: 30px;
            box-shadow: 0 0 30px rgba(0,0,0,0.8), inset 0 0 50px rgba(0,255,65,0.05);
            /* Cantos cortados (Clip-path) */
            clip-path: polygon(
                20px 0, 100% 0, 
                100% calc(100% - 20px), calc(100% - 20px) 100%, 
                0 100%, 0 20px
            );
        }

        h1 {
            font-family: 'Orbitron', sans-serif;
            text-align: center;
            font-size: 3rem;
            margin-top: 0;
            text-transform: uppercase;
            letter-spacing: 4px;
            background: linear-gradient(to bottom, #fff, var(--jungle-green));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 10px var(--jungle-green);
        }

        /* --- PLAYER DECK (ESTILO INDUSTRIAL) --- */
        #player-deck {
            background: #000;
            border: 2px solid #333;
            border-top: 4px solid var(--jungle-green);
            margin-bottom: 25px;
            position: relative;
            box-shadow: 0 10px 30px #000;
        }

        /* Luz de Status */
        #status-light {
            width: 12px; height: 12px; 
            background: #333; position: absolute; top: 15px; right: 15px;
            z-index: 10; transition: 0.3s;
            clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%); /* Losango */
        }
        #status-light.playing { background: var(--jungle-green); box-shadow: 0 0 15px var(--jungle-green); }
        #status-light.paused { background: var(--toxic-yellow); box-shadow: 0 0 15px var(--toxic-yellow); }

        #player-wrapper {
            position: relative; padding-bottom: 56.25%; height: 0;
            opacity: 0.9; transition: opacity 0.5s;
            border-bottom: 1px solid #333;
        }
        #player { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }

        /* --- INPUTS --- */
        .input-group { display: flex; gap: 0; margin-bottom: 25px; border: 1px solid var(--jungle-green); }
        input[type="text"] {
            flex: 1; padding: 15px; border: none;
            background: rgba(0,0,0,0.6); color: var(--jungle-green); outline: none;
            font-family: 'Orbitron', sans-serif; font-size: 0.9rem;
        }
        input[type="text"]::placeholder { color: rgba(0, 255, 65, 0.3); }

        .btn {
            padding: 12px 25px; border: none; cursor: pointer; font-weight: bold;
            text-transform: uppercase; letter-spacing: 1px; font-family: 'Orbitron', sans-serif;
            transition: 0.2s; position: relative; overflow: hidden;
        }
        
        /* Botão INSERIR (Integrado ao input) */
        .btn-insert {
            background: var(--jungle-green); color: #000;
            clip-path: polygon(20% 0, 100% 0, 100% 100%, 0 100%);
            padding-left: 30px;
        }
        .btn-insert:hover { background: #fff; }

        /* Controles Principais */
        .controls { display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; margin-bottom: 30px; }
        
        .btn-ctrl {
            background: transparent; border: 1px solid var(--jungle-green); color: var(--jungle-green);
            box-shadow: inset 0 0 10px rgba(0,255,65,0.1);
        }
        .btn-ctrl:hover { background: var(--jungle-green); color: #000; box-shadow: 0 0 20px var(--jungle-green); }
        
        /* Checkbox customizado */
        .cyber-check {
            display: flex; align-items: center; cursor: pointer; border: 1px solid var(--poison-purple);
            color: var(--poison-purple); padding: 10px 20px; transition: 0.3s;
        }
        .cyber-check:has(input:checked) { background: var(--poison-purple); color: #fff; box-shadow: 0 0 15px var(--poison-purple); }
        .cyber-check input { display: none; }

        /* --- PLAYLIST (FITAS K7 BIOLÓGICAS) --- */
        #playlist { 
            list-style: none; padding: 0; display: flex; flex-direction: column; gap: 15px; 
            max-height: 500px; overflow-y: auto; padding-right: 5px;
        }

        #playlist::-webkit-scrollbar { width: 4px; }
        #playlist::-webkit-scrollbar-thumb { background: var(--jungle-green); }

        .k7-tape {
            background: rgba(10, 20, 10, 0.8);
            border: 1px solid #333;
            border-left: 5px solid #333;
            padding: 10px;
            display: flex; align-items: center;
            position: relative;
            transition: all 0.3s ease;
            /* CRUCIAL: Impede o esmagamento */
            flex-shrink: 0; 
            min-height: 80px; 
        }

        /* Efeito de musgo/sujeira digital */
        .k7-tape::before {
            content: ''; position: absolute; top: 0; right: 0; width: 30px; height: 100%;
            background: repeating-linear-gradient(45deg, transparent, transparent 5px, rgba(0,0,0,0.5) 5px, rgba(0,0,0,0.5) 10px);
            pointer-events: none;
        }

        .k7-tape:hover { border-color: var(--jungle-green); transform: translateX(5px); }

        .k7-tape.active {
            border-left-color: var(--jungle-green);
            background: rgba(0, 50, 20, 0.6);
            box-shadow: inset 0 0 20px rgba(0,255,65,0.1);
        }

        /* Adesivo da Fita (Thumbnail) */
        .k7-label-area {
            width: 100px; height: 60px;
            background: #000;
            border: 2px solid #555;
            margin-right: 15px;
            position: relative;
            flex-shrink: 0; /* Garante que a imagem não esmaga */
            overflow: hidden;
        }
        .k7-label-img { width: 100%; height: 100%; object-fit: cover; filter: grayscale(80%) contrast(120%); transition: 0.3s; }
        .active .k7-label-img { filter: grayscale(0%) sepia(20%); }

        .k7-info { flex: 1; z-index: 2; overflow: hidden; }
        .k7-title { 
            font-weight: 700; font-size: 1rem; color: #fff; 
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;
        }
        .k7-status { font-size: 0.8rem; color: var(--jungle-green); letter-spacing: 2px; }

        /* Reels (Engrenagens) */
        .k7-reels { display: flex; gap: 10px; margin-right: 20px; }
        .reel-svg { width: 35px; height: 35px; fill: none; stroke: #444; stroke-width: 2; }
        .active .reel-svg { stroke: var(--jungle-green); }
        
        @keyframes cyber-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .active.playing .reel-svg { animation: cyber-spin 2s linear infinite; }

        .btn-remove {
            background: transparent; border: none; color: #444; cursor: pointer; font-size: 1.5rem;
            transition: 0.2s; padding: 0 10px;
        }
        .btn-remove:hover { color: var(--toxic-yellow); text-shadow: 0 0 10px var(--toxic-yellow); }

        /* --- SYNC AREA --- */
        .sync-area { 
            display: flex; justify-content: space-between; align-items: center; margin-top: 20px; 
            border-top: 1px solid #333; padding-top: 20px;
        }
        .btn-sync { font-size: 0.7rem; letter-spacing: 0; padding: 8px 15px; }
        .btn-pull { background: #222; color: #aaa; border: 1px solid #444; }
        .btn-pull:hover { color: #fff; border-color: #fff; }
        
        .btn-force { background: #300; color: #f55; border: 1px solid #500; }
        .btn-force:hover { background: #f00; color: #000; box-shadow: 0 0 15px #f00; }

        /* --- TOAST FIX (Z-INDEX ALTO) --- */
        #toast { 
            position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); 
            background: #000; 
            border: 1px solid var(--jungle-green); 
            color: var(--jungle-green); 
            padding: 15px 40px; 
            text-transform: uppercase; letter-spacing: 2px; font-weight: bold;
            box-shadow: 0 0 30px rgba(0,255,65,0.3);
            opacity: 0; transition: 0.3s; pointer-events: none;
            z-index: 10000; /* CORREÇÃO DO PROBLEMA DE VISIBILIDADE */
        }
        
        /* Overlay Inicial */
        #overlay { 
            position: fixed; top:0; left:0; width:100%; height:100%; 
            background: rgba(0,10,5,0.95); z-index: 9999; 
            display:flex; justify-content:center; align-items:center; flex-direction:column; 
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div id="overlay" onclick="startSession()">
        <h1 style="font-size: 5rem; margin-bottom:0; text-shadow: 0 0 20px var(--jungle-green);">START</h1>
        <p style="color: var(--jungle-green); letter-spacing: 4px; font-family: 'Orbitron';">INITIALIZE SYSTEM</p>
    </div>
    
    <div id="toast">SYSTEM READY</div>

    <div class="container">
        <h1>CYBER<span style="color:#fff">JUNGLE</span></h1>

        <div id="player-deck">
            <div id="status-light"></div>
            <div id="player-wrapper"><div id="player"></div></div>
        </div>

        <div class="input-group">
            <input type="text" id="linkInput" placeholder="> INSERT YOUTUBE DATA LINK..." onkeypress="if(event.key==='Enter') addLink()">
            <button class="btn btn-insert" onclick="addLink()">LOAD</button>
        </div>

        <div class="controls">
            <button class="btn btn-ctrl" onclick="sendControl('playpause')">⏯ EXECUTE</button>
            <button class="btn btn-ctrl" onclick="sendControl('next')">⏭ NEXT TRACK</button>
            <button class="btn btn-ctrl" onclick="sendControl('shuffle')">🔀 RANDOMIZE</button>
            <label class="btn cyber-check">
                <input type="checkbox" id="autoDjCheck" onchange="toggleDj()" checked> 
                🤖 AUTO-DJ
            </label>
        </div>

        <ul id="playlist">
            </ul>

        <div class="sync-area">
            <button class="btn btn-sync btn-pull" onclick="requestSync()">⬇️ RE-SYNC DATA</button>
            <div style="color: #444; font-size: 0.7rem; font-family: 'Orbitron';">HEARTBEAT: ACTIVE</div>
            <button class="btn btn-sync btn-force" onclick="masterSync()">⬆️ MASTER OVERRIDE</button>
        </div>
    </div>

    <svg style="display: none;">
        <symbol id="gear-icon" viewBox="0 0 24 24">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z" fill="currentColor" opacity="0.3"/>
            <path d="M12 4V2M12 20v2M4 12H2m20 0h-2m-2.17-5.83l-1.42-1.42M17.59 17.59l-1.42-1.42M6.41 6.41L5 5m1.41 12.59L5 19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/>
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
            if(url) { showToast("DECODING..."); socket.emit('add_video', url); document.getElementById('linkInput').value = ''; }
        }

        function sendControl(type) {
            if(!isReady) return;
            if(type === 'playpause') {
                let action = player.getPlayerState() === 1 ? 'pause' : 'play';
                socket.emit('control_action', {action: action, time: player.getCurrentTime()});
            } else if (type === 'next') socket.emit('next_video');
            else if (type === 'shuffle') socket.emit('shuffle');
        }
        
        function requestSync() { showToast("SYNCING..."); socket.emit('request_sync'); }
        function masterSync() {
            if(!isReady) return; showToast("OVERRIDING...");
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
                        <span class="k7-status">${isActive ? (isPlaying ? '▶ ACTIVE' : '⏸ STANDBY') : 'QUEUED'}</span>
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