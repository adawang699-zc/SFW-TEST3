"""
ASN.1 BER 编码器 - 用于 IEC 61850 GOOSE/SV 协议
简化实现，支持基本的 ASN.1 类型编码
"""
import struct
from datetime import datetime


class ASN1Encoder:
    """ASN.1 BER 编码器"""

    # ASN.1 标签类型
    TAG_BOOLEAN = 0x01
    TAG_INTEGER = 0x02
    TAG_BIT_STRING = 0x03
    TAG_OCTET_STRING = 0x04
    TAG_NULL = 0x05
    TAG_OBJECT_IDENTIFIER = 0x06
    TAG_UTF8String = 0x0C
    TAG_VISIBLE_STRING = 0x1A  # IEC 61850 GOOSE 使用 VisibleString
    TAG_UTCTIME = 0x17  # UTC Time - IEC 61850 GOOSE 使用此类型
    TAG_REAL = 0x09  # REAL (浮点数) - ASN.1 类型
    TAG_SEQUENCE = 0x30  # 通用序列
    TAG_APPLICATION_SEQUENCE = 0x61  # 应用层序列 - IEC 61850 GOOSE PDU 使用此类型
    TAG_SEQUENCE_OF = 0x30
    TAG_SET = 0x31

    @staticmethod
    def encode_length(length, force_long_format=False):
        """编码长度字段 - ASN.1 BER 格式"""
        if force_long_format or length >= 128:
            if length <= 255:
                return bytes([0x81, length & 0xFF])
            elif length <= 0xFFFF:
                return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])
            else:
                length_bytes = []
                temp = length
                while temp > 0:
                    length_bytes.insert(0, temp & 0xFF)
                    temp >>= 8
                return bytes([0x80 | len(length_bytes)] + length_bytes)
        else:
            return bytes([length])

    @staticmethod
    def encode_tag(tag, constructed=False, context_specific=False, context_tag=None):
        """编码标签字段"""
        if context_specific and context_tag is not None:
            if context_tag < 31:
                return bytes([0x80 | (0x20 if constructed else 0x00) | context_tag])
            else:
                return bytes([0x80 | (0x20 if constructed else 0x00)])
        else:
            if tag < 31:
                return bytes([tag | (0x20 if constructed else 0x00)])
            else:
                return bytes([tag])

    @staticmethod
    def encode_boolean(value, context_tag=None):
        """编码布尔值"""
        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_BOOLEAN, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_BOOLEAN)
        length = ASN1Encoder.encode_length(1)
        content = b'\xFF' if value else b'\x00'
        return tag + length + content

    @staticmethod
    def encode_integer(value, context_tag=None):
        """编码整数 - ASN.1 BER 格式"""
        if value is None:
            raise ValueError("Cannot encode None as integer")
        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_INTEGER, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_INTEGER)

        if value == 0:
            content = b'\x00'
        elif value < 0:
            if value >= -128:
                content = struct.pack('>b', value)
            elif value >= -32768:
                content = struct.pack('>h', value)
            else:
                content = struct.pack('>i', value)
            while len(content) > 1 and content[0] == 0xFF and (content[1] & 0x80) != 0:
                content = content[1:]
        else:
            if value < 128:
                content = struct.pack('>B', value)
            elif value < 32768:
                content = struct.pack('>H', value)
            else:
                content = struct.pack('>I', value)
            while len(content) > 1 and content[0] == 0x00 and (content[1] & 0x80) == 0:
                content = content[1:]

        if len(content) == 0:
            raise ValueError(f"Integer encoding resulted in empty content for value {value}")

        length = ASN1Encoder.encode_length(len(content))
        result = tag + length + content
        if len(result) < 3:
            raise ValueError(f"Encoded integer result is too short: {len(result)} bytes")
        return result

    @staticmethod
    def encode_integer_fixed(value, context_tag=None, fixed_bytes=3):
        """编码整数 - 使用固定字节数格式"""
        if value is None:
            raise ValueError("Cannot encode None as integer")
        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_INTEGER, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_INTEGER)

        if fixed_bytes == 1:
            if value < 0:
                content = struct.pack('>b', value)
            else:
                content = struct.pack('>B', value & 0xFF)
        elif fixed_bytes == 2:
            if value < 0:
                content = struct.pack('>h', value)
            else:
                content = struct.pack('>H', value & 0xFFFF)
        elif fixed_bytes == 3:
            if value < 0:
                packed = struct.pack('>i', value)
                content = packed[1:]
            else:
                packed = struct.pack('>I', value & 0xFFFFFF)
                content = packed[1:]
        elif fixed_bytes == 4:
            if value < 0:
                content = struct.pack('>i', value)
            else:
                content = struct.pack('>I', value & 0xFFFFFFFF)
        elif fixed_bytes == 8:
            if value < 0:
                content = struct.pack('>q', value)
            else:
                content = struct.pack('>Q', value & 0xFFFFFFFFFFFFFFFF)
        else:
            raise ValueError(f"Unsupported fixed_bytes: {fixed_bytes}")

        length = ASN1Encoder.encode_length(len(content))
        result = tag + length + content
        if len(result) < 3:
            raise ValueError(f"Encoded integer result is too short: {len(result)} bytes")
        return result

    @staticmethod
    def encode_octet_string(value):
        """编码八位字节串"""
        if isinstance(value, str):
            value = value.encode('utf-8')
        tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_OCTET_STRING)
        length = ASN1Encoder.encode_length(len(value))
        return tag + length + value

    @staticmethod
    def encode_utf8_string(value):
        """编码 UTF-8 字符串"""
        if value is None:
            raise ValueError("Cannot encode None as UTF-8 string")
        if isinstance(value, str):
            value = value.encode('utf-8')
        tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_UTF8String)
        length = ASN1Encoder.encode_length(len(value))
        result = tag + length + value
        if len(result) < 2:
            raise ValueError("Encoded UTF-8 string result is too short")
        return result

    @staticmethod
    def encode_visible_string(value, context_tag=None):
        """编码 VisibleString - IEC 61850 GOOSE 使用此类型"""
        if value is None:
            raise ValueError("Cannot encode None as VisibleString")
        if isinstance(value, str):
            value = value.encode('utf-8')
        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_VISIBLE_STRING, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_VISIBLE_STRING)
        length = ASN1Encoder.encode_length(len(value))
        result = tag + length + value
        if len(result) < 2:
            raise ValueError("Encoded VisibleString result is too short")
        return result

    @staticmethod
    def encode_sequence(elements, context_tag=None, force_long_length=False):
        """编码序列"""
        content_parts = []
        for idx, elem in enumerate(elements):
            if isinstance(elem, bytes):
                if len(elem) == 0:
                    raise ValueError(f"Sequence element {idx} is empty bytes")
                content_parts.append(elem)
            elif isinstance(elem, str):
                encoded = elem.encode('utf-8')
                if len(encoded) == 0:
                    raise ValueError(f"Sequence element {idx} (string) encoded to empty bytes")
                content_parts.append(encoded)
            else:
                encoded = bytes(elem)
                if len(encoded) == 0:
                    raise ValueError(f"Sequence element {idx} converted to empty bytes")
                content_parts.append(encoded)
        content = b''.join(content_parts)
        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_SEQUENCE, constructed=True, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_SEQUENCE, constructed=True)
        length = ASN1Encoder.encode_length(len(content), force_long_format=force_long_length)
        result = tag + length + content
        if len(result) < 2:
            raise ValueError("Encoded sequence result is too short (less than 2 bytes)")
        return result

    @staticmethod
    def encode_utc_time(dt=None, context_tag=None, use_binary_format=True):
        """编码 UTC 时间 - IEC 61850 GOOSE 使用 UTCTime"""
        if dt is None:
            dt = datetime.utcnow()

        year_bcd = ((dt.year % 100) // 10 << 4) | (dt.year % 10)
        month_bcd = ((dt.month // 10) << 4) | (dt.month % 10)
        day_bcd = ((dt.day // 10) << 4) | (dt.day % 10)
        hour_bcd = ((dt.hour // 10) << 4) | (dt.hour % 10)
        minute_bcd = ((dt.minute // 10) << 4) | (dt.minute % 10)
        second_bcd = ((dt.second // 10) << 4) | (dt.second % 10)

        milliseconds = dt.microsecond // 1000
        ms_high = (milliseconds >> 8) & 0xFF
        ms_low = milliseconds & 0xFF

        time_bytes = bytes([year_bcd, month_bcd, day_bcd, hour_bcd, minute_bcd, second_bcd, ms_high, ms_low])

        if len(time_bytes) != 8:
            raise ValueError(f"UTCTime encoding length mismatch: expected 8 bytes, got {len(time_bytes)}")

        if context_tag is not None:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_UTCTIME, context_specific=True, context_tag=context_tag)
        else:
            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_UTCTIME)
        length = ASN1Encoder.encode_length(len(time_bytes))
        result = tag + length + time_bytes
        if len(result) < 2:
            raise ValueError("Encoded UTCTime result is too short")
        return result


class GOOSEEncoder:
    """GOOSE 报文编码器"""

    @staticmethod
    def encode_goose_pdu(config):
        """编码 GOOSE PDU - 符合 IEC 61850-8-1 标准"""
        if config is None:
            raise ValueError("GOOSE config cannot be None")

        elements = []
        element_names = []

        try:
            gocb_ref = config.get('gocb_ref', 'IED1/LLN0$GO$GSE1')
            if not gocb_ref:
                gocb_ref = 'IED1/LLN0$GO$GSE1'
            encoded = ASN1Encoder.encode_visible_string(gocb_ref, context_tag=0)
            if len(encoded) == 0:
                raise ValueError("gocbRef encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("gocbRef")

            time_allowed = config.get('timeallowedtolive', 2000)
            if time_allowed is None:
                time_allowed = 2000
            encoded = ASN1Encoder.encode_integer(time_allowed, context_tag=1)
            if len(encoded) == 0:
                raise ValueError("timeAllowedToLive encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("timeAllowedToLive")

            datset = config.get('datset', 'IED1/LLN0$DataSet1')
            if not datset:
                datset = 'IED1/LLN0$DataSet1'
            encoded = ASN1Encoder.encode_visible_string(datset, context_tag=2)
            if len(encoded) == 0:
                raise ValueError("datSet encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("datSet")

            go_id = config.get('go_id', '')
            if not go_id:
                go_id = config.get('gocb_ref', 'IED1/LLN0$GO$GSE1')
            encoded = ASN1Encoder.encode_visible_string(go_id, context_tag=3)
            if len(encoded) == 0:
                raise ValueError("goID encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("goID")

            encoded = ASN1Encoder.encode_utc_time(context_tag=4, use_binary_format=True)
            if len(encoded) == 0:
                raise ValueError("t (UtcTime) encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("t")

            stnum = config.get('stnum', 1)
            if stnum is None:
                stnum = 1
            encoded = ASN1Encoder.encode_integer(stnum, context_tag=5)
            if len(encoded) == 0:
                raise ValueError("stNum encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("stNum")

            sqnum = config.get('sqnum', 0)
            if sqnum is None:
                sqnum = 0
            encoded = ASN1Encoder.encode_integer(sqnum, context_tag=6)
            if len(encoded) == 0:
                raise ValueError("sqNum encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("sqNum")

            test = config.get('test', False)
            if test is None:
                test = False
            encoded = ASN1Encoder.encode_boolean(test, context_tag=7)
            if len(encoded) == 0:
                raise ValueError("test encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("test")

            conf_rev = config.get('confrev', 1)
            if conf_rev is None:
                conf_rev = 1
            encoded = ASN1Encoder.encode_integer(conf_rev, context_tag=8)
            if len(encoded) == 0:
                raise ValueError("confRev encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("confRev")

            nds_com = config.get('ndscom', False)
            if nds_com is None:
                nds_com = False
            encoded = ASN1Encoder.encode_boolean(nds_com, context_tag=9)
            if len(encoded) == 0:
                raise ValueError("ndsCom encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("ndsCom")

            data = config.get('data', {})
            if data is None:
                data = {}
            num_entries = len(data) if data else 0
            encoded = ASN1Encoder.encode_integer(num_entries, context_tag=10)
            if len(encoded) == 0:
                raise ValueError("numDatSetEntries encoding resulted in empty bytes")
            elements.append(encoded)
            element_names.append("numDatSetEntries")

            data_elements = []
            if data:
                for key, value in data.items():
                    try:
                        if value is None:
                            key_lower = key.lower()
                            if 'time' in key_lower or 'timestamp' in key_lower:
                                value = ""
                            else:
                                continue

                        if isinstance(value, bool):
                            encoded_elem = ASN1Encoder.encode_boolean(value, context_tag=3)
                        elif isinstance(value, int):
                            if value >= 0:
                                if value <= 255:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x88-0x80, fixed_bytes=1)
                                elif value <= 65535:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x89-0x80, fixed_bytes=2)
                                elif value <= 4294967295:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x8A-0x80, fixed_bytes=4)
                                else:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x87-0x80, fixed_bytes=8)
                            else:
                                if value >= -128 and value <= 127:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x84-0x80, fixed_bytes=1)
                                elif value >= -32768 and value <= 32767:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x85-0x80, fixed_bytes=2)
                                elif value >= -2147483648 and value <= 2147483647:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x86-0x80, fixed_bytes=4)
                                else:
                                    encoded_elem = ASN1Encoder.encode_integer_fixed(value, context_tag=0x87-0x80, fixed_bytes=8)
                        elif isinstance(value, float):
                            float_bytes = struct.pack('>f', value)
                            tag = ASN1Encoder.encode_tag(ASN1Encoder.TAG_REAL, context_specific=True, context_tag=0x8B-0x80)
                            length = ASN1Encoder.encode_length(4)
                            encoded_elem = tag + length + float_bytes
                        elif isinstance(value, str):
                            encoded_elem = ASN1Encoder.encode_visible_string(value, context_tag=13)
                        elif isinstance(value, datetime):
                            time_str = value.strftime("%Y-%m-%d %H:%M:%S")
                            encoded_elem = ASN1Encoder.encode_visible_string(time_str, context_tag=13)
                        elif value is None:
                            encoded_elem = ASN1Encoder.encode_visible_string("", context_tag=13)
                        else:
                            encoded_elem = ASN1Encoder.encode_visible_string(str(value), context_tag=13)

                        if len(encoded_elem) == 0:
                            raise ValueError(f"Data element '{key}' encoding resulted in empty bytes")
                        data_elements.append(encoded_elem)
                    except Exception as e:
                        raise ValueError(f"Failed to encode data element '{key}': {str(e)}")

            if len(data_elements) != num_entries:
                raise ValueError(f"Data elements count mismatch: numDataSetEntries={num_entries}, actual data elements={len(data_elements)}")

            all_data = ASN1Encoder.encode_sequence(data_elements, context_tag=11, force_long_length=True)
            if len(all_data) == 0:
                raise ValueError("allData sequence encoding resulted in empty bytes")
            elements.append(all_data)
            element_names.append("allData")

            if len(elements) < 11:
                raise ValueError(f"GOOSE PDU has insufficient elements: {len(elements)} (expected at least 11)")

            for idx, (elem, name) in enumerate(zip(elements, element_names)):
                if len(elem) == 0:
                    raise ValueError(f"GOOSE PDU element {idx} ({name}) is empty")

            content = b''.join(elements)
            content_length = len(content)
            tag = bytes([0x61])
            length = ASN1Encoder.encode_length(content_length, force_long_format=True)
            goose_pdu = tag + length + content

            if len(length) == 2:
                declared_length = length[1]
                if declared_length != content_length:
                    raise ValueError(f"Root sequence length mismatch: declared {declared_length}, actual {content_length}")
            elif len(length) == 3:
                declared_length = (length[1] << 8) | length[2]
                if declared_length != content_length:
                    raise ValueError(f"Root sequence length mismatch: declared {declared_length}, actual {content_length}")

            if len(goose_pdu) == 0:
                raise ValueError("GOOSE PDU encoding resulted in zero bytes")

            if len(goose_pdu) < 20:
                raise ValueError(f"GOOSE PDU is too short: {len(goose_pdu)} bytes (expected at least 20)")

            return goose_pdu

        except Exception as e:
            error_msg = f"GOOSE PDU encoding failed: {str(e)}\n"
            error_msg += f"Elements encoded so far: {len(elements)}/{len(element_names)}\n"
            if element_names:
                error_msg += f"Last element: {element_names[-1] if element_names else 'N/A'}"
            raise ValueError(error_msg) from e


class SVEncoder:
    """SV 报文编码器"""

    @staticmethod
    def encode_sv_pdu(config):
        """编码 SV PDU - 符合 IEC 61850-9-2 标准"""
        if config is None:
            raise ValueError("SV config cannot be None")

        try:
            asdu_elements = []
            asdu_element_names = []

            svid = config.get('svid', 'ML2201BMU/LLN0$SV$MSVCB01')
            if not svid:
                svid = 'ML2201BMU/LLN0$SV$MSVCB01'
            try:
                svid.encode('ascii')
            except UnicodeEncodeError:
                raise ValueError(f"svID must be ASCII string, got non-ASCII characters: {svid}")
            if len(svid) > 65:
                raise ValueError(f"svID length ({len(svid)}) exceeds maximum (65 bytes)")
            encoded = ASN1Encoder.encode_visible_string(svid, context_tag=0)
            if len(encoded) == 0:
                raise ValueError("svID encoding resulted in empty bytes")
            asdu_elements.append(encoded)
            asdu_element_names.append("svID")

            smpcnt = config.get('smpcnt', 0)
            if smpcnt is None:
                smpcnt = 0
            encoded = ASN1Encoder.encode_integer_fixed(smpcnt, context_tag=2, fixed_bytes=2)
            if len(encoded) == 0:
                raise ValueError("smpCnt encoding resulted in empty bytes")
            asdu_elements.append(encoded)
            asdu_element_names.append("smpCnt")

            conf_rev = config.get('confrev', 1)
            if conf_rev is None:
                conf_rev = 1
            if conf_rev < 0 or conf_rev > 0xFFFFFFFF:
                conf_rev = conf_rev & 0xFFFFFFFF
            encoded = ASN1Encoder.encode_integer_fixed(conf_rev, context_tag=3, fixed_bytes=4)
            if len(encoded) == 0:
                raise ValueError("confRev encoding resulted in empty bytes")
            asdu_elements.append(encoded)
            asdu_element_names.append("confRev")

            smp_synch = config.get('smpsynch', True)
            if smp_synch is None:
                smp_synch = True
            if isinstance(smp_synch, int):
                smp_synch = (smp_synch == 1)
            tag = bytes([0x85])
            length = bytes([0x01])
            content = bytes([0x01 if smp_synch else 0x00])
            encoded = tag + length + content
            if len(encoded) == 0:
                raise ValueError("smpSynch encoding resulted in empty bytes")
            asdu_elements.append(encoded)
            asdu_element_names.append("smpSynch")

            samples = config.get('samples', {})
            if samples is None:
                samples = {}
            data_bytes = []
            if samples:
                for key, value in samples.items():
                    try:
                        if isinstance(value, (int, float)):
                            float_value = float(value)
                            float_bytes = struct.pack('>f', float_value)

                            quality = config.get('quality', {}).get(key, 0x00)
                            if not isinstance(quality, int) or quality < 0 or quality > 255:
                                quality = 0x00
                            quality_byte = bytes([quality & 0xFF])

                            data_bytes.append(float_bytes + quality_byte)
                        else:
                            try:
                                float_value = float(value)
                                float_bytes = struct.pack('>f', float_value)
                                quality = config.get('quality', {}).get(key, 0x00)
                                if not isinstance(quality, int) or quality < 0 or quality > 255:
                                    quality = 0x00
                                quality_byte = bytes([quality & 0xFF])
                                data_bytes.append(float_bytes + quality_byte)
                            except (ValueError, TypeError):
                                raise ValueError(f"Cannot encode sample '{key}' as float")
                    except Exception as e:
                        raise ValueError(f"Failed to encode sample element '{key}': {str(e)}")

            seq_data_content = b''.join(data_bytes)
            SEQ_DATA_LEN = 16
            if len(seq_data_content) < SEQ_DATA_LEN:
                seq_data_content = seq_data_content + bytes(SEQ_DATA_LEN - len(seq_data_content))
            elif len(seq_data_content) > SEQ_DATA_LEN:
                seq_data_content = seq_data_content[:SEQ_DATA_LEN]
            tag = bytes([0x87])
            length = bytes([SEQ_DATA_LEN])
            seq_data = tag + length + seq_data_content
            if len(seq_data) == 0:
                raise ValueError("seqData encoding resulted in empty bytes")
            asdu_elements.append(seq_data)
            asdu_element_names.append("seqData")

            asdu_sequence = ASN1Encoder.encode_sequence(asdu_elements)
            if len(asdu_sequence) == 0:
                raise ValueError("ASDU sequence encoding resulted in empty bytes")

            seq_asdu = ASN1Encoder.encode_sequence([asdu_sequence], context_tag=2)
            if len(seq_asdu) == 0:
                raise ValueError("seqASDU encoding resulted in empty bytes")

            root_elements = []
            root_element_names = []

            no_asdu = config.get('noASDU', 1)
            if no_asdu < 1 or no_asdu > 255:
                no_asdu = 1
            encoded = ASN1Encoder.encode_integer_fixed(no_asdu, context_tag=0, fixed_bytes=1)
            if len(encoded) == 0:
                raise ValueError("noASDU encoding resulted in empty bytes")
            root_elements.append(encoded)
            root_element_names.append("noASDU")

            root_elements.append(seq_asdu)
            root_element_names.append("seqASDU")

            root_content = b''.join(root_elements)
            root_tag = bytes([0x60])
            root_length = ASN1Encoder.encode_length(len(root_content))
            sv_pdu = root_tag + root_length + root_content

            if len(sv_pdu) == 0:
                raise ValueError("SV PDU encoding resulted in zero bytes")

            if len(sv_pdu) < 15:
                raise ValueError(f"SV PDU is too short: {len(sv_pdu)} bytes (expected at least 15)")

            return sv_pdu

        except Exception as e:
            error_msg = f"SV PDU encoding failed: {str(e)}"
            raise ValueError(error_msg) from e


class IEC61850Encoder:
    """IEC 61850 报文编码器 - 包含头部和 PDU"""

    @staticmethod
    def encode_goose_packet(config):
        """编码完整的 GOOSE 报文（包含头部和 PDU）"""
        goose_pdu = GOOSEEncoder.encode_goose_pdu(config)

        if not goose_pdu or len(goose_pdu) == 0:
            raise ValueError("GOOSE PDU is empty, cannot create packet")

        appid = config.get('appid', 0x100)
        reserved1 = 0x0000
        reserved2 = 0x0000

        pdu_length = len(goose_pdu)
        length = 4 + pdu_length

        if length > 65535:
            raise ValueError(f"GOOSE packet length ({length}) exceeds maximum (65535)")

        header = struct.pack('>HHHH', appid, length, reserved1, reserved2)

        packet = header + goose_pdu

        if len(packet) != 8 + pdu_length:
            raise ValueError(f"GOOSE packet length mismatch: expected {8 + pdu_length}, got {len(packet)}")

        return packet

    @staticmethod
    def encode_sv_packet(config):
        """编码完整的 SV 报文（包含头部和 PDU）"""
        sv_pdu = SVEncoder.encode_sv_pdu(config)

        if not sv_pdu or len(sv_pdu) == 0:
            raise ValueError("SV PDU is empty, cannot create packet")

        appid = config.get('appid', 0x4019)
        if appid < 0x4000 or appid > 0x7FFF:
            raise ValueError(f"APPID must be in range 0x4000~0x7FFF, got 0x{appid:04X}")
        reserved1 = 0x5356  # "SV" in ASCII
        reserved2 = 0x0000

        pdu_length = len(sv_pdu)
        length = 4 + pdu_length

        if length > 65535:
            raise ValueError(f"SV packet length ({length}) exceeds maximum (65535)")

        header = struct.pack('>HHHH', appid, length, reserved1, reserved2)

        packet = header + sv_pdu

        if len(packet) != 8 + pdu_length:
            raise ValueError(f"SV packet length mismatch: expected {8 + pdu_length}, got {len(packet)}")

        return packet