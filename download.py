import enum
from torrent import Torrent
from piece_manager import PieceManager
from tracker import Tracker
import asyncio
import time


class DownloadStatus(enum.Enum):
    STARTING = 0
    DOWNLOADING = 1
    SEEDING = 2
    COMPLETED = 3
    ERROR = 4
    PAUSED = 5


class Download:
    def __init__(self, torrent: Torrent, destination: str, file_selection: list[bool]):
        self.torrent = torrent
        self.destination = destination
        self.file_selection = file_selection
        self.status = DownloadStatus.STARTING

        self.piece_manager = PieceManager(
            self.torrent, self.destination, self.file_selection
        )

        self.tracker: Tracker = Tracker(self.torrent)
        self.peers: list = []
        self.peers_queue: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None

        self.start_time: float | None = None
        self.end_time: float | None = None
        self.speed: float = 0.0
        self.last_downloaded: int = 0
        self.last_speed_check_time: float = time.time()

    def create_task(self, task: asyncio.Task):
        self.task = task
