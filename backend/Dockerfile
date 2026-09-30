FROM python:3.11-slim
ARG WITH_ML=0
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
COPY ml/requirements.txt ml/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
 && if [ "$WITH_ML" = "1" ]; then pip install --no-cache-dir -r ml/requirements.txt; fi
COPY backend backend
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
