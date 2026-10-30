// P4.1: design §4.1/§10 static application-port baseline.
#include <core.p4>
#include <v1model.p4>

#include "headers.p4"
#include "parser.p4"

@name(".reg_congestion_flag") register<bit<1>>(1) reg_congestion_flag;

control VerifyChecksumControl(inout headers_t hdr, inout metadata_t meta) {
    apply { }
}

control IngressPipeline(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    @name(".policy_version")
    register<bit<32>>(1) policy_version;

    #include "qos.p4"

    @name(".set_diffserv_class")
    action set_diffserv_class(bit<2> traffic_class) {
        meta.traffic_class = traffic_class;
    }

    // DESIGN GAP: baseline-only exact port table; frozen system tables remain unchanged.
    @name(".tbl_diffserv_port")
    table tbl_diffserv_port {
        key = {
            meta.protocol: exact;
            meta.server_port: exact;
        }
        actions = { set_diffserv_class; }
        size = 1024;
        default_action = set_diffserv_class(CLASS_UNKNOWN);
    }

    @name(".set_egress_port")
    action set_egress_port(bit<9> port) {
        standard_metadata.egress_spec = port;
    }

    @name(".flood")
    action flood() {
        standard_metadata.mcast_grp = (bit<16>) FLOOD_GROUP;
    }

    @name(".tbl_l2_forward")
    table tbl_l2_forward {
        key = { hdr.ethernet.dst_addr: exact; }
        actions = { set_egress_port; flood; }
        size = 1024;
        default_action = flood();
    }

    apply {
        // §7.15: uncertain parse paths keep UNKNOWN/best-effort forwarding.
        if (meta.feature_valid == 1) {
            meta.protocol = hdr.ipv4.protocol;
            if (hdr.tcp.isValid()) {
                if (standard_metadata.ingress_port == (bit<9>) TARGET_SWITCH_PORT) {
                    meta.server_port = hdr.tcp.src_port;
                } else {
                    meta.server_port = hdr.tcp.dst_port;
                }
            } else if (hdr.udp.isValid()) {
                if (standard_metadata.ingress_port == (bit<9>) TARGET_SWITCH_PORT) {
                    meta.server_port = hdr.udp.src_port;
                } else {
                    meta.server_port = hdr.udp.dst_port;
                }
            }
            tbl_diffserv_port.apply();
        }
        if (hdr.ethernet.isValid()) { tbl_l2_forward.apply(); }
        tbl_class_action.apply();
        reg_congestion_flag.read(meta.congestion, 0);
        // §7.16: interactive red is ECN-only; only bulk may be dropped on red.
        if (meta.meter_id == 1) {
            M_AI.execute_meter((bit<32>) meta.congestion, meta.meter_color);
        } else if (meta.meter_id == 2) {
            M_AB.execute_meter((bit<32>) meta.congestion, meta.meter_color);
        }
        if (meta.congestion == 1 && meta.meter_color == 2 && hdr.ipv4.isValid()) {
            if ((hdr.ipv4.diffserv & 8w3) != 0) {
                hdr.ipv4.diffserv = (hdr.ipv4.diffserv & 8w252) | 8w3;
            } else if (meta.traffic_class == CLASS_AGENT_BULK) {
                mark_to_drop(standard_metadata);
            }
        }
    }
}

control EgressPipeline(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    apply {
        #include "qos_egress.p4"
    }
}

control ComputeChecksumControl(inout headers_t hdr, inout metadata_t meta) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.total_len, hdr.ipv4.identification, hdr.ipv4.flags,
              hdr.ipv4.frag_offset, hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.src_addr, hdr.ipv4.dst_addr },
            hdr.ipv4.hdr_checksum,
            HashAlgorithm.csum16);
    }
}

control PacketDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.ipv4_options);
        packet.emit(hdr.tcp);
        packet.emit(hdr.tcp_options);
        packet.emit(hdr.udp);
        packet.emit(hdr.tls_hello_fixed);
        packet.emit(hdr.tls_session);
        packet.emit(hdr.tls_cipher_length);
        packet.emit(hdr.tls_ciphers);
        packet.emit(hdr.tls_compression_length);
        packet.emit(hdr.tls_compression);
        packet.emit(hdr.tls_extensions_length);
        packet.emit(hdr.tls_extensions);
    }
}

V1Switch(
    PacketParser(), VerifyChecksumControl(), IngressPipeline(), EgressPipeline(),
    ComputeChecksumControl(), PacketDeparser()
) main;
