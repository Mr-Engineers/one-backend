FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

# backend-2's load balancer uses a certificate from our own CA (no public domain).
# Trust it on top of the public CAs (certifi), which Supabase still needs.
# SSL_CERT_FILE covers ssl/httpx/aiohttp, REQUESTS_CA_BUNDLE covers requests.
COPY certs/backend-2-ca.crt /tmp/backend-2-ca.crt
RUN cat "$(python -c 'import certifi; print(certifi.where())')" /tmp/backend-2-ca.crt > /etc/ssl/ca-bundle.pem \
    && rm /tmp/backend-2-ca.crt
ENV SSL_CERT_FILE=/etc/ssl/ca-bundle.pem \
    REQUESTS_CA_BUNDLE=/etc/ssl/ca-bundle.pem

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
