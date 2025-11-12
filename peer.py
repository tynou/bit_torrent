import asyncio
import struct
import math
from piece_manager import PieceManager
from torrent import Torrent


MAX_PENDING_REQUESTS = 20


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
        self.is_choking = True  # Пир "душит" нас (не дает качать)
        self.is_interested = False  # Мы заинтересованы в пире

        self.pending_requests = 0

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), timeout=10
            )
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            print(f"Не удалось подключиться к {self.ip}:{self.port}: {e}")
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
            await self.send_bitfield()
            return True
        except (asyncio.TimeoutError, ConnectionResetError, ValueError) as e:
            print(f"Ошибка рукопожатия с {self.ip}:{self.port}: {e}")
            await self.close()
            return False

    async def send_bitfield(self):
        # bitfield - это последовательность байт, где каждый бит представляет кусок
        # Сначала создаем байтовый массив, заполненный нулями
        bitfield_len = math.ceil(self.torrent.num_pieces / 8)
        bitfield = bytearray(bitfield_len)

        # Устанавливаем биты для тех кусков, которые у нас есть
        for i, have in enumerate(self.piece_manager.have_pieces):
            if have:
                byte_index = i // 8
                bit_index = i % 8
                bitfield[byte_index] |= 1 << (7 - bit_index)

        # Формируем и отправляем сообщение (длина, ID=5, payload)
        msg = struct.pack(f">Ib{bitfield_len}s", 1 + bitfield_len, 5, bytes(bitfield))
        self.writer.write(msg)
        await self.writer.drain()
        print(f"Отправлен bitfield пиру {self}")

    async def send_interested(self):
        msg = struct.pack(">Ib", 1, 2)  # length=1, id=2 (interested)
        self.writer.write(msg)
        await self.writer.drain()
        self.is_interested = True

    async def request_piece(self, piece_index, offset, length):
        # print(f"Пир {self.random_id} хочет {piece_index}[{offset}:{offset + length}]")
        msg = struct.pack(">IbIII", 13, 6, piece_index, offset, length)
        self.writer.write(msg)
        await self.writer.drain()

    async def _send_requests(self):
        """Пытается заполнить конвейер запросов до MAX_PENDING_REQUESTS."""
        while not self.is_choking and self.pending_requests < MAX_PENDING_REQUESTS:
            request = self.piece_manager.get_next_request(self)
            if not request:
                # Больше нет доступных блоков для запроса
                break

            piece_index, offset, length = request
            # print(f"{self} запрашивает {piece_index}[{offset}:{offset+length}]")
            await self.request_piece(piece_index, offset, length)
            self.pending_requests += 1

    async def message_loop(self):
        # Сразу после unchoke'а пытаемся отправить пачку запросов
        await self._send_requests()

        while True:
            try:
                msg_len_data = await asyncio.wait_for(
                    self.reader.readexactly(4), timeout=120
                )
                msg_len = struct.unpack(">I", msg_len_data)[0]

                if msg_len == 0:  # keep-alive
                    continue

                msg_data = await self.reader.readexactly(msg_len)
                msg_id = msg_data[0]

                if msg_id == 0:  # Choke
                    self.is_choking = True
                    # Когда нас "душат", все наши запросы отменяются пиром.
                    # Сбросим счетчик, хотя PieceManager обработает таймауты.
                    self.pending_requests = 0
                elif msg_id == 1:  # Unchoke
                    self.is_choking = False
                    # Пир нас "раздушил", немедленно отправляем новые запросы
                    await self._send_requests()
                elif msg_id == 5:  # Bitfield (добавим обработку на будущее)
                    # Пока просто логируем, но в будущем здесь нужно будет парсить
                    # и сохранять, какие куски есть у пира
                    print(f"Получен bitfield от {self}")
                elif msg_id == 7:  # Piece
                    self.pending_requests -= 1
                    index, begin = struct.unpack(">II", msg_data[1:9])
                    block_data = msg_data[9:]
                    self.piece_manager.block_received(index, begin, block_data)
                    # print(f"{self} получил блок от части {index}")

                    # Как только получили блок, конвейер освободился.
                    # Пытаемся отправить еще запросы.
                    await self._send_requests()

            except (
                asyncio.IncompleteReadError,
                ConnectionResetError,
                asyncio.TimeoutError,
            ) as e:
                print(f"Соединение с {self} потеряно: {e}")
                await self.close()
                break
            except Exception as e:
                print(f"Неожиданная ошибка с пиром {self}: {e}")
                await self.close()
                break

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
