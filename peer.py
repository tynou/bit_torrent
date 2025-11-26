import asyncio
import struct
import math
from piece_manager import PieceManager
from torrent import Torrent

MAX_PENDING_REQUESTS = 100
MAX_PENDING_PIECES = 50


class PeerConnection:
    def __init__(
        self,
        torrent: Torrent,
        piece_manager: PieceManager,
        ip: str,
        port: int,
        peer_id: str,
        info_hash: bytes,
    ):
        self.torrent = torrent
        self.piece_manager = piece_manager
        self.ip = ip
        self.port = port
        self.my_peer_id = peer_id
        self.info_hash = info_hash
        self.reader = None
        self.writer = None
        self.remote_peer_id = None
        self.is_choking = True
        self.is_interested = False

        self.peer_interested = False
        self.am_choking = True

        self.pending_requests = 0

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port),
                timeout=5,
            )
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return False

    async def perform_handshake(self):
        handshake_msg = struct.pack(
            ">B19s8s20s20s",
            19,
            b"BitTorrent protocol",
            b"\x00" * 8,
            self.info_hash,
            self.my_peer_id.encode(),
        )
        self.writer.write(handshake_msg)
        await self.writer.drain()

        try:
            response = await asyncio.wait_for(self.reader.readexactly(68), timeout=5)
            _, _, _, info_hash, peer_id = struct.unpack(">B19s8s20s20s", response)
            if info_hash != self.info_hash:
                raise ValueError("Info hash не совпадает")
            self.remote_peer_id = peer_id
            await self.send_bitfield()
            return True
        except (asyncio.TimeoutError, ConnectionResetError, ValueError):
            await self.close()
            return False

    async def send_bitfield(self):
        bitfield_len = math.ceil(self.torrent.num_pieces / 8)
        bitfield = bytearray(bitfield_len)
        for i, have in enumerate(self.piece_manager.have_pieces):
            if have:
                byte_index = i // 8
                bit_index = i % 8
                bitfield[byte_index] |= 1 << (7 - bit_index)
        msg = struct.pack(f">Ib{bitfield_len}s", 1 + bitfield_len, 5, bytes(bitfield))
        self.writer.write(msg)
        await self.writer.drain()

    async def send_interested(self):
        msg = struct.pack(">Ib", 1, 2)
        self.writer.write(msg)
        await self.writer.drain()
        self.is_interested = True

    async def send_unchoke(self):
        if self.am_choking:
            msg = struct.pack(">Ib", 1, 1)
            self.writer.write(msg)
            await self.writer.drain()
            self.am_choking = False

    async def send_piece(self, index, begin, block_data):
        msg_len = 9 + len(block_data)
        msg = struct.pack(
            f">IbII{len(block_data)}s", msg_len, 7, index, begin, block_data
        )
        self.writer.write(msg)
        await self.writer.drain()

    async def request_piece(self, piece_index, offset, length):
        msg = struct.pack(">IbIII", 13, 6, piece_index, offset, length)
        self.writer.write(msg)
        await self.writer.drain()

    async def _send_requests(self):
        if len(self.piece_manager.pending_pieces) >= MAX_PENDING_PIECES:
            pass

        while not self.is_choking and self.pending_requests < MAX_PENDING_REQUESTS:
            request = self.piece_manager.get_next_request(self, MAX_PENDING_PIECES)
            if not request:
                break

            piece_index, offset, length = request
            await self.request_piece(piece_index, offset, length)
            self.pending_requests += 1

    async def message_loop(self):
        await self._send_requests()

        while True:
            try:
                msg_len_data = await self.reader.readexactly(4)
                msg_len = struct.unpack(">I", msg_len_data)[0]

                if msg_len == 0:  # Keep-alive
                    continue

                msg_data = await self.reader.readexactly(msg_len)
                msg_id = msg_data[0]

                if msg_id == 0:  # Choke
                    self.is_choking = True
                    self.pending_requests = 0
                elif msg_id == 1:  # Unchoke
                    self.is_choking = False
                    await self._send_requests()
                elif msg_id == 2:  # Interested
                    self.peer_interested = True
                    await self.send_unchoke()
                elif msg_id == 3:  # Not Interested
                    self.peer_interested = False
                elif msg_id == 6:  # Request
                    if len(msg_data) >= 13:
                        index, begin, length = struct.unpack(">III", msg_data[1:13])
                        if (
                            self.piece_manager.have_pieces[index]
                            and not self.am_choking
                        ):
                            if length > 2**17:
                                await self.close()
                                break

                            loop = asyncio.get_running_loop()
                            block_data = await loop.run_in_executor(
                                None,
                                self.piece_manager.read_block,
                                index,
                                begin,
                                length,
                            )
                            if block_data:
                                await self.send_piece(index, begin, block_data)

                elif msg_id == 7:  # Piece
                    self.pending_requests -= 1
                    index, begin = struct.unpack(">II", msg_data[1:9])
                    block_data = msg_data[9:]

                    await self.piece_manager.block_received_async(
                        index, begin, block_data
                    )

                    await self._send_requests()

            except (
                asyncio.IncompleteReadError,
                ConnectionResetError,
                asyncio.TimeoutError,
                OSError,
            ):
                await self.close()
                break
            except Exception as e:
                print(f"Peer error: {e}")
                await self.close()
                break

    async def close(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
