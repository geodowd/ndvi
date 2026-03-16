# Build stage
FROM python:3.12-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache \
    expat \
    gdal \
    gdal-dev \
    gcc \
    g++ \
    musl-dev \
    python3-dev \
    linux-headers \
    && rm -rf /var/cache/apk/*

COPY ./requirements.txt /app

# Install Python packages (this will compile rasterio and other packages)
RUN pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.12-alpine AS runtime

WORKDIR /app

# Install only runtime dependencies (no build tools)
RUN apk add --no-cache \
    expat \
    gdal \
    && rm -rf /var/cache/apk/*

# Copy Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY ./*.py /app

# Default command kept for backwards compatibility; CWL overrides via baseCommand.
CMD ["python3", "-u", "run_ndvi.py"]


