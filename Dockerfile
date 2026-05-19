FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --pre -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "run.py"]
