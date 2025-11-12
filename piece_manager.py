import os
import math
import hashlib
from torrent import Torrent

BLOCK_SIZE = 2**14  # 16 KB - стандартный размер блока для запроса


class Piece:
    def __init__(self, index: int, length: int, hash_value):
        self.index = index
        self.length = length
        self.hash_value = hash_value
        self.blocks = [False] * math.ceil(length / BLOCK_SIZE)
        self.data = bytearray(length)
        self.num_blocks_received = 0

    def add_block(self, offset, block_data):
        block_index = offset // BLOCK_SIZE
        if not self.blocks[block_index]:
            self.blocks[block_index] = True
            start = offset % self.length
            self.data[start : start + len(block_data)] = block_data
            self.num_blocks_received += 1

    def is_complete(self):
        return all(self.blocks)

    def is_hash_valid(self):
        return hashlib.sha1(self.data).digest() == self.hash_value


class PieceManager:
    def __init__(self, torrent: Torrent, destination: str):
        self.torrent: Torrent = torrent
        self.destination: str = destination
        self.have_pieces: list = [False] * torrent.num_pieces
        self.pending_pieces: dict[int, Piece] = {}  # {piece_index: Piece object}
        self.missing_pieces: list = list(range(torrent.num_pieces))
        self.total_downloaded: int = 0
        self._setup_files()

    def _setup_files(self):
        if self.torrent.files:  # Multi-file torrent
            base_dir = os.path.join(self.destination, self.torrent.name)
            os.makedirs(base_dir, exist_ok=True)
            self.file_handles = []
            current_offset = 0
            for file_info in self.torrent.files:
                path_parts = [base_dir] + file_info["path"]
                file_path = os.path.join(*path_parts)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                f = open(file_path, "rb+" if os.path.exists(file_path) else "wb+")
                f.truncate(file_info["length"])  # Pre-allocate space

                self.file_handles.append(
                    {
                        "handle": f,
                        "start": current_offset,
                        "end": current_offset + file_info["length"],
                    }
                )
                current_offset += file_info["length"]
        else:  # Single-file torrent
            file_path = os.path.join(self.destination, self.torrent.name)
            f = open(file_path, "rb+" if os.path.exists(file_path) else "wb+")
            f.truncate(self.torrent.total_size)
            self.file_handles = [
                {"handle": f, "start": 0, "end": self.torrent.total_size}
            ]

    def get_next_request(self, peer):
        # Простая стратегия: запрашиваем первую доступную часть
        # print(
        #     self.torrent.num_pieces,
        #     len(self.pending_pieces.values()),
        #     self.total_downloaded,
        # )
        for piece_index in self.missing_pieces:
            # print(1)
            piece_length = self._get_piece_length(piece_index)
            if piece_index not in self.pending_pieces:
                # print(2)
                self.pending_pieces[piece_index] = Piece(
                    piece_index, piece_length, self.torrent.pieces_hashes[piece_index]
                )

            # Запрашиваем первый блок этой части
            for block_index, received in enumerate(
                self.pending_pieces[piece_index].blocks
            ):
                if not received:
                    offset = block_index * BLOCK_SIZE
                    length = min(BLOCK_SIZE, piece_length - offset)
                    return (piece_index, offset, length)

        # for piece_index in self.missing_pieces:
        #     if piece_index not in self.pending_pieces:
        #         piece_length = self._get_piece_length(piece_index)
        #         self.pending_pieces[piece_index] = Piece(
        #             piece_index, piece_length, self.torrent.pieces_hashes[piece_index]
        #         )
        return None

    def block_received(self, piece_index, offset, data):
        if piece_index in self.pending_pieces:
            piece = self.pending_pieces[piece_index]
            piece.add_block(offset, data)
            self.total_downloaded += len(data)

            if piece.is_complete():
                if piece.is_hash_valid():
                    self._write_piece_to_disk(piece)
                    self.have_pieces[piece_index] = True
                    self.missing_pieces.remove(piece_index)
                    del self.pending_pieces[piece_index]
                    print(f"\nЧасть {piece_index} успешно скачана и проверена.")
                else:
                    print(f"\nОшибка хэша для части {piece_index}. Повторная загрузка.")
                    del self.pending_pieces[piece_index]
            return True
        return False

    def _write_piece_to_disk(self, piece: Piece):
        piece_offset = piece.index * self.torrent.piece_length
        data_ptr = 0

        while data_ptr < piece.length:
            file_info = self._get_file_for_offset(piece_offset)
            if not file_info:
                break

            handle = file_info["handle"]
            file_start = file_info["start"]
            file_end = file_info["end"]

            # Считаем смещение внутри файла
            write_pos = piece_offset - file_start

            # Считаем, сколько данных можно записать в этот файл
            to_write = min(piece.length - data_ptr, file_end - piece_offset)

            handle.seek(write_pos)
            handle.write(piece.data[data_ptr : data_ptr + to_write])

            data_ptr += to_write
            piece_offset += to_write

    def _get_file_for_offset(self, global_offset):
        for f in self.file_handles:
            if f["start"] <= global_offset < f["end"]:
                return f
        return None

    def _get_piece_length(self, piece_index):
        if piece_index == self.torrent.num_pieces - 1:
            return (
                self.torrent.total_size % self.torrent.piece_length
                or self.torrent.piece_length
            )
        return self.torrent.piece_length

    def get_progress(self):
        return sum(self.have_pieces) / self.torrent.num_pieces * 100

    def is_complete(self):
        return all(self.have_pieces)

    def close_files(self):
        for f in self.file_handles:
            f["handle"].close()
