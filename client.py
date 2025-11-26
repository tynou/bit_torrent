import asyncio
import time
from torrent import Torrent
from peer import PeerConnection
from tracker import Tracker
from download import Download, DownloadStatus

MAX_PEERS = 50


class TorrentClient:
    def __init__(self) -> None:
        self.downloads: list[Download] = []
        self.running = True

    def stop(self):
        self.running = False
        for download in self.downloads:
            self.stop_download(download)

    def stop_download(self, download: Download):
        if download.task and not download.task.done():
            download.task.cancel()

        async def close_peers():
            for peer in download.peers:
                await peer.close()
            download.peers.clear()

            if download.tracker:
                await download.tracker.close()

        asyncio.create_task(close_peers())

    async def add_torrent(
        self,
        torrent_path: str,
        destination_path: str,
        file_selection: list[bool],
    ) -> Download:
        try:
            loop = asyncio.get_running_loop()
            torrent = await loop.run_in_executor(None, Torrent, torrent_path)
            download = await loop.run_in_executor(
                None, lambda: Download(torrent, destination_path, file_selection)
            )
            await loop.run_in_executor(None, download.piece_manager.initialize)
            download.tracker = Tracker(download.torrent)
        except Exception as e:
            print(f"Error adding torrent: {e}")
            raise e

        download.start_time = time.time()
        self.downloads.append(download)
        await self.resume_torrent(download)
        return download

    async def pause_torrent(self, download: Download):
        if download.status == DownloadStatus.PAUSED:
            return

        print(f"Pausing {download.torrent.name}...")
        download.status = DownloadStatus.PAUSED
        download.speed = 0
        self.stop_download(download)

    async def resume_torrent(self, download: Download):
        if download.status in [DownloadStatus.DOWNLOADING, DownloadStatus.SEEDING]:
            return

        print(f"Resuming {download.torrent.name}...")
        download.status = DownloadStatus.STARTING
        download.create_task(asyncio.create_task(self._download_loop(download)))

    async def _download_loop(self, download: Download):
        piece_manager = download.piece_manager
        tracker = download.tracker

        if not tracker:
            return

        initial_check_complete = piece_manager.is_complete()

        last_tracker_announce = 0.0
        tracker_interval = 0

        completed_event_sent = initial_check_complete

        first_iteration = True

        try:
            while (
                download.status != DownloadStatus.PAUSED
                and download.status != DownloadStatus.ERROR
            ):
                current_time = time.time()

                if piece_manager.is_complete():
                    if not initial_check_complete:
                        if download.status == DownloadStatus.DOWNLOADING:
                            print(
                                f"[{download.torrent.name}] COMPLETED! Switching to SEEDING."
                            )
                            download.status = DownloadStatus.SEEDING
                            download.end_time = time.time()
                            download.speed = 0
                    else:
                        download.status = DownloadStatus.SEEDING
                elif download.status == DownloadStatus.STARTING:
                    download.status = DownloadStatus.DOWNLOADING

                if (
                    current_time - last_tracker_announce > tracker_interval
                    or first_iteration
                ):
                    needed_bytes = (
                        piece_manager.total_pieces_to_download
                        * download.torrent.piece_length
                    )
                    left = max(0, needed_bytes - piece_manager.total_downloaded)

                    event = ""
                    if first_iteration:
                        event = "started"
                    elif (
                        download.status == DownloadStatus.SEEDING
                        and not completed_event_sent
                    ):
                        event = "completed"
                        completed_event_sent = True

                    uploaded = 0

                    try:
                        tracker_peers, new_interval = await tracker.get_peers(
                            piece_manager.total_downloaded, uploaded, left, event
                        )

                        tracker_interval = new_interval if new_interval else 60
                        last_tracker_announce = current_time
                        first_iteration = False

                        if tracker_peers:
                            for peer in tracker_peers:
                                await download.peers_queue.put(peer)
                    except Exception as e:
                        print(f"Tracker error: {e}")
                        tracker_interval = 60

                while not download.peers_queue.empty():
                    if len(download.peers) >= MAX_PEERS:
                        break
                    try:
                        peer_info = download.peers_queue.get_nowait()
                        ip, port = peer_info

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

                if download.status == DownloadStatus.DOWNLOADING:
                    now = time.time()
                    time_delta = now - download.last_speed_check_time
                    if time_delta > 1:
                        data_delta = (
                            piece_manager.total_downloaded - download.last_downloaded
                        )
                        download.speed = data_delta / time_delta
                        download.last_downloaded = piece_manager.total_downloaded
                        download.last_speed_check_time = now
                else:
                    download.speed = 0

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            print(f"Task cancelled for {download.torrent.name}")
            return
        finally:
            pass

    async def _manage_peer(self, peer: PeerConnection, download: Download):
        try:
            if not await peer.connect():
                return
            if not await peer.perform_handshake():
                return

            await peer.send_interested()
            await peer.message_loop()
        except Exception:
            pass
        finally:
            if peer in download.peers:
                download.peers.remove(peer)
            await peer.close()
