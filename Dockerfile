FROM python:3.13-slim
WORKDIR /app
ADD . /app
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8080
CMD ["python", "main.py"]
