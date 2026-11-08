FROM python:3.12-slim
WORKDIR /workspace/backend
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev
COPY backend ./
COPY model_services /workspace/model_services
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
