FROM golang:1.24.8-alpine AS build

ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z

RUN CGO_ENABLED=0 go install -trimpath -ldflags="-s -w" "github.com/minio/minio@${MINIO_VERSION}"

FROM alpine:3.22

RUN apk add --no-cache ca-certificates tzdata \
    && addgroup -S -g 10001 minio \
    && adduser -S -u 10001 -G minio minio \
    && mkdir -p /data \
    && chown minio:minio /data

COPY --from=build /go/bin/minio /usr/local/bin/minio

USER minio
VOLUME ["/data"]
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
