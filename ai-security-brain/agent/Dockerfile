FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

# Default: run locus mock connector. Override with docker compose command.
CMD ["python3", "locus_connector.py", "--mock"]
