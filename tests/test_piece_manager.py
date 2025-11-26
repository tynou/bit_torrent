import unittest
from unittest.mock import MagicMock, patch, mock_open
import hashlib
import time
import os
from piece_manager import PieceManager, Piece, BLOCK_SIZE


class TestPiece(unittest.TestCase):
    def setUp(self):
        self.index = 0
        self.length = BLOCK_SIZE * 2
        self.data = b"a" * self.length
        self.hash_value = hashlib.sha1(self.data).digest()
        self.piece = Piece(self.index, self.length, self.hash_value)

    def test_add_block(self):
        block_data = b"a" * BLOCK_SIZE

        self.piece.add_block(0, block_data)
        self.assertTrue(self.piece.blocks[0])
        self.assertFalse(self.piece.blocks[1])
        self.assertEqual(self.piece.num_blocks_received, 1)

        self.assertEqual(self.piece.data[:BLOCK_SIZE], block_data)

    def test_is_complete(self):
        block_data = b"a" * BLOCK_SIZE
        self.piece.add_block(0, block_data)
        self.assertFalse(self.piece.is_complete())

        self.piece.add_block(BLOCK_SIZE, block_data)
        self.assertTrue(self.piece.is_complete())

    def test_is_hash_valid(self):
        self.piece.add_block(0, b"a" * BLOCK_SIZE)
        self.piece.add_block(BLOCK_SIZE, b"a" * BLOCK_SIZE)
        self.assertTrue(self.piece.is_hash_valid())

        bad_piece = Piece(0, self.length, self.hash_value)
        bad_piece.add_block(0, b"b" * BLOCK_SIZE)
        bad_piece.add_block(BLOCK_SIZE, b"b" * BLOCK_SIZE)
        self.assertFalse(bad_piece.is_hash_valid())

    def test_block_timeout_logic(self):
        self.piece.mark_block_requested(0)
        self.assertFalse(self.piece.is_block_available(0))

        with patch("time.time", return_value=time.time() + 10):
            self.assertTrue(self.piece.is_block_available(0))

            timed_out = self.piece.get_timed_out_blocks()
            self.assertEqual(timed_out, [0])


class TestPieceManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_torrent = MagicMock()
        self.mock_torrent.name = "test_torrent"
        self.mock_torrent.piece_length = 32768
        self.mock_torrent.num_pieces = 2
        self.mock_torrent.total_size = 65536

        self.mock_torrent.pieces_hashes = [b"1" * 20, b"2" * 20]

        self.mock_torrent.files = []

        self.dest = "/tmp/downloads"

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    @patch("builtins.open", new_callable=mock_open)
    def test_initialize_single_file(self, mock_file, mock_exists, mock_makedirs):
        pm = PieceManager(self.mock_torrent, self.dest)
        pm.initialize()

        expected_path = os.path.join(self.dest, "test_torrent")

        mock_file.assert_called_with(expected_path, "wb")
        mock_file().truncate.assert_called_with(65536)

        self.assertEqual(len(pm.files_info), 1)
        self.assertEqual(pm.files_info[0]["end"], 65536)

    def test_calculate_needed_pieces(self):
        self.mock_torrent.files = [
            {"path": ["f1.txt"], "length": 10000},
            {"path": ["f2.txt"], "length": 50000},
        ]
        self.mock_torrent.piece_length = 32768

        selection = [False, True]
        pm = PieceManager(self.mock_torrent, self.dest, selection)

        self.assertEqual(pm.missing_pieces, [0, 1])

    def test_get_next_request_new_piece(self):
        pm = PieceManager(self.mock_torrent, self.dest)
        mock_peer = MagicMock()

        res = pm.get_next_request(mock_peer, 5)

        self.assertIsNotNone(res)
        index, offset, length = res

        self.assertIn(index, [0, 1])
        self.assertEqual(offset, 0)
        self.assertEqual(length, BLOCK_SIZE)
        self.assertIn(index, pm.pending_pieces)

    def test_get_next_request_existing_piece(self):
        pm = PieceManager(self.mock_torrent, self.dest)
        pm.pending_pieces[0] = Piece(0, 32768, b"1" * 20)
        pm.pending_pieces[0].blocks[0] = False
        pm.pending_pieces[0].requested_blocks[0] = time.time()

        mock_peer = MagicMock()

        index, offset, _ = pm.get_next_request(mock_peer, 5)

        self.assertEqual(index, 0)
        self.assertEqual(offset, BLOCK_SIZE)

    @patch("builtins.open", new_callable=mock_open, read_data=b"DATA" * 4096)
    def test_read_block(self, mock_file):
        pm = PieceManager(self.mock_torrent, self.dest)
        pm.have_pieces[0] = True
        pm.files_info = [{"path": "dummy", "start": 0, "end": 65536}]

        data = pm.read_block(0, 0, 100)

        self.assertTrue(len(data) > 0)
        mock_file.assert_called_with("dummy", "rb")
        mock_file().seek.assert_called_with(0)

    @patch("piece_manager.Piece.is_hash_valid", return_value=True)
    @patch("piece_manager.PieceManager._write_piece_to_disk")
    async def test_block_received_valid(self, mock_write, mock_hash):
        pm = PieceManager(self.mock_torrent, self.dest)

        piece_len = BLOCK_SIZE
        pm.pending_pieces[0] = Piece(0, piece_len, b"1" * 20)

        data = b"x" * piece_len
        await pm.block_received_async(0, 0, data)

        self.assertTrue(pm.have_pieces[0])
        self.assertNotIn(0, pm.missing_pieces)
        self.assertNotIn(0, pm.pending_pieces)
        mock_write.assert_called_once()
        self.assertEqual(pm.total_downloaded, piece_len)

    @patch("piece_manager.Piece.is_hash_valid", return_value=False)
    async def test_block_received_invalid(self, mock_hash):
        pm = PieceManager(self.mock_torrent, self.dest)
        piece_len = BLOCK_SIZE
        pm.pending_pieces[0] = Piece(0, piece_len, b"1" * 20)

        data = b"x" * piece_len
        await pm.block_received_async(0, 0, data)

        self.assertFalse(pm.have_pieces[0])
        self.assertNotIn(0, pm.pending_pieces)
        self.assertIn(0, pm.missing_pieces)
        self.assertEqual(pm.total_downloaded, 0)


if __name__ == "__main__":
    unittest.main()
