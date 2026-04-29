# ── Build stage ────────────────���──────────────────────────���──────────────────
FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /asb-core ./cmd/server

# ── Production stage ────────────────────────────────��────────────────────────
FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /asb-core /usr/local/bin/asb-core

EXPOSE 8080
ENTRYPOINT ["asb-core"]
