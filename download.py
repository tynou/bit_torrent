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
    COMPLETED = 3  # Добавим статус для завершенных
    ERROR = 4


class Download:
    def __init__(self, torrent, destination, status) -> None:
        self.torrent: Torrent = torrent
        self.destination: str = destination
        self.status: DownloadStatus = status
        self.piece_manager: PieceManager = PieceManager(self.torrent, self.destination)
        self.tracker: Tracker = Tracker(self.torrent)
        self.peers: list = []
        self.peers_queue: asyncio.Queue = asyncio.Queue()  # Очередь для пиров из DHT

        self.start_time: float | None = None
        self.end_time: float | None = None

        # Атрибуты для расчета скорости
        self.speed: float = 0.0
        self.last_downloaded: int = 0
        self.last_speed_check_time: float = time.time()

    def create_task(self, task: asyncio.Task) -> None:
        self.task: asyncio.Task = task
