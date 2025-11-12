import os
import math
import hashlib
import time
import random
from torrent import Torrent

BLOCK_SIZE = 2**14  # 16 KB - стандартный размер блока для запроса
REQUEST_TIMEOUT = 5


class Piece:
    def __init__(self, index: int, length: int, hash_value):
        self.index = index
        self.length = length
        self.hash_value = hash_value
        self.blocks = [False] * math.ceil(length / BLOCK_SIZE)
        self.requested_blocks = [0] * math.ceil(length / BLOCK_SIZE)
        self.data = bytearray(length)
        self.num_blocks_received = 0

    def add_block(self, offset: int, block_data: bytes):
        block_index = offset // BLOCK_SIZE
        if not self.blocks[block_index]:
            self.blocks[block_index] = True
            self.requested_blocks[block_index] = 0
            start = offset % self.length
            self.data[start : start + len(block_data)] = block_data
            self.num_blocks_received += 1

    def mark_block_requested(self, block_index: int):
        self.requested_blocks[block_index] = int(time.time())

    def is_block_available(self, block_index: int):
        if self.blocks[block_index]:
            return False

        requested_time = self.requested_blocks[block_index]
        if requested_time == 0 or time.time() - requested_time > REQUEST_TIMEOUT:
            return True

        return False

    def get_timed_out_blocks(self):
        timed_out_blocks = []
        current_time = time.time()

        for block_index, requested_time in enumerate(self.requested_blocks):
            if (
                requested_time != 0
                and not self.blocks[block_index]
                and current_time - requested_time > REQUEST_TIMEOUT
            ):
                timed_out_blocks.append(block_index)

        return timed_out_blocks

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
        # Создаем временный список недостающих частей и перемешиваем его.
        # Это обеспечит случайный выбор части, что гораздо эффективнее
        # последовательного выбора.
        shuffled_missing_pieces = self.missing_pieces[:]
        random.shuffle(shuffled_missing_pieces)

        # Сначала проверяем части, которые уже начали качать (вдруг там таймаут)
        for piece_index in list(self.pending_pieces.keys()):
            piece = self.pending_pieces[piece_index]
            timed_out_blocks = piece.get_timed_out_blocks()

            if timed_out_blocks:
                # print(f"BLOCK TIMED OUT for piece {piece_index}")
                block_index = timed_out_blocks[0]
                piece_length = self._get_piece_length(piece_index)
                offset = block_index * BLOCK_SIZE
                length = min(BLOCK_SIZE, piece_length - offset)
                piece.mark_block_requested(block_index)
                return (piece_index, offset, length)

        # Теперь ищем новые части для загрузки в случайном порядке
        for piece_index in shuffled_missing_pieces:
            piece_length = self._get_piece_length(piece_index)
            if piece_index not in self.pending_pieces:
                self.pending_pieces[piece_index] = Piece(
                    piece_index, piece_length, self.torrent.pieces_hashes[piece_index]
                )

            piece = self.pending_pieces[piece_index]

            for block_index in range(len(piece.blocks)):
                if piece.is_block_available(block_index):
                    offset = block_index * BLOCK_SIZE
                    length = min(BLOCK_SIZE, piece_length - offset)
                    piece.mark_block_requested(block_index)
                    return (piece_index, offset, length)
        return None

    def block_received(self, piece_index: int, offset: int, data: bytes):
        if piece_index in self.pending_pieces:
            piece = self.pending_pieces[piece_index]

            # Проверяем, не получен ли уже этот блок
            block_index = offset // BLOCK_SIZE
            if not piece.blocks[block_index]:
                piece.add_block(offset, data)
                self.total_downloaded += len(data)  # Считаем только новые блоки

                if piece.is_complete():
                    if piece.is_hash_valid():
                        self._write_piece_to_disk(piece)
                        self.have_pieces[piece_index] = True
                        self.missing_pieces.remove(piece_index)
                        del self.pending_pieces[piece_index]
                        print(f"\nЧасть {piece_index} успешно скачана и проверена.")
                    else:
                        print(
                            f"\nОшибка хэша для части {piece_index}. Повторная загрузка."
                        )
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
