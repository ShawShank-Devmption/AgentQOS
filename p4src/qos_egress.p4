// P3.13/P3.9: included inside EgressPipeline.apply.
if (standard_metadata.instance_type == 32w1) {
    hdr.cpu_header.setValid();
    hdr.cpu_header.flow_hash = meta.flow_hash;
    hdr.cpu_header.class = (bit<8>) meta.traffic_class;
    hdr.cpu_header.reason = meta.punt_reason;
    hdr.cpu_header.ingress_port = meta.original_ingress_port;
    hdr.cpu_header.pad = 0;
} else {
#ifdef AAN_GATEWAY_MODE
    // C1: suppress reflected flood replicas, independently of classification (§7.15).
    if (standard_metadata.egress_port == standard_metadata.ingress_port) {
        mark_to_drop(standard_metadata);
    }
#endif
    // §7.7: only egress updates the congestion flag, with separate set/clear thresholds.
    @atomic {
#ifdef AAN_GATEWAY_MODE
        reg_congestion_flag.read(meta.congestion,
            standard_metadata.egress_port == (bit<9>) GATEWAY_LAN_PORT ? 32w0 : 32w1);
#else
        reg_congestion_flag.read(meta.congestion, 0);
#endif
        if (standard_metadata.deq_qdepth >= (bit<19>) CONGESTION_HIGH_PACKETS) {
            meta.congestion = 1;
#ifdef AAN_GATEWAY_MODE
            reg_congestion_flag.write(
                standard_metadata.egress_port == (bit<9>) GATEWAY_LAN_PORT ? 32w0 : 32w1, 1);
#else
            reg_congestion_flag.write(0, 1);
#endif
        } else if (standard_metadata.deq_qdepth <= (bit<19>) CONGESTION_LOW_PACKETS) {
            meta.congestion = 0;
#ifdef AAN_GATEWAY_MODE
            reg_congestion_flag.write(
                standard_metadata.egress_port == (bit<9>) GATEWAY_LAN_PORT ? 32w0 : 32w1, 0);
#else
            reg_congestion_flag.write(0, 0);
#endif
        }
    }
    if (meta.congestion == 1 && meta.ecn_threshold != 0 && hdr.ipv4.isValid() &&
        standard_metadata.deq_qdepth >= (bit<19>) meta.ecn_threshold &&
        (hdr.ipv4.diffserv & 8w3) != 0) {
        hdr.ipv4.diffserv = (hdr.ipv4.diffserv & 8w252) | 8w3;
    }
}
