// P3.13: design §4.1/§5.4. Included inside IngressPipeline.
@name(".M_AI") meter(2, MeterType.bytes) M_AI;
@name(".M_AB") meter(2, MeterType.bytes) M_AB;
@name(".M_PUNT") meter(1, MeterType.packets) M_PUNT;

@name(".set_class_action")
action set_class_action(bit<6> dscp, bit<2> queue_priority,
                        bit<2> meter_id, bit<16> ecn_threshold) {
    if (hdr.ipv4.isValid()) {
        hdr.ipv4.diffserv = ((bit<8>) dscp << 2) | (hdr.ipv4.diffserv & 8w3);
    }
    standard_metadata.priority = (bit<3>) queue_priority;
    meta.meter_id = meter_id;
    meta.ecn_threshold = ecn_threshold;
}

@name(".tbl_class_action")
table tbl_class_action {
    key = { meta.traffic_class : exact; }
    actions = { set_class_action; }
    size = 4;
    default_action = set_class_action(6w0, 2w1, 2w0, 16w0);
}

@name(".punt")
action punt() { meta.punt_requested = 1; }

@name(".tbl_punt_filter")
table tbl_punt_filter {
    key = { meta.punt_reason : exact; }
    actions = { punt; NoAction; }
    size = 2;
    default_action = NoAction();
}
