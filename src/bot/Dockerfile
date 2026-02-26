FROM docker.io/python:3.10-alpine AS builder

ARG LIBOLM_VERSION=3.2.16
RUN apk add --no-cache \
    make \
    cmake \
    gcc \
    g++ \
    git \
    libffi-dev \
    python3-dev  \
    && rm -rf /var/lib/apt/lists/*

RUN git clone -b ${LIBOLM_VERSION} https://gitlab.matrix.org/matrix-org/olm.git /olm 
WORKDIR /olm
RUN sed -i 's/cmake_minimum_required(VERSION 3.4)/cmake_minimum_required(VERSION 3.5)/' CMakeLists.txt
RUN cmake . -Bbuild
RUN cmake --build build
RUN make install 
WORKDIR /olm/python
RUN make olm-python3 
RUN mkdir -p /python-libs 
RUN DESTDIR="/python-libs" make install-python3

ENV CMAKE_POLICY_VERSION_MINIMUM=3.5
RUN pip install --prefix="/python-libs" --no-warn-script-location "matrix-nio[e2e]"

FROM docker.io/python:3.10-alpine

COPY --from=builder /python-libs /usr/local

COPY --from=builder /usr/local/lib/libolm* /usr/local/lib/

RUN apk add --no-cache libstdc++


WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY archive.py .

ENTRYPOINT [ "python3" ]
CMD [ "/app/archive.py" ]
