FROM golang:1.26.1-bookworm@sha256:ab3d6955bbc813a0f3fdf220c1d817dd89c0b3f283777db8ece4a32fe7858edd AS build
WORKDIR /src
COPY giddy/ ./
RUN CGO_ENABLED=0 go build -trimpath -o /giddy ./cmd/bot
FROM scratch
COPY --from=build /giddy /giddy
COPY giddy/LICENSE /LICENSE-giddy
USER 65534:65534
ENTRYPOINT ["/giddy"]
