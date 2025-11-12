import asyncio
import sys
import os
from torrent import Torrent
from peer import PeerConnection
from download import Download, DownloadStatus

MAX_PEERS = 40


class TorrentClient:
    def __init__(self) -> None:
        self.downloads: list[Download] = []

    async def add_torrent(self, torrent_path: str, destination_path: str) -> None:
        download = Download(
            Torrent(torrent_path), destination_path, DownloadStatus.STARTING
        )

        self.downloads.append(download)
        print(f"Добавлен торрент: {download.torrent.name}")
        download.create_task(asyncio.create_task(self._start_download(download)))

    async def _start_download(self, download: Download):
        piece_manager = download.piece_manager
        tracker = download.tracker

        while not piece_manager.is_complete():
            print(1)
            peers_list = await tracker.get_peers(
                piece_manager.total_downloaded, 0, piece_manager.torrent.total_size
            )
            print(peers_list)
            # sys.exit(1)

            print(f"[{download.torrent.name}] Найдено {len(peers_list)} пиров.")

            tasks = []
            for ip, port in peers_list:
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

            await asyncio.sleep(60)  # Пауза перед повторным запросом к трекеру

        print(f"\n[{download.torrent.name}] ЗАГРУЗКА ЗАВЕРШЕНА!")
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
                progress = pm.get_progress()
                bar_length = 20
                filled_len = int(round(bar_length * progress / 100))
                bar = "█" * filled_len + "-" * (bar_length - filled_len)

                downloaded_mb = pm.total_downloaded / (1024 * 1024)
                total_mb = download.torrent.total_size / (1024 * 1024)

                print(f"{i + 1}. {download.torrent.name[:40]:<40}")
                print(f"   [{bar}] {progress:.2f}%")
                print(f"   Скачано: {downloaded_mb:.2f} / {total_mb:.2f} MB")
                print(f"   Статус: {download.status}, Пиры: {len(download.peers)}")
                print("-" * 50)

            all_complete = all(d.piece_manager.is_complete() for d in self.downloads)
            if self.downloads and all_complete:
                print("Все загрузки завершены!")
                break

            await asyncio.sleep(2)


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
