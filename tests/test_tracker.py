import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import struct
import socket
from tracker import Tracker
from bencode import encode


class TestTracker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_torrent = MagicMock()
        self.mock_torrent.info_hash = b"12345678901234567890"
        self.mock_torrent.total_size = 1000
        self.mock_torrent.trackers = ["http://tracker.test/announce"]

        self.session_patcher = patch("tracker.aiohttp.ClientSession")
        self.MockSessionClass = self.session_patcher.start()

        self.mock_session = AsyncMock()
        self.MockSessionClass.return_value = self.mock_session

        self.tracker = Tracker(self.mock_torrent)

    def tearDown(self):
        self.session_patcher.stop()

    async def asyncTearDown(self):
        await self.tracker.close()

    def test_initialization(self):
        self.assertTrue(self.tracker.peer_id.startswith("-PY0001-"))
        self.assertEqual(len(self.tracker.peer_id), 20)
        self.assertEqual(self.tracker.port, 6881)

    def test_parse_peers(self):
        ip_bytes = socket.inet_aton("192.168.0.1")
        port_bytes = struct.pack("!H", 6881)
        peers_blob = ip_bytes + port_bytes + b"\x00\x01"

        peers = self.tracker._parse_peers(peers_blob)

        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0], ("192.168.0.1", 6881))

    async def test_get_peers_failure_reason(self):
        response_data = {b"failure reason": b"Invalid info_hash"}
        encoded_response = encode(response_data)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read.return_value = encoded_response
        self.mock_session.get.return_value.__aenter__.return_value = mock_response

        peers, _ = await self.tracker.get_peers(0, 0, 1000)

        self.assertEqual(peers, [])

    async def test_get_peers_http_error(self):
        mock_response = AsyncMock()
        mock_response.status = 404
        self.mock_session.get.return_value.__aenter__.return_value = mock_response

        peers, _ = await self.tracker.get_peers(0, 0, 1000)

        self.assertEqual(peers, [])

    async def test_multiple_trackers_aggregation(self):
        self.mock_torrent.trackers = ["http://t1.com", "http://t2.com"]

        with patch.object(self.tracker, "_get_peers_from_tracker") as mock_single:
            mock_single.side_effect = [
                ([("127.0.0.1", 8080)], 100),
                ([("127.0.0.2", 8081)], 50),
            ]

            peers, interval = await self.tracker.get_peers(0, 0, 1000)

            self.assertEqual(len(peers), 2)
            self.assertEqual(interval, 60)


if __name__ == "__main__":
    unittest.main()
