import unittest
from unittest.mock import MagicMock, patch
import asyncio
from download import Download, DownloadStatus


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.mock_torrent = MagicMock()
        self.mock_torrent.num_pieces = 10
        self.mock_torrent.piece_length = 16384

        self.destination = "/tmp/test_download"
        self.file_selection = [True, False]

    @patch("download.PieceManager")
    def test_initialization(self, MockPieceManager):
        download = Download(self.mock_torrent, self.destination, self.file_selection)

        self.assertEqual(download.status, DownloadStatus.STARTING)

        self.assertEqual(download.destination, self.destination)
        self.assertEqual(download.file_selection, self.file_selection)

        self.assertEqual(download.speed, 0.0)
        self.assertEqual(download.last_downloaded, 0)
        self.assertIsNone(download.start_time)

        self.assertEqual(download.peers, [])
        self.assertIsInstance(download.peers_queue, asyncio.Queue)

        MockPieceManager.assert_called_once_with(
            self.mock_torrent, self.destination, self.file_selection
        )

    @patch("download.PieceManager")
    def test_create_task(self, MockPieceManager):
        download = Download(self.mock_torrent, self.destination, self.file_selection)

        mock_task = MagicMock(spec=asyncio.Task)

        download.create_task(mock_task)
        self.assertEqual(download.task, mock_task)

    @patch("download.PieceManager")
    def test_status_enum(self, MockPieceManager):
        download = Download(self.mock_torrent, self.destination, self.file_selection)

        download.status = DownloadStatus.DOWNLOADING
        self.assertEqual(download.status, DownloadStatus.DOWNLOADING)

        download.status = DownloadStatus.COMPLETED
        self.assertEqual(download.status, DownloadStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
