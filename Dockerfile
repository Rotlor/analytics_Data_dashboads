FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY sql ./sql
COPY tests ./tests
CMD ["python", "-m", "src.pipeline"]
