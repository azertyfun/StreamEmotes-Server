FROM python:3.13-alpine

RUN pip install httpx sanic 'uvicorn[standard]' twitchAPI 'tortoise-orm[asyncpg]'

COPY twitchemotes_server /app/twitchemotes_server
WORKDIR /app

CMD ["uvicorn", "--host", "0.0.0.0", "--port", "8080", "twitchemotes_server.server:APP"]
