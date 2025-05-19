import codecs
from eth_utils import to_bytes


def hex_to_bytes(hex_string):
    return codecs.decode(hex_string.replace("0x", ""), "hex")


def bytes_to_int(byte_array):
    return int.from_bytes(byte_array, byteorder="big")


def decode_function_parameters(function_abi, data_bytes):

    # Remove the 4-byte function selector
    data_without_selector = data_bytes[4:]

    offset = 0
    decoded_params = []

    for param in function_abi["inputs"]:
        param_type = param["type"]

        if param_type == "string":
            # Decode string (dynamic)
            param_offset = bytes_to_int(
                data_without_selector[offset:offset + 32])
            string_offset = param_offset
            string_length = bytes_to_int(
                data_without_selector[string_offset:string_offset + 32])
            string_data = data_without_selector[string_offset +
                                                32:string_offset + 32 + string_length]
            decoded_params.append(string_data.decode("utf-8"))

        elif param_type == "address":
            # Decode address (last 20 bytes of the 32-byte slot)
            address_bytes = data_without_selector[offset + 12:offset + 32]
            decoded_params.append("0x" + address_bytes.hex())

        elif param_type.startswith("uint"):
            # Decode uint (entire 32 bytes)
            uint_bytes = data_without_selector[offset:offset + 32]
            decoded_params.append(str(bytes_to_int(uint_bytes)))

        else:
            raise ValueError(f"Unsupported parameter type: {param_type}")

        # Move to the next 32-byte slot
        offset += 32

    return decoded_params
