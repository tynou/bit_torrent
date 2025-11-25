import asyncio
import time
from torrent import Torrent
from peer import PeerConnection
from download import Download, DownloadStatus
from dht import DHTClient

MAX_PEERS = 50


class TorrentClient:
    def __init__(self) -> None:
        self.downloads: list[Download] = []
        self.dht = DHTClient()
        self.running = True

    async def start(self):
        await self.dht.start()

    def stop(self):
        self.running = False
        self.dht.stop()
        for download in self.downloads:
            self.stop_download(download)

    def stop_download(self, download: Download):
        """Останавливает задачи и отключает пиров для конкретной загрузки."""
        if download.task and not download.task.done():
            download.task.cancel()

        # Закрываем соединения с пирами
        async def close_peers():
            for peer in download.peers:
                await peer.close()
            download.peers.clear()

        # Запускаем закрытие в фоне, так как stop синхронный
        asyncio.create_task(close_peers())

    async def add_torrent(
        self,
        torrent_path: str,
        destination_path: str,
        file_selection: list[bool],
    ) -> Download:
        try:
            download = Download(Torrent(torrent_path), destination_path, file_selection)
        except Exception as e:
            print(f"Error adding torrent: {e}")
            raise e

        download.start_time = time.time()
        self.downloads.append(download)

        # Начинаем скачивание
        await self.resume_torrent(download)
        return download

    async def pause_torrent(self, download: Download):
        if download.status == DownloadStatus.PAUSED:
            return

        print(f"Pausing {download.torrent.name}...")
        download.status = DownloadStatus.PAUSED
        download.speed = 0
        self.stop_download(download)
        # Сохраняем файлы (flush buffers)
        # download.piece_manager.close_files() # Можно закрыть хендлы, если нужно

    async def resume_torrent(self, download: Download):
        if download.status == DownloadStatus.DOWNLOADING:
            return

        print(f"Resuming {download.torrent.name}...")
        download.status = DownloadStatus.STARTING  # Временно, пока ищет пиров

        # Пересоздаем задачу скачивания
        download.create_task(asyncio.create_task(self._download_loop(download)))

        # Запускаем поиск пиров
        asyncio.create_task(
            self.dht.find_peers_for_infohash(
                download.torrent.info_hash, download.peers_queue
            )
        )

    async def _download_loop(self, download: Download):
        """Основной цикл скачивания одной раздачи."""
        download.status = DownloadStatus.DOWNLOADING
        piece_manager = download.piece_manager
        tracker = download.tracker

        last_tracker_announce = 0.0
        tracker_interval = 60

        try:
            while (
                not piece_manager.is_complete()
                and download.status != DownloadStatus.PAUSED
            ):
                current_time = time.time()

                # 1. Трекер
                if current_time - last_tracker_announce > tracker_interval:
                    # Для расчета left берем только нужные куски
                    needed_bytes = (
                        piece_manager.total_pieces_to_download
                        * download.torrent.piece_length
                    )
                    left = max(0, needed_bytes - piece_manager.total_downloaded)

                    tracker_peers, new_interval = await tracker.get_peers(
                        piece_manager.total_downloaded, 0, left
                    )
                    tracker_interval = new_interval or 60
                    last_tracker_announce = current_time
                    if tracker_peers:
                        for peer in tracker_peers:
                            await download.peers_queue.put(peer)

                # 2. Обработка очереди пиров
                while not download.peers_queue.empty():
                    if len(download.peers) >= MAX_PEERS:
                        break
                    try:
                        peer_info = download.peers_queue.get_nowait()
                        ip, port = peer_info

                        # Проверка дубликатов
                        if any(p.ip == ip and p.port == port for p in download.peers):
                            continue

                        peer = PeerConnection(
                            download.torrent,
                            piece_manager,
                            ip,
                            port,
                            tracker.peer_id,
                            tracker.torrent.info_hash,
                        )
                        download.peers.append(peer)
                        asyncio.create_task(self._manage_peer(peer, download))
                    except asyncio.QueueEmpty:
                        break

                # Расчет скорости
                now = time.time()
                time_delta = now - download.last_speed_check_time
                if time_delta > 1:
                    data_delta = (
                        piece_manager.total_downloaded - download.last_downloaded
                    )
                    download.speed = data_delta / time_delta
                    download.last_downloaded = piece_manager.total_downloaded
                    download.last_speed_check_time = now

                await asyncio.sleep(1)

            if piece_manager.is_complete():
                print(f"[{download.torrent.name}] COMPLETED")
                download.status = DownloadStatus.COMPLETED
                download.end_time = time.time()
                download.speed = 0
                piece_manager.close_files()

        except asyncio.CancelledError:
            print(f"Task cancelled for {download.torrent.name}")
            return

    async def _manage_peer(self, peer: PeerConnection, download: Download):
        try:
            if not await peer.connect():
                return
            if not await peer.perform_handshake():
                return

            await peer.send_interested()
            await peer.message_loop()
        except Exception as e:
            print(f"Client error: {e}")
        finally:
            if peer in download.peers:
                download.peers.remove(peer)
            await peer.close()
