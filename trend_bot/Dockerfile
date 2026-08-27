FROM python:3.12-slim
WORKDIR /app
COPY trend_bot.py config.json ./
ENV STATE_DIR=/data
VOLUME /data
EXPOSE 8080
CMD ["python3", "trend_bot.py"]
