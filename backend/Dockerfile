FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# --proxy-headers: take the client IP from X-Forwarded-For, but only from the proxies listed
# in FORWARDED_ALLOW_IPS (set it to your host's load balancer IP/range; default 127.0.0.1).
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
