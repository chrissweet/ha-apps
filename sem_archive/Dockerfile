FROM python:3.12.7-alpine3.20
RUN pip install --no-cache-dir paho-mqtt==2.1.0
COPY archive.py /archive.py
CMD ["python3", "-u", "/archive.py"]
