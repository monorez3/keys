# Ключи — образ для monoblock.casa/keys
FROM python:3.12-slim

WORKDIR /app

# зависимости отдельным слоем: код меняется часто, они — почти никогда
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY keys/ ./keys/
COPY clients/ ./clients/
COPY run.py .

# базы (кэш и ключи) живут на томе, иначе пересборка стирает выданные ключи
VOLUME ["/app/data"]
ENV KEYS_DATA_DIR=/app/data

EXPOSE 8105

# root-path — приложение живёт под /keys/, и должно знать об этом само:
# так все ссылки и примеры кода на страницах получаются сразу правильными
CMD ["uvicorn", "app:app", "--app-dir", "core", "--host", "0.0.0.0", "--port", "8105", \
     "--root-path", "/keys", "--proxy-headers", "--forwarded-allow-ips", "*"]
