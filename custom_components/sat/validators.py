from urllib.parse import urlparse


def valid_serial_device(value: str) -> bool:
    if value.startswith("socket://"):
        parsed_url = urlparse(value)
        if parsed_url.hostname is not None and (parsed_url.port or 0) > 0:
            return True
    elif value.startswith("/dev/"):
        return True

    return False
