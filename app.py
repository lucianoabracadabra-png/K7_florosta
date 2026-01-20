import os
import time
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from yt_dlp import YoutubeDL

# ==========================================
# 1. CONFIGURAÇÃO
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aurora-secret-key')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='gevent')

# ==========================================
# 2. GESTÃO DE ESTADO (MULTI-SALAS)
# ==========================================

# Estrutura: rooms[room_id] = { ... estado da sala ... }
rooms = {}

# Mapeia ID do socket -> { 'room': '...', 'username': '...' }
sid_map = {}

def init_room_state(password):
    """Cria o estado inicial de uma nova sala"""
    return {
        'password': password,
        'playlist': [],             
        'current_video_index': 0,   
        'is_playing': False,        
        'anchor_time': 0,           
        'server_start_time': 0,     
        'auto_dj_enabled': True,
        'users': [] # Lista de nomes (Strings)
    }

# ==========================================
# 3. ROTAS
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')

# ==========================================
# 4. LÓGICA AUXILIAR (Mixes & AutoDJ)
# ==========================================

def extract_info_smart(url):
    """
    MODO FLASH: Usa extract_flat=True para TUDO.
    É mais rápido, evita bloqueios de IP e funciona para Vídeo Único e Playlist.
    """
    try:
        url = url.strip() # Remove espaços acidentais
        
        ydl_opts = {
            'quiet': True,
            'extract_flat': True, # O SEGREDO: Nunca baixa a página, só lê metadados
            'noplaylist': False,  # Aceita tudo
            'playlistend': 20,
            'ignoreerrors': True  # Pula vídeos com erro na lista
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info: return None
            
            detected = []

            # CASO 1: É Playlist ou Mix (Tem 'entries')
            if 'entries' in info:
                print(f"📂 Playlist/Mix detectada: {info.get('title')}")
                for entry in info['entries']:
                    # Validação tripla para garantir que o item é válido
                    if entry and entry.get('id') and entry.get('title'):
                        detected.append({
                            'id': entry['id'],
                            'title': entry['title'],
                            'thumbnail': f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"
                        })

            # CASO 2: É Vídeo Único (Não tem 'entries', é o próprio objeto)
            elif info.get('id') and info.get('title'):
                print(f"🎬 Vídeo Único detectado: {info.get('title')}")
                detected.append({
                    'id': info['id'],
                    'title': info['title'],
                    'thumbnail': f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                })
            
            # Se a lista estiver vazia, retorna None para disparar o erro no front
            return detected if detected else None

    except Exception as e:
        print(f"❌ Erro Crítico: {e}")
        return None

def find_recommendation(last_title):
    """Auto-DJ"""
    try:
        ydl_opts = {'quiet': True, 'default_search': 'ytsearch', 'noplaylist': True, 'extract_flat': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"{last_title} music", download=False)
            if 'entries' in info and len(info['entries']) > 0:
                rec = random.choice(info['entries']) # Pega um aleatório dos resultados
                return {
                    'id': rec['id'], 
                    'title': f"📻 Auto: {rec['title']}", 
                    'thumbnail': f"https://i.ytimg.com/vi/{rec['id']}/hqdefault.jpg"
                }
        return None
    except: return None

def get_room_packet(room_id):
    """Monta o pacote de dados para enviar para a sala"""
    if room_id not in rooms: return None
    state = rooms[room_id].copy()
    state['server_now'] = time.time()
    del state['password'] # Nunca envia a senha pro frontend
    return state

# Loop de Heartbeat (Atualizado para iterar por todas as salas ativas)
def heartbeat_loop():
    while True:
        socketio.sleep(10)
        # Convertemos keys() para list() para evitar erro se uma sala for deletada durante o loop
        active_rooms = list(rooms.keys())
        for r_id in active_rooms:
            socketio.emit('heartbeat', get_room_packet(r_id), to=r_id)

socketio.start_background_task(heartbeat_loop)

# ==========================================
# 5. EVENTOS SOCKET.IO
# ==========================================

@socketio.on('join_room_event')
def handle_join(data):
    username = data.get('username')
    room_id = data.get('room')
    password = data.get('password')

    if not username or not room_id:
        return emit('error_msg', "Preencha Nome e Sala!")

    # Cria sala se não existir
    if room_id not in rooms:
        rooms[room_id] = init_room_state(password)
    else:
        # Se existir, confere a senha (se houver senha definida)
        if rooms[room_id]['password'] and rooms[room_id]['password'] != password:
            return emit('error_msg', "Senha Incorreta!")

    # Login Sucesso
    join_room(room_id)
    
    # Registra usuário no mapa global e na sala
    sid_map[request.sid] = {'room': room_id, 'username': username}
    if username not in rooms[room_id]['users']:
        rooms[room_id]['users'].append(username)

    emit('login_success', {'room': room_id, 'username': username})
    emit('update_state', get_room_packet(room_id), to=room_id)
    emit('notification', f"🟢 {username} conectou.", to=room_id)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in sid_map:
        user_data = sid_map[request.sid]
        room_id = user_data['room']
        name = user_data['username']
        
        del sid_map[request.sid] # Remove do mapa global

        if room_id in rooms:
            if name in rooms[room_id]['users']:
                rooms[room_id]['users'].remove(name)
            
            emit('notification', f"🔴 {name} saiu.", to=room_id)
            emit('update_state', get_room_packet(room_id), to=room_id)

            # Auto-Delete: Se não sobrou ninguém, apaga a sala da memória
            if len(rooms[room_id]['users']) == 0:
                print(f"🧹 Sala vazia deletada: {room_id}")
                del rooms[room_id]

@socketio.on('add_video')
def handle_add(url):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    username = sid_map[request.sid]['username']
    
    emit('notification', "Lendo Fita...", to=room_id)
    items = extract_info_smart(url)
    
    if items:
        # Marca quem adicionou
        for item in items:
            item['added_by'] = username
            
        rooms[room_id]['playlist'].extend(items)
        
        # Se a lista estava vazia, dá play automático
        if len(rooms[room_id]['playlist']) == len(items):
            rooms[room_id]['current_video_index'] = 0
            rooms[room_id]['is_playing'] = True
            rooms[room_id]['anchor_time'] = 0
            rooms[room_id]['server_start_time'] = time.time()
            
        emit('update_state', get_room_packet(room_id), to=room_id)
        msg = f"📚 {len(items)} faixas" if len(items) > 1 else f"Fita: {items[0]['title'][:15]}..."
        emit('notification', f"{msg} (por {username})", to=room_id)
    else:
        emit('notification', "❌ Link Inválido ou Erro", to=request.sid)

@socketio.on('control_action')
def handle_control(d):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    
    rooms[room_id]['is_playing'] = (d['action'] == 'play')
    rooms[room_id]['anchor_time'] = d['time']
    rooms[room_id]['server_start_time'] = time.time()
    emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('seek_event')
def handle_seek(d):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    
    rooms[room_id]['anchor_time'] = d['time']
    rooms[room_id]['server_start_time'] = time.time()
    emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('next_video')
def handle_next():
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    room = rooms[room_id]
    
    has_next = False
    if room['current_video_index'] + 1 < len(room['playlist']):
        room['current_video_index'] += 1
        has_next = True
    elif room['auto_dj_enabled'] and len(room['playlist']) > 0:
        last = room['playlist'][-1]
        rec = find_recommendation(last['title'])
        if rec:
            rec['added_by'] = '🤖 Auto-DJ'
            room['playlist'].append(rec)
            room['current_video_index'] += 1
            has_next = True
            emit('notification', "Auto-DJ inseriu uma fita", to=room_id)
    
    if has_next:
        room['is_playing'] = True
        room['anchor_time'] = 0
        room['server_start_time'] = time.time()
        emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('master_sync_force')
def handle_master_force(data):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    username = sid_map[request.sid]['username']
    
    rooms[room_id]['anchor_time'] = data['time']
    rooms[room_id]['is_playing'] = data['is_playing']
    rooms[room_id]['server_start_time'] = time.time()
    emit('update_state', get_room_packet(room_id), to=room_id)
    emit('notification', f"⚠️ Sync Forçado por {username}", to=room_id)

@socketio.on('shuffle')
def handle_shuffle():
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    
    idx = rooms[room_id]['current_video_index']
    playlist = rooms[room_id]['playlist']
    if len(playlist) > idx + 1:
        future = playlist[idx+1:]
        random.shuffle(future)
        rooms[room_id]['playlist'] = playlist[:idx+1] + future
        emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('remove')
def handle_remove(i):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    
    if i > rooms[room_id]['current_video_index']:
        rooms[room_id]['playlist'].pop(i)
        emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('toggle_autodj')
def handle_tdj(v): 
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    rooms[room_id]['auto_dj_enabled'] = v
    emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('request_sync')
def handle_req_sync(): 
    if request.sid in sid_map:
        room_id = sid_map[request.sid]['room']
        emit('update_state', get_room_packet(room_id), to=request.sid)

@socketio.on('video_ended')
def handle_ended(): handle_next()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)