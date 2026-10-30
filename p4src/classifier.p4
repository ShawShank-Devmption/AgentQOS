// P3.10: design §4.2/§5.3. Included inside IngressPipeline.
@name(".tree_next")
action tree_next(bit<16> next_node) {
    meta.tree_node = next_node;
}

@name(".tree_leaf")
action tree_leaf(bit<2> class, bit<8> reason) {
    meta.traffic_class = class;
    meta.punt_reason = reason;
    meta.tree_node = 16w65535;
}

@name(".set_flow_class")
action set_flow_class(bit<2> class) {
    meta.traffic_class = class;
}

// BEGIN GENERATED: ml/compile_tree.py (do not edit)
    @name(".tbl_tree_l0")
    table tbl_tree_l0 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 2;
    }

    @name(".tbl_tree_l1")
    table tbl_tree_l1 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 4;
    }

    @name(".tbl_tree_l2")
    table tbl_tree_l2 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 8;
    }

    @name(".tbl_tree_l3")
    table tbl_tree_l3 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 16;
    }

    @name(".tbl_tree_l4")
    table tbl_tree_l4 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 32;
    }

    @name(".tbl_tree_l5")
    table tbl_tree_l5 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 64;
    }

    @name(".tbl_tree_l6")
    table tbl_tree_l6 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 128;
    }

    @name(".tbl_tree_l7")
    table tbl_tree_l7 {
        key = {
            meta.tree_node : exact;
            meta.features.iat_ewma_us : range;
            meta.features.iat_var_ewma : range;
            meta.features.pkt_count : range;
            meta.features.mean_pkt_size_up : range;
            meta.features.updown_ratio_x100 : range;
            meta.features.first8_size_bucket : range;
            meta.features.fanout_new_flows : range;
            meta.features.tls_ext_count : range;
            meta.features.tls_alpn_class : range;
            meta.features.flow_age_ms : range;
        }
        actions = { tree_next; tree_leaf; NoAction; }
        default_action = NoAction();
        size = 256;
    }
// END GENERATED

@name(".tbl_flow_override")
table tbl_flow_override {
    key = {
        meta.client_ip : exact;
        meta.server_ip : exact;
        meta.protocol : exact;
        meta.client_port : exact;
        meta.server_port : exact;
    }
    actions = { set_flow_class; NoAction; }
    default_action = NoAction();
    size = 65536;
}

