#include <core.p4>
#include <v1model.p4>
#include "headers.p4"
#include "parser.p4"

const bit<16> M1_FLOOD_GROUP = 1;

control VerifyL2Checksum(
    inout headers_t hdr,
    inout metadata_t meta)
{
    apply { }
}

control IngressPipeline(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    @name("policy_version")
    register<bit<32>>(1) policy_version;

#ifdef PARSER_PROBE
    // P2.1 test build only: expose parser metadata without altering packets.
    @name("reg_parser_probe")
    register<bit<32>>(1) reg_parser_probe;
    @name("reg_parser_probe_aux")
    register<bit<32>>(1) reg_parser_probe_aux;

    action write_parser_probe() {
        bit<32> value = (bit<32>) meta.feature_valid;
        value = value | ((bit<32>) meta.tls_record_candidate << 1);
        value = value | ((bit<32>) meta.tls_truncated << 2);
        value = value | ((bit<32>) meta.tls_ext_count << 3);
        value = value | ((bit<32>) meta.tls_cipher_count << 11);
        bit<32> aux = (bit<32>) meta.tls_sni_length;
        aux = aux | ((bit<32>) meta.tls_sni_len_bucket << 16);
        aux = aux | ((bit<32>) meta.tls_alpn_present << 18);
        aux = aux | ((bit<32>) meta.tls_alpn_h1 << 19);
        aux = aux | ((bit<32>) meta.tls_alpn_h2 << 20);
        aux = aux | ((bit<32>) meta.tls_alpn_other << 21);
        aux = aux | ((bit<32>) meta.tcp_payload_present << 22);
        if (hdr.ethernet.isValid() &&
            hdr.ethernet.src_addr == 0x000000000001) {
            reg_parser_probe.write(0, value);
            reg_parser_probe_aux.write(0, aux);
        }
    }
#endif

    action set_egress_port(bit<9> port) {
        standard_metadata.egress_spec = port;
    }

    action flood() {
        standard_metadata.mcast_grp = M1_FLOOD_GROUP;
    }

    @name("tbl_l2_forward")
    table tbl_l2_forward {
        key = {
            hdr.ethernet.dst_addr: exact;
        }
        actions = {
            set_egress_port;
            flood;
        }
        size = 1024;
        default_action = flood();
    }

    apply {
        if (standard_metadata.parser_error != error.NoError) {
            // §7.15: malformed packets keep forwarding but cannot update features.
            meta.feature_valid = 0;
            meta.tls_record_candidate = 0;
        }
#ifdef PARSER_PROBE
        write_parser_probe();
#endif
        if (hdr.ethernet.isValid()) {
            tbl_l2_forward.apply();
        }
    }
}

control EgressPipeline(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    apply { }
}

control ComputeL2Checksum(
    inout headers_t hdr,
    inout metadata_t meta)
{
    apply { }
}

control PacketDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
        packet.emit(hdr.tcp_options);
        packet.emit(hdr.udp);
        packet.emit(hdr.tls_record);
        packet.emit(hdr.tls_handshake);
        packet.emit(hdr.tls_hello_fixed);
        packet.emit(hdr.tls_session_id);
        packet.emit(hdr.tls_cipher_len);
        packet.emit(hdr.tls_cipher_suites);
        packet.emit(hdr.tls_compression_len);
        packet.emit(hdr.tls_compression_methods);
        packet.emit(hdr.tls_extensions_len);
        packet.emit(hdr.tls_extensions);
    }
}

V1Switch(
    PacketParser(),
    VerifyL2Checksum(),
    IngressPipeline(),
    EgressPipeline(),
    ComputeL2Checksum(),
    PacketDeparser()
) main;
