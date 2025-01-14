FROM python:3.13-alpine

RUN pip install httpx sanic 'uvicorn[standard]' twitchAPI 'tortoise-orm[asyncpg]'

COPY stream_emotes /app/stream_emotes
WORKDIR /app

CMD ["uvicorn", "--host", "0.0.0.0", "--port", "8080", "stream_emotes.server:APP"]
