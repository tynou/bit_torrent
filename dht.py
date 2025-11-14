#!/usr/bin/env python3
import asyncio
import socket
import os
import struct
from collections import deque
from torrent import Torrent

from bencode import encode, decode

BOOTSTRAP_NODES = [
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.bittorrent.com", 6881),
]


class DHTClient:
    """
    A simplified BitTorrent DHT client.
    """

    def __init__(self, port=6881):
        self.port = port
        self.node_id = os.urandom(20)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.pending_requests = {}
        self.routing_table = deque(maxlen=200)  # Simple list for known nodes

    async def start(self):
        """Binds the socket and starts the listening loop."""
        try:
            self.socket.bind(("0.0.0.0", self.port))
            print(f"DHT client listening on port {self.port}...")
        except OSError as e:
            print(f"Error binding to port {self.port}: {e}")
            print("Trying a random port...")
            self.socket.bind(("0.0.0.0", 0))
            self.port = self.socket.getsockname()[1]
            print(f"DHT client listening on port {self.port}...")

        # The listener must run in the background to receive responses
        loop = asyncio.get_running_loop()
        self.listener_task = loop.create_task(self._listen_for_responses())

    def stop(self):
        """Stops the client and cleans up."""
        if hasattr(self, "listener_task"):
            self.listener_task.cancel()
        self.socket.close()
        print("DHT client stopped.")

    def _generate_tid(self):
        """Generate a unique 2-byte transaction ID."""
        return os.urandom(2)

    def _parse_nodes(self, nodes_bytes):
        """Parse the compact node info format."""
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
        """Parse the compact peer info format."""
        peers = set()
        for peer_bytes in peers_list:
            if len(peer_bytes) == 6:
                try:
                    ip = socket.inet_ntoa(peer_bytes[:4])
                    port = struct.unpack("!H", peer_bytes[4:])[0]
                    peers.add((ip, port))
                except struct.error:
                    continue
        return peers

    async def _send_query(self, address, query_type, args):
        """Sends a query to a given address and waits for a response."""
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
            # print(f"Timeout waiting for response from {address}")
            return None
        except OSError as e:
            # print(f"Socket error sending to {address}: {e}")
            return None
        finally:
            self.pending_requests.pop(tid, None)

    async def _listen_for_responses(self):
        """The main loop that listens for incoming UDP packets."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.socket, 1024)
                message = decode(data)

                # We only care about responses ('y'='r') for our queries
                if message.get(b"y") == b"r":
                    tid = message.get(b"t")
                    if tid in self.pending_requests:
                        future = self.pending_requests[tid]
                        if not future.done():
                            # The 'r' key contains the response data
                            future.set_result(message.get(b"r"))

                # A full client would also handle incoming queries ('y'='q')
                # and add the sender to its routing table.

            except KeyError:
                # Malformed packet, ignore
                continue
            except Exception as e:
                # print(f"Error in listener: {e}")
                continue

    async def bootstrap(self):
        """Populates the routing table by contacting bootstrap nodes."""
        print("Bootstrapping into the DHT network...")
        for host, port in BOOTSTRAP_NODES:
            try:
                # find_node with our own ID gets us nodes near us
                await self.find_node((host, port), self.node_id)
            except Exception as e:
                print(f"Bootstrap node {host}:{port} failed: {e}")

        if not self.routing_table:
            print("Failed to bootstrap. No nodes found. Exiting.")
            return False

        print(f"Bootstrap complete. Found {len(self.routing_table)} initial nodes.")
        return True

    async def find_node(self, address, target_id):
        """Sends a find_node query."""
        args = {"id": self.node_id, "target": target_id}
        response = await self._send_query(address, "find_node", args)
        if response and b"nodes" in response:
            nodes = self._parse_nodes(response[b"nodes"])
            for node in nodes:
                if node not in self.routing_table:
                    self.routing_table.append(node)

    async def get_peers(self, address, info_hash):
        """Sends a get_peers query and returns peers or closer nodes."""
        args = {"id": self.node_id, "info_hash": info_hash}
        response = await self._send_query(address, "get_peers", args)

        if not response:
            return None

        # If the node has peers, it returns a 'values' key
        if b"values" in response:
            # print(response)
            print(len(response[b"values"]))
            peers_list = response[b"values"]
            return self._parse_peers(peers_list)

        # Otherwise, it returns other nodes closer to the info_hash
        if b"nodes" in response:
            return self._parse_nodes(response[b"nodes"])

        return None

    async def find_peers_for_infohash(self, info_hash):
        """
        The main iterative process to find peers for a given infohash.
        """
        try:
            if len(info_hash) != 20:
                raise ValueError()
        except ValueError:
            print("Error: Invalid infohash. Must be a 40-character hex string.")
            return

        if not await self.bootstrap():
            return

        # Nodes we need to query
        nodes_to_query = deque(self.routing_table.copy())
        # Nodes we have already sent a query to
        queried_nodes = set()
        # Peers we have found
        found_peers = set()

        print(f"\nSearching for peers for infohash: {info_hash}")

        # Limit the number of concurrent requests
        CONCURRENT_REQUESTS = 10

        while nodes_to_query:
            tasks = []
            # Create a batch of tasks
            for _ in range(min(CONCURRENT_REQUESTS, len(nodes_to_query))):
                node = nodes_to_query.popleft()
                node_addr_tuple = (node["ip"], node["port"])

                if node_addr_tuple in queried_nodes:
                    continue

                queried_nodes.add(node_addr_tuple)
                tasks.append(self.get_peers(node_addr_tuple, info_hash))

            # Run the tasks concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue

                # Check if the result is a list of peers (set) or nodes (list of dicts)
                if isinstance(result, set):
                    new_peers = result - found_peers
                    if new_peers:
                        found_peers.update(new_peers)
                        print(
                            f"Found {len(new_peers)} new peers! Total: {len(found_peers)}"
                        )
                        print(found_peers)
                        # for peer in new_peers:
                        #     print(f"  - {peer[0]}:{peer[1]}")
                elif isinstance(result, list):
                    # It's a list of new nodes to query
                    for node in result:
                        if (node["ip"], node["port"]) not in queried_nodes:
                            nodes_to_query.append(node)

        if not found_peers:
            print("\nCould not find any peers for the given infohash.")
        else:
            print(f"\nSearch complete. Found a total of {len(found_peers)} peers.")


async def main():
    # parser = argparse.ArgumentParser(description="Find peers for a torrent using DHT.")
    # parser.add_argument("infohash", help="The 40-character infohash of the torrent.")
    # args = parser.parse_args()

    torrent = Torrent("./torrents/ubuntu-25.10-desktop-amd64.iso.torrent")
    print(torrent.info_hash)

    client = DHTClient()
    # print(client._parse_peers([b"\xbc\xf3\xb6\xfd(\x0c"]))
    # return
    try:
        await client.start()
        await client.find_peers_for_infohash(torrent.info_hash)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        client.stop()


if __name__ == "__main__":
    # Example usage:
    # python dht_peer_finder.py 248d0a1cd08284299de78c5c1ed359bb46717d8c
    # (This is the infohash for an Ubuntu 22.04.1 Desktop image)
    asyncio.run(main())
