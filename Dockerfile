# Python and dependency versions match the tests: Python from .python-version, packages from uv.lock.
# rasterio's wheel bundles GDAL, so no system GDAL or compiler is needed.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

# The only system library the rasterio wheel does not bundle.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache/uv

# World-readable so the code runs whatever user the workflow runner uses.
COPY --chmod=0644 ./src/*.py /app/

# CWL overrides this via baseCommand (run_product.py --product <ndvi|ndwi|clip>).
CMD ["python3", "-u", "run_product.py", "--help"]
