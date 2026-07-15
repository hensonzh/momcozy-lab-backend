FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN addgroup --system app && adduser --system --ingroup app app

COPY production_backend/requirements.txt production_backend/requirements.txt
RUN pip install --upgrade pip && pip install -r production_backend/requirements.txt

COPY production_backend production_backend
RUN mkdir -p /workspace/production_backend/.local/object_storage \
    && chown -R app:app /workspace

USER app

EXPOSE 8000

CMD ["uvicorn", "production_backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
