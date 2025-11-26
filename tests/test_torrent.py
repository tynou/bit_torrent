import unittest
from unittest.mock import patch, mock_open
import hashlib
from torrent import Torrent
from bencode import encode


class TestTorrent(unittest.TestCase):
    def setUp(self):
        self.base_info = {
            b"name": b"test_torrent",
            b"piece length": 16384,
            b"pieces": b"\xaa" * 20 + b"\xbb" * 20,
        }

    def test_single_file_torrent(self):
        info = self.base_info.copy()
        info[b"length"] = 102400

        meta_info = {b"announce": b"http://tracker.example.com", b"info": info}

        file_content = encode(meta_info)

        expected_info_hash = hashlib.sha1(encode(info)).digest()

        with patch("builtins.open", mock_open(read_data=file_content)):
            torrent = Torrent("dummy.torrent")

            self.assertEqual(torrent.announce, "http://tracker.example.com")
            self.assertEqual(torrent.name, "test_torrent")
            self.assertEqual(torrent.total_size, 102400)
            self.assertEqual(torrent.piece_length, 16384)
            self.assertEqual(torrent.info_hash, expected_info_hash)

            self.assertEqual(len(torrent.pieces_hashes), 2)
            self.assertEqual(torrent.pieces_hashes[0], b"\xaa" * 20)
            self.assertEqual(torrent.pieces_hashes[1], b"\xbb" * 20)

            self.assertEqual(torrent.files, [])

    def test_multi_file_torrent(self):
        info = self.base_info.copy()
        info[b"files"] = [
            {b"length": 1000, b"path": [b"folder", b"file1.txt"]},
            {b"length": 2000, b"path": [b"file2.txt"]},
        ]

        meta_info = {b"announce": b"http://tracker.example.com", b"info": info}

        file_content = encode(meta_info)

        with patch("builtins.open", mock_open(read_data=file_content)):
            torrent = Torrent("dummy.torrent")

            self.assertEqual(torrent.total_size, 3000)

            self.assertEqual(len(torrent.files), 2)

            self.assertEqual(torrent.files[0]["length"], 1000)
            self.assertEqual(torrent.files[0]["path"], ["folder", "file1.txt"])

            self.assertEqual(torrent.files[1]["length"], 2000)
            self.assertEqual(torrent.files[1]["path"], ["file2.txt"])

    def test_announce_list_handling(self):
        info = self.base_info.copy()
        info[b"length"] = 100

        meta_info = {
            b"announce": b"http://primary.com",
            b"announce-list": [
                [b"http://tracker1.com"],
                [b"udp://bad-tracker.com"],
                [b"http://tracker2.com", b"http://tracker1.com"],
            ],
            b"info": info,
        }

        file_content = encode(meta_info)

        with patch("builtins.open", mock_open(read_data=file_content)):
            torrent = Torrent("dummy.torrent")

            expected_trackers = sorted(["http://tracker1.com", "http://tracker2.com"])

            self.assertEqual(torrent.trackers, expected_trackers)

    def test_num_pieces_calculation(self):
        info = self.base_info.copy()
        info[b"pieces"] = b"1" * 20 + b"2" * 20 + b"3" * 20
        info[b"length"] = 100

        meta_info = {b"announce": b"url", b"info": info}

        with patch("builtins.open", mock_open(read_data=encode(meta_info))):
            torrent = Torrent("t.torrent")
            self.assertEqual(torrent.num_pieces, 3)
            self.assertEqual(len(torrent.pieces_hashes), 3)


if __name__ == "__main__":
    unittest.main()
