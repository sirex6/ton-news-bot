FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY bot.py .
COPY content_hashes.json .
COPY sent_news.json .
COPY user_languages.json .
CMD ["python", "bot.py"]
