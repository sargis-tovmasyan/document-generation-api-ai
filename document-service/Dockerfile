FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/contracts/python

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libffi8 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY document-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY document-service/app ./app
COPY document-service/templates ./templates
COPY contracts/python /contracts/python

RUN mkdir -p /app/data /app/generated/invoices

EXPOSE 8001 50051

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
