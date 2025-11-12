def decode(bencoded_data: bytes):
    """Декодирует bencoded данные."""
    try:
        data, length = decode_recursive(bencoded_data)
        if length != len(bencoded_data):
            raise ValueError("Лишние данные в конце")
        return data
    except (ValueError, IndexError) as e:
        raise ValueError(f"Некорректные bencoded данные: {e}")


def decode_recursive(data: bytes):
    if data.startswith(b"i"):
        return decode_int(data)
    elif data.startswith(b"l"):
        return decode_list(data)
    elif data.startswith(b"d"):
        return decode_dict(data)
    elif data[0:1].isdigit():
        return decode_string(data)
    else:
        raise ValueError("Неизвестный тип bencode")


def decode_int(data: bytes):
    end_index = data.find(b"e")
    if end_index == -1:
        raise ValueError("Не найден 'e' для integer")
    num_str = data[1:end_index]
    return int(num_str), end_index + 1


def decode_string(data: bytes):
    colon_index = data.find(b":")
    if colon_index == -1:
        raise ValueError("Не найден ':' для string")
    length = int(data[:colon_index])
    start = colon_index + 1
    end = start + length
    return data[start:end], end


def decode_list(data: bytes):
    items = []
    i = 1
    while data[i : i + 1] != b"e":
        item, length = decode_recursive(data[i:])
        items.append(item)
        i += length
    return items, i + 1


def decode_dict(data: bytes):
    d = {}
    i = 1
    while data[i : i + 1] != b"e":
        key, length = decode_string(data[i:])
        i += length
        value, length = decode_recursive(data[i:])
        d[key] = value
        i += length
    return d, i + 1


def encode(data):
    if isinstance(data, bytes):
        return str(len(data)).encode() + b":" + data
    elif isinstance(data, str):
        return encode(data.encode("utf-8"))
    elif isinstance(data, int):
        return b"i" + str(data).encode() + b"e"
    elif isinstance(data, list):
        return b"l" + b"".join(encode(item) for item in data) + b"e"
    elif isinstance(data, dict):
        encoded_items = b"".join(encode(k) + encode(v) for k, v in sorted(data.items()))
        return b"d" + encoded_items + b"e"
    raise TypeError(f"Неподдерживаемый тип для bencoding: {type(data)}")
