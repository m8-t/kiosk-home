FROM python:3.14-slim
RUN pip install --no-cache-dir aiohttp==3.14.3
WORKDIR /app
COPY kiosk/ kiosk/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-m", "kiosk"]
