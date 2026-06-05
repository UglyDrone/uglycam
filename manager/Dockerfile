FROM python:3.10-slim

# Install system dependencies including lm-sensors
RUN apt-get update && apt-get install -y --no-install-recommends \
    lm-sensors \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy manager application files
COPY server.py /app/server.py
COPY static /app/static

# Expose the API and dashboard web portal port
EXPOSE 80

# Run management server
CMD ["python", "server.py"]
