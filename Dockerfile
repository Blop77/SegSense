# Web demo image: deployable to Nebius Serverless Endpoints or any container host.
FROM python:3.12-slim

# gcc ships with libasan/libubsan on Debian, which is all SegSense needs to build sanitized binaries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY segsense ./segsense
COPY examples ./examples

# Untrusted C code is compiled and run here, so never run as root.
RUN useradd --create-home --uid 10001 segsense
USER segsense

ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "segsense", "serve"]
