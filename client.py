import asyncio
import sys
import os
import time
from torrent import Torrent
from peer import PeerConnection
from download import Download, DownloadStatus

MAX_PEERS = 40


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

    async def add_torrent(self, torrent_path: str, destination_path: str) -> None:
        download = Download(
            Torrent(torrent_path), destination_path, DownloadStatus.STARTING
        )

        download.start_time = time.time()

        self.downloads.append(download)
        print(f"Добавлен торрент: {download.torrent.name}")
        download.create_task(asyncio.create_task(self._start_download(download)))

    async def _start_download(self, download: Download):
        download.status = DownloadStatus.DOWNLOADING

        piece_manager = download.piece_manager
        tracker = download.tracker

        tracker_interval = 60

        while not piece_manager.is_complete():
            peers_list, tracker_interval = await tracker.get_peers(
                piece_manager.total_downloaded, 0, piece_manager.torrent.total_size
            )
            print(peers_list)

            print(f"[{download.torrent.name}] Найдено {len(peers_list)} пиров.")

            tasks = []
            for ip, port in peers_list:
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
                print(peer)
                tasks.append(asyncio.create_task(self._manage_peer(peer, download)))

            await asyncio.sleep(tracker_interval)  # Пауза перед повторным запросом

        print(f"\n[{download.torrent.name}] ЗАГРУЗКА ЗАВЕРШЕНА!")
        download.end_time = time.time()
        download.status = DownloadStatus.SEEDING
        # В режиме раздачи (seeding) мы должны отвечать на запросы пиров.
        # Эта логика не реализована в PeerConnection, но можно добавить.

    async def _manage_peer(self, peer: PeerConnection, download: Download):
        if not await peer.connect():
            return
        if not await peer.perform_handshake():
            return

        download.peers.append(peer)
        await peer.send_interested()
        await peer.message_loop()

        # Удаляем пира из списка активных после отключения
        if peer in download.peers:
            download.peers.remove(peer)

    async def run(self):
        """Основной цикл для отображения статуса."""
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print("--- Python BitTorrent Client ---")
            if not self.downloads:
                print("Нет активных загрузок. Добавьте .torrent файл.")

            for i, download in enumerate(self.downloads):
                pm = download.piece_manager
                bar_length = 20

                downloaded_mb = pm.total_downloaded / (1024 * 1024)
                total_mb = download.torrent.total_size / (1024 * 1024)

                progress = pm.total_downloaded / download.torrent.total_size
                filled_len = int(round(bar_length * progress))
                bar = "█" * filled_len + "-" * (bar_length - filled_len)

                elapsed_time_str = "00:00:00"
                if download.start_time:
                    if download.end_time:
                        # Загрузка завершена, показываем общее время
                        elapsed_seconds = download.end_time - download.start_time
                    else:
                        # Загрузка в процессе, показываем текущее время
                        elapsed_seconds = time.time() - download.start_time
                    elapsed_time_str = format_time(elapsed_seconds)

                print(f"{i + 1}. {download.torrent.name[:40]:<40}")
                print(f"   [{bar}] {progress * 100:.2f}%")
                print(f"   Скачано: {downloaded_mb:.2f} / {total_mb:.2f} MB")
                print(f"   Статус: {download.status}")
                print(f"   Пиры: {len(download.peers)}")
                print(f"   Прошло: {elapsed_time_str}")
                print("-" * 50)

            all_complete = all(d.piece_manager.is_complete() for d in self.downloads)
            if self.downloads and all_complete:
                print("Все загрузки завершены!")
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

    # Можно добавить несколько торрентов
    torrent_path = sys.argv[1]
    destination_path = sys.argv[2]

    if not os.path.exists(torrent_path):
        print(f"Ошибка: .torrent файл не найден по пути {torrent_path}")
        return

    os.makedirs(destination_path, exist_ok=True)

    # Запускаем клиент и добавляем торрент
    run_task = asyncio.create_task(client.run())
    await client.add_torrent(torrent_path, destination_path)

    await run_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nКлиент остановлен.")
