import unittest
from bencode import decode, encode


class TestBencode(unittest.TestCase):
    def test_decode_int(self):
        self.assertEqual(decode(b"i0e"), 0)
        self.assertEqual(decode(b"i42e"), 42)
        self.assertEqual(decode(b"i-42e"), -42)
        self.assertEqual(decode(b"i12345678901234567890e"), 12345678901234567890)

    def test_decode_string(self):
        self.assertEqual(decode(b"0:"), b"")
        self.assertEqual(decode(b"4:spam"), b"spam")
        self.assertEqual(decode(b"13:Hello, World!"), b"Hello, World!")
        self.assertEqual(decode(b"3:\x01\x02\x03"), b"\x01\x02\x03")

    def test_decode_list(self):
        self.assertEqual(decode(b"le"), [])
        self.assertEqual(decode(b"li42ee"), [42])
        self.assertEqual(decode(b"l4:spami42ee"), [b"spam", 42])
        self.assertEqual(decode(b"lli1eei2ee"), [[1], 2])

    def test_decode_dict(self):
        self.assertEqual(decode(b"de"), {})
        self.assertEqual(decode(b"d3:bar4:spame"), {b"bar": b"spam"})
        self.assertEqual(
            decode(b"d3:foo3:bar5:helloi52ee"), {b"foo": b"bar", b"hello": 52}
        )
        self.assertEqual(decode(b"d1:ad1:bi1eee"), {b"a": {b"b": 1}})

    def test_decode_complex(self):
        data = b"d8:announce13:http://url.pl4:infod6:lengthi100e4:name4:testee"
        expected = {
            b"announce": b"http://url.pl",
            b"info": {b"length": 100, b"name": b"test"},
        }
        self.assertEqual(decode(data), expected)

    def test_decode_extra_data(self):
        with self.assertRaisesRegex(ValueError, "Лишние данные в конце"):
            decode(b"i42ejunk")

    def test_decode_malformed_int(self):
        with self.assertRaisesRegex(ValueError, "Не найден 'e'"):
            decode(b"i42")
        with self.assertRaisesRegex(ValueError, "invalid literal"):
            decode(b"ixye")

    def test_decode_malformed_string(self):
        with self.assertRaisesRegex(ValueError, "Не найден ':'"):
            decode(b"5spam")
        with self.assertRaisesRegex(ValueError, "Некорректные bencoded данные"):
            decode(b"10:spam")

    def test_decode_malformed_container(self):
        with self.assertRaisesRegex(ValueError, "Некорректные bencoded данные"):
            decode(b"li1e")
        with self.assertRaisesRegex(ValueError, "Некорректные bencoded данные"):
            decode(b"d3:foo")

    def test_decode_unknown_type(self):
        with self.assertRaisesRegex(ValueError, "Неизвестный тип bencode"):
            decode(b"x")

    def test_encode_int(self):
        self.assertEqual(encode(0), b"i0e")
        self.assertEqual(encode(42), b"i42e")
        self.assertEqual(encode(-100), b"i-100e")

    def test_encode_string(self):
        self.assertEqual(encode(b"spam"), b"4:spam")
        self.assertEqual(encode("spam"), b"4:spam")
        self.assertEqual(
            encode("привет"), b"12:\xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"
        )
        self.assertEqual(encode(""), b"0:")

    def test_encode_list(self):
        self.assertEqual(encode([]), b"le")
        self.assertEqual(encode([1, "a"]), b"li1e1:ae")
        self.assertEqual(encode([[1]]), b"lli1eee")

    def test_encode_dict(self):
        self.assertEqual(encode({}), b"de")

        data = {"b": 2, "a": 1}
        self.assertEqual(encode(data), b"d1:ai1e1:bi2ee")

        data_bytes = {b"key": b"val"}
        self.assertEqual(encode(data_bytes), b"d3:key3:vale")

    def test_encode_unsupported_type(self):
        with self.assertRaisesRegex(TypeError, "Неподдерживаемый тип"):
            encode(1.5)
        with self.assertRaisesRegex(TypeError, "Неподдерживаемый тип"):
            encode(None)
        with self.assertRaisesRegex(TypeError, "Неподдерживаемый тип"):
            encode({1, 2})


if __name__ == "__main__":
    unittest.main()
