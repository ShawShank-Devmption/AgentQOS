// P2.1: design §5.1 base parser; TLS ClientHello parsing remains a separate increment.
#ifndef AGENT_AWARE_PARSER_P4
#define AGENT_AWARE_PARSER_P4

parser PacketParser(
    packet_in packet,
    out headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    state start {
        meta.features = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
        meta.tree_node = 0;
        meta.traffic_class = CLASS_UNKNOWN;
        meta.feature_valid = 0;
        meta.key_valid = 0;
        meta.ip_header_bytes = 0;
        meta.transport_bytes = 0;
        meta.tcp_header_bytes = 0;
        meta.payload_bytes = 0;
        meta.tls_ext_count = 0;
        meta.tls_alpn_class = ALPN_NONE;
        meta.tls_parsed = 0;
        meta.tls_truncated = 0;
        // §7.15: bounds precede extraction; uncertain packets retain their original bytes.
        transition select(standard_metadata.packet_length >= 14) {
            true: parse_ethernet;
            false: accept;
        }
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type, standard_metadata.packet_length >= 34) {
            (0x0800, true): parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        meta.ip_header_bytes = (bit<32>) hdr.ipv4.ihl << 2;
        // §7.15: all fragments, unsupported protocols and malformed lengths remain UNKNOWN.
        transition select(
            hdr.ipv4.version == 4 && hdr.ipv4.ihl >= 5,
            hdr.ipv4.frag_offset == 0 && (hdr.ipv4.flags & 1) == 0,
            (bit<32>) hdr.ipv4.total_len >= meta.ip_header_bytes &&
                (bit<32>) hdr.ipv4.total_len <= standard_metadata.packet_length - 14) {
            (true, true, true): ipv4_options;
            default: accept;
        }
    }

    state ipv4_options {
        meta.transport_bytes = (bit<32>) hdr.ipv4.total_len - meta.ip_header_bytes;
        transition select(hdr.ipv4.ihl > 5) {
            true: parse_ipv4_options;
            false: transport;
        }
    }

    state parse_ipv4_options {
        packet.extract(hdr.ipv4_options, (meta.ip_header_bytes - 20) << 3);
        transition transport;
    }

    state transport {
        transition select(
            hdr.ipv4.protocol, meta.transport_bytes >= 20, meta.transport_bytes >= 8) {
            (6, true, _): parse_tcp;
            (17, _, true): parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        meta.tcp_header_bytes = (bit<32>) hdr.tcp.data_offset << 2;
        transition select(
            hdr.tcp.data_offset >= 5 && meta.tcp_header_bytes <= meta.transport_bytes) {
            true: tcp_options;
            false: accept;
        }
    }

    state tcp_options {
        transition select(hdr.tcp.data_offset > 5) {
            true: parse_tcp_options;
            false: tls_start;
        }
    }

    state parse_tcp_options {
        packet.extract(hdr.tcp_options, (meta.tcp_header_bytes - 20) << 3);
        transition tls_start;
    }

    state tls_start {
        meta.payload_bytes = meta.transport_bytes - meta.tcp_header_bytes;
        transition select(meta.payload_bytes >= 44) {
            true: parse_tls_fixed;
            false: valid_transport;
        }
    }

    state parse_tls_fixed {
        packet.extract(hdr.tls_hello_fixed);
        meta.tls_remaining = meta.payload_bytes - 44;
        transition select(
            hdr.tls_hello_fixed.record_type == 8w22,
            (hdr.tls_hello_fixed.record_version & 16w65280) == 16w768,
            hdr.tls_hello_fixed.handshake_type == 8w1,
            hdr.tls_hello_fixed.record_length >= 39 &&
                (bit<32>) hdr.tls_hello_fixed.record_length + 5 <= meta.payload_bytes,
            hdr.tls_hello_fixed.session_length <= 32 &&
                meta.tls_remaining >= (bit<32>) hdr.tls_hello_fixed.session_length + 2) {
            (true, true, true, true, true): parse_tls_session;
            default: valid_transport;
        }
    }

    state parse_tls_session {
        packet.extract(hdr.tls_session, (bit<32>) hdr.tls_hello_fixed.session_length << 3);
        meta.tls_remaining = meta.tls_remaining -
            (bit<32>) hdr.tls_hello_fixed.session_length;
        transition parse_tls_cipher_length;
    }

    state parse_tls_cipher_length {
        packet.extract(hdr.tls_cipher_length);
        meta.tls_remaining = meta.tls_remaining - 2;
        transition select(hdr.tls_cipher_length.value > 0 &&
            hdr.tls_cipher_length.value <= 512 &&
            (hdr.tls_cipher_length.value & 16w1) == 0 &&
            meta.tls_remaining >= (bit<32>) hdr.tls_cipher_length.value + 1) {
            true: parse_tls_ciphers;
            false: valid_transport;
        }
    }

    state parse_tls_ciphers {
        packet.extract(hdr.tls_ciphers, (bit<32>) hdr.tls_cipher_length.value << 3);
        meta.tls_remaining = meta.tls_remaining - (bit<32>) hdr.tls_cipher_length.value;
        transition parse_tls_compression_length;
    }

    state parse_tls_compression_length {
        packet.extract(hdr.tls_compression_length);
        meta.tls_remaining = meta.tls_remaining - 1;
        transition select(hdr.tls_compression_length.value > 0 &&
            hdr.tls_compression_length.value <= 32 &&
            meta.tls_remaining >= (bit<32>) hdr.tls_compression_length.value + 2) {
            true: parse_tls_compression;
            false: valid_transport;
        }
    }

    state parse_tls_compression {
        packet.extract(hdr.tls_compression,
            (bit<32>) hdr.tls_compression_length.value << 3);
        meta.tls_remaining = meta.tls_remaining -
            (bit<32>) hdr.tls_compression_length.value;
        transition parse_tls_extensions_length;
    }

    state parse_tls_extensions_length {
        packet.extract(hdr.tls_extensions_length);
        meta.tls_remaining = meta.tls_remaining - 2;
        meta.tls_extensions_remaining = (bit<32>) hdr.tls_extensions_length.value;
        transition select(meta.tls_extensions_remaining <= meta.tls_remaining) {
            true: tls_extensions_start;
            false: valid_transport;
        }
    }

    state tls_extensions_start {
        transition select(meta.tls_extensions_remaining == 0,
            meta.tls_ext_count >= (bit<8>) TLS_MAX_EXTENSIONS,
            meta.tls_extensions_remaining >= 4) {
            (true, _, _): tls_complete;
            (false, true, _): tls_bounded_complete;
            (false, false, true): tls_extension_header;
            default: valid_transport;
        }
    }

    state tls_extension_header {
        meta.tls_extension_word = packet.lookahead<bit<32>>();
        meta.tls_extension_bytes = (bit<32>) (meta.tls_extension_word & 32w65535);
        transition select(meta.tls_extension_bytes <= 1024 &&
            meta.tls_extension_bytes + 4 <= meta.tls_extensions_remaining) {
            true: tls_extension_kind;
            false: valid_transport;
        }
    }

    state tls_extension_kind {
        transition select((meta.tls_extension_word >> 16) == 32w16,
            meta.tls_alpn_class == ALPN_NONE,
            meta.tls_extension_bytes >= 5) {
            (true, true, true): tls_alpn_short;
            default: parse_tls_extension;
        }
    }

    state tls_alpn_short {
        meta.tls_alpn_class = ALPN_OTHER;
        meta.tls_alpn_short = packet.lookahead<bit<72>>();
        if (meta.tls_alpn_short[23:16] == 8w2 &&
            meta.tls_alpn_short[15:0] == 16w26674) {
            meta.tls_alpn_class = ALPN_H2;
        }
        transition select(meta.tls_extension_bytes >= 11 &&
            meta.tls_alpn_class == ALPN_OTHER) {
            true: tls_alpn_long;
            false: parse_tls_extension;
        }
    }

    state tls_alpn_long {
        meta.tls_alpn_long = packet.lookahead<bit<120>>();
        if (meta.tls_alpn_long[71:64] == 8w8 &&
            meta.tls_alpn_long[63:0] == 64w7526768902419852849) {
            meta.tls_alpn_class = ALPN_H1;
        }
        transition parse_tls_extension;
    }

    state parse_tls_extension {
        packet.extract(hdr.tls_extensions.next, meta.tls_extension_bytes << 3);
        meta.tls_extensions_remaining = meta.tls_extensions_remaining -
            meta.tls_extension_bytes - 4;
        meta.tls_ext_count = meta.tls_ext_count + 1;
        transition tls_extensions_start;
    }

    state tls_bounded_complete {
        meta.tls_truncated = 1;
        meta.tls_parsed = 1;
        transition valid_transport;
    }

    state tls_complete {
        meta.tls_parsed = 1;
        transition valid_transport;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition select(
            hdr.udp.length >= 8 && (bit<32>) hdr.udp.length <= meta.transport_bytes) {
            true: valid_transport;
            false: accept;
        }
    }

    state valid_transport {
        meta.feature_valid = 1;
        transition accept;
    }
}

#endif
