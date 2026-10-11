FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install uv
WORKDIR /app/server
COPY server/pyproject.toml ./
RUN uv pip install --system fastapi "uvicorn[standard]" httpx pymongo anthropic python-dotenv numpy python-multipart \
    && (uv pip install --system raindrop-ai || true)
COPY server/ /app/server/
COPY web/ /app/web/
COPY phone/ /app/phone/
EXPOSE 8000
CMD ["uvicorn", "labrat.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
