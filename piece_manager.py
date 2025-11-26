import os
import math
import hashlib
import time
import random
import asyncio
from torrent import Torrent

BLOCK_SIZE = 2**14
REQUEST_TIMEOUT = 2


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
            end = start + len(block_data)
            if end > self.length:
                return
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
        return self.num_blocks_received == len(self.blocks)

    def is_hash_valid(self):
        return hashlib.sha1(self.data).digest() == self.hash_value


class PieceManager:
    def __init__(
        self, torrent: Torrent, destination: str, file_selection: list[bool] = None
    ):
        self.torrent: Torrent = torrent
        self.destination: str = destination

        if file_selection is None:
            self.file_selection = (
                [True] * len(torrent.files) if torrent.files else [True]
            )
        else:
            self.file_selection = file_selection

        self.have_pieces: list = [False] * torrent.num_pieces
        self.pending_pieces: dict[int, Piece] = {}
        self.total_downloaded: int = 0

        self.missing_pieces = self._calculate_needed_pieces()
        self.total_pieces_to_download = len(self.missing_pieces)
        self.total_selected_size = self._calculate_total_selected_size()
        self.files_info = []

    def _calculate_total_selected_size(self) -> int:
        total_size = 0
        if self.torrent.files:
            for i, file_info in enumerate(self.torrent.files):
                if self.file_selection[i]:
                    total_size += file_info["length"]
        elif self.file_selection and self.file_selection[0]:
            total_size = self.torrent.total_size
        return total_size

    def _calculate_needed_pieces(self):
        needed_pieces = set()
        piece_length = self.torrent.piece_length
        current_offset = 0

        if not self.torrent.files:
            if self.file_selection and self.file_selection[0]:
                return list(range(self.torrent.num_pieces))
            else:
                return []

        for i, file_info in enumerate(self.torrent.files):
            length = file_info["length"]
            start_byte = current_offset
            end_byte = current_offset + length

            if self.file_selection[i]:
                start_piece = start_byte // piece_length
                end_piece = (end_byte - 1) // piece_length
                for p in range(start_piece, end_piece + 1):
                    needed_pieces.add(p)

            current_offset += length

        return sorted(list(needed_pieces))

    def initialize(self):
        if self.torrent.files:
            base_dir = os.path.join(self.destination, self.torrent.name)
            os.makedirs(base_dir, exist_ok=True)
            current_offset = 0

            for i, file_info in enumerate(self.torrent.files):
                if self.file_selection[i]:
                    path_parts = [base_dir] + file_info["path"]
                    file_path = os.path.join(*path_parts)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)

                    if not os.path.exists(file_path):
                        with open(file_path, "wb") as f:
                            f.truncate(file_info["length"])

                    self.files_info.append(
                        {
                            "path": file_path,
                            "start": current_offset,
                            "end": current_offset + file_info["length"],
                        }
                    )
                current_offset += file_info["length"]
        else:
            if self.file_selection and self.file_selection[0]:
                file_path = os.path.join(self.destination, self.torrent.name)
                if not os.path.exists(file_path):
                    with open(file_path, "wb") as f:
                        f.truncate(self.torrent.total_size)
                self.files_info = [
                    {"path": file_path, "start": 0, "end": self.torrent.total_size}
                ]

    def get_next_request(self, peer, max_pending_global_pieces):
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

            for block_index in range(len(piece.blocks)):
                if piece.is_block_available(block_index):
                    piece_length = self._get_piece_length(piece_index)
                    offset = block_index * BLOCK_SIZE
                    length = min(BLOCK_SIZE, piece_length - offset)
                    piece.mark_block_requested(block_index)
                    return (piece_index, offset, length)

        if len(self.pending_pieces) >= max_pending_global_pieces:
            return None

        if not self.missing_pieces:
            return None

        piece_index = random.choice(self.missing_pieces)

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

    def read_block(self, piece_index: int, offset: int, length: int) -> bytes:
        if not self.have_pieces[piece_index]:
            return b""

        global_offset = piece_index * self.torrent.piece_length + offset
        result_data = bytearray()
        bytes_to_read = length
        current_pos = global_offset

        while bytes_to_read > 0:
            file_info = self._get_file_for_offset(current_pos)
            if not file_info:
                break

            file_start = file_info["start"]
            file_end = file_info["end"]
            file_path = file_info["path"]

            pos_in_file = current_pos - file_start
            chunk_size = min(bytes_to_read, file_end - current_pos)

            try:
                with open(file_path, "rb") as f:
                    f.seek(pos_in_file)
                    chunk = f.read(chunk_size)
                    result_data.extend(chunk)
            except IOError:
                return b""

            current_pos += len(chunk)
            bytes_to_read -= len(chunk)

        return bytes(result_data)

    async def block_received_async(self, piece_index: int, offset: int, data: bytes):
        if piece_index not in self.pending_pieces:
            return False

        piece = self.pending_pieces[piece_index]
        block_index = offset // BLOCK_SIZE

        if block_index < len(piece.blocks) and not piece.blocks[block_index]:
            piece.add_block(offset, data)
            self.total_downloaded += len(data)

            if piece.is_complete():
                loop = asyncio.get_running_loop()

                is_valid = await loop.run_in_executor(None, piece.is_hash_valid)

                if is_valid:
                    await loop.run_in_executor(None, self._write_piece_to_disk, piece)
                    self.have_pieces[piece_index] = True
                    if piece_index in self.missing_pieces:
                        self.missing_pieces.remove(piece_index)
                    if piece_index in self.pending_pieces:
                        del self.pending_pieces[piece_index]
                else:
                    print(f"Hash error piece {piece_index}, re-downloading")
                    self.total_downloaded -= piece.length
                    if piece_index in self.pending_pieces:
                        del self.pending_pieces[piece_index]
        return True

    def _write_piece_to_disk(self, piece: Piece):
        piece_offset = piece.index * self.torrent.piece_length
        data_ptr = 0

        while data_ptr < piece.length:
            file_info = self._get_file_for_offset(piece_offset)

            if file_info:
                file_path = file_info["path"]
                file_start = file_info["start"]
                file_end = file_info["end"]

                write_pos_in_file = piece_offset - file_start

                bytes_to_write = min(piece.length - data_ptr, file_end - piece_offset)

                try:
                    mode = "r+b" if os.path.exists(file_path) else "wb"
                    with open(file_path, mode) as f:
                        f.seek(write_pos_in_file)
                        f.write(piece.data[data_ptr : data_ptr + bytes_to_write])
                except IOError as e:
                    print(f"Disk write error for {file_path}: {e}")

                data_ptr += bytes_to_write
                piece_offset += bytes_to_write
            else:
                next_file_start = float("inf")
                for f in self.files_info:
                    if f["start"] > piece_offset:
                        next_file_start = f["start"]
                        break

                bytes_to_skip = min(
                    piece.length - data_ptr, next_file_start - piece_offset
                )

                data_ptr += bytes_to_skip
                piece_offset += bytes_to_skip

    def _get_file_for_offset(self, global_offset):
        for f in self.files_info:
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
