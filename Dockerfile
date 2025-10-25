# minimal Dockerfile for a Python script
# build: docker build -t pres-action-scraper --build-arg SCRIPT=your_script.py .
# run:   docker run --rm pres-action-scraper
FROM python:3.13-slim

WORKDIR /app

# optional requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy project
COPY . .

# default script (override with --build-arg SCRIPT=your_script.py)
ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "python script.py"]