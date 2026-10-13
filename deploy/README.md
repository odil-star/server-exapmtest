# Деплой на VPS (Ubuntu/Debian, nginx + gunicorn + Postgres)

Файлы рядом: `demotest.service`, `nginx.conf`, `.env.example`. Замени `example.com`,
пароли и пути на свои.

## 1. Пакеты и база
```bash
sudo apt update
sudo apt install -y python3-venv python3-dev nginx postgresql certbot python3-certbot-nginx

sudo -u postgres psql <<'SQL'
CREATE USER demotest_user WITH PASSWORD 'STRONG_PASSWORD';
CREATE DATABASE demotest OWNER demotest_user;
SQL
```
Используй отдельного пользователя БД, а не `postgres`.

## 2. Код и окружение
```bash
sudo mkdir -p /srv/demotest /etc/demotest
sudo chown $USER /srv/demotest
git clone <repo-url> /srv/demotest
cd /srv/demotest
python3 -m venv venv
venv/bin/pip install -r requirements.txt

sudo cp deploy/.env.example /etc/demotest/demotest.env   # отредактируй значения
sudo chown root:www-data /etc/demotest/demotest.env
sudo chmod 640 /etc/demotest/demotest.env
```

## 3. Миграции и статика
Переменные подхватываем из того же env-файла:
```bash
set -a; source /etc/demotest/demotest.env; set +a
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
sudo chown -R www-data:www-data /srv/demotest/staticfiles
```

## 4. Сервис и nginx
```bash
sudo cp deploy/demotest.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now demotest

sudo cp deploy/nginx.conf /etc/nginx/sites-available/demotest
sudo ln -s /etc/nginx/sites-available/demotest /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d example.com -d www.example.com
```

## 5. Проверка
```bash
curl https://example.com/api/health/        # {"status":"ok"}
sudo journalctl -u demotest -f              # логи приложения
```

## Обновление
```bash
cd /srv/demotest && git pull
venv/bin/pip install -r requirements.txt
set -a; source /etc/demotest/demotest.env; set +a
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart demotest
```

## Бэкапы
```bash
sudo -u postgres pg_dump demotest | gzip > /var/backups/demotest-$(date +%F).sql.gz
```
Добавь это в cron.

## Чеклист безопасности
- `DEBUG=False`, уникальный `SECRET_KEY`, сильный пароль БД.
- Порт 5432 наружу не открывать (по умолчанию слушает только localhost).
- Файрвол: открыты только 22, 80, 443 (`sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable`).
- Фронтенд на `https://odil-star.github.io` должен ходить на `https://example.com/api/`.
