# Multi-stage build: compile Naruto practice driver, then copy Python runtime
FROM golang:1.21-alpine AS naruto-builder

WORKDIR /build/naruto
COPY naruto/go.mod naruto/go.sum* ./
RUN go mod download

COPY naruto/ ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o naruto-bin ./cmd/bot

# Python runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy Naruto binary
COPY --from=naruto-builder /build/naruto/naruto-bin /app/naruto-bin

# Copy Python bot files
COPY bob_mix_improved.py /app/
COPY tsunade.py /app/
COPY lance_bot.py /app/
COPY navigation.py /app/
COPY policy.py /app/
COPY mechfight.py /app/
COPY bot.py /app/
COPY train.py /app/
COPY arena_sim.py /app/
COPY arena_trials.py /app/
COPY arena_calibrate.py /app/

# Copy model weights
COPY models/ /app/models/

# Copy licenses
COPY LICENSE-lance /app/
COPY LICENSE-naruto /app/

# Ensure Naruto binary is executable
RUN chmod +x /app/naruto-bin

# Default entrypoint - runs Naruto (bob_mix_improved)
ENTRYPOINT ["python3", "-m", "mechfight", "run", "--bot", "bob_mix_improved:act"]