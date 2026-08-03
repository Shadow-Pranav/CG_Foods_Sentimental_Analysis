# Matches the Python version this project is built and tested against
# (see README's Prerequisites / Notes).
FROM python:3.9-slim

WORKDIR /app

# Install dependencies first so this layer is cached across builds unless
# requirements.txt itself changes (avoids re-downloading torch/transformers
# on every code change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
