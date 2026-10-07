// P2.1, design §§4.2 and 5.1: packet fields shared by parser and pipeline.
header ethernet_t {
    bit<48> dst_addr;
    bit<48> src_addr;
    bit<16> ether_type;
}

header ipv4_t {
    bit<4> version;
    bit<4> ihl;
    bit<8> diffserv;
    bit<16> total_len;
    bit<16> identification;
    bit<3> flags;
    bit<13> frag_offset;
    bit<8> ttl;
    bit<8> protocol;
    bit<16> hdr_checksum;
    bit<32> src_addr;
    bit<32> dst_addr;
}

header tcp_t {
    bit<16> src_port;
    bit<16> dst_port;
    bit<32> seq_no;
    bit<32> ack_no;
    bit<4> data_offset;
    bit<3> reserved;
    bit<9> flags;
    bit<16> window;
    bit<16> checksum;
    bit<16> urgent_ptr;
}

header tcp_options_t {
    varbit<320> bytes;
}

header udp_t {
    bit<16> src_port;
    bit<16> dst_port;
    bit<16> length;
    bit<16> checksum;
}

header tls_record_t {
    bit<8> content_type;
    bit<16> legacy_version;
    bit<16> length;
}

header tls_handshake_t {
    bit<8> msg_type;
    bit<24> length;
}

header tls_hello_fixed_t {
    bit<16> legacy_version;
    bit<256> random;
    bit<8> session_id_len;
}

header tls_session_id_t {
    varbit<256> bytes;
}

header tls_cipher_len_t {
    bit<16> bytes;
}

header tls_cipher_suites_t {
    varbit<4096> bytes;
}

header tls_compression_len_t {
    bit<8> bytes;
}

header tls_compression_methods_t {
    varbit<256> bytes;
}

header tls_extensions_len_t {
    bit<16> bytes;
}

header tls_extension_t {
    bit<16> ext_type;
    bit<16> ext_len;
    varbit<4096> bytes;
}

struct headers_t {
    ethernet_t ethernet;
    ipv4_t ipv4;
    tcp_t tcp;
    tcp_options_t tcp_options;
    udp_t udp;
    tls_record_t tls_record;
    tls_handshake_t tls_handshake;
    tls_hello_fixed_t tls_hello_fixed;
    tls_session_id_t tls_session_id;
    tls_cipher_len_t tls_cipher_len;
    tls_cipher_suites_t tls_cipher_suites;
    tls_compression_len_t tls_compression_len;
    tls_compression_methods_t tls_compression_methods;
    tls_extensions_len_t tls_extensions_len;
    tls_extension_t[16] tls_extensions;
}

struct metadata_t {
    bit<1> feature_valid;
    bit<32> wire_len;
    bit<1> ipv4_envelope_valid;
    bit<1> tls_record_candidate;
    bit<32> tls_hello_offset;
    bit<8> tls_cipher_count;
    bit<32> tls_bytes_remaining;
    bit<16> tls_extension_size;
    bit<32> tls_ext_bytes_remaining;
    bit<8> tls_ext_count;
    bit<1> tls_truncated;
    bit<1> tls_payload_available;
    bit<1> tcp_payload_present;
    bit<16> tls_extension_type;
    bit<16> tls_name_list_len;
    bit<8> tls_first_name_len;
    bit<1> tls_alpn_present;
    bit<1> tls_alpn_h1;
    bit<1> tls_alpn_h2;
    bit<1> tls_alpn_other;
    bit<16> tls_sni_length;
    bit<2> tls_sni_len_bucket;
}
