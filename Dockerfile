FROM golang:1.26.1-bookworm@sha256:ab3d6955bbc813a0f3fdf220c1d817dd89c0b3f283777db8ece4a32fe7858edd AS build
WORKDIR /src
COPY giddy/ ./
RUN CGO_ENABLED=0 go build -trimpath -o /sasuke ./cmd/bot
FROM python:3.13.9-slim
WORKDIR /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /sasuke /bot/sasuke-bin
COPY bob_mix.py madara.py bot.py navigation.py policy.py sakura.py mechfight.py LICENSE-lance ./
COPY giddy/LICENSE /LICENSE-giddy
USER 65534:65534
ENTRYPOINT ["python3", "/bot/bob_mix.py"]
