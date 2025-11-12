import asyncio
import struct
from piece_manager import BLOCK_SIZE, PieceManager
from torrent import Torrent


class PeerConnection:
    def __init__(
        self,
        torrent: Torrent,
        piece_manager: PieceManager,
        ip: str,
        port: int,
        peer_id: str,
        info_hash: str,
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
        self.is_choking = True  # Пир "душит" нас (не дает качать)
        self.is_interested = False  # Мы заинтересованы в пире

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), timeout=10
            )
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            # print(f"Не удалось подключиться к {self.ip}:{self.port}: {e}")
            return False

    async def perform_handshake(self):
        handshake_msg = struct.pack(
            ">B19s8x20s20s",
            19,
            b"BitTorrent protocol",
            self.info_hash,
            self.my_peer_id.encode(),
        )
        self.writer.write(handshake_msg)
        await self.writer.drain()

        try:
            response = await asyncio.wait_for(self.reader.readexactly(68), timeout=10)
            print(struct.unpack(">B19s8x20s20s", response), self.info_hash)
            pstrlen, pstr, info_hash, peer_id = struct.unpack(">B19s8x20s20s", response)
            if info_hash != self.info_hash:
                raise ValueError("Info hash не совпадает")
            self.remote_peer_id = peer_id
            return True
        except (asyncio.TimeoutError, ConnectionResetError, ValueError) as e:
            # print(f"Ошибка рукопожатия с {self.ip}:{self.port}: {e}")
            await self.close()
            return False

    async def send_interested(self):
        msg = struct.pack(">Ib", 1, 2)  # length=1, id=2 (interested)
        self.writer.write(msg)
        await self.writer.drain()
        self.is_interested = True

    async def request_piece(self, piece_index, offset, length):
        msg = struct.pack(">IbIII", 13, 6, piece_index, offset, length)
        self.writer.write(msg)
        await self.writer.drain()

    async def message_loop(self):
        while True:
            try:
                # Читаем длину сообщения (4 байта)
                msg_len_data = await asyncio.wait_for(
                    self.reader.readexactly(4), timeout=120
                )
                msg_len = struct.unpack(">I", msg_len_data)[0]

                if msg_len == 0:  # keep-alive
                    continue

                # Читаем само сообщение
                msg_data = await self.reader.readexactly(msg_len)
                msg_id = msg_data[0]
                # print(msg_id)

                if msg_id == 0:  # Choke
                    self.is_choking = True
                elif msg_id == 1:  # Unchoke
                    self.is_choking = False
                elif msg_id == 7:  # Piece
                    index, begin = struct.unpack(">II", msg_data[1:9])
                    block_data = msg_data[9:]
                    self.piece_manager.block_received(index, begin, block_data)
                # print(msg_id, self.piece_manager.total_downloaded)

                # После unchoke, если мы заинтересованы, запрашиваем следующую часть
                if not self.is_choking and self.is_interested:
                    request = self.piece_manager.get_next_request(self)
                    # print("->", request)
                    if request:
                        await self.request_piece(*request)

            except (
                asyncio.IncompleteReadError,
                ConnectionResetError,
                asyncio.TimeoutError,
            ) as e:
                print(f"Соединение с {self.ip}:{self.port} потеряно: {e}")
                await self.close()
                break
            except Exception as e:
                print(f"Неожиданная ошибка с пиром {self.ip}:{self.port}: {e}")
                await self.close()
                break

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
