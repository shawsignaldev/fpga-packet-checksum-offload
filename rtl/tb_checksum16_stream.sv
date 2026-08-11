`timescale 1ns/1ps

module tb_checksum16_stream;
    logic clk;
    initial clk = 1'b0;
    always #5 clk = ~clk;

    logic reset_n16;
    logic request_valid16;
    logic request_ready16;
    logic [63:0] request_data16;
    logic [7:0] request_keep16;
    logic request_first16;
    logic request_last16;
    logic [15:0] request_seed16;
    logic response_ready16;
    logic response_valid16;
    logic [2:0] response_status16;
    logic [15:0] response_checksum16;
    logic [15:0] response_folded_sum16;
    logic [15:0] response_byte_length16;

    logic reset_n4;
    logic request_valid4;
    logic request_ready4;
    logic [63:0] request_data4;
    logic [7:0] request_keep4;
    logic request_first4;
    logic request_last4;
    logic [15:0] request_seed4;
    logic response_ready4;
    logic response_valid4;
    logic [2:0] response_status4;
    logic [15:0] response_checksum4;
    logic [15:0] response_folded_sum4;
    logic [3:0] response_byte_length4;

    logic [31:0] pseudo_source;
    logic [31:0] pseudo_destination;
    logic [7:0] pseudo_protocol;
    logic [15:0] pseudo_length;
    logic [15:0] pseudo_seed;

    checksum16_stream #(
        .DATA_WIDTH(64),
        .KEEP_WIDTH(8),
        .LENGTH_WIDTH(16)
    ) dut16 (
        .clk(clk),
        .reset_n(reset_n16),
        .request_valid(request_valid16),
        .request_ready(request_ready16),
        .request_data(request_data16),
        .request_keep(request_keep16),
        .request_first(request_first16),
        .request_last(request_last16),
        .request_seed(request_seed16),
        .response_ready(response_ready16),
        .response_valid(response_valid16),
        .response_status(response_status16),
        .response_checksum(response_checksum16),
        .response_folded_sum(response_folded_sum16),
        .response_byte_length(response_byte_length16)
    );

    checksum16_stream #(
        .DATA_WIDTH(64),
        .KEEP_WIDTH(8),
        .LENGTH_WIDTH(4)
    ) dut4 (
        .clk(clk),
        .reset_n(reset_n4),
        .request_valid(request_valid4),
        .request_ready(request_ready4),
        .request_data(request_data4),
        .request_keep(request_keep4),
        .request_first(request_first4),
        .request_last(request_last4),
        .request_seed(request_seed4),
        .response_ready(response_ready4),
        .response_valid(response_valid4),
        .response_status(response_status4),
        .response_checksum(response_checksum4),
        .response_folded_sum(response_folded_sum4),
        .response_byte_length(response_byte_length4)
    );

    ipv4_pseudo_header_seed pseudo_header (
        .source_ipv4(pseudo_source),
        .destination_ipv4(pseudo_destination),
        .protocol(pseudo_protocol),
        .transport_length(pseudo_length),
        .seed(pseudo_seed)
    );

    checksum_stream_monitor #(.LENGTH_WIDTH(16)) monitor16 (
        .clk(clk),
        .reset_n(reset_n16),
        .request_valid(request_valid16),
        .request_ready(request_ready16),
        .request_data(request_data16),
        .request_first(request_first16),
        .request_last(request_last16),
        .request_keep(request_keep16),
        .request_seed(request_seed16),
        .response_valid(response_valid16),
        .response_ready(response_ready16),
        .response_status(response_status16),
        .response_checksum(response_checksum16),
        .response_folded_sum(response_folded_sum16),
        .response_byte_length(response_byte_length16)
    );

    checksum_stream_monitor #(.LENGTH_WIDTH(4)) monitor4 (
        .clk(clk),
        .reset_n(reset_n4),
        .request_valid(request_valid4),
        .request_ready(request_ready4),
        .request_data(request_data4),
        .request_first(request_first4),
        .request_last(request_last4),
        .request_keep(request_keep4),
        .request_seed(request_seed4),
        .response_valid(response_valid4),
        .response_ready(response_ready4),
        .response_status(response_status4),
        .response_checksum(response_checksum4),
        .response_folded_sum(response_folded_sum4),
        .response_byte_length(response_byte_length4)
    );

    task automatic mismatch(
        input string case_name,
        input integer cycle_number,
        input string field_name
    );
        begin
            $fatal(1, "%s cycle %0d: %s mismatch", case_name, cycle_number, field_name);
        end
    endtask

    task automatic check_reset16;
        begin
            if (response_valid16 !== 1'b0)
                $fatal(1, "RESET_FIELD_RESPONSE_VALID");
            if (response_status16 !== 3'd0)
                $fatal(1, "RESET_FIELD_STATUS");
            if (response_checksum16 !== 16'd0)
                $fatal(1, "RESET_FIELD_CHECKSUM");
            if (response_folded_sum16 !== 16'd0)
                $fatal(1, "RESET_FIELD_FOLDED_SUM");
            if (response_byte_length16 !== 16'd0)
                $fatal(1, "RESET_FIELD_BYTE_LENGTH");
        end
    endtask

    task automatic check_reset4;
        begin
            if (response_valid4 !== 1'b0)
                $fatal(1, "RESET_FIELD_RESPONSE_VALID");
            if (response_status4 !== 3'd0)
                $fatal(1, "RESET_FIELD_STATUS");
            if (response_checksum4 !== 16'd0)
                $fatal(1, "RESET_FIELD_CHECKSUM");
            if (response_folded_sum4 !== 16'd0)
                $fatal(1, "RESET_FIELD_FOLDED_SUM");
            if (response_byte_length4 !== 4'd0)
                $fatal(1, "RESET_FIELD_BYTE_LENGTH");
        end
    endtask

    task automatic vector_reset16;
        begin
            @(negedge clk);
            reset_n16 = 1'b0;
            request_valid16 = 1'b0;
            request_data16 = 64'd0;
            request_keep16 = 8'd0;
            request_first16 = 1'b0;
            request_last16 = 1'b0;
            request_seed16 = 16'd0;
            response_ready16 = 1'b1;
            @(posedge clk);
            #1;
            check_reset16();
            @(negedge clk);
            reset_n16 = 1'b1;
            response_ready16 = 1'b0;
        end
    endtask

    task automatic vector_reset4;
        begin
            @(negedge clk);
            reset_n4 = 1'b0;
            request_valid4 = 1'b0;
            request_data4 = 64'd0;
            request_keep4 = 8'd0;
            request_first4 = 1'b0;
            request_last4 = 1'b0;
            request_seed4 = 16'd0;
            response_ready4 = 1'b1;
            @(posedge clk);
            #1;
            check_reset4();
            @(negedge clk);
            reset_n4 = 1'b1;
            response_ready4 = 1'b0;
        end
    endtask

    task automatic vector_cycle16(
        input string case_name,
        input integer cycle_number,
        input logic reset_value,
        input logic valid_value,
        input logic [63:0] data_value,
        input logic [7:0] keep_value,
        input logic first_value,
        input logic last_value,
        input logic [15:0] seed_value,
        input logic ready_value,
        input logic expected_request_ready,
        input logic expected_accept,
        input logic expected_response_valid,
        input logic [2:0] expected_status,
        input logic [15:0] expected_checksum,
        input logic [15:0] expected_folded_sum,
        input logic [15:0] expected_length
    );
        begin
            @(negedge clk);
            reset_n16 = reset_value;
            request_valid16 = valid_value;
            request_data16 = data_value;
            request_keep16 = keep_value;
            request_first16 = first_value;
            request_last16 = last_value;
            request_seed16 = seed_value;
            response_ready16 = ready_value;
            #1;
            if (request_ready16 !== expected_request_ready)
                mismatch(case_name, cycle_number, "request_ready pre-edge");
            if ((reset_n16 && request_valid16 && request_ready16) !== expected_accept)
                mismatch(case_name, cycle_number, "request acceptance pre-edge");
            @(posedge clk);
            #1;
            if (response_valid16 !== expected_response_valid)
                mismatch(case_name, cycle_number, "response_valid post-edge");
            if (!reset_value) begin
                check_reset16();
            end else if (expected_response_valid) begin
                if (response_status16 !== expected_status)
                    mismatch(case_name, cycle_number, "response_status");
                if (response_checksum16 !== expected_checksum)
                    mismatch(case_name, cycle_number, "response_checksum");
                if (response_folded_sum16 !== expected_folded_sum)
                    mismatch(case_name, cycle_number, "response_folded_sum");
                if (response_byte_length16 !== expected_length)
                    mismatch(case_name, cycle_number, "response_byte_length");
            end
        end
    endtask

    task automatic vector_cycle4(
        input string case_name,
        input integer cycle_number,
        input logic reset_value,
        input logic valid_value,
        input logic [63:0] data_value,
        input logic [7:0] keep_value,
        input logic first_value,
        input logic last_value,
        input logic [15:0] seed_value,
        input logic ready_value,
        input logic expected_request_ready,
        input logic expected_accept,
        input logic expected_response_valid,
        input logic [2:0] expected_status,
        input logic [15:0] expected_checksum,
        input logic [15:0] expected_folded_sum,
        input logic [3:0] expected_length
    );
        begin
            @(negedge clk);
            reset_n4 = reset_value;
            request_valid4 = valid_value;
            request_data4 = data_value;
            request_keep4 = keep_value;
            request_first4 = first_value;
            request_last4 = last_value;
            request_seed4 = seed_value;
            response_ready4 = ready_value;
            #1;
            if (request_ready4 !== expected_request_ready)
                mismatch(case_name, cycle_number, "request_ready pre-edge");
            if ((reset_n4 && request_valid4 && request_ready4) !== expected_accept)
                mismatch(case_name, cycle_number, "request acceptance pre-edge");
            @(posedge clk);
            #1;
            if (response_valid4 !== expected_response_valid)
                mismatch(case_name, cycle_number, "response_valid post-edge");
            if (!reset_value) begin
                check_reset4();
            end else if (expected_response_valid) begin
                if (response_status4 !== expected_status)
                    mismatch(case_name, cycle_number, "response_status");
                if (response_checksum4 !== expected_checksum)
                    mismatch(case_name, cycle_number, "response_checksum");
                if (response_folded_sum4 !== expected_folded_sum)
                    mismatch(case_name, cycle_number, "response_folded_sum");
                if (response_byte_length4 !== expected_length)
                    mismatch(case_name, cycle_number, "response_byte_length");
            end
        end
    endtask

    task automatic check_pseudo_header(
        input logic [31:0] source_value,
        input logic [31:0] destination_value,
        input logic [7:0] protocol_value,
        input logic [15:0] length_value,
        input logic [15:0] expected_seed
    );
        begin
            pseudo_source = source_value;
            pseudo_destination = destination_value;
            pseudo_protocol = protocol_value;
            pseudo_length = length_value;
            #1;
            if (pseudo_seed !== expected_seed)
                $fatal(1, "pseudo-header seed mismatch: expected %04x got %04x", expected_seed, pseudo_seed);
        end
    endtask

    task automatic run_reset_stall_recovery;
        begin
            vector_reset16();

            // Produce and stall a valid output before asserting reset.
            @(negedge clk);
            request_valid16 = 1'b1;
            request_data16 = 64'h00000000000000a5;
            request_keep16 = 8'h01;
            request_first16 = 1'b1;
            request_last16 = 1'b1;
            request_seed16 = 16'd0;
            response_ready16 = 1'b0;
            @(posedge clk);
            #1;
            if (response_valid16 !== 1'b1)
                $fatal(1, "reset-stall setup did not produce response");

            @(negedge clk);
            request_valid16 = 1'b0;
            @(posedge clk);
            #1;
            if (response_valid16 !== 1'b1)
                $fatal(1, "reset-stall setup did not preserve response");

            // Reset while response stalled and check every promised reset field.
            @(negedge clk);
            reset_n16 = 1'b0;
            @(posedge clk);
            #1;
            check_reset16();

            // A clean single-byte packet must work immediately after reset.
            @(negedge clk);
            reset_n16 = 1'b1;
            request_valid16 = 1'b1;
            request_data16 = 64'h000000000000005a;
            request_keep16 = 8'h01;
            request_first16 = 1'b1;
            request_last16 = 1'b1;
            request_seed16 = 16'd0;
            response_ready16 = 1'b0;
            @(posedge clk);
            #1;
            if (response_valid16 !== 1'b1
                || response_status16 !== 3'd0
                || response_checksum16 !== 16'ha5ff
                || response_folded_sum16 !== 16'h5a00
                || response_byte_length16 !== 16'd1) begin
                $fatal(1, "reset-stall clean recovery failed");
            end
        end
    endtask

    `include "checksum_vectors.svh"

    initial begin
        reset_n16 = 1'b0;
        request_valid16 = 1'b0;
        request_data16 = 64'd0;
        request_keep16 = 8'd0;
        request_first16 = 1'b0;
        request_last16 = 1'b0;
        request_seed16 = 16'd0;
        response_ready16 = 1'b0;
        reset_n4 = 1'b0;
        request_valid4 = 1'b0;
        request_data4 = 64'd0;
        request_keep4 = 8'd0;
        request_first4 = 1'b0;
        request_last4 = 1'b0;
        request_seed4 = 16'd0;
        response_ready4 = 1'b0;
        pseudo_source = 32'd0;
        pseudo_destination = 32'd0;
        pseudo_protocol = 8'd0;
        pseudo_length = 16'd0;

        repeat (2) @(posedge clk);

        check_pseudo_header(32'hc0000201, 32'hc6336402, 8'd17, 16'd32, 16'hec68);
        check_pseudo_header(32'h0a000001, 32'h0a0000fe, 8'd6, 16'd1460, 16'h1ab9);
        check_pseudo_header(32'hffffffff, 32'hffffffff, 8'd255, 16'hffff, 16'h00ff);

        run_checksum_vectors_v1();
        run_reset_stall_recovery();

        $display("ALL CHECKSUM STREAM TESTS PASSED");
        $finish;
    end
endmodule
