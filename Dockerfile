FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kkoma_slack ./kkoma_slack
COPY data/secrets.txt ./data/secrets.txt
COPY data/frequent_words.txt ./data/frequent_words.txt
RUN mkdir -p ./data/near

ENV KKOMA_DATA_DIR=/app/data
ENV KKOMA_STATE_DB=/app/data/game_state.db
ENV PORT=3339
EXPOSE 3339

CMD ["sh", "-c", "gunicorn kkoma_slack.app:app --bind 0.0.0.0:${PORT:-3339} --workers 1 --threads 4"]
