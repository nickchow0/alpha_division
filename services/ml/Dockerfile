FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["python", "pipeline.py"]
