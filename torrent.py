import hashlib
from bencode import decode, encode


class Torrent:
    def __init__(self, filename: str):
        with open(filename, "rb") as f:
            meta_info = decode(f.read())

        self.info = meta_info[b"info"]
        self.announce = meta_info[b"announce"].decode("utf-8")

        self.meta = meta_info

        # Инфо-хэш - это SHA1 хэш bencoded-словаря 'info'
        self.info_hash = hashlib.sha1(encode(self.info)).digest()

        self.piece_length = self.info[b"piece length"]
        self.pieces_hashes = self._split_pieces_hashes(self.info[b"pieces"])
        self.name = self.info[b"name"].decode("utf-8")

        if b"files" in self.info:
            # Торрент с несколькими файлами
            self.files = [
                {
                    "path": [p.decode("utf-8") for p in f[b"path"]],
                    "length": f[b"length"],
                }
                for f in self.info[b"files"]
            ]
            self.total_size = sum(f["length"] for f in self.files)
        else:
            # Торрент с одним файлом
            self.files = []
            self.total_size = self.info[b"length"]

        self.num_pieces = len(self.pieces_hashes)

    def _split_pieces_hashes(self, pieces_blob: bytes):
        # Хэши кусков хранятся как конкатенация 20-байтных SHA1 хэшей
        return [pieces_blob[i : i + 20] for i in range(0, len(pieces_blob), 20)]

    def __str__(self):
        return f"Torrent: {self.name}, Size: {self.total_size / 1024**2:.2f} MB"
