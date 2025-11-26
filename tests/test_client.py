import unittest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
import asyncio
import time
from client import TorrentClient
from download import DownloadStatus


class TestTorrentClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TorrentClient()

    async def asyncTearDown(self):
        self.client.stop()

    @patch("client.Torrent")
    @patch("client.Download")
    @patch("client.Tracker")
    async def test_add_torrent(self, MockTracker, MockDownload, MockTorrent):
        mock_download_instance = MockDownload.return_value
        mock_download_instance.piece_manager = MagicMock()

        t_path = "test.torrent"
        dest = "/tmp"
        selection = [True]

        download_obj = await self.client.add_torrent(t_path, dest, selection)

        MockTorrent.assert_called_with(t_path)
        MockDownload.assert_called_with(ANY, dest, selection)

        mock_download_instance.piece_manager.initialize.assert_called()

        self.assertIsNotNone(download_obj.tracker)

        self.assertIn(download_obj, self.client.downloads)

    async def test_stop_download(self):
        mock_download = MagicMock()

        mock_download.peers = []
        mock_download.tracker = AsyncMock()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_download.task = mock_task

        self.client.stop_download(mock_download)

        mock_task.cancel.assert_called_once()
        await asyncio.sleep(0)
        mock_download.tracker.close.assert_called_once()

    @patch("client.PeerConnection")
    async def test_download_loop_connect_peers(self, MockPeerConnection):
        mock_download = MagicMock()
        mock_download.status = DownloadStatus.STARTING
        mock_download.peers = []
        mock_download.peers_queue = asyncio.Queue()
        mock_download.last_speed_check_time = time.time()
        mock_download.last_downloaded = 0
        mock_download.speed = 0

        mock_download.torrent = MagicMock()
        mock_download.torrent.piece_length = 100
        mock_download.torrent.name = "TestTorrent"

        mock_pm = MagicMock()
        mock_pm.is_complete.return_value = False
        mock_pm.total_pieces_to_download = 10
        mock_pm.total_downloaded = 0
        mock_download.piece_manager = mock_pm

        mock_tracker = AsyncMock()
        mock_tracker.get_peers.return_value = ([("127.0.0.1", 8080)], 1800)
        mock_download.tracker = mock_tracker

        MockPeerConnection.return_value = AsyncMock()

        async def sleep_side_effect(seconds):
            if mock_download.status == DownloadStatus.DOWNLOADING:
                mock_download.status = DownloadStatus.PAUSED
            return

        with patch("asyncio.sleep", side_effect=sleep_side_effect):
            await self.client._download_loop(mock_download)

        mock_tracker.get_peers.assert_awaited()
        MockPeerConnection.assert_called_with(
            mock_download.torrent, mock_pm, "127.0.0.1", 8080, ANY, ANY
        )
        self.assertEqual(len(mock_download.peers), 1)


if __name__ == "__main__":
    unittest.main()
