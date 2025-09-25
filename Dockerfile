# Use an official Python runtime as the base image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies (including libpq-dev for PostgreSQL)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies separately
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application
COPY . .

# Expose Flask port
EXPOSE 5000

# Run Gunicorn server
# CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:5000", "app:app"]

CMD ["gunicorn", "--workers", "4", "--threads", "2", "--timeout", "120", "--bind", "0.0.0.0:5000", "run:app"]

# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]


# CMD ["gunicorn", "--workers", "$(nproc)", "--bind", "0.0.0.0:5000", "app:app"]