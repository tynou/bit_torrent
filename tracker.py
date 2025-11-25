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
        self.port: int = 6881
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()

    def _generate_peer_id(self) -> str:
        return "-PY0001-" + "".join(random.choice("0123456789") for _ in range(12))

    async def _get_peers_from_tracker(self, tracker_url, downloaded, uploaded, left):
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

        url = tracker_url + ("&" if "?" in tracker_url else "?") + urlencode(params)

        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    print(f"Ошибка трекера {tracker_url}: {response.status}")
                    return None, None
                data = await response.read()
                tracker_response = decode(data)

                if b"failure reason" in tracker_response:
                    print(
                        f"Ошибка от трекера {tracker_url}: {tracker_response[b'failure reason'].decode()}"
                    )
                    return None, None

                peers = self._parse_peers(tracker_response.get(b"peers", b""))
                interval = tracker_response.get(b"interval", 120)
                return peers, interval
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            print(f"Не удалось подключиться к трекеру {tracker_url}: {e}")
            return None, None

    async def get_peers(self, downloaded, uploaded, left):
        all_peers = set()
        min_interval = 60

        tasks = [
            self._get_peers_from_tracker(url, downloaded, uploaded, left)
            for url in self.torrent.trackers
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception) or result is None:
                continue

            peers, interval = result
            if peers is not None:
                all_peers.update(peers)
            if interval is not None:
                min_interval = min(min_interval, interval)

        return list(all_peers), min_interval

    def _parse_peers(self, peers_blob: bytes):
        peers = []
        for i in range(0, len(peers_blob), 6):
            try:
                ip_bytes = peers_blob[i : i + 4]
                port_bytes = peers_blob[i + 4 : i + 6]
                if len(port_bytes) < 2:
                    continue
                ip = socket.inet_ntoa(ip_bytes)
                port = struct.unpack("!H", port_bytes)[0]
                peers.append((ip, port))
            except (struct.error, IndexError):
                continue
        return peers

    async def close(self):
        if not self.session.closed:
            await self.session.close()
