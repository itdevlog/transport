# Развёртывание «Маршрут»

Рабочий пример — хост `box-974106` (Debian 13), где сервис живёт рядом с
сайтом расписания `3125.ru`:

- `3125.ru` → Caddy → `127.0.0.1:8090` (raspisanie-edge)
- `ekb.3125.ru` → Caddy → `127.0.0.1:8091` (**этот сервис**)

Caddy терминирует TLS (Let's Encrypt, http-01) и проксирует на сервис,
слушающий только `127.0.0.1`.

## 1. Пользователь и код

```bash
useradd --system --no-create-home --shell /usr/sbin/nologin marshrut
git clone https://github.com/itdevlog/transport.git /opt/transport
chown -R marshrut:marshrut /opt/transport
git config --global --add safe.directory /opt/transport
```

## 2. Локальная конфигурация

`config.json` в репозитории трогать не нужно. Настройки конкретной машины —
в `config.local.json` (он в `.gitignore`, `git pull` его не затирает):

```bash
printf '{ "host": "127.0.0.1", "port": 8091 }\n' > /opt/transport/config.local.json
chown marshrut:marshrut /opt/transport/config.local.json
```

Проверка слияния: `cd /opt/transport && python3 -c "import server; print(server.load_config())"`.

## 3. systemd

`systemd/marshrut.service` уже содержит нужный hardening
(`ProtectSystem=strict`, `ProtectHome=yes`, `ReadWritePaths=/opt/transport` —
без последнего не запишутся ни `favorites.json`, ни `catalog.db`). База
зеркала по умолчанию создаётся рядом с `server.py`, т.е. в `/opt/transport`.

```bash
cp /opt/transport/systemd/marshrut.service /etc/systemd/system/marshrut.service
systemctl daemon-reload
systemctl enable --now marshrut
systemctl status marshrut --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8091/
```

## 4. Caddy

Добавить site-блок в `/etc/caddy/Caddyfile` (сначала сделать бэкап):

```
ekb.3125.ru {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8091
	log {
		output file /var/log/caddy/ekb-access.log
	}
}
```

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%s)
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

### Грабли

`caddy validate`, запущенный от `root`, создаёт файл лога с владельцем
`root:root` и режимом `0600`. Тогда `reload` падает с `permission denied`,
потому что caddy не может открыть лог. Лечится удалением файла — Caddy
пересоздаст его сам с нужным владельцем:

```bash
rm -f /var/log/caddy/ekb-access.log
systemctl reload caddy
```

Сертификат Let's Encrypt для домена выпускается автоматически при первом
запросе (нужна A-запись на этот хост и доступность порта 80).

## 5. Проверка

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://ekb.3125.ru/
curl -s -o /dev/null -w "%{http_code}\n" https://ekb.3125.ru/app.js
curl -s https://ekb.3125.ru/api/forecast?sid=5830 | head -c 200
curl -s https://ekb.3125.ru/api/catalog
```

`/api/catalog` покажет состояние зеркала (`building`, `lastRefresh`, `counts`).
Пока идёт первое фоновое наполнение, справочные запросы отвечает портал;
при желании форсировать — `curl -s -X POST https://ekb.3125.ru/api/refresh`.

## Обновление

```bash
git -C /opt/transport pull
systemctl restart marshrut
```

Конфликтов по `config.local.json` и `favorites.json` не будет — они
не отслеживаются git.
