import base64

from tplink_deco_exporter.api import aes_decrypt, aes_encrypt, decode_name_with_fallback


def test_aes_round_trip_and_name_decoding():
    key = b"1234567890123456"
    iv = b"6543210987654321"
    raw = b"payload"
    encrypted = aes_encrypt(key, iv, raw)
    decrypted = aes_decrypt(key, iv, encrypted)
    assert decrypted[: -decrypted[-1]] == raw
    assert (
        decode_name_with_fallback(base64.b64encode(b"Kitchen phone").decode())
        == "Kitchen phone"
    )
    assert decode_name_with_fallback("plain name") == "plain name"


def test_api_source_has_no_router_mutation_entrypoints():
    from tplink_deco_exporter.api import TplinkDecoApi

    names = set(dir(TplinkDecoApi))
    assert not any(
        word in name for name in names for word in ("reboot", "configure", "delete")
    )
