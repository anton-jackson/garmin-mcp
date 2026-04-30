FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "garmin_mcp.server"]
