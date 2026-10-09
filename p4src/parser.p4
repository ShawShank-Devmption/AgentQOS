// P2.1, §7.15: unsupported packets reach ingress with features disabled.
parser PacketParser(
    packet_in packet,
    out headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    state start {
        meta.feature_valid = 0;
        meta.wire_len = standard_metadata.packet_length;
        meta.ipv4_envelope_valid = 0;
        meta.tls_record_candidate = 0;
        meta.tls_hello_offset = 0;
        meta.tls_cipher_count = 0;
        meta.tls_bytes_remaining = 0;
        meta.tls_extension_size = 0;
        meta.tls_ext_bytes_remaining = 0;
        meta.tls_ext_count = 0;
        meta.tls_truncated = 0;
        meta.tls_payload_available = 0;
        meta.tcp_payload_present = 0;
        meta.tls_extension_type = 0;
        meta.tls_name_list_len = 0;
        meta.tls_first_name_len = 0;
        meta.tls_alpn_present = 0;
        meta.tls_alpn_h1 = 0;
        meta.tls_alpn_h2 = 0;
        meta.tls_alpn_other = 0;
        meta.tls_sni_length = 0;
        meta.tls_sni_len_bucket = 0;
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            0x0800: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        meta.ipv4_envelope_valid = (hdr.ipv4.version == 4 &&
            hdr.ipv4.ihl >= 5 &&
            hdr.ipv4.total_len >= ((bit<16>) hdr.ipv4.ihl << 2) &&
            (bit<32>) hdr.ipv4.total_len + 14 <= meta.wire_len) ? 1w1 : 1w0;
        transition select(meta.ipv4_envelope_valid, hdr.ipv4.ihl,
                          hdr.ipv4.frag_offset, hdr.ipv4.protocol) {
            (1, 5, 0, 6): parse_tcp;
            (1, 5, 0, 17): parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        meta.feature_valid = (hdr.tcp.data_offset >= 5 &&
            hdr.ipv4.total_len >= (((bit<16>) hdr.tcp.data_offset + 5) << 2)) ? 1w1 : 1w0;
        meta.tcp_payload_present = (meta.feature_valid == 1 &&
            hdr.ipv4.total_len > (((bit<16>) hdr.tcp.data_offset + 5) << 2)) ? 1w1 : 1w0;
        transition select(meta.feature_valid, hdr.tcp.data_offset, hdr.ipv4.total_len) {
            (1, 5, 45..65535): check_tls_record;
            (1, 6..15, _): parse_tcp_options;
            default: accept;
        }
    }

    state parse_tcp_options {
        packet.extract(hdr.tcp_options,
            (((bit<32>) hdr.tcp.data_offset - 5) << 5));
        meta.tls_payload_available = ((bit<32>) hdr.ipv4.total_len >=
            (20 + ((bit<32>) hdr.tcp.data_offset << 2) + 5)) ? 1w1 : 1w0;
        transition select(meta.tls_payload_available) {
            1: check_tls_record;
            default: accept;
        }
    }

    state check_tls_record {
        // A lookahead keeps the original TCP payload intact for forwarding.
        transition select(packet.lookahead<bit<40>>()) {
            0x1603000000 &&& 0xffff000000: parse_tls_record;
            default: accept;
        }
    }

    state parse_tls_record {
        packet.extract(hdr.tls_record);
        // §7.15: incomplete records cannot contribute classifier features.
        meta.feature_valid = (hdr.tls_record.length >= 4 &&
            ((bit<32>) hdr.tls_record.length + 5) <=
            ((bit<32>) hdr.ipv4.total_len - 40)) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_handshake;
            default: accept;
        }
    }

    state parse_tls_handshake {
        packet.extract(hdr.tls_handshake);
        transition select(hdr.tls_handshake.msg_type) {
            1: check_client_hello_length;
            default: accept;
        }
    }

    state check_client_hello_length {
        meta.feature_valid = (hdr.tls_handshake.length >= 35 &&
            ((bit<32>) hdr.tls_handshake.length + 4) <=
            (bit<32>) hdr.tls_record.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_hello_fixed;
            default: accept;
        }
    }

    state parse_tls_hello_fixed {
        packet.extract(hdr.tls_hello_fixed);
        meta.tls_hello_offset = 35 + (bit<32>) hdr.tls_hello_fixed.session_id_len;
        meta.feature_valid = (hdr.tls_hello_fixed.session_id_len <= 32 &&
            meta.tls_hello_offset <=
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid, hdr.tls_hello_fixed.session_id_len) {
            (1, 0): check_cipher_len_space;
            (1, 1..32): parse_tls_session_id;
            default: accept;
        }
    }

    state parse_tls_session_id {
        packet.extract(hdr.tls_session_id,
            (bit<32>) hdr.tls_hello_fixed.session_id_len << 3);
        transition check_cipher_len_space;
    }

    state check_cipher_len_space {
        meta.feature_valid = (meta.tls_hello_offset + 2 <=
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_cipher_len;
            default: accept;
        }
    }

    state parse_tls_cipher_len {
        packet.extract(hdr.tls_cipher_len);
        meta.tls_hello_offset = meta.tls_hello_offset + 2;
        meta.feature_valid = (hdr.tls_cipher_len.bytes >= 2 &&
            hdr.tls_cipher_len.bytes <= 512 &&
            (hdr.tls_cipher_len.bytes & 1) == 0 &&
            meta.tls_hello_offset + (bit<32>) hdr.tls_cipher_len.bytes <=
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_cipher_suites;
            default: accept;
        }
    }

    state parse_tls_cipher_suites {
        packet.extract(hdr.tls_cipher_suites,
            (bit<32>) hdr.tls_cipher_len.bytes << 3);
        meta.tls_hello_offset = meta.tls_hello_offset +
            (bit<32>) hdr.tls_cipher_len.bytes;
        meta.tls_cipher_count = hdr.tls_cipher_len.bytes == 512 ?
            8w255 : (bit<8>) (hdr.tls_cipher_len.bytes >> 1);
        transition check_compression_len_space;
    }

    state check_compression_len_space {
        meta.feature_valid = (meta.tls_hello_offset + 1 <=
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_compression_len;
            default: accept;
        }
    }

    state parse_tls_compression_len {
        packet.extract(hdr.tls_compression_len);
        meta.tls_hello_offset = meta.tls_hello_offset + 1;
        meta.feature_valid = (hdr.tls_compression_len.bytes >= 1 &&
            hdr.tls_compression_len.bytes <= 32 &&
            meta.tls_hello_offset + (bit<32>) hdr.tls_compression_len.bytes <=
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_compression_methods;
            default: accept;
        }
    }

    state parse_tls_compression_methods {
        packet.extract(hdr.tls_compression_methods,
            (bit<32>) hdr.tls_compression_len.bytes << 3);
        meta.tls_hello_offset = meta.tls_hello_offset +
            (bit<32>) hdr.tls_compression_len.bytes;
        transition check_extensions_len_space;
    }

    state check_extensions_len_space {
        meta.tls_bytes_remaining = (bit<32>) hdr.tls_handshake.length -
            meta.tls_hello_offset;
        transition select(meta.tls_bytes_remaining) {
            0: tls_client_hello_complete;
            2..0xffffffff: parse_tls_extensions_len;
            default: invalid_tls_hello;
        }
    }

    state parse_tls_extensions_len {
        packet.extract(hdr.tls_extensions_len);
        meta.tls_hello_offset = meta.tls_hello_offset + 2;
        meta.feature_valid = (meta.tls_hello_offset +
            (bit<32>) hdr.tls_extensions_len.bytes ==
            (bit<32>) hdr.tls_handshake.length) ? meta.feature_valid : 1w0;
        meta.tls_ext_bytes_remaining = (bit<32>) hdr.tls_extensions_len.bytes;
        transition select(meta.feature_valid) {
            1: check_tls_extension;
            default: accept;
        }
    }

    state check_tls_extension {
        transition select(meta.tls_ext_bytes_remaining, meta.tls_ext_count) {
            (0, _): tls_client_hello_complete;
            (_, 16): tls_extensions_truncated;
            (4..0xffffffff, _): peek_tls_extension;
            default: invalid_tls_hello;
        }
    }

    state peek_tls_extension {
        bit<32> ext_header = packet.lookahead<bit<32>>();
        meta.tls_extension_type = (bit<16>) (ext_header >> 16);
        meta.tls_extension_size = (bit<16>) ext_header;
        meta.feature_valid = (4 + (bit<32>) meta.tls_extension_size <=
            meta.tls_ext_bytes_remaining) ? meta.feature_valid : 1w0;
        meta.tls_truncated = meta.tls_extension_size > 512 ? 1w1 : 1w0;
        transition select(meta.feature_valid, meta.tls_truncated,
                          meta.tls_extension_type, meta.tls_extension_size) {
            (1, 0, 16, 0..4): invalid_tls_hello;
            (1, 0, 16, 5..10): inspect_alpn_short;
            (1, 0, 16, 11..512): inspect_alpn_long;
            (1, 0, 0, 0..4): invalid_tls_hello;
            (1, 0, 0, 5..512): inspect_sni;
            (1, 0, _, _): parse_tls_extension;
            (1, 1, _, _): tls_extensions_truncated;
            default: accept;
        }
    }

    state inspect_alpn_short {
        bit<72> alpn_header = packet.lookahead<bit<72>>();
        bit<16> first_two = (bit<16>) alpn_header;
        meta.tls_name_list_len = (bit<16>) (alpn_header >> 24);
        meta.tls_first_name_len = (bit<8>) (alpn_header >> 16);
        meta.feature_valid = (meta.tls_name_list_len + 2 ==
            meta.tls_extension_size && meta.tls_first_name_len >= 1 &&
            (bit<16>) meta.tls_first_name_len + 1 <=
            meta.tls_name_list_len) ? meta.feature_valid : 1w0;
        meta.tls_alpn_present = meta.feature_valid;
        meta.tls_alpn_h2 = (meta.feature_valid == 1 &&
            meta.tls_first_name_len == 2 && first_two == 0x6832) ? 1w1 : 1w0;
        meta.tls_alpn_other = meta.feature_valid == 1 &&
            meta.tls_alpn_h2 == 0 ? 1w1 : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_extension;
            default: accept;
        }
    }

    state inspect_alpn_long {
        bit<120> alpn_header = packet.lookahead<bit<120>>();
        bit<16> first_two = (bit<16>) (alpn_header >> 48);
        bit<64> first_eight = (bit<64>) alpn_header;
        meta.tls_name_list_len = (bit<16>) (alpn_header >> 72);
        meta.tls_first_name_len = (bit<8>) (alpn_header >> 64);
        meta.feature_valid = (meta.tls_name_list_len + 2 ==
            meta.tls_extension_size && meta.tls_first_name_len >= 1 &&
            (bit<16>) meta.tls_first_name_len + 1 <=
            meta.tls_name_list_len) ? meta.feature_valid : 1w0;
        meta.tls_alpn_present = meta.feature_valid;
        meta.tls_alpn_h1 = (meta.feature_valid == 1 &&
            meta.tls_first_name_len == 8 &&
            first_eight == 0x687474702f312e31) ? 1w1 : 1w0;
        meta.tls_alpn_h2 = (meta.feature_valid == 1 &&
            meta.tls_first_name_len == 2 && first_two == 0x6832) ? 1w1 : 1w0;
        meta.tls_alpn_other = (meta.feature_valid == 1 &&
            meta.tls_alpn_h1 == 0 && meta.tls_alpn_h2 == 0) ? 1w1 : 1w0;
        transition select(meta.feature_valid) {
            1: parse_tls_extension;
            default: accept;
        }
    }

    state inspect_sni {
        bit<72> sni_header = packet.lookahead<bit<72>>();
        bit<8> name_type = (bit<8>) (sni_header >> 16);
        meta.tls_name_list_len = (bit<16>) (sni_header >> 24);
        meta.tls_sni_length = (bit<16>) sni_header;
        meta.feature_valid = (meta.tls_name_list_len + 2 ==
            meta.tls_extension_size && name_type == 0 &&
            meta.tls_sni_length >= 1 &&
            (bit<32>) meta.tls_sni_length + 3 <=
            (bit<32>) meta.tls_name_list_len) ? meta.feature_valid : 1w0;
        meta.tls_sni_len_bucket = meta.tls_sni_length <= 32 ? 2w1 :
            (meta.tls_sni_length <= 128 ? 2w2 : 2w3);
        transition select(meta.feature_valid) {
            1: parse_tls_extension;
            default: accept;
        }
    }

    state parse_tls_extension {
        packet.extract(hdr.tls_extensions.next,
            (bit<32>) meta.tls_extension_size << 3);
        meta.tls_ext_bytes_remaining = meta.tls_ext_bytes_remaining -
            (4 + (bit<32>) meta.tls_extension_size);
        meta.tls_ext_count = meta.tls_ext_count + 1;
        transition check_tls_extension;
    }

    state tls_extensions_truncated {
        meta.tls_truncated = 1;
        meta.tls_record_candidate = 1;
        transition accept;
    }

    state invalid_tls_hello {
        meta.feature_valid = 0;
        transition accept;
    }

    state tls_client_hello_complete {
        meta.tls_record_candidate = 1;
        transition accept;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        meta.feature_valid = (hdr.ipv4.total_len >= 28 &&
            hdr.udp.length >= 8 &&
            hdr.udp.length <= hdr.ipv4.total_len - 20) ? 1w1 : 1w0;
        transition accept;
    }
}
