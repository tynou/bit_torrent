import enum
from torrent import Torrent
from piece_manager import PieceManager
from tracker import Tracker
import asyncio


class DownloadStatus(enum.Enum):
    STARTING = 0
    SEEDING = 1


class Download:
    def __init__(self, torrent, destination, status) -> None:
        self.torrent: Torrent = torrent
        self.destination: str = destination
        self.status: DownloadStatus = status
        self.piece_manager: PieceManager = PieceManager(self.torrent, self.destination)
        self.tracker: Tracker = Tracker(self.torrent)
        self.peers: list = []

    def create_task(self, task: asyncio.Task) -> None:
        self.task: asyncio.Task = task
