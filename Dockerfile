
FROM python:3.13

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем зависимости и исходный код
COPY requirements.txt ./

# Устанавливаем зависимости (используем --no-cache-dir для уменьшения размера образа)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы проекта
COPY . .

# Команда для запуска приложения (замените на свою, например `python main.py`)
CMD ["python", "bot.py"]