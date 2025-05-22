FROM python:3.12-slim

# Install build tools and Python headers for pip packages with C extensions
RUN apt-get update && apt-get install -y \
    python3-distutils \
    build-essential \
    python3-dev \
    gcc \
    libffi-dev \
    libssl-dev

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "new.py"]
