FROM python:3.9-alpine

RUN adduser pics;echo 'pics:1234567890' | chpasswd
USER pics

WORKDIR /app
COPY --chown=pics:pics . .
RUN mkdir /app/app
RUN mkdir /app/app/static
RUN mkdir /app/app/static/outputs
RUN chown -R pics:pics .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "cleaner.py"]
