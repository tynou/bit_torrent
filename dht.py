#!/usr/bin/env python3
import asyncio
import socket
import os
import struct
from collections import deque
from bencode import encode, decode

BOOTSTRAP_NODES = [
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.bittorrent.com", 6881),
]


class DHTClient:
    """
    A simplified BitTorrent DHT client that runs in the background.
    """

    def __init__(self, port=6881):
        self.port = port
        self.node_id = os.urandom(20)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.pending_requests = {}
        self.routing_table = deque(maxlen=200)
        self.listener_task = None
        self.running = False

    async def start(self):
        """Binds the socket and starts the listening loop."""
        if self.running:
            return
        try:
            self.socket.bind(("0.0.0.0", self.port))
            print(f"DHT client listening on port {self.port}...")
        except OSError as e:
            print(f"Error binding to port {self.port}: {e}. Trying a random port...")
            self.socket.bind(("0.0.0.0", 0))
            self.port = self.socket.getsockname()[1]
            print(f"DHT client listening on port {self.port}...")

        loop = asyncio.get_running_loop()
        self.listener_task = loop.create_task(self._listen_for_responses())
        self.running = True
        print("DHT client started.")

    def stop(self):
        """Stops the client and cleans up."""
        if self.listener_task:
            self.listener_task.cancel()
        self.socket.close()
        self.running = False
        print("DHT client stopped.")

    def _generate_tid(self):
        return os.urandom(2)

    def _parse_nodes(self, nodes_bytes):
        nodes = []
        for i in range(0, len(nodes_bytes), 26):
            try:
                node_id = nodes_bytes[i : i + 20]
                ip_bytes = nodes_bytes[i + 20 : i + 24]
                port_bytes = nodes_bytes[i + 24 : i + 26]
                ip = socket.inet_ntoa(ip_bytes)
                port = struct.unpack("!H", port_bytes)[0]
                nodes.append({"id": node_id, "ip": ip, "port": port})
            except (struct.error, IndexError):
                continue
        return nodes

    def _parse_peers(self, peers_list):
        peers = set()
        for peer_bytes in peers_list:
            if len(peer_bytes) == 6:
                try:
                    ip = socket.inet_ntoa(peer_bytes[:4])
                    port = struct.unpack("!H", peer_bytes[4:])[0]
                    # Простая проверка на валидность порта
                    if port > 0 and port < 65536:
                        peers.add((ip, port))
                except struct.error:
                    continue
        return peers

    async def _send_query(self, address, query_type, args):
        tid = self._generate_tid()
        message = {"t": tid, "y": "q", "q": query_type, "a": args}
        bencoded_message = encode(message)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[tid] = future

        try:
            self.socket.sendto(bencoded_message, address)
            return await asyncio.wait_for(future, timeout=4.0)
        except asyncio.TimeoutError:
            return None
        except OSError:
            return None
        finally:
            self.pending_requests.pop(tid, None)

    async def _listen_for_responses(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.socket, 1024)
                message = decode(data)

                if message.get(b"y") == b"r":
                    tid = message.get(b"t")
                    if tid in self.pending_requests:
                        future = self.pending_requests[tid]
                        if not future.done():
                            future.set_result(message.get(b"r"))
            except (KeyError, ValueError, IndexError):
                continue
            except Exception:
                continue

    async def bootstrap(self):
        """Populates the routing table by contacting bootstrap nodes."""
        print("Bootstrapping into the DHT network...")
        tasks = [
            self.find_node((host, port), self.node_id) for host, port in BOOTSTRAP_NODES
        ]
        await asyncio.gather(*tasks)

        if not self.routing_table:
            print("Failed to bootstrap. No nodes found.")
            return False

        print(f"Bootstrap complete. Found {len(self.routing_table)} initial nodes.")
        return True

    async def find_node(self, address, target_id):
        args = {"id": self.node_id, "target": target_id}
        response = await self._send_query(address, "find_node", args)
        if response and b"nodes" in response:
            nodes = self._parse_nodes(response[b"nodes"])
            for node in nodes:
                if node not in self.routing_table:
                    self.routing_table.append(node)

    async def get_peers(self, address, info_hash):
        args = {"id": self.node_id, "info_hash": info_hash}
        response = await self._send_query(address, "get_peers", args)

        if not response:
            return None

        if b"values" in response:
            peers_list = response[b"values"]
            return self._parse_peers(peers_list)
        if b"nodes" in response:
            return self._parse_nodes(response[b"nodes"])
        return None

    async def find_peers_for_infohash(
        self, info_hash: bytes, peers_queue: asyncio.Queue
    ):
        """
        Continuously searches for peers for a given infohash and puts them in a queue.
        """
        if not self.running:
            print("Error: DHT client is not running. Call start() first.")
            return

        if not self.routing_table:
            if not await self.bootstrap():
                return

        nodes_to_query = deque(self.routing_table.copy())
        queried_nodes: set[tuple[str, int]] = set()

        print(f"Starting continuous DHT peer search for infohash: {info_hash.hex()}")

        while True:  # Бесконечный цикл поиска
            if not nodes_to_query:
                # Если список для опроса пуст, пополняем его из таблицы маршрутизации
                nodes_to_query.extend(self.routing_table)
                # Очищаем множество уже опрошенных, чтобы периодически переспрашивать
                queried_nodes.clear()
                # Небольшая пауза перед новым кругом
                await asyncio.sleep(5)

            max_requests = 100

            tasks = []
            for _ in range(min(max_requests, len(nodes_to_query))):
                node = nodes_to_query.popleft()
                node_addr_tuple = (node["ip"], node["port"])

                if node_addr_tuple in queried_nodes:
                    continue

                queried_nodes.add(node_addr_tuple)
                tasks.append(self.get_peers(node_addr_tuple, info_hash))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue

                if isinstance(result, set):  # Нашли пиров
                    for peer in result:
                        await peers_queue.put(peer)
                elif isinstance(result, list):  # Нашли новые узлы
                    for node in result:
                        if (node["ip"], node["port"]) not in queried_nodes:
                            nodes_to_query.append(node)

            # Небольшая задержка, чтобы не перегружать сеть
            await asyncio.sleep(1)
