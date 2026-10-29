FROM python:3.12.4-slim

WORKDIR /app

# Install dependencies before copying source for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create and switch to a non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
