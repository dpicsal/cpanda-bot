FROM python:3.12-slim

# Install system dependencies for pip and packages with C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Upgrade pip and install setuptools/wheel for PEP517 builds
RUN pip install --upgrade pip setuptools wheel

# Install Python dependencies
RUN pip install -r requirements.txt

# (Optional) If you use playwright, install browsers
RUN python -m playwright install --with-deps

CMD ["python", "bot.py"]
