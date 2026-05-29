package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class RunCancelResponse {
    @SerializedName("run_id")
    public String runId;

    @SerializedName("status")
    public String status;

    @SerializedName("cancelled")
    public boolean cancelled;
}
