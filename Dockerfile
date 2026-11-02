# One service: builds the website, then serves it and the API together. Used by Render (see render.yaml).
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist ./static
ENV PYTHONPATH=/app TALO_STATIC_DIR=/app/static
EXPOSE 8000
CMD ["sh", "-c", "uvicorn talo.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
