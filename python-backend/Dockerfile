FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD python -c "import os; import uvicorn; uvicorn.run('main:app', host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))"