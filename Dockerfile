# CYBERPRO Platform Production Container
FROM python:3.11-slim

WORKDIR /app

# Install dependencies if present
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy complete project codebase and assets
COPY . .

# Ensure data directory exists
RUN mkdir -p data

# Expose server port (default: 8000)
EXPOSE 8000

ENV CYBERPRO_HOST=0.0.0.0
ENV CYBERPRO_PORT=8000

CMD ["python", "server.py"]
