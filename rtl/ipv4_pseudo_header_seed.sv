`timescale 1ns/1ps

// Fold an IPv4 transport pseudo-header into an uncomplemented checksum seed.
module ipv4_pseudo_header_seed (
    input  logic [31:0] source_ipv4,
    input  logic [31:0] destination_ipv4,
    input  logic [7:0]  protocol,
    input  logic [15:0] transport_length,
    output logic [15:0] seed
);
    logic [18:0] raw_sum;
    logic [19:0] first_fold;
    logic [19:0] second_fold;
    logic [15:0] protocol_word;

    always_comb begin
        protocol_word = {8'h00, protocol};
        raw_sum = {3'd0, source_ipv4[31:16]}
                + {3'd0, source_ipv4[15:0]}
                + {3'd0, destination_ipv4[31:16]}
                + {3'd0, destination_ipv4[15:0]}
                + {3'd0, protocol_word}
                + {3'd0, transport_length};
        first_fold = {4'd0, raw_sum[15:0]} + {17'd0, raw_sum[18:16]};
        second_fold = {4'd0, first_fold[15:0]}
                    + {16'd0, first_fold[19:16]};
        seed = second_fold[15:0] + {12'd0, second_fold[19:16]};
    end
endmodule
