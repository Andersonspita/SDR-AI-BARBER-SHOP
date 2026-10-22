# Build a partir da raiz do projeto (docker-compose: context ".") para incluir database/init.sql
FROM python:3.11-slim

# O código importa `backend.app.*`, então o pacote fica em /srv/backend
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY database ./database
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
