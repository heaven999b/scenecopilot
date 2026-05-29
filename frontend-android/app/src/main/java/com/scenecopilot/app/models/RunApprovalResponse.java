package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class RunApprovalResponse {
    @SerializedName("run_id")
    public String runId;

    @SerializedName("status")
    public String status;

    @SerializedName("approval_status")
    public String approvalStatus;

    @SerializedName("reviewer_note")
    public String reviewerNote;

    @SerializedName("continuation_run_id")
    public String continuationRunId;

    @SerializedName("continuation_queue_position")
    public Integer continuationQueuePosition;
}
