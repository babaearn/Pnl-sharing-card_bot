FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py .
COPY data_manager.py .
COPY leaderboard.py .
COPY utils.py .

# Create data directory with proper permissions
# This ensures JSON files can be written during runtime
RUN mkdir -p /app/data && chmod 777 /app/data

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "bot.py"]
