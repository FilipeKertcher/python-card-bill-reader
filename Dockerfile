FROM python:3.11-slim-buster as builder

RUN apt-get update \
    && apt-get -y install libpq-dev gcc ffmpeg libsm6 libxext6 poppler-utils \
    && pip3 install psycopg2

RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN pip3 install --upgrade pip
RUN pip3 install --extra-index-url https://alpine-wheels.github.io/index numpy
RUN pip3 install -r requirements.txt
RUN pip3 install ultralytics

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1 \ 
    PATH="/opt/venv/bin:$PATH"
    
WORKDIR /app

COPY . /app
ENV FLASK_APP=app
CMD ["python","index.py"]

# FROM python:3.11-slim-buster as prod
# COPY requirements.txt .

# # RUN pip3 install -r requirements.txt

# COPY --from=builder /opt/venv /opt/venv

