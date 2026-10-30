#include <core.p4>
#include <v1model.p4>

#include "headers.p4"
#include "parser.p4"

@name(".reg_congestion_flag") register<bit<1>>(1) reg_congestion_flag;

control VerifyChecksumControl(
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
    @name(".policy_version")
    register<bit<32>>(1) policy_version;

    #include "features.p4"
    #include "classifier.p4"
    #include "qos.p4"

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
        meta.slot_owned = 0;
        meta.punt_reason = 0;
        meta.punt_requested = 0;
        meta.first_payload_hello = 0;
        meta.original_ingress_port = (bit<16>) standard_metadata.ingress_port;
        if (meta.feature_valid == 1) {
            canonical_key();
            // §7.2: ownership check/reset and all state updates form one transaction.
            @atomic {
                claim_slot();
                if (meta.slot_owned == 1) {
                    update_timing_and_bytes();
                    derive_flow_features();
                    update_sketch();
                }
            }
        }
        meta.tree_node = 0;
        if (meta.feature_valid == 1 && meta.slot_owned == 1 &&
            meta.features.pkt_count >= WARMUP_PACKETS) {
            tbl_tree_l0.apply();
            if (meta.tree_node != 16w65535) { tbl_tree_l1.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l2.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l3.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l4.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l5.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l6.apply(); }
            if (meta.tree_node != 16w65535) { tbl_tree_l7.apply(); }
        }
        // §7.1: an exact override may classify a live collision without changing its owner.
        if (meta.key_valid == 1) { tbl_flow_override.apply(); }
        if (meta.slot_owned == 1 && meta.feature_valid == 1) {
            // §7.2: publish the current label atomically for controller snapshots.
            @atomic { reg_flow_class.write((bit<32>) meta.flow_hash, meta.traffic_class); }
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
        if (meta.first_payload_hello == 1) {
            meta.punt_reason = (bit<8>) PUNT_REASON_FIRST_PACKET;
        }
        if (meta.punt_reason != 0) {
            tbl_punt_filter.apply();
            if (meta.punt_requested == 1) {
                // §7.6: only clones consume punt tokens; forwarding is independent.
                M_PUNT.execute_meter(0, meta.meter_color);
                if (meta.meter_color != 2) {
                    clone_preserving_field_list(CloneType.I2E, CPU_CLONE_SESSION, 1);
                }
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

control ComputeChecksumControl(
    inout headers_t hdr,
    inout metadata_t meta)
{
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            {hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv, hdr.ipv4.total_len,
             hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.frag_offset,
             hdr.ipv4.ttl, hdr.ipv4.protocol, hdr.ipv4.src_addr, hdr.ipv4.dst_addr},
            hdr.ipv4.hdr_checksum, HashAlgorithm.csum16);
    }
}

control PacketDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.cpu_header);
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
    PacketParser(),
    VerifyChecksumControl(),
    IngressPipeline(),
    EgressPipeline(),
    ComputeChecksumControl(),
    PacketDeparser()
) main;
