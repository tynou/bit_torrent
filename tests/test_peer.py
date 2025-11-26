import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import struct
from peer import PeerConnection


class TestPeerConnection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_torrent = MagicMock()
        self.mock_torrent.num_pieces = 10
        self.mock_torrent.info_hash = b"1" * 20
        self.mock_torrent.piece_length = 16384

        self.mock_pm = MagicMock()
        self.mock_pm.have_pieces = [False] * 10
        self.mock_pm.pending_pieces = []
        self.mock_pm.block_received_async = AsyncMock()

        self.ip = "127.0.0.1"
        self.port = 6881
        self.my_peer_id = "-PY0001-123456789012"

        self.peer = PeerConnection(
            self.mock_torrent,
            self.mock_pm,
            self.ip,
            self.port,
            self.my_peer_id,
            self.mock_torrent.info_hash,
        )

        self.mock_reader = AsyncMock()
        self.mock_writer = MagicMock()
        self.peer.reader = self.mock_reader
        self.peer.writer = self.mock_writer

    async def test_connect_success(self):
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = (self.mock_reader, self.mock_writer)

            result = await self.peer.connect()

            self.assertTrue(result)
            mock_open.assert_called_with(self.ip, self.port)
            self.assertEqual(self.peer.reader, self.mock_reader)
            self.assertEqual(self.peer.writer, self.mock_writer)

    async def test_connect_failure(self):
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            result = await self.peer.connect()
            self.assertFalse(result)

    async def test_message_loop_unchoke(self):
        self.mock_reader.readexactly.side_effect = [
            struct.pack(">I", 1),
            b"\x01",
            ConnectionResetError,
        ]

        await self.peer.message_loop()

        self.assertFalse(self.peer.is_choking)

    async def test_message_loop_piece(self):
        index = 0
        begin = 0
        block_data = b"\xff" * 10
        msg_len = 9 + len(block_data)

        msg_body = struct.pack(">BII", 7, index, begin) + block_data

        self.mock_reader.readexactly.side_effect = [
            struct.pack(">I", msg_len),
            msg_body,
            asyncio.IncompleteReadError(b"", None),
        ]

        self.peer.pending_requests = 1

        await self.peer.message_loop()

        self.mock_pm.block_received_async.assert_called_with(index, begin, block_data)
        self.assertEqual(self.peer.pending_requests, 0)

    async def test_message_loop_incoming_request(self):
        self.mock_pm.have_pieces[0] = True
        self.peer.am_choking = False

        msg_body = struct.pack(">BIII", 6, 0, 0, 100)
        msg_len = len(msg_body)

        self.mock_reader.readexactly.side_effect = [
            struct.pack(">I", msg_len),
            msg_body,
            ConnectionResetError,
        ]

        self.mock_pm.read_block.return_value = b"DATA" * 25

        await self.peer.message_loop()

        self.mock_pm.read_block.assert_called_with(0, 0, 100)

        self.mock_writer.write.assert_called()
        args, _ = self.mock_writer.write.call_args
        self.assertEqual(args[0][4], 7)


if __name__ == "__main__":
    unittest.main()
