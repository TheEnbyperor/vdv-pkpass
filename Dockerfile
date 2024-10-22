FROM python:3.12 AS barkoder

RUN apt-get update && apt-get install -y cmake libgl1 libcurl4-openssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN pip install -U pip pybind11[global]

COPY barkoder /barkoder
RUN mkdir /barkoder/build
WORKDIR /barkoder/build
RUN cmake .. && make

FROM python:3.12

RUN mkdir /app
RUN useradd app
WORKDIR /app
RUN apt-get update && apt-get install -y openjdk-17-jre-headless libldap2-dev libssl-dev libsasl2-dev libgl1 pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN pip install -U pip

COPY requirements.txt /app/
RUN pip install -r requirements.txt

USER app:app

COPY --from=barkoder /barkoder/build/Barkoder.cpython-312-x86_64-linux-gnu.so /usr/local/lib/python3.12/site-packages/Barkoder.cpython-312-x86_64-linux-gnu.so
COPY main /app/main
COPY vdv_pkpass /app/vdv_pkpass
COPY manage.py /app/manage.py
COPY aztec/target/aztec-1.0.jar /app/aztec-1.0.jar