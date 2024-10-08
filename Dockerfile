FROM python:3.12

RUN mkdir /app
RUN useradd app
WORKDIR /app
RUN apt update && apt install -y openjdk-11-jre-headless && rm -rf /var/lib/apt/lists/*
RUN pip install -U pip

COPY requirements.txt /app/
RUN pip install -r requirements.txt

USER app:app

COPY main /app/main
COPY vdv_pkpass /app/vdv_pkpass
COPY manage.py /app/manage.py
COPY aztec/target/aztec-1.0.jar /app/aztec-1.0.jar