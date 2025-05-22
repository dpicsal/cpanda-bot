FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install required system packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    python3-dev \
    build-essential \
    swig \
    curl \
    git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Upgrade pip and install build tools
RUN pip install --upgrade pip setuptools wheel build

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers (if used)
RUN python -m playwright install --with-deps

# Start your bot (make sure this file exists)
CMD ["python", "bot.py"]
