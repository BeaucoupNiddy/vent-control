# Stage 1: build the dashboard
FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build   # writes to /src/backend/ventctl/static

# Stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ventctl ./ventctl
COPY --from=web /src/backend/ventctl/static ./ventctl/static
ENV VENTCTL_DB=/data/ventctl.db VENTCTL_HOST=0.0.0.0 VENTCTL_PORT=8000 TZ=America/Chicago
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"VENTCTL_PORT\"]}/api/health')"
CMD ["python", "-m", "ventctl"]
