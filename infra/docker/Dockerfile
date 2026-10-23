FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY xeonolab ./xeonolab
COPY infra/plugins/builtin-capabilities.json ./infra/plugins/builtin-capabilities.json
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home platform
RUN mkdir /data && chown platform:platform /data
USER platform
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "xeonolab.services:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
