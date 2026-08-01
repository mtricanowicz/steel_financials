# Build from the steel-dashboard root so both the app and the generated data
# are inside the build context:
#   docker build -f streamlit-app/Dockerfile -t steel-streamlit .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DASHBOARD_DATA_DIR=/app/data/generated

WORKDIR /app

COPY streamlit-app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY streamlit-app/ ./streamlit-app/
COPY assets/ ./assets/
COPY data/generated/ ./data/generated/

RUN useradd --create-home appuser
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "streamlit run streamlit-app/app.py --server.port ${PORT} --server.address 0.0.0.0"]
