# ShiftSense pilot image. Build from this folder (application/):  docker build -t shiftsense:pilot .
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SHIFTSENSE_ENV=pilot
RUN useradd --create-home --uid 1000 shiftsense
WORKDIR /opt/shiftsense
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=shiftsense:shiftsense . .
RUN mkdir -p data/backups data/inbox && chown -R shiftsense:shiftsense data
USER shiftsense
EXPOSE 8501
HEALTHCHECK --interval=5m --timeout=60s --start-period=2m CMD python -m shiftsense.health --json > /dev/null || exit 1
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
