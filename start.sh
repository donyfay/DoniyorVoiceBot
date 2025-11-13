#!/bin/bash
# Обеспечиваем, что скрипт исполняемый
chmod +x main.py

# Активируем виртуальное окружение Render
source .venv/bin/activate

# Обновляем pip
pip install --upgrade pip

# Устанавливаем зависимости
pip install -r requirements.txt

# Запуск бота
python main.py
