import os
import time
import random
import secrets
from collections import Counter, defaultdict
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from yt_dlp import YoutubeDL

# ==========================================
# 1. CONFIGURAÇÃO
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
socketio = SocketIO(
    app, 
    cors_allowed_origins=os.environ.get('ALLOWED_ORIGINS', '*'),
    async_mode='gevent'
)

# Rate Limiting Manual (sem biblioteca externa)
rate_limits = defaultdict(list)

def check_rate_limit(key, max_requests, window_seconds):
    """Rate limiter simples em memória"""
    now = time.time()
    
    # Limpa requisições antigas
    rate_limits[key] = [t for t in rate_limits[key] if now - t < window_seconds]
    
    # Verifica se excedeu o limite
    if len(rate_limits[key]) >= max_requests:
        return False
    
    # Registra esta requisição
    rate_limits[key].append(now)
    return True

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
        'users': [],
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
    
    allowed_domains = [
        'youtube.com', 'www.youtube.com', 
        'youtu.be', 'm.youtube.com',
        'music.youtube.com'
    ]
    
    if not any(domain in url for domain in allowed_domains):
        return None
    
    if not url.startswith(('http://', 'https://')):
        return None
        
    return url

def extract_info_smart(url):
    """
    Extrai informações de vídeos/playlists do YouTube.
    MÁXIMA PROTEÇÃO ANTI-BOT: Usa client iOS que raramente é bloqueado.
    """
    try:
        url = sanitize_url(url)
        if not url:
            print("❌ URL inválida ou não permitida")
            return None
        
        # ESTRATÉGIA ANTI-BLOQUEIO MÁXIMA
        ydl_opts = {
            'quiet': False,  # Mostra erros para debug
            'no_warnings': False,
            'extract_flat': 'in_playlist',
            'noplaylist': False,
            'playlistend': 20,
            'ignoreerrors': True,
            'socket_timeout': 25,
            
            # CHAVE: Client iOS é o mais difícil de bloquear
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'android'],  # iOS primeiro, Android fallback
                    'player_skip': ['webpage'],
                    'skip': ['dash', 'hls'],  # Só precisamos de metadados
                }
            },
            
            # User-Agent do iPhone
            'user_agent': 'com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)',
            
            # Headers iOS
            'http_headers': {
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'X-Youtube-Client-Name': '5',  # iOS
                'X-Youtube-Client-Version': '19.09.3',
                'Origin': 'https://www.youtube.com',
                'Connection': 'keep-alive',
            }
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info: 
                print("❌ yt-dlp não retornou informações")
                return None
            
            detected = []

            # CASO 1: Playlist/Mix (tem 'entries')
            if 'entries' in info and info['entries']:
                print(f"📂 Playlist/Mix: {info.get('title', 'Sem título')} ({len(info['entries'])} vídeos)")
                for entry in info['entries']:
                    if entry and entry.get('id'):
                        title = entry.get('title') or entry.get('id')  # Fallback para ID
                        title = title[:MAX_VIDEO_TITLE_LENGTH]
                        detected.append({
                            'id': entry['id'],
                            'title': title,
                            'thumbnail': f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"
                        })

            # CASO 2: Vídeo Único
            elif info.get('id'):
                print(f"🎬 Vídeo único: {info.get('title', 'Sem título')} [ID: {info['id']}]")
                title = info.get('title') or info['id']  # Fallback para ID se título não vier
                title = title[:MAX_VIDEO_TITLE_LENGTH]
                detected.append({
                    'id': info['id'],
                    'title': title,
                    'thumbnail': info.get('thumbnail') or f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                })
            
            if not detected:
                print("⚠️ Nenhum vídeo válido encontrado na resposta")
                return None
                
            print(f"✅ {len(detected)} vídeo(s) extraído(s) com sucesso")
            return detected

    except Exception as e:
        error_msg = str(e)
        print(f"❌ Erro em extract_info_smart: {type(e).__name__}: {error_msg}")
        
        # Se for erro de bot, tenta fallback com innertube
        if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
            print("🔄 Tentando método alternativo (innertube API)...")
            return extract_fallback_innertube(url)
        
        return None

def extract_fallback_innertube(url):
    """
    Fallback quando client iOS falha: usa InnerTube API diretamente.
    Extrai apenas ID do vídeo da URL e constrói metadados mínimos.
    """
    try:
        import re
        
        # Extrai video ID da URL
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',  # youtube.com/watch?v=ID ou youtu.be/ID
            r'(?:embed\/)([0-9A-Za-z_-]{11})',   # youtube.com/embed/ID
        ]
        
        video_id = None
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                break
        
        if not video_id:
            print("❌ Não conseguiu extrair ID do vídeo da URL")
            return None
        
        print(f"🆔 ID extraído: {video_id}")
        
        # Monta resposta mínima (funcional)
        return [{
            'id': video_id,
            'title': f"Vídeo {video_id}",  # Título genérico, player vai carregar o real
            'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        }]
        
    except Exception as e:
        print(f"❌ Fallback também falhou: {e}")
        return None

def find_recommendation(room_id):
    """Auto-DJ inteligente baseado nas preferências da sala"""
    try:
        room = rooms[room_id]
        playlist = room['playlist']
        
        if not playlist:
            return None
        
        # Analisa últimos 10 vídeos para encontrar padrões
        all_words = []
        for video in playlist[-10:]:
            title = video['title'].replace('📻 Auto:', '').lower()
            words = [w for w in title.split() if len(w) > 3]
            all_words.extend(words)
        
        # Monta busca baseada em palavras mais comuns
        if all_words:
            common = Counter(all_words).most_common(3)
            search_term = ' '.join([word for word, _ in common])
        else:
            search_term = playlist[-1]['title']
        
        print(f"🎲 Auto-DJ buscando: {search_term}")
        
        ydl_opts = {
            'quiet': True,
            'default_search': 'ytsearch3',
            'noplaylist': True,
            'extract_flat': True,
            'socket_timeout': 15,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'extractor_args': {'youtube': {'player_client': ['android']}},
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_term, download=False)
            
            if 'entries' in info and info['entries']:
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

# Heartbeat
def heartbeat_loop():
    while True:
        socketio.sleep(10)
        
        # Limpa salas vazias antigas (> 1 hora)
        now = time.time()
        to_delete = [r_id for r_id, room in list(rooms.items()) 
                     if len(room['users']) == 0 and (now - room['created_at']) > 3600]
        
        for r_id in to_delete:
            print(f"🧹 Sala expirada deletada: {r_id}")
            del rooms[r_id]
        
        # Heartbeat apenas para salas ativas
        for r_id in list(rooms.keys()):
            if len(rooms[r_id]['users']) > 0:
                socketio.emit('heartbeat', get_room_packet(r_id), to=r_id)

socketio.start_background_task(heartbeat_loop)

# ==========================================
# 5. EVENTOS SOCKET.IO
# ==========================================

@socketio.on('join_room_event')
def handle_join(data):
    # Rate limit: 10 joins por minuto por IP
    if not check_rate_limit(f"join_{request.remote_addr}", 10, 60):
        return emit('error_msg', "Muitas tentativas! Aguarde um momento.")
    
    username = data.get('username', '').strip()[:50]
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
        
        # Verifica limite de usuários
        if len(rooms[room_id]['users']) >= MAX_ROOM_USERS:
            return emit('error_msg', "Sala Cheia!")
        
        # Verifica nome duplicado
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
def handle_add(url):
    if request.sid not in sid_map: 
        return
    
    # Rate limit: 20 adds por minuto
    if not check_rate_limit(f"add_{request.sid}", 20, 60):
        return emit('notification', "⏱️ Calma! Aguarde alguns segundos.", to=request.sid)
    
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
        emit('notification', "❌ Link inválido ou YouTube bloqueou. Tente outro vídeo.", to=request.sid)

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
def handle_shuffle():
    if request.sid not in sid_map: return
    
    # Rate limit: 5 shuffles por minuto
    if not check_rate_limit(f"shuffle_{request.sid}", 5, 60):
        return
    
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