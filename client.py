import asyncio
import sys
import os
import time
from torrent import Torrent
from peer import PeerConnection
from download import Download, DownloadStatus
from dht import DHTClient  # Импортируем DHT клиент

MAX_PEERS = 50  # Немного увеличим лимит


def format_time(seconds: float) -> str:
    """Форматирует секунды в строку ЧЧ:ММ:СС."""
    if seconds < 0:
        return "00:00:00"
    s = int(seconds)
    hours, remainder = divmod(s, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class TorrentClient:
    def __init__(self) -> None:
        self.downloads: list[Download] = []
        self.dht = DHTClient()  # Создаем один DHT клиент для всего приложения
        self.running = True

    async def start_dht(self):
        await self.dht.start()

    def stop(self):
        self.running = False
        self.dht.stop()
        for download in self.downloads:
            if download.task and not download.task.done():
                download.task.cancel()

    async def add_torrent(self, torrent_path: str, destination_path: str) -> None:
        try:
            download = Download(
                Torrent(torrent_path), destination_path, DownloadStatus.STARTING
            )
        except Exception as e:
            print(f"Ошибка при добавлении торрента {torrent_path}: {e}")
            return

        download.start_time = time.time()
        self.downloads.append(download)
        print(f"Добавлен торрент: {download.torrent.name}")

        # Запускаем задачу скачивания
        download.create_task(asyncio.create_task(self._start_download(download)))
        # Запускаем задачу поиска пиров через DHT для этой загрузки
        asyncio.create_task(
            self.dht.find_peers_for_infohash(
                download.torrent.info_hash, download.peers_queue
            )
        )

    async def _start_download(self, download: Download):
        download.status = DownloadStatus.DOWNLOADING
        piece_manager = download.piece_manager
        tracker = download.tracker

        last_tracker_announce = 0.0
        tracker_interval = 60

        while not piece_manager.is_complete():
            current_time = time.time()

            # 1. Получаем пиров с трекера, если пришло время
            if current_time - last_tracker_announce > tracker_interval:
                tracker_peers, new_interval = await tracker.get_peers(
                    piece_manager.total_downloaded, 0, piece_manager.torrent.total_size
                )
                tracker_interval = new_interval
                last_tracker_announce = current_time
                print(
                    f"[{download.torrent.name}] Получено {len(tracker_peers)} пиров с трекера."
                )
                for peer in tracker_peers:
                    await download.peers_queue.put(peer)

            # 2. Пытаемся получить пиров из очереди DHT (с таймаутом, чтобы не блокировать)
            new_peers_to_connect = []
            while not download.peers_queue.empty():
                try:
                    peer = download.peers_queue.get_nowait()
                    new_peers_to_connect.append(peer)
                except asyncio.QueueEmpty:
                    break

            # 3. Подключаемся к новым пирам
            if new_peers_to_connect:
                # print(f"[{download.torrent.name}] Найдено {len(new_peers_to_connect)} новых пиров (DHT/Tracker).")
                tasks = []
                for ip, port in new_peers_to_connect:
                    # Проверяем, что мы еще не подключены к такому пиру
                    if f"{ip}:{port}" in [f"{p.ip}:{p.port}" for p in download.peers]:
                        continue

                    if len(download.peers) >= MAX_PEERS:
                        break

                    peer = PeerConnection(
                        download.torrent,
                        piece_manager,
                        ip,
                        port,
                        tracker.peer_id,
                        tracker.torrent.info_hash,
                    )
                    tasks.append(asyncio.create_task(self._manage_peer(peer, download)))

            await asyncio.sleep(1)  # Небольшая пауза, чтобы не загружать CPU

        print(f"\n[{download.torrent.name}] ЗАГРУЗКА ЗАВЕРШЕНА!")
        download.end_time = time.time()
        download.status = DownloadStatus.COMPLETED  # Меняем статус на COMPLETED
        # В режиме раздачи (seeding) мы должны отвечать на запросы пиров.
        # Эта логика не реализована, поэтому просто завершаем.

    async def _manage_peer(self, peer: PeerConnection, download: Download):
        try:
            if not await peer.connect():
                return
            if not await peer.perform_handshake():
                return

            download.peers.append(peer)
            await peer.send_interested()
            await peer.message_loop()
        finally:
            # Удаляем пира из списка активных после отключения
            if peer in download.peers:
                download.peers.remove(peer)

    async def run(self):
        """Основной цикл для отображения статуса."""
        while self.running:
            os.system("cls" if os.name == "nt" else "clear")
            print("--- Python BitTorrent Client ---")
            if not self.downloads:
                print("Нет активных загрузок. Добавьте .torrent файл.")

            for i, download in enumerate(self.downloads):
                pm = download.piece_manager
                bar_length = 20

                # Расчет скорости
                now = time.time()
                time_delta = now - download.last_speed_check_time
                if time_delta > 1:  # Обновляем скорость не чаще раза в секунду
                    data_delta = pm.total_downloaded - download.last_downloaded
                    download.speed = data_delta / time_delta
                    download.last_downloaded = pm.total_downloaded
                    download.last_speed_check_time = now

                downloaded_mb = pm.total_downloaded / (1024 * 1024)
                total_mb = download.torrent.total_size / (1024 * 1024)
                speed_mbps = download.speed / (1024 * 1024)

                progress = 0
                if download.torrent.total_size > 0:
                    progress = pm.total_downloaded / download.torrent.total_size

                filled_len = int(round(bar_length * progress))
                bar = "█" * filled_len + "-" * (bar_length - filled_len)

                elapsed_time_str = "00:00:00"
                if download.start_time:
                    if download.end_time:
                        elapsed_seconds = download.end_time - download.start_time
                    else:
                        elapsed_seconds = time.time() - download.start_time
                    elapsed_time_str = format_time(elapsed_seconds)

                print(f"{i + 1}. {download.torrent.name}")
                print(f"   [{bar}] {progress * 100:.2f}%")
                print(f"   Скачано: {downloaded_mb:.2f} / {total_mb:.2f} MB")
                print(f"   Скорость: {speed_mbps:.2f} MB/s")
                print(f"   Статус: {download.status.name}")
                print(f"   Пиры: {len(download.peers)}")
                print(f"   Прошло: {elapsed_time_str}")
                print("-" * 50)

            all_complete = all(
                d.status == DownloadStatus.COMPLETED for d in self.downloads
            )
            if self.downloads and all_complete:
                print("Все загрузки завершены!")
                self.stop()  # Останавливаем клиент после завершения всех загрузок
                break

            await asyncio.sleep(1)


async def main():
    if len(sys.argv) < 3:
        print(
            "Использование: python client.py <путь_к_торрент_файлу> <папка_назначения>"
        )
        print("Пример: python client.py ubuntu.torrent ./downloads")
        return

    client = TorrentClient()

    torrent_path = sys.argv[1]
    destination_path = sys.argv[2]

    if not os.path.exists(torrent_path):
        print(f"Ошибка: .torrent файл не найден по пути {torrent_path}")
        return

    os.makedirs(destination_path, exist_ok=True)

    try:
        await client.start_dht()  # Сначала запускаем DHT
        run_task = asyncio.create_task(client.run())
        await client.add_torrent(torrent_path, destination_path)
        # await client.add_torrent(
        #     "./torrents/ubuntu-25.10-desktop-amd64.iso.torrent", "./downloads"
        # )
        await run_task
    except asyncio.CancelledError:
        pass  # Ожидаемое исключение при остановке
    finally:
        client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nКлиент остановлен.")
