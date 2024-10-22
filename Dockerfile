FROM python:3.12

RUN mkdir /app
RUN useradd app
WORKDIR /app
RUN apt-get update && apt-get install -y openjdk-17-jre-headless libldap2-dev libssl-dev libsasl2-dev pkg-config && rm -rf /var/lib/apt/lists/*
RUN pip install -U pip

COPY requirements.txt /app/
RUN pip install -r requirements.txt

USER app:app

COPY barkoder/Barkoder.py /usr/local/lib/python3.12/site-packages/
COPY barkoder/BarkoderSDK.cpython-311-x86_64-linux-gnu.so /usr/local/lib/python3.12/site-packages/BarkoderSDK.cpython-312-x86_64-linux-gnu.so
COPY main /app/main
COPY vdv_pkpass /app/vdv_pkpass
COPY manage.py /app/manage.py
COPY aztec/target/aztec-1.0.jar /app/aztec-1.0.jar