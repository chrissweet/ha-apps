FROM python:3.12.7-alpine3.20
RUN pip install --no-cache-dir websockets==13.1
COPY archive.py /archive.py
CMD ["python3", "-u", "/archive.py"]
