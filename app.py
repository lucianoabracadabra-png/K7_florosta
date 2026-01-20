import os
import time
import random
import secrets
from collections import Counter
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from yt_dlp import YoutubeDL

# ==========================================
# 1. CONFIGURAÇÃO
# ==========================================
app = Flask(__name__)
# Gera SECRET_KEY segura se não existir no ambiente
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
socketio = SocketIO(
    app, 
    cors_allowed_origins=os.environ.get('ALLOWED_ORIGINS', '*'),  # Configure no ambiente
    async_mode='gevent'
)

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per hour"],
    storage_uri="memory://"
)

# ==========================================
# 2. GESTÃO DE ESTADO (MULTI-SALAS)
# ==========================================

rooms = {}
sid_map = {}

# Limites de segurança
MAX_PLAYLIST_SIZE = 100
MAX_ROOM_USERS = 50
MAX_VIDEO_TITLE_LENGTH = 200

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
        'users': [],  # Lista de nomes (Strings)
        'created_at': time.time()
    }

# ==========================================
# 3. ROTAS
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')

# ==========================================
# 4. LÓGICA AUXILIAR
# ==========================================

def sanitize_url(url):
    """Valida e limpa URLs do YouTube"""
    url = url.strip()
    
    # Lista branca de domínios permitidos
    allowed_domains = [
        'youtube.com', 'www.youtube.com', 
        'youtu.be', 'm.youtube.com',
        'music.youtube.com'
    ]
    
    if not any(domain in url for domain in allowed_domains):
        return None
    
    # Básico: deve começar com http
    if not url.startswith(('http://', 'https://')):
        return None
        
    return url

def extract_info_smart(url):
    """
    Extrai informações de vídeos/playlists do YouTube.
    CORRIGIDO: Agora funciona com vídeos únicos também.
    """
    try:
        url = sanitize_url(url)
        if not url:
            print("❌ URL inválida ou não permitida")
            return None
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',  # CORREÇÃO: Flat apenas em playlists
            'noplaylist': False,
            'playlistend': 20,
            'ignoreerrors': True,
            'socket_timeout': 15,  # Timeout de 15s
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info: 
                print("❌ yt-dlp não retornou informações")
                return None
            
            detected = []

            # CASO 1: É Playlist ou Mix (tem 'entries')
            if 'entries' in info and info['entries']:
                print(f"📂 Playlist/Mix: {info.get('title', 'Sem título')}")
                for entry in info['entries']:
                    if entry and entry.get('id'):
                        # Para flat extract, usa title direto
                        title = entry.get('title', 'Sem título')[:MAX_VIDEO_TITLE_LENGTH]
                        detected.append({
                            'id': entry['id'],
                            'title': title,
                            'thumbnail': f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"
                        })

            # CASO 2: Vídeo Único (não tem 'entries')
            elif info.get('id'):
                print(f"🎬 Vídeo único: {info.get('title', 'Sem título')}")
                title = info.get('title', 'Sem título')[:MAX_VIDEO_TITLE_LENGTH]
                detected.append({
                    'id': info['id'],
                    'title': title,
                    'thumbnail': info.get('thumbnail') or f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                })
            
            if not detected:
                print("⚠️ Nenhum vídeo válido encontrado")
                return None
                
            print(f"✅ {len(detected)} vídeo(s) extraído(s)")
            return detected

    except Exception as e:
        print(f"❌ Erro em extract_info_smart: {type(e).__name__}: {str(e)}")
        return None

def find_recommendation(room_id):
    """
    Auto-DJ inteligente: analisa a playlist da sala e busca algo similar.
    """
    try:
        room = rooms[room_id]
        playlist = room['playlist']
        
        if not playlist:
            return None
        
        # Conta palavras-chave nos títulos da playlist (preferências da sala)
        all_words = []
        for video in playlist[-10:]:  # Últimos 10 vídeos
            # Remove marcadores do Auto-DJ e palavras comuns
            title = video['title'].replace('📻 Auto:', '').lower()
            words = [w for w in title.split() if len(w) > 3]
            all_words.extend(words)
        
        # Pega as palavras mais comuns
        if all_words:
            common = Counter(all_words).most_common(3)
            search_term = ' '.join([word for word, _ in common])
        else:
            # Fallback: usa o último vídeo
            search_term = playlist[-1]['title']
        
        print(f"🎲 Auto-DJ buscando: {search_term}")
        
        ydl_opts = {
            'quiet': True,
            'default_search': 'ytsearch3',  # Busca 3 resultados
            'noplaylist': True,
            'extract_flat': True,
            'socket_timeout': 10
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_term, download=False)
            
            if 'entries' in info and info['entries']:
                # Filtra vídeos já na playlist
                existing_ids = {v['id'] for v in playlist}
                candidates = [e for e in info['entries'] if e and e.get('id') not in existing_ids]
                
                if candidates:
                    rec = random.choice(candidates)
                    return {
                        'id': rec['id'],
                        'title': f"📻 Auto: {rec['title'][:MAX_VIDEO_TITLE_LENGTH]}",
                        'thumbnail': f"https://i.ytimg.com/vi/{rec['id']}/hqdefault.jpg"
                    }
        
        return None
        
    except Exception as e:
        print(f"⚠️ Auto-DJ falhou: {e}")
        return None

def get_room_packet(room_id):
    """Monta o pacote de dados para enviar para a sala"""
    if room_id not in rooms: 
        return None
    
    state = rooms[room_id].copy()
    state['server_now'] = time.time()
    
    # Remove dados sensíveis
    if 'password' in state:
        del state['password']
    if 'created_at' in state:
        del state['created_at']
    
    return state

# Heartbeat melhorado
def heartbeat_loop():
    while True:
        socketio.sleep(10)
        
        # Limpa salas antigas vazias (> 1 hora)
        now = time.time()
        to_delete = []
        for r_id, room in list(rooms.items()):
            if len(room['users']) == 0 and (now - room['created_at']) > 3600:
                to_delete.append(r_id)
        
        for r_id in to_delete:
            print(f"🧹 Sala expirada deletada: {r_id}")
            del rooms[r_id]
        
        # Envia heartbeat apenas para salas ativas
        for r_id in list(rooms.keys()):
            if len(rooms[r_id]['users']) > 0:
                socketio.emit('heartbeat', get_room_packet(r_id), to=r_id)

socketio.start_background_task(heartbeat_loop)

# ==========================================
# 5. EVENTOS SOCKET.IO
# ==========================================

@socketio.on('join_room_event')
@limiter.limit("10 per minute")
def handle_join(data):
    username = data.get('username', '').strip()[:50]  # Limita tamanho do nome
    room_id = data.get('room', '').strip()[:50]
    password = data.get('password', '')

    if not username or not room_id:
        return emit('error_msg', "Preencha Nome e Sala!")

    # Cria sala se não existir
    if room_id not in rooms:
        rooms[room_id] = init_room_state(password)
    else:
        # Verifica senha
        if rooms[room_id]['password'] and rooms[room_id]['password'] != password:
            return emit('error_msg', "Senha Incorreta!")
        
        # NOVO: Verifica limite de usuários
        if len(rooms[room_id]['users']) >= MAX_ROOM_USERS:
            return emit('error_msg', "Sala Cheia!")
        
        # NOVO: Verifica nome duplicado
        if username in rooms[room_id]['users']:
            return emit('error_msg', f"Nome '{username}' já está em uso nesta sala!")

    # Login Sucesso
    join_room(room_id)
    
    sid_map[request.sid] = {'room': room_id, 'username': username}
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
        
        del sid_map[request.sid]

        if room_id in rooms:
            if name in rooms[room_id]['users']:
                rooms[room_id]['users'].remove(name)
            
            emit('notification', f"🔴 {name} saiu.", to=room_id)
            emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('add_video')
@limiter.limit("20 per minute")
def handle_add(url):
    if request.sid not in sid_map: 
        return
    
    room_id = sid_map[request.sid]['room']
    username = sid_map[request.sid]['username']
    
    # Verifica limite da playlist
    if len(rooms[room_id]['playlist']) >= MAX_PLAYLIST_SIZE:
        return emit('notification', f"❌ Playlist cheia! (Max: {MAX_PLAYLIST_SIZE})", to=request.sid)
    
    emit('notification', "🔍 Lendo Fita...", to=room_id)
    items = extract_info_smart(url)
    
    if items:
        # Limita quantidade de vídeos adicionados de uma vez
        remaining_space = MAX_PLAYLIST_SIZE - len(rooms[room_id]['playlist'])
        items = items[:remaining_space]
        
        for item in items:
            item['added_by'] = username
            
        rooms[room_id]['playlist'].extend(items)
        
        # Auto-play se estava vazio
        if len(rooms[room_id]['playlist']) == len(items):
            rooms[room_id]['current_video_index'] = 0
            rooms[room_id]['is_playing'] = True
            rooms[room_id]['anchor_time'] = 0
            rooms[room_id]['server_start_time'] = time.time()
            
        emit('update_state', get_room_packet(room_id), to=room_id)
        
        msg = f"📚 {len(items)} fita(s)" if len(items) > 1 else f"♪ {items[0]['title'][:30]}..."
        emit('notification', f"{msg} (por {username})", to=room_id)
    else:
        emit('notification', "❌ Link inválido, erro na leitura ou não é YouTube", to=request.sid)

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
    
    # Tenta próximo da fila
    if room['current_video_index'] + 1 < len(room['playlist']):
        room['current_video_index'] += 1
        has_next = True
    
    # Tenta Auto-DJ
    elif room['auto_dj_enabled'] and len(room['playlist']) > 0:
        if len(room['playlist']) < MAX_PLAYLIST_SIZE:
            rec = find_recommendation(room_id)
            if rec:
                rec['added_by'] = '🤖 Auto-DJ'
                room['playlist'].append(rec)
                room['current_video_index'] += 1
                has_next = True
                emit('notification', "🤖 Auto-DJ adicionou uma fita", to=room_id)
    
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
    emit('notification', f"⚡ Sync forçado por {username}", to=room_id)

@socketio.on('shuffle')
@limiter.limit("5 per minute")
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
        
        username = sid_map[request.sid]['username']
        emit('notification', f"🔀 {username} embaralhou a fila", to=room_id)

@socketio.on('remove')
def handle_remove(i):
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    
    # Só pode remover músicas futuras
    if i > rooms[room_id]['current_video_index'] and i < len(rooms[room_id]['playlist']):
        removed = rooms[room_id]['playlist'].pop(i)
        emit('update_state', get_room_packet(room_id), to=room_id)
        emit('notification', f"🗑️ '{removed['title'][:30]}...' removida", to=room_id)

@socketio.on('toggle_autodj')
def handle_tdj(v): 
    if request.sid not in sid_map: return
    room_id = sid_map[request.sid]['room']
    rooms[room_id]['auto_dj_enabled'] = v
    
    status = "ativado" if v else "desativado"
    emit('notification', f"🤖 Auto-DJ {status}", to=room_id)
    emit('update_state', get_room_packet(room_id), to=room_id)

@socketio.on('request_sync')
def handle_req_sync(): 
    if request.sid in sid_map:
        room_id = sid_map[request.sid]['room']
        emit('update_state', get_room_packet(room_id), to=request.sid)

@socketio.on('video_ended')
def handle_ended(): 
    handle_next()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"🎵 Aurora Player rodando na porta {port}")
    print(f"🔐 SECRET_KEY: {'definida por ambiente' if 'SECRET_KEY' in os.environ else 'gerada automaticamente'}")
    socketio.run(app, host='0.0.0.0', port=port)