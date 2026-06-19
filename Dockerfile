FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# G-A6: Create a dedicated non-root user before installing the package.
# UID 10001 is a conventional non-root UID that avoids conflicts with well-known
# system UIDs (0-999 on Debian/Ubuntu) while being below the 65535 nobody range.
RUN useradd --create-home --uid 10001 hachi

WORKDIR /app

# Install the package as root (pip needs write access to site-packages), then
# hand ownership of the working directory to the non-root user.
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir . && chown -R hachi:hachi /app

# Switch to the non-root user for all subsequent layers and at runtime.
USER hachi

EXPOSE 8787

CMD ["hachi", "gateway", "--host", "0.0.0.0", "--port", "8787", "--runtime"]
