FROM python:3.9-alpine

RUN adduser -D pics
USER pics

WORKDIR /app
COPY --chown=pics:pics . .
RUN mkdir -p app/app/static/outputs

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "cleaner.py"]
