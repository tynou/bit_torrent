import os
import math
import hashlib
import time
import random
from torrent import Torrent

BLOCK_SIZE = 2**14
REQUEST_TIMEOUT = 10


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
        if block_index < len(self.blocks) and not self.blocks[block_index]:
            self.blocks[block_index] = True
            self.requested_blocks[block_index] = 0
            start = offset % self.length
            # Защита от переполнения
            end = start + len(block_data)
            if end > self.length:
                return  # Invalid block data
            self.data[start:end] = block_data
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
    def __init__(
        self, torrent: Torrent, destination: str, file_selection: list[bool] = None
    ):
        self.torrent: Torrent = torrent
        self.destination: str = destination

        # Если выбор не передан, выбираем все
        if file_selection is None:
            self.file_selection = (
                [True] * len(torrent.files) if torrent.files else [True]
            )
        else:
            self.file_selection = file_selection

        self.have_pieces: list = [False] * torrent.num_pieces
        self.pending_pieces: dict[int, Piece] = {}
        self.total_downloaded: int = 0

        # Определяем, какие куски нам нужны, исходя из выбранных файлов
        self.missing_pieces = self._calculate_needed_pieces()
        self.total_pieces_to_download = len(self.missing_pieces)

        self._setup_files()

    def _calculate_needed_pieces(self):
        """Определяет индексы кусков, которые затрагивают выбранные файлы."""
        needed_pieces = set()
        piece_length = self.torrent.piece_length
        current_offset = 0

        # Если это single-file torrent
        if not self.torrent.files:
            return list(range(self.torrent.num_pieces))

        for i, file_info in enumerate(self.torrent.files):
            length = file_info["length"]
            start_byte = current_offset
            end_byte = current_offset + length

            # Если файл выбран
            if self.file_selection[i]:
                start_piece = start_byte // piece_length
                end_piece = (end_byte - 1) // piece_length
                for p in range(start_piece, end_piece + 1):
                    needed_pieces.add(p)

            current_offset += length

        return sorted(list(needed_pieces))

    def _setup_files(self):
        self.file_handles = []
        if self.torrent.files:
            base_dir = os.path.join(self.destination, self.torrent.name)
            os.makedirs(base_dir, exist_ok=True)
            current_offset = 0

            for i, file_info in enumerate(self.torrent.files):
                f = None

                if self.file_selection[i]:
                    path_parts = [base_dir] + file_info["path"]
                    file_path = os.path.join(*path_parts)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    f = open(file_path, "rb+" if os.path.exists(file_path) else "wb+")
                    f.truncate(file_info["length"])

                self.file_handles.append(
                    {
                        "handle": f,  # Может быть None
                        "start": current_offset,
                        "end": current_offset + file_info["length"],
                    }
                )
                current_offset += file_info["length"]
        else:
            file_path = os.path.join(self.destination, self.torrent.name)
            f = open(file_path, "rb+" if os.path.exists(file_path) else "wb+")
            f.truncate(self.torrent.total_size)
            self.file_handles = [
                {"handle": f, "start": 0, "end": self.torrent.total_size}
            ]

    def get_next_request(self, peer):
        # Логика та же, но self.missing_pieces теперь содержит только нужные нам куски
        shuffled_missing_pieces = self.missing_pieces[:]
        random.shuffle(shuffled_missing_pieces)

        for piece_index in list(self.pending_pieces.keys()):
            piece = self.pending_pieces[piece_index]
            timed_out_blocks = piece.get_timed_out_blocks()
            if timed_out_blocks:
                block_index = timed_out_blocks[0]
                piece_length = self._get_piece_length(piece_index)
                offset = block_index * BLOCK_SIZE
                length = min(BLOCK_SIZE, piece_length - offset)
                piece.mark_block_requested(block_index)
                return (piece_index, offset, length)

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
            block_index = offset // BLOCK_SIZE
            if not piece.blocks[block_index]:
                piece.add_block(offset, data)
                self.total_downloaded += len(data)
                if piece.is_complete():
                    if piece.is_hash_valid():
                        self._write_piece_to_disk(piece)
                        self.have_pieces[piece_index] = True
                        if piece_index in self.missing_pieces:
                            self.missing_pieces.remove(piece_index)
                        del self.pending_pieces[piece_index]
                    else:
                        print(f"Hash error piece {piece_index}")
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

            write_pos = piece_offset - file_start
            to_write = min(piece.length - data_ptr, file_end - piece_offset)

            if handle:
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

    def is_complete(self):
        return len(self.missing_pieces) == 0

    def close_files(self):
        for f in self.file_handles:
            if f["handle"]:
                f["handle"].close()
