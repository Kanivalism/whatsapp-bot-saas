FROM rasa/rasa-sdk:3.6.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER 1001

CMD ["start", "--actions", "actions"]
