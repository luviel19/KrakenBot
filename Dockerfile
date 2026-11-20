FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN useradd --create-home --uid 1000 bot && mkdir -p /data && chown bot:bot /data
USER bot
VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "bot.py"]
