FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /workspace
RUN addgroup --system specialty && adduser --system --ingroup specialty specialty
COPY services/stool_specialty/requirements.txt /workspace/requirements.txt
RUN pip install --upgrade pip && pip install -r /workspace/requirements.txt
COPY --chown=specialty:specialty services/__init__.py /workspace/services/__init__.py
COPY --chown=specialty:specialty services/stool_specialty /workspace/services/stool_specialty
USER specialty
EXPOSE 8000
CMD ["uvicorn", "services.stool_specialty.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
