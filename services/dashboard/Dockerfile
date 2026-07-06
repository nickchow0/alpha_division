FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ is mounted as a volume in docker-compose.yml
# so it does not need to be COPY'd here

COPY *.py .
COPY templates/ templates/
COPY static/ static/

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "main:app"]
