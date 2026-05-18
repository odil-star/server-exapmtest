# Online Tests

Небольшой Django-сайт для онлайн-тестов. Администратор создает пользователей, загружает тесты из `.txt` файла и смотрит результаты. Пользователи входят в систему, проходят доступные тесты и видят результат.

## Запуск

```powershell
cd D:\Test\Server.Test\Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

После запуска:

- сайт: http://127.0.0.1:8000/
- админ-панель: http://127.0.0.1:8000/admin/
- загрузка теста: http://127.0.0.1:8000/admin-upload-test/

## Логика блоков

Один загруженный `.txt` или `.docx` файл создает один общий тест. Если в файле больше 25 вопросов, сайт автоматически делит вопросы на блоки:

- 1-25 вопросы: `Блок 1`
- 26-50 вопросы: `Блок 2`
- 51-75 вопросы: `Блок 3`

Пользователь открывает `/tests/`, выбирает тест, затем выбирает конкретный блок на странице `/tests/<test_id>/`. Прохождение блока идет по адресу `/tests/<test_id>/block/<block_id>/`.

## Формат файла теста

```txt
+++++
Вопрос 1
Вариант 1
Вариант 2
#Вариант 3
Вариант 4
======
+++++
Вопрос 2
Вариант 1
#Вариант 2
Вариант 3
Вариант 4
======
```

Правила:

- `++++++` начинает новый вопрос
- первая строка после `++++++` — текст вопроса
- строка с `#` — правильный ответ
- у каждого вопроса должен быть один правильный ответ
- у каждого вопроса должно быть минимум 2 варианта ответа

## Исправление `OperationalError: attempt to write a readonly database`

На Windows эта ошибка почти всегда означает, что процесс Django не может записать в `db.sqlite3` или в папку рядом с ним. При логине Django пишет новую строку в таблицу `django_session`, поэтому проблема часто проявляется именно на странице входа.

### Проверка прав и readonly-атрибута

```powershell
cd D:\Test\Server.Test\Backend
attrib db.sqlite3
icacls .
icacls db.sqlite3
```

Если у файла есть атрибут `R`, снимите его:

```powershell
attrib -R db.sqlite3
```

Выдайте текущему пользователю права на запись в папку проекта и базу:

```powershell
icacls . /grant "$($env:USERDOMAIN)\$($env:USERNAME):(OI)(CI)F"
icacls db.sqlite3 /grant "$($env:USERDOMAIN)\$($env:USERNAME):F"
```

Важно: SQLite должен иметь право писать не только в сам `db.sqlite3`, но и в папку `Backend`, потому что рядом с базой создаются временные файлы `db.sqlite3-journal`, `db.sqlite3-wal` или `db.sqlite3-shm`.

### Проверка, что база не занята другим процессом

Закройте DB Browser for SQLite, редакторы и лишние запущенные серверы Django. Проверить порт `8000`:

```powershell
netstat -ano | Select-String ':8000'
```

Остановить конкретный процесс по PID:

```powershell
Stop-Process -Id <PID> -Force
```

Если после сбоя остался временный journal-файл и сервер Django остановлен:

```powershell
Remove-Item .\db.sqlite3-journal -Force
```

### Проверка `settings.py`

В `online_tests/settings.py` база должна быть настроена так:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
```

### Проверка миграций и session-таблицы

```powershell
python manage.py migrate
python -c "import sqlite3; c=sqlite3.connect('db.sqlite3'); print(c.execute('pragma integrity_check').fetchone()[0]); c.close()"
```

Проверка записи, логина и создания session:

```powershell
python manage.py shell -c "from django.contrib.auth import get_user_model; from django.test import Client; from django.contrib.sessions.models import Session; User=get_user_model(); username='login_check_user'; User.objects.filter(username=username).delete(); user=User.objects.create_user(username=username, password='StrongPass123!'); client=Client(); print('login=', client.login(username=username, password='StrongPass123!')); print('sessions=', Session.objects.count()); user.delete()"
```

### Если база повреждена

Сначала сделайте резервную копию, затем создайте новую базу:

```powershell
cd D:\Test\Server.Test\Backend
Stop-Process -Id <PID> -Force
Copy-Item .\db.sqlite3 .\db.sqlite3.backup
Remove-Item .\db.sqlite3 -Force
Remove-Item .\db.sqlite3-journal -Force -ErrorAction SilentlyContinue
python manage.py migrate
python manage.py createsuperuser
```

Если старая база мешает после изменения моделей и тестовые данные не нужны, можно создать SQLite заново:

```powershell
cd D:\Test\Server.Test\Backend
Stop-Process -Id <PID> -Force
Copy-Item .\db.sqlite3 .\db.sqlite3.backup
Remove-Item .\db.sqlite3 -Force
Remove-Item .\db.sqlite3-journal -Force -ErrorAction SilentlyContinue
python manage.py migrate
python manage.py createsuperuser
```

## Структура проекта

```text
Backend/
├── manage.py
├── requirements.txt
├── README.md
├── db.sqlite3
├── online_tests/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── quiz/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── forms.py
│   ├── models.py
│   ├── parser.py
│   ├── urls.py
│   ├── views.py
│   └── migrations/
│       └── __init__.py
├── templates/
│   ├── base.html
│   ├── registration/
│   │   └── login.html
│   └── quiz/
│       ├── admin_upload_test.html
│       ├── result.html
│       ├── take_test.html
│       └── test_list.html
└── static/
    └── css/
        └── styles.css
```
