# Walle Player: сервер библиотеки на Linux

Домашний сервер (мини-ПК) без окна программы: раздаёт библиотеку телефонам и веб-плееру, держит туннель на свой VPS,
каждые 30 минут подхватывает новые и переехавшие файлы и раз в сутки делает резервную копию базы. Служба
`walle-server` (systemd) запускается при загрузке, входить в систему не нужно.

Собирается на Windows: `python scripts/build_linux_server.py` → `dist/linux/walle-server-<версия>.tar.gz`.

## Установка

Debian 12 или Ubuntu 22.04+ (Python 3.11+), без рабочего стола достаточно.

```bash
tar xf walle-server-<версия>.tar.gz
cd walle-server-<версия>
sudo MUSIC_DIR=/mnt/media/Music/mp3 ./install.sh
```

Программа ставится в `/opt/walle-server`, данные лежат в `~/.local/share/WallePlayer` пользователя, запустившего
`sudo`. Обновление: тот же `sudo ./install.sh` из нового архива (данные не трогаются).

- Журнал: `journalctl -u walle-server -f` и `~/.local/share/WallePlayer/server.log`.
- Остановить / запустить: `sudo systemctl stop|start walle-server`.
- Порт 8780 (TCP — сервер, UDP — поиск на телефонах); при включённом ufw установщик открывает его сам.
- Настроение новых треков по умолчанию здесь не определяется: анализ звука тяжёл для слабого домашнего сервера,
  это делает программа на мощном компьютере. Включить на сервере: `sudo WITH_AUTOTAG=1 ./install.sh`
  (для модели с признаками CLAP ещё `WITH_CLAP=1` — torch, около 1 ГБ).

## Переезд с Windows (данные и библиотека сохраняются)

1. **На Windows, пока она ещё стоит:** закрыть Walle Player (трей → «Выход») и скопировать папку данных
   `%LOCALAPPDATA%\WallePlayer` на внешний диск, например в `D:\WallePlayer-data`. Нужны `library.db` (вместе с
   `library.db-wal` / `-shm`, если они есть), `settings.json`, `remote.json`, `relay_key*`, `relay_known_hosts`,
   `covers`, `models`; старые `backups` — по желанию.
2. **Поставить Linux.** Внешний диск с музыкой (NTFS) подключить постоянно: узнать UUID (`sudo blkid`) и добавить
   в `/etc/fstab` строку
   `UUID=<uuid>  /mnt/media  ntfs3  uid=<ваш логин>,gid=<ваш логин>,nofail  0  0`, затем `sudo mount -a`.
3. **Установить сервер с данными:**
   ```bash
   sudo IMPORT_DIR=/mnt/media/WallePlayer-data MUSIC_DIR=/mnt/media/Music/mp3 ./install.sh
   ```
   Первое сканирование узнаёт треки на новом месте по исполнителю, альбому, имени и размеру файла (пути вида
   `D:\Music\mp3\…` из Windows разбираются): метки, лайки и прослушивания остаются при своих треках.
4. Слушатели, их ключи и туннель на VPS переезжают вместе с `remote.json` и `relay_key`: телефоны продолжают
   работать через VPS сразу, дома находят сервер по UDP. Держать туннель может только один компьютер — на
   остальных раздачу выключить.
