from typing import Optional, Protocol, Tuple


AsciiFrameResult = Tuple[bool, str]
GrayFrameResult = Tuple[bool, Optional[bytes]]
VideoMetadata = Tuple[int, int, float]


class AsciiFrameReader(Protocol):
    fps: float

    def read_ascii(self) -> AsciiFrameResult:
        ...

    def get_pos_msec(self) -> float:
        ...

    def skip_frame(self) -> bool:
        ...

    def release(self) -> None:
        ...


class GrayFrameReader(Protocol):
    fps: float
    height: int

    def read_gray(self) -> GrayFrameResult:
        ...

    def release(self) -> None:
        ...
