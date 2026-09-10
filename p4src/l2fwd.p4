#include <core.p4>
#include <v1model.p4>

const bit<16> M1_FLOOD_GROUP = 1;

header ethernet_t {
    bit<48> dst_addr;
    bit<48> src_addr;
    bit<16> ether_type;
}

struct headers_t {
    ethernet_t ethernet;
}

struct metadata_t { }

parser PacketParser(
    packet_in packet,
    out headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    state start {
        packet.extract(hdr.ethernet);
        transition accept;
    }
}

control VerifyChecksum(
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

control ComputeChecksum(
    inout headers_t hdr,
    inout metadata_t meta)
{
    apply { }
}

control PacketDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet);
    }
}

V1Switch(
    PacketParser(),
    VerifyChecksum(),
    IngressPipeline(),
    EgressPipeline(),
    ComputeChecksum(),
    PacketDeparser()
) main;
