import aiohttp
import asyncio
import random
import struct
import socket
from urllib.parse import urlencode
from bencode import decode
from torrent import Torrent


class Tracker:
    def __init__(self, torrent: Torrent):
        self.torrent: Torrent = torrent
        self.peer_id: str = self._generate_peer_id()
        self.port: int = 6881  # Порт, который мы будем "слушать"
        self.session = aiohttp.ClientSession()

    def _generate_peer_id(self) -> str:
        # Генерируем уникальный ID для нашего клиента
        return "-PY0001-" + "".join(random.choice("0123456789") for _ in range(12))

    async def get_peers(self, downloaded, uploaded, left):
        params = {
            "info_hash": self.torrent.info_hash,
            "peer_id": self.peer_id,
            "port": self.port,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "left": left,
            "compact": 1,
            "event": "started",
        }

        url = (
            self.torrent.announce
            + ("&" if "?" in self.torrent.announce else "?")
            + urlencode(params)
        )
        print(f"Запрос к трекеру: {self.torrent.announce}")

        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    print(f"Ошибка трекера: {response.status}")
                    return [], 60
                data = await response.read()
                tracker_response = decode(data)
                print(tracker_response)
                peers = self._parse_peers(tracker_response.get(b"peers", b""))
                interval = tracker_response.get(b"interval", 60)

                return peers, interval
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Не удалось подключиться к трекеру: {e}")
            return [], 60

    def _parse_peers(self, peers_blob: bytes):
        # Пиры в компактном режиме: 6 байт на пира (4 байта IP, 2 байта порт)
        peers = []
        for i in range(0, len(peers_blob), 6):
            ip_bytes = peers_blob[i : i + 4]
            port_bytes = peers_blob[i + 4 : i + 6]
            ip = socket.inet_ntoa(ip_bytes)
            port = struct.unpack("!H", port_bytes)[0]
            peers.append((ip, port))
        return peers

    async def close(self):
        """Не забываем закрывать сессию при завершении работы клиента."""
        if not self.session.closed:
            await self.session.close()
