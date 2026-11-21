FROM python:3.13.9-slim
WORKDIR /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY bot.py policy.py mechfight.py navigation.py ./
COPY models/trained.json models/trained.json
USER 65534:65534
ENTRYPOINT ["python3", "/bot/bot.py"]
