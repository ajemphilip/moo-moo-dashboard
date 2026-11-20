from __future__ import annotations

import unittest

from bluetooth_service.protocol import MessageAssembler, ProtocolError, encode_frames


class BluetoothProtocolTests(unittest.TestCase):
    def test_round_trip_in_order(self) -> None:
        payload = b'{"method":"GET","path":"/status"}'
        assembler = MessageAssembler()
        complete = None
        for frame in encode_frames(42, payload):
            complete = assembler.accept(frame)
        self.assertEqual(complete, (42, payload))

    def test_round_trip_out_of_order(self) -> None:
        payload = b"abcdefghijklmnopqrstuvwxyz" * 4
        frames = encode_frames(7, payload)
        assembler = MessageAssembler()
        complete = None
        for frame in reversed(frames):
            result = assembler.accept(frame)
            if result is not None:
                complete = result
        self.assertEqual(complete, (7, payload))

    def test_rejects_short_frame(self) -> None:
        with self.assertRaises(ProtocolError):
            MessageAssembler().accept(b"bad")


if __name__ == "__main__":
    unittest.main()
