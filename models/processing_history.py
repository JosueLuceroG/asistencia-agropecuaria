"""Serializable history records for processed reports."""
from dataclasses import asdict, dataclass


@dataclass
class ProcessingHistoryItem:
    file_name: str
    file_path: str
    processed_at: str
    device: str
    mode: str
    period: str
    status: str
    result: str
    records: int = 0
    people: int = 0
    meals: int = 0
    errors: int = 0
    output_path: str = ""

    def to_dict(self):
        return asdict(self)

