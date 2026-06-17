FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the package into the image.
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir .

EXPOSE 8787

CMD ["hachi", "gateway", "--host", "0.0.0.0", "--port", "8787", "--runtime"]
