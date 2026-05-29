package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class RunRetryResponse {
    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("run_id")
    public String runId;

    @SerializedName("source_run_id")
    public String sourceRunId;

    @SerializedName("state")
    public String state;

    @SerializedName("queue_position")
    public int queuePosition;
}
