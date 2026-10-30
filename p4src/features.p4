// P3.1: design §5.2. Included inside IngressPipeline.
@name(".reg_flow_key") register<bit<32>>(FLOW_SLOTS) reg_flow_key;
@name(".reg_last_ts") register<bit<48>>(FLOW_SLOTS) reg_last_ts;
@name(".reg_first_ts") register<bit<48>>(FLOW_SLOTS) reg_first_ts;
@name(".reg_iat_ewma") register<bit<32>>(FLOW_SLOTS) reg_iat_ewma;
@name(".reg_iat_var_ewma") register<bit<32>>(FLOW_SLOTS) reg_iat_var_ewma;
@name(".reg_pkt_count") register<bit<32>>(FLOW_SLOTS) reg_pkt_count;
@name(".reg_bytes_up") register<bit<32>>(FLOW_SLOTS) reg_bytes_up;
@name(".reg_bytes_down") register<bit<32>>(FLOW_SLOTS) reg_bytes_down;
@name(".reg_first_sizes") register<bit<16>>(FLOW_SLOTS * FIRST_SIZE_COUNT) reg_first_sizes;
@name(".reg_flow_class") register<bit<2>>(FLOW_SLOTS) reg_flow_class;
@name(".reg_proto_meta") register<bit<16>>(FLOW_SLOTS) reg_proto_meta;
@name(".reg_cm_sketch_0") register<bit<16>>(CM_SKETCH_ROWS * CM_SKETCH_COLUMNS) reg_cm_sketch_0;
@name(".reg_cm_sketch_1") register<bit<16>>(CM_SKETCH_ROWS * CM_SKETCH_COLUMNS) reg_cm_sketch_1;
@name(".reg_epoch_flag") register<bit<1>>(1) reg_epoch_flag;
@name(".reg_collision_ctr") register<bit<32>>(1) reg_collision_ctr;

bit<32> owner;
bit<32> count;
bit<48> last_ts;
bit<48> first_ts;
bit<48> gap;
bit<32> mean;
bit<32> variance;
bit<32> up;
bit<32> down;
bit<16> proto_meta;
bit<32> sample;
bit<32> deviation;
bit<32> collision_count;
bit<32> size_index;
bit<16> size_value;
bit<5> count_shift;
bit<5> down_shift;
bit<64> ratio;
bit<64> mean_size;
bit<1> epoch;
bit<32> column;
bit<32> sketch_index;
bit<16> sketch_value;

// §7.1: canonical directional key is shared by slot hash and exact overrides.
action canonical_key() {
    meta.key_valid = 1;
    meta.protocol = hdr.ipv4.protocol;
    meta.client_ip = hdr.ipv4.src_addr;
    meta.server_ip = hdr.ipv4.dst_addr;
    if (hdr.tcp.isValid()) {
        meta.client_port = hdr.tcp.src_port;
        meta.server_port = hdr.tcp.dst_port;
        meta.payload_bytes = meta.transport_bytes - meta.tcp_header_bytes;
    } else {
        meta.client_port = hdr.udp.src_port;
        meta.server_port = hdr.udp.dst_port;
        meta.payload_bytes = (bit<32>) hdr.udp.length - 8;
    }
    meta.upstream = 1;
    if (standard_metadata.ingress_port == (bit<9>) TARGET_SWITCH_PORT) {
        meta.upstream = 0;
        meta.client_ip = hdr.ipv4.dst_addr;
        meta.server_ip = hdr.ipv4.src_addr;
        if (hdr.tcp.isValid()) {
            meta.client_port = hdr.tcp.dst_port;
            meta.server_port = hdr.tcp.src_port;
        } else {
            meta.client_port = hdr.udp.dst_port;
            meta.server_port = hdr.udp.src_port;
        }
    }
    hash(meta.flow_hash, HashAlgorithm.crc32, 16w0,
        {meta.client_ip, meta.server_ip, meta.protocol, meta.client_port, meta.server_port,
         (bit<8>) FLOW_KEY_SLOT_SALT}, FLOW_SLOTS);
    // A 64-bit max preserves the full 32-bit CRC domain.
    hash(meta.flow_tag, HashAlgorithm.crc32, 32w0,
        {meta.client_ip, meta.server_ip, meta.protocol, meta.client_port, meta.server_port,
         (bit<8>) FLOW_KEY_TAG_SALT}, 64w4294967296);
}

action claim_slot() {
    reg_flow_key.read(owner, (bit<32>) meta.flow_hash);
    reg_pkt_count.read(count, (bit<32>) meta.flow_hash);
    reg_last_ts.read(last_ts, (bit<32>) meta.flow_hash);
    gap = standard_metadata.ingress_global_timestamp - last_ts;
    meta.slot_owned = 1;
    meta.new_flow = 0;
    if (count == 0 || (owner != meta.flow_tag && gap > (bit<48>) IDLE_TIMEOUT_US)) {
        // §7.11: reset EVERY per-slot field before ownership publication.
        reg_first_ts.write((bit<32>) meta.flow_hash, standard_metadata.ingress_global_timestamp);
        reg_last_ts.write((bit<32>) meta.flow_hash, standard_metadata.ingress_global_timestamp);
        reg_iat_ewma.write((bit<32>) meta.flow_hash, 0);
        reg_iat_var_ewma.write((bit<32>) meta.flow_hash, 0);
        reg_pkt_count.write((bit<32>) meta.flow_hash, 0);
        reg_bytes_up.write((bit<32>) meta.flow_hash, 0);
        reg_bytes_down.write((bit<32>) meta.flow_hash, 0);
        reg_proto_meta.write((bit<32>) meta.flow_hash, 0);
        reg_flow_class.write((bit<32>) meta.flow_hash, CLASS_UNKNOWN);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 0, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 1, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 2, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 3, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 4, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 5, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 6, 0);
        reg_first_sizes.write((bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 7, 0);
        reg_flow_key.write((bit<32>) meta.flow_hash, meta.flow_tag);
        count = 0;
        meta.new_flow = 1;
    } else if (owner != meta.flow_tag) {
        // §7.1: a live collision never mutates the owner's state.
        meta.slot_owned = 0;
        meta.feature_valid = 0;
        reg_collision_ctr.read(collision_count, 0);
        reg_collision_ctr.write(0, collision_count |+| 1);
    }
}

action update_timing_and_bytes() {
    reg_last_ts.read(last_ts, (bit<32>) meta.flow_hash);
    reg_first_ts.read(first_ts, (bit<32>) meta.flow_hash);
    reg_iat_ewma.read(mean, (bit<32>) meta.flow_hash);
    reg_iat_var_ewma.read(variance, (bit<32>) meta.flow_hash);
    reg_bytes_up.read(up, (bit<32>) meta.flow_hash);
    reg_bytes_down.read(down, (bit<32>) meta.flow_hash);
    gap = standard_metadata.ingress_global_timestamp - last_ts;
    sample = gap > 48w0xffffffff ? 32w0xffffffff : (bit<32>) gap;
    // §7.12: no first-packet or zero-gap update; §7.3: clamp before narrowing.
    if (count != 0 && sample != 0) {
        if (mean == 0) {
            mean = sample;
        } else {
            deviation = sample >= mean ? sample - mean : mean - sample;
            if (sample >= mean) { mean = mean |+| ((sample - mean) >> (bit<8>) EWMA_SHIFT); }
            else { mean = mean - ((mean - sample) >> (bit<8>) EWMA_SHIFT); }
            if (deviation >= variance) {
                variance = variance |+| ((deviation - variance) >> (bit<8>) EWMA_SHIFT);
            } else { variance = variance - ((variance - deviation) >> (bit<8>) EWMA_SHIFT); }
        }
    }
    reg_iat_ewma.write((bit<32>) meta.flow_hash, mean);
    reg_iat_var_ewma.write((bit<32>) meta.flow_hash, variance);
    reg_last_ts.write((bit<32>) meta.flow_hash, standard_metadata.ingress_global_timestamp);
    if (count < FIRST_SIZE_COUNT) {
        size_index = (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + count;
        size_value = standard_metadata.packet_length > 65535 ? 16w65535 :
            (bit<16>) standard_metadata.packet_length;
        reg_first_sizes.write(size_index, size_value);
    }
    count = count |+| 1;
    reg_pkt_count.write((bit<32>) meta.flow_hash, count);
    if (meta.upstream == 1) { up = up |+| standard_metadata.packet_length; }
    else { down = down |+| standard_metadata.packet_length; }
    reg_bytes_up.write((bit<32>) meta.flow_hash, up);
    reg_bytes_down.write((bit<32>) meta.flow_hash, down);
    reg_proto_meta.read(proto_meta, (bit<32>) meta.flow_hash);
    // §7.15: partial or malformed ClientHello bytes never become classifier metadata.
    if (meta.tls_parsed == 0) {
        meta.tls_ext_count = 0;
        meta.tls_alpn_class = ALPN_NONE;
    }
    // §7.11/7.15: only the first nonempty payload commits parsed protocol metadata.
    if ((proto_meta & (bit<16>) PROTO_META_PAYLOAD_SEEN) == 0 && meta.payload_bytes > 0) {
        if (meta.tls_parsed == 1) { meta.first_payload_hello = 1; }
        proto_meta = (bit<16>) PROTO_META_PAYLOAD_SEEN |
            ((bit<16>) meta.tls_ext_count << (bit<8>) PROTO_META_EXT_COUNT_SHIFT) |
            (bit<16>) meta.tls_alpn_class;
        reg_proto_meta.write((bit<32>) meta.flow_hash, proto_meta);
    }
    meta.features.iat_ewma_us = mean;
    meta.features.iat_var_ewma = variance;
    meta.features.pkt_count = count;
    meta.features.tls_ext_count = (bit<8>)
        ((proto_meta >> (bit<8>) PROTO_META_EXT_COUNT_SHIFT) & (bit<16>) PROTO_META_EXT_COUNT_MASK);
    meta.features.tls_alpn_class = (bit<8>) proto_meta;
    gap = standard_metadata.ingress_global_timestamp - first_ts;
    sample = gap > 48w0xffffffff ? 32w0xffffffff : (bit<32>) gap;
    meta.features.flow_age_ms = sample >> (bit<8>) FLOW_AGE_SHIFT;
}

action derive_flow_features() {
    // P2.4: both divisors are rounded down to a power of two, matching the Python mirror.
    count_shift = 0;
    if (count >= 32w65536) { count_shift = 16; }
    else if (count >= 32w256) { count_shift = 8; }
    if (count >= (32w1 << (count_shift + 4))) { count_shift = count_shift + 4; }
    if (count >= (32w1 << (count_shift + 2))) { count_shift = count_shift + 2; }
    if (count >= (32w1 << (count_shift + 1))) { count_shift = count_shift + 1; }
    mean_size = (bit<64>) up >> count_shift;
    meta.features.mean_pkt_size_up = mean_size > 64w65535 ? 16w65535 : (bit<16>) mean_size;

    down_shift = 0;
    if (down >= 32w65536) { down_shift = 16; }
    else if (down >= 32w256) { down_shift = 8; }
    if (down >= (32w1 << (down_shift + 4))) { down_shift = down_shift + 4; }
    if (down >= (32w1 << (down_shift + 2))) { down_shift = down_shift + 2; }
    if (down >= (32w1 << (down_shift + 1))) { down_shift = down_shift + 1; }
    ratio = ((bit<64>) up << 6) + ((bit<64>) up << 5) + ((bit<64>) up << 2);
    ratio = ratio >> down_shift;
    meta.features.updown_ratio_x100 = down == 0 || ratio > 64w65535 ?
        16w65535 : (bit<16>) ratio;

    meta.features.first8_size_bucket = 0;
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 0);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w128; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 1);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w64; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 2);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w32; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 3);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w16; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 4);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w8; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 5);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w4; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 6);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w2; }
    reg_first_sizes.read(size_value, (bit<32>) meta.flow_hash * FIRST_SIZE_COUNT + 7);
    if (size_value >= (bit<16>) FIRST_SIZE_LARGE_BYTES) { meta.features.first8_size_bucket |= 8w1; }
}

action update_sketch() {
    // §7.10: a packet uses the epoch read inside this transaction; retired arrays are
    // cleared by the controller only after a later epoch tick.
    reg_epoch_flag.read(epoch, 0);
    meta.features.fanout_new_flows = 16w65535;
    hash(column, HashAlgorithm.crc32, 32w0, {meta.client_ip, 8w0}, CM_SKETCH_COLUMNS);
    sketch_index = column;
    if (epoch == 0) {
        reg_cm_sketch_0.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_0.write(sketch_index, sketch_value);
        }
    } else {
        reg_cm_sketch_1.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_1.write(sketch_index, sketch_value);
        }
    }
    if (sketch_value < meta.features.fanout_new_flows) {
        meta.features.fanout_new_flows = sketch_value;
    }
    hash(column, HashAlgorithm.crc32, 32w0, {meta.client_ip, 8w1}, CM_SKETCH_COLUMNS);
    sketch_index = CM_SKETCH_COLUMNS + column;
    if (epoch == 0) {
        reg_cm_sketch_0.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_0.write(sketch_index, sketch_value);
        }
    } else {
        reg_cm_sketch_1.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_1.write(sketch_index, sketch_value);
        }
    }
    if (sketch_value < meta.features.fanout_new_flows) {
        meta.features.fanout_new_flows = sketch_value;
    }
    hash(column, HashAlgorithm.crc32, 32w0, {meta.client_ip, 8w2}, CM_SKETCH_COLUMNS);
    sketch_index = (CM_SKETCH_COLUMNS << 1) + column;
    if (epoch == 0) {
        reg_cm_sketch_0.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_0.write(sketch_index, sketch_value);
        }
    } else {
        reg_cm_sketch_1.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_1.write(sketch_index, sketch_value);
        }
    }
    if (sketch_value < meta.features.fanout_new_flows) {
        meta.features.fanout_new_flows = sketch_value;
    }
    hash(column, HashAlgorithm.crc32, 32w0, {meta.client_ip, 8w3}, CM_SKETCH_COLUMNS);
    sketch_index = CM_SKETCH_COLUMNS + (CM_SKETCH_COLUMNS << 1) + column;
    if (epoch == 0) {
        reg_cm_sketch_0.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_0.write(sketch_index, sketch_value);
        }
    } else {
        reg_cm_sketch_1.read(sketch_value, sketch_index);
        if (meta.new_flow == 1) {
            sketch_value = sketch_value |+| 1;
            reg_cm_sketch_1.write(sketch_index, sketch_value);
        }
    }
    if (sketch_value < meta.features.fanout_new_flows) {
        meta.features.fanout_new_flows = sketch_value;
    }
}
