import aiohttp
import asyncio
import random
import struct
from urllib.parse import urlencode
from bencode import decode
from torrent import Torrent


class Tracker:
    def __init__(self, torrent: Torrent):
        self.torrent: Torrent = torrent
        self.peer_id: str = self._generate_peer_id()
        self.port: int = 6881  # Порт, который мы будем "слушать"

    def _generate_peer_id(self) -> str:
        # Генерируем уникальный ID для нашего клиента
        return "-PY0001-" + "".join(random.choice("0123456789") for _ in range(12))

    async def get_peers(self, downloaded, uploaded, left):
        params = {
            "info_hash": self.torrent.info_hash,
            "peer_id": self.peer_id.encode(),
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
        print(url)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        print(f"Ошибка трекера: {response.status}")
                        return []
                    data = await response.read()
                    tracker_response = decode(data)
                    print(tracker_response)
                    return self._parse_peers(tracker_response[b"peers"])
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Не удалось подключиться к трекеру: {e}")
            return []

    def _parse_peers(self, peers_blob: bytes):
        # Пиры в компактном режиме: 6 байт на пира (4 байта IP, 2 байта порт)
        peers = []
        for i in range(0, len(peers_blob), 6):
            ip_bytes = peers_blob[i : i + 4]
            port_bytes = peers_blob[i + 4 : i + 6]
            ip = ".".join(str(b) for b in ip_bytes)
            port = struct.unpack("!H", port_bytes)[0]
            peers.append((ip, port))
        return peers
