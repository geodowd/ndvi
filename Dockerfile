FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libexpat1 \
    libgdal-dev \
    gdal-bin \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /app

RUN pip install --no-cache-dir -r requirements.txt

COPY ./*.py /app

ENV PATH="/app:${PATH}"

CMD ["python3", "-u" ,"run.py"]
