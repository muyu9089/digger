FROM python:3.12-slim
WORKDIR /app
COPY app.py ./
COPY static ./static
RUN useradd --create-home workbench && mkdir /app/data && chown workbench:workbench /app/data
USER workbench
EXPOSE 8765
CMD ["python", "app.py", "--host", "0.0.0.0"]
