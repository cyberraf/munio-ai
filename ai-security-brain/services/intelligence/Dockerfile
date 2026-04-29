FROM python:3.11-slim

WORKDIR /app

# System dependencies:
#   - libglib2.0-0 libsm6 libxrender1 libxext6  → opencv-python-headless / scikit-learn
#   - tesseract-ocr libtesseract-dev            → pytesseract OCR (room labels & dimensions)
#   - potrace                                   → vector tracing for clean blueprints
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 \
    tesseract-ocr libtesseract-dev \
    potrace \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

EXPOSE 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
