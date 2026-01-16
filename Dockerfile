# Usa uma imagem leve do Python
FROM python:3.9-slim

# Instala ffmpeg (opcional, mas bom pro yt-dlp) e git
RUN apt-get update && apt-get install -y ffmpeg git

# Cria a pasta do app
WORKDIR /app

# Copia os requisitos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Expõe a porta
EXPOSE 5000

# Comando para iniciar (usando Gunicorn + Gevent para produção)
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", "-w", "1", "-b", "0.0.0.0:5000", "app:app"]