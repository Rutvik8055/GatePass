FROM python:3.12-slim
WORKDIR /app
COPY backend ./backend
COPY frontend ./frontend
COPY database ./database
ENV TOURGUARD_HOST=0.0.0.0 TOURGUARD_PORT=8080
EXPOSE 8080
CMD ["python", "backend/server.py"]
